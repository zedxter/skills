#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Internet Archive Wayback Machine without a key: is a URL archived, which snapshots exist, what did a page say
at a given time, and (when asked) archive a page now. Standard library only.

    uv run wayback.py check example.com                         # first and latest good capture, archive links
    uv run wayback.py check https://example.com/ --at 2015-06   # also the capture closest to a date
    uv run wayback.py list example.com --from 2010 --to 2012 --changes   # captures whose content changed
    uv run wayback.py list example.com/blog/ --match prefix --unique -f urls   # archived URLs under a path
    uv run wayback.py get https://example.com/ --at 20150615    # what the page said then, as text
    uv run wayback.py get https://example.com/ -o page.html     # the original bytes of the latest good capture
    uv run wayback.py save https://example.com/                 # Save Page Now (publishes a public copy)

Exit codes:
    0 ok · 1 not archived, archived only as error pages, or nothing matched · 2 bad input · 3 a capture exists
    but archive.org would not return it · 4 rate-limited by archive.org · 5 Save Page Now refused or blocked ·
    6 network error or archive.org down (5xx) · 7 other · 130 interrupted
"""

import argparse
import codecs
import contextlib
import email.utils
import http.client
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

EXIT_OK, EXIT_NONE, EXIT_BAD_INPUT, EXIT_FETCH, EXIT_RATE, EXIT_SPN, EXIT_NETWORK, EXIT_OTHER = 0, 1, 2, 3, 4, 5, 6, 7

USER_AGENT = "zedxter-skills-wayback/1.0 (+https://github.com/zedxter/skills)"
WEB = "https://web.archive.org"
AVAILABILITY = "https://archive.org/wayback/available"
ARCHIVE_URL = r"https?://web\.archive\.org"  # tests point WEB elsewhere and widen this
MIN_INTERVAL = 1.0           # seconds between requests to archive.org, across runs on this machine
MAX_RETRY_AFTER = 60         # longer waits than this are reported (exit 4), not slept through
MAX_BODY = 25_000_000
CDX_FIELDS = ["timestamp", "original", "statuscode", "mimetype", "digest", "length"]
GOOD = "statuscode:[23].."   # captures the site served successfully (or as a redirect)
NO_REVISITS = "!mimetype:warc/revisit"
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml+xml", "application/javascript")


class Failure(Exception):
    def __init__(self, code: int, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.hint = code, hint


# ------------------------------------------------------------------ input

def normalize_url(value: str) -> str:
    """A web URL to look up, ASCII-safe (IDNA host, percent-encoded path); a bare host gets http://, which the
    archive treats as any scheme.

    >>> normalize_url("example.com/a?b=1"), normalize_url(" https://Example.com/ "), normalize_url("bücher.de/ä b")
    ('http://example.com/a?b=1', 'https://example.com/', 'http://xn--bcher-kva.de/%C3%A4%20b')
    >>> normalize_url("ftp://x")
    Traceback (most recent call last):
    ...
    wayback.Failure: only http and https URLs are archived: 'ftp://x'
    """
    return parse_target(value)[0]


def parse_target(value: str) -> tuple[str, str | None]:
    """(URL, timestamp) from a URL or an archive link; an archive link carries the capture time it points at."""
    s = value.strip()
    if not s:
        raise Failure(EXIT_BAD_INPUT, "empty URL")
    if re.match(r"(?i)[a-z][a-z0-9+.-]*://", s) and not re.match(r"(?i)https?://", s):
        raise Failure(EXIT_BAD_INPUT, f"only http and https URLs are archived: {value!r}")
    if not re.match(r"(?i)https?://", s):
        s = ("https://" if re.match(r"(?i)web\.archive\.org/", s) else "http://") + s
    try:
        p = urllib.parse.urlsplit(s)
        host = (p.hostname or "").lower().rstrip(".")
        port = p.port
    except ValueError:
        raise Failure(EXIT_BAD_INPUT, f"not a valid URL: {value!r}", "check the host and port") from None
    if p.username or p.password:
        raise Failure(EXIT_BAD_INPUT, "URLs with a user name or password are not looked up")
    if host == "web.archive.org":  # an archive link: the page it is a copy of, and when
        m = re.match(r"/web/(\d{1,14}|\*|\d{0,13}\*)[a-z_]*/(.+)", p.path + (f"?{p.query}" if p.query else ""))
        if not m:
            raise Failure(EXIT_BAD_INPUT, f"an archive.org link without a page in it: {value!r}")
        url, _ = parse_target(m.group(2))
        ts = m.group(1)
        return url, (ts if re.fullmatch(r"\d{4}(?:\d{2}){0,5}", ts) else None)
    is_ip = False
    with contextlib.suppress(ValueError):
        ipaddress.ip_address(host)
        is_ip = True
    if not host or (" " in host) or ("." not in host and ":" not in host and host != "localhost"):
        raise Failure(EXIT_BAD_INPUT, f"not a valid URL: {value!r}", "pass a URL such as https://example.com/page")
    if not is_ip:
        try:
            host = ".".join(label.encode("idna").decode("ascii") if label else "" for label in host.split("."))
        except UnicodeError:
            raise Failure(EXIT_BAD_INPUT, f"not a valid host name: {value!r}") from None
    netloc = (f"[{host}]" if ":" in host else host) + (f":{port}" if port else "")
    path = urllib.parse.quote(p.path or "/", safe="/%:@!$&'()*+,;=~")
    query = urllib.parse.quote(p.query, safe="=&/%:@!$'()*+,;~?")
    return urllib.parse.urlunsplit((p.scheme.lower(), netloc, path, query, "")), None


def parse_when(value: str) -> str:
    """A date or time as the archive's timestamp prefix: 2015, 2015-06, 2015-06-15, ISO 8601 with a time, or
    4–14 digits in pairs after the year.

    >>> parse_when("2015"), parse_when("2015-06-15"), parse_when("2015-06-15T10:30Z"), parse_when("20150615103000")
    ('2015', '20150615', '201506151030', '20150615103000')
    """
    v = value.strip().rstrip("Z")
    m = re.fullmatch(r"(\d{4})(?:-?(\d{2})(?:-?(\d{2})(?:[T ]?(\d{2}):?(\d{2})(?::?(\d{2}))?)?)?)?", v, re.ASCII)
    if not m:
        raise Failure(EXIT_BAD_INPUT, f"bad date {value!r}", "use YYYY, YYYY-MM, YYYY-MM-DD or a 14-digit timestamp")
    ts = "".join(g for g in m.groups() if g)
    try:
        datetime.strptime(ts + "0101000000"[len(ts) - 4:] if len(ts) < 14 else ts, "%Y%m%d%H%M%S")
    except ValueError:
        raise Failure(EXIT_BAD_INPUT, f"not a real date: {value!r}") from None
    if ts[:4] < "1990":
        raise Failure(EXIT_BAD_INPUT, f"the archive starts in 1996: {value!r}")
    return ts


def to_datetime(ts: str) -> datetime:
    """A timestamp or prefix as a datetime (missing parts: the start of the period)."""
    full = ts + "00000101000000"[len(ts):] if len(ts) < 14 else ts[:14]
    try:
        return datetime.strptime(full, "%Y%m%d%H%M%S")
    except ValueError:
        return datetime(max(1, int(full[:4] or 1)), 1, 1)


def iso(ts: str) -> str:
    """'20150615000821' → '2015-06-15T00:08:21Z'."""
    return to_datetime(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def snapshot_urls(ts: str, original: str) -> dict[str, str]:
    return {"archive_url": f"{WEB}/web/{ts}/{original}", "raw_url": f"{WEB}/web/{ts}id_/{original}"}


def same_page(a: str, b: str) -> bool:
    """Whether two URLs name the same page as the archive sees it: scheme, www., :80 and a trailing / don't count."""
    def key(u: str) -> str:
        u = re.sub(r"(?i)^https?://", "", urllib.parse.unquote(u)).split("#")[0]
        u = re.sub(r"(?i)^www\d?\.", "", u)
        u = re.sub(r":(80|443)(?=/|$)", "", u)
        return u.rstrip("/").lower()
    return key(a) == key(b)


SAFE_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f‪-‮⁦-⁩]")


def safe(s: str | None) -> str | None:
    """Text from an archived page, made safe to print: no terminal escapes, no C1 or bidi controls."""
    return SAFE_TEXT.sub("", s) if s is not None else None


# ------------------------------------------------------------------ network

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_a, **_k):  # we read Location ourselves
        return None


OPENER = urllib.request.build_opener()
NO_REDIRECT = urllib.request.build_opener(NoRedirect)


def throttle_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_CACHE_HOME")
    return Path(base or Path.home() / ".cache") / "zedxter-skills" / "wayback-slots"


@contextlib.contextmanager
def locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as fh:
        if os.name == "nt":
            import msvcrt
            for _ in range(200):
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                with contextlib.suppress(OSError):
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def throttle() -> None:
    """At most one request a second to archive.org from this machine, across concurrent runs: each request reserves
    the next free slot under a file lock, then sleeps until it (best effort if the cache folder is unwritable)."""
    try:
        path = throttle_path()
        with locked(path.with_suffix(".lock")):
            now = time.time()
            try:
                last = float(path.read_text() or 0)
            except (OSError, ValueError):
                last = 0.0
            slot = max(now, last + MIN_INTERVAL) if last <= now + 60 else now
            path.write_text(f"{slot:.3f}")
    except (OSError, RuntimeError):
        return
    wait = slot - time.time()
    if wait > 0:
        time.sleep(wait)


class Response:
    def __init__(self, status: int, url: str, headers: dict[str, str], body: bytes) -> None:
        self.status, self.url, self.headers, self.body = status, url, headers, body


def retry_after(value: str) -> int | None:
    """Seconds from a Retry-After header: a number or an HTTP date."""
    if value.strip().isdigit():
        return int(value.strip())
    with contextlib.suppress(TypeError, ValueError, IndexError):
        when = email.utils.parsedate_to_datetime(value)
        return max(0, int((when - datetime.now(UTC)).total_seconds()))
    return None


def request(url: str, timeout: float, *, follow: bool = True, method: str = "GET", data: bytes | None = None,
            headers: dict[str, str] | None = None, note=lambda m: None, limit: int = MAX_BODY) -> Response:
    """One request with the throttle and one retry after a dropped connection or a short Retry-After (never for a
    POST, which could submit twice). HTTP errors come back as a Response; a 429 beyond the retry is exit 4."""
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})}
    opener = OPENER if follow else NO_REDIRECT
    attempts = (1,) if method == "POST" else (1, 2)
    for attempt in attempts:
        throttle()
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with opener.open(req, timeout=timeout) as resp:
                body = resp.read(limit + 1)
                h = {k.lower(): v for k, v in resp.headers.items()}
                expected = h.get("content-length", "")
                if expected.isdigit() and len(body) < int(expected) <= limit:
                    raise ConnectionError(f"connection closed after {len(body)} of {expected} bytes")
                return Response(resp.status, resp.url, h, body)
        except urllib.error.HTTPError as e:
            h = {k.lower(): v for k, v in (e.headers or {}).items()}
            if e.code == 429:
                secs = retry_after(h.get("retry-after", ""))
                if attempt < attempts[-1] and (secs is None or secs <= MAX_RETRY_AFTER):
                    wait = 10 if secs is None else secs
                    note(f"archive.org rate-limited the request; waiting {wait} s once")
                    time.sleep(wait)
                    continue
                raise Failure(EXIT_RATE, "archive.org rate limit (HTTP 429)",
                              f"wait {secs if secs and secs > 60 else 60} s or more before the next request; "
                              "do not retry in a loop") from None
            body = b""
            with contextlib.suppress(Exception):
                body = e.read(200_000)
            return Response(e.code, e.geturl() or url, h, body)
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
            reason = getattr(e, "reason", e)
            if "CERTIFICATE_VERIFY_FAILED" in str(reason):
                raise Failure(EXIT_NETWORK, f"TLS certificate verification failed ({reason})",
                              "on a python.org macOS build run 'Install Certificates.command', or use uv's managed "
                              "Python; a proxy may be intercepting TLS") from None
            if attempt == attempts[-1]:
                raise Failure(EXIT_NETWORK, f"{urllib.parse.urlsplit(url).hostname}: {reason or type(e).__name__}",
                              "check the connection (HTTPS_PROXY is honoured); archive.org is sometimes slow or down: "
                              "one retry later is fine") from None
            note("connection problem, retrying once")
            time.sleep(3)
    raise AssertionError("unreachable")


# ------------------------------------------------------------------ the index and lookups

def cdx(url: str, args: argparse.Namespace, note, **params: Any) -> list[dict[str, str]] | None:
    """CDX rows as dicts, duplicates dropped; None when the index refuses this URL (HTTP 403 for some sites)."""
    q = [("url", url), ("output", "json"), ("fl", ",".join(CDX_FIELDS))]
    for k, v in params.items():
        if v is None:
            continue
        for item in (v if isinstance(v, list) else [v]):
            q.append((k, str(item)))
    query = f"{WEB}/cdx/search/cdx?{urllib.parse.urlencode(q)}"
    r = request(query, args.timeout, note=note, limit=20_000_000)
    if r.status in (502, 503, 504):  # the index server is often briefly overloaded
        note(f"the snapshot index answered HTTP {r.status}; retrying once in 5 s")
        time.sleep(5)
        r = request(query, args.timeout, note=note, limit=20_000_000)
    if r.status == 403:
        return None
    if r.status >= 500:
        raise Failure(EXIT_NETWORK, f"snapshot index error (HTTP {r.status})",
                      "archive.org is overloaded, or the query is too big: narrow it with --from/--to, or use "
                      "--match prefix on a path instead of a whole host; retry later")
    if r.status != 200:
        raise Failure(EXIT_OTHER, f"snapshot index lookup failed (HTTP {r.status})")
    text = r.body.decode("utf-8", "replace").strip()
    if not text:
        return []
    try:
        rows = json.loads(text)
    except json.JSONDecodeError:
        raise Failure(EXIT_NETWORK, "the snapshot index returned a page instead of data",
                      "archive.org is probably degraded or offline; retry later") from None
    if not isinstance(rows, list) or not rows:
        return []
    head, *data = rows
    out, seen = [], set()
    for row in data:
        if not isinstance(row, list) or len(row) != len(head):
            continue
        d = dict(zip(head, row))
        key = (d.get("timestamp"), d.get("original"), d.get("digest"))
        if key in seen:  # the index lists some captures twice
            continue
        seen.add(key)
        out.append(d)
    return out


def snapshot_record(row: dict[str, str]) -> dict[str, Any]:
    ts, original = row["timestamp"], row["original"]
    status, length = row.get("statuscode", ""), row.get("length", "")
    return {"timestamp": ts, "datetime": iso(ts), "status": int(status) if status.isdigit() else None,
            "mimetype": row.get("mimetype") or None, "length": int(length) if length.isdigit() else None,
            "digest": row.get("digest") or None, "original": original, **snapshot_urls(ts, original)}


def bare_snapshot(ts: str, original: str, status: int | None = None, mimetype: str | None = None) -> dict[str, Any]:
    """The same shape as an index row, for captures found another way."""
    return {"timestamp": ts, "datetime": iso(ts), "status": status, "mimetype": mimetype, "length": None,
            "digest": None, "original": original, **snapshot_urls(ts, original)}


class Capture:
    """What following /web/<ts>id_/<url> found: a good capture (with its bytes), an archived error page, a capture
    the index knows but archive.org will not return, or nothing."""
    def __init__(self, kind: str, snap: dict[str, Any] | None = None, response: Response | None = None) -> None:
        self.kind, self.snap, self.response = kind, snap, response


def follow_capture(url: str, ts: str, args: argparse.Namespace, note, limit: int) -> Capture:
    """The archive's own choice of capture closest to `ts`, following its redirects (a capture may itself be a
    redirect the site served). Reads at most `limit` bytes of the final capture."""
    target = f"{WEB}/web/{ts}id_/{url}"
    seen: set[str] = set()
    for _ in range(6):
        r = request(target, args.timeout, follow=False, note=note, limit=limit)
        stamp_m = re.match(rf"{ARCHIVE_URL}/web/(\d{{14}})id_/(.+)", r.url)
        memento = r.headers.get("memento-datetime")
        if r.status in (301, 302, 303, 307, 308):
            loc = urllib.parse.urljoin(r.url, r.headers.get("location", ""))
            if not re.match(rf"{ARCHIVE_URL}/web/\d{{14}}", loc) or loc in seen:
                return Capture("none")
            seen.add(loc)
            target = loc
            continue
        stamp = stamp_m.group(1) if stamp_m else None
        if memento and not stamp:
            with contextlib.suppress(TypeError, ValueError):
                stamp = email.utils.parsedate_to_datetime(memento).strftime("%Y%m%d%H%M%S")
        original = stamp_m.group(2) if stamp_m else url
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower() or None
        if r.status == 200 and memento and stamp:
            return Capture("good", bare_snapshot(stamp, original, 200, ctype), r)
        if memento and stamp:  # an archived error page: the site answered 4xx/5xx when it was captured
            return Capture("error", bare_snapshot(stamp, original, r.status, ctype))
        if r.status == 404 and stamp:  # redirected to a capture archive.org then failed to return
            return Capture("lost", bare_snapshot(stamp, original))
        if r.status == 404:
            return Capture("none")
        if r.status >= 500:
            raise Failure(EXIT_NETWORK, f"archive.org error (HTTP {r.status})", "retry later")
        return Capture("none")
    return Capture("none")


def availability(url: str, ts: str | None, args: argparse.Namespace, note) -> dict[str, Any] | None:
    """The Availability API: a last resort. It rate-limits early, so any failure here just means "not found"."""
    q = {"url": url, **({"timestamp": ts} if ts else {})}
    try:
        r = request(f"{AVAILABILITY}?{urllib.parse.urlencode(q)}", args.timeout, note=lambda m: None, limit=1_000_000)
        snap = json.loads(r.body).get("archived_snapshots", {}).get("closest") or {} if r.status == 200 else {}
    except (Failure, json.JSONDecodeError, AttributeError):
        return None
    m = re.match(rf"{ARCHIVE_URL}/web/(\d{{14}})/(.+)", str(snap.get("url", "")))
    status = str(snap.get("status", ""))
    if not snap.get("available") or not m or not status.startswith(("2", "3")):
        return None
    return bare_snapshot(m.group(1), m.group(2), int(status))


def find_snapshot(url: str, at: str | None, args: argparse.Namespace, note,
                  limit: int = 2_000_000) -> tuple[dict[str, Any], Response | None]:
    """The good capture closest to `at` (or the latest), with its bytes when the redirect already fetched them.
    Raises exit 1 when there is none (or only archived error pages) and exit 3 when the archive will not return it."""
    full = to_datetime(at).strftime("%Y%m%d%H%M%S") if at else "2"  # "2" sorts after every timestamp: the newest
    cap = follow_capture(url, full, args, note, limit)
    if cap.kind == "good":
        return cap.snap, cap.response
    # The redirect found an error page, a lost capture or nothing: ask the index for good captures only.
    rows = cdx(url, args, note, filter=[GOOD, NO_REVISITS],
               **({"closest": full, "sort": "closest", "limit": 1} if at else {"limit": -1}))
    if rows:
        snap = snapshot_record(rows[0] if at else rows[-1])
        cap2 = follow_capture(snap["original"], snap["timestamp"], args, note, limit)
        if cap2.kind == "good":
            return cap2.snap, cap2.response
        return snap, None
    if rows is None:  # the index is hidden for this site
        snap = availability(url, at, args, note)
        if snap:
            return snap, None
    if cap.kind == "error":
        raise Failure(EXIT_NONE, f"{url} is archived only as HTTP {cap.snap['status']} pages "
                      f"(the closest from {cap.snap['datetime'][:10]})",
                      "the page was already gone when it was captured; try an earlier --at, the site's home page, "
                      "or list HOST --match host")
    if cap.kind == "lost":
        raise Failure(EXIT_FETCH, f"the capture from {cap.snap['datetime']} is listed but archive.org will not "
                      "return it", f"open {cap.snap['archive_url']} in a browser, or try another --at")
    raise Failure(EXIT_NONE, f"no capture of {url}", "check the URL with: wayback.py check URL")


# ------------------------------------------------------------------ commands

def cmd_check(args: argparse.Namespace, note) -> dict[str, Any]:
    url, link_ts = parse_target(args.url)
    at = parse_when(args.at) if args.at else link_ts
    out: dict[str, Any] = {"url": url, "archived": False, "first": None, "latest": None, "closest": None,
                           "error_captures": 0, "calendar_url": f"{WEB}/web/*/{url}"}
    first = cdx(url, args, note, limit=1, filter=[GOOD, NO_REVISITS])
    if first is None:
        note("archive.org hides the snapshot index for this site; using the archive's redirect instead")
    elif first:
        out["first"] = snapshot_record(first[0])
        last = cdx(url, args, note, limit=-1, filter=[GOOD, NO_REVISITS])
        out["latest"] = snapshot_record(last[-1]) if last else out["first"]
    else:  # no good capture: are there archived error pages?
        errors = cdx(url, args, note, limit=-100, filter=[NO_REVISITS])
        if errors:
            out["error_captures"] = len(errors)
            out["latest_error"] = snapshot_record(errors[-1])
    if at or first is None:
        try:
            snap, _ = find_snapshot(url, at, args, note)
        except Failure as e:
            if e.code not in (EXIT_NONE, EXIT_FETCH):
                raise
            snap = None
        if at:
            out["closest"] = snap
            if snap and abs((to_datetime(snap["timestamp"]) - to_datetime(at)).days) > 365:
                note(f"the closest capture is from {snap['datetime'][:10]}, more than a year from the date asked for")
        elif snap:
            out["latest"] = snap
    out["archived"] = bool(out["latest"] or out["closest"] or out["first"])
    return out


def cmd_list(args: argparse.Namespace, note) -> dict[str, Any]:
    url, _ = parse_target(args.url)
    if args.unique and args.match == "exact":
        raise Failure(EXIT_BAD_INPUT, "--unique needs --match prefix, host or domain")
    if args.unique and args.changes:
        raise Failure(EXIT_BAD_INPUT, "--unique and --changes cannot be combined")
    if args.newest and args.match != "exact":
        raise Failure(EXIT_BAD_INPUT, "--newest works with an exact URL only",
                      "with --match the index answers in URL order; narrow the period with --from/--to instead")
    path_prefix = None
    query_url = url
    if args.match != "exact":
        p = urllib.parse.urlsplit(url)
        query_url = p.netloc + p.path if args.match == "prefix" else p.netloc
        if args.match == "prefix" and p.path.endswith("/") and len(p.path) > 1:
            path_prefix = p.path.lower()  # the index drops the slash: blog/ would also match blog-old
    params: dict[str, Any] = {"matchType": None if args.match == "exact" else args.match}
    if args.since:
        params["from"] = parse_when(args.since)
    if args.until:
        params["to"] = parse_when(args.until)
    filters = [] if args.revisits else [NO_REVISITS]
    if args.status:
        if not re.fullmatch(r"[1-5]\d\d", args.status, re.ASCII):
            raise Failure(EXIT_BAD_INPUT, f"bad --status {args.status!r}", "a three-digit HTTP status such as 200")
        filters.append(f"statuscode:{args.status}")
    if args.mime:
        filters.append(f"mimetype:{re.escape(args.mime.strip().lower())}")  # the index takes a regex
    params["filter"] = filters
    if args.changes:
        params["collapse"] = "digest"
    elif args.unique:
        params["collapse"] = "urlkey"
    asked = min(args.limit * (4 if path_prefix else 2), 10000)  # headroom for rows dropped below
    params["limit"] = -asked if args.newest else asked
    rows = cdx(query_url, args, note, **params)
    if rows is None:
        raise Failure(EXIT_OTHER, "archive.org does not list snapshots for this site (the index answered 403)",
                      "check and get still find individual captures: wayback.py check URL --at DATE")
    if path_prefix:
        rows = [r for r in rows if urllib.parse.urlsplit(r["original"]).path.lower().startswith(path_prefix)]
    snaps = [snapshot_record(r) for r in rows]
    if args.newest:
        snaps.reverse()
    snaps = snaps[:args.limit]
    return {"url": query_url if args.match != "exact" else url, "match": args.match,
            "order": "url" if args.match != "exact" else ("newest" if args.newest else "oldest"),
            "count": len(snaps), "snapshots": snaps}


class TextExtractor(HTMLParser):
    """Readable text from HTML with the standard library: no scripts, styles or inline SVG; block breaks kept."""
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header",
             "footer", "blockquote", "pre", "table", "ul", "ol", "dd", "dt", "hr"}
    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self.in_title = False
        self.title_done = False

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title" and not self.skip and not self.title_done:
            self.in_title = True
        elif tag in self.BLOCK:
            self.in_title = False
            self.parts.append("\n")

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == "title" and self.in_title:
            self.in_title, self.title_done = False, True
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):  # noqa: ANN001
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(re.sub(r"\s+", " ", data))  # newlines inside HTML text are just spaces

    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self.parts).splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def decode(body: bytes, headers: dict[str, str]) -> str:
    """Bytes as text: a BOM, then the Content-Type charset, then a <meta> charset, then UTF-8; text codecs only."""
    for bom, enc in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
        if body.startswith(bom):
            return body.decode(enc, errors="replace")
    m = re.search(r"charset=\"?([\w.:-]+)", headers.get("content-type", ""), re.I)
    charset = m.group(1) if m else None
    if not charset:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w.:-]+)", body[:4000], re.I)
        charset = m.group(1).decode("ascii", "replace") if m else "utf-8"
    try:
        if not codecs.lookup(charset)._is_text_encoding:  # e.g. unicode_escape would turn "\x1b" into ESC
            charset = "utf-8"
        return body.decode(charset, errors="replace")
    except (LookupError, UnicodeError, ValueError):
        return body.decode("utf-8", errors="replace")


def cmd_get(args: argparse.Namespace, note) -> dict[str, Any]:
    url, link_ts = parse_target(args.url)
    at = parse_when(args.at) if args.at else link_ts
    if args.output == "-":
        raise Failure(EXIT_BAD_INPUT, "-o needs a file name", "the text goes to stdout without -o")
    snap, resp = find_snapshot(url, at, args, note, limit=MAX_BODY)
    r = resp or request(snap["raw_url"], args.timeout, note=note)
    if r.status >= 500:
        raise Failure(EXIT_NETWORK, f"archive.org error fetching the capture (HTTP {r.status})", "retry later")
    if r.status != 200:
        raise Failure(EXIT_FETCH, f"the capture from {snap['datetime']} exists but archive.org would not return it "
                      f"(HTTP {r.status})", f"open it in a browser: {snap['archive_url']}; or try another --at")
    if len(r.body) > MAX_BODY:
        raise Failure(EXIT_FETCH, f"the capture is larger than {MAX_BODY // 1_000_000} MB",
                      f"download it directly: {snap['raw_url']}")
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    snap = {**snap, "mimetype": ctype or snap["mimetype"]}
    out: dict[str, Any] = {"url": url, "requested": iso(at) if at else "latest", "snapshot": snap,
                           "bytes": len(r.body), "title": None, "text": None, "saved_to": None}
    if at and abs((to_datetime(snap["timestamp"]) - to_datetime(at)).days) > 365:
        note(f"the closest capture is from {snap['datetime'][:10]}, more than a year from the date asked for")
    if not same_page(snap["original"], url):
        note(f"the capture is of {snap['original']}, a variant of the URL asked for")
    if args.output:
        dest = Path(args.output).expanduser()
        try:
            dest.write_bytes(r.body)
        except OSError as e:
            raise Failure(EXIT_BAD_INPUT, f"cannot write {args.output}: {e.strerror or e}") from None
        out["saved_to"] = str(dest)
    is_html = ctype in ("text/html", "application/xhtml+xml") or (not ctype and b"<html" in r.body[:2000].lower())
    if is_html:
        ex = TextExtractor()
        ex.feed(decode(r.body, r.headers))
        ex.close()
        out["title"] = safe(" ".join(ex.title.split())) or None
    if args.output and not (args.text or args.raw):
        return out  # the bytes are in the file; print only where they went
    if is_html:
        out["text"] = safe(decode(r.body, r.headers) if args.raw else ex.text())
    elif ctype.startswith(TEXT_TYPES):
        out["text"] = safe(decode(r.body, r.headers))
    elif not args.output:
        note(f"the capture is {ctype or 'binary'}; save it with -o FILE")
    return out


def spn_keys() -> tuple[str, str] | None:
    key, secret = os.environ.get("WAYBACK_ACCESS_KEY", ""), os.environ.get("WAYBACK_SECRET_KEY", "")
    return (key, secret) if key and secret else None


def private_host(host: str) -> bool:
    """Hosts the public archive cannot reach (and that should not be announced to it).

    >>> [private_host(h) for h in ("localhost.", "a.localhost", "10.0.0.1", "0x7f.0.0.1", "::ffff:127.0.0.1",
    ...                             "printer.local", "router.home.arpa", "8.8.8.8", "example.com")]
    [True, True, True, True, True, True, True, False, False]
    """
    h = host.strip("[]").rstrip(".").lower()
    if h == "localhost" or h.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa", ".corp", ".intranet")):
        return True
    ip = None
    with contextlib.suppress(ValueError):
        ip = ipaddress.ip_address(h)
    if ip is None and re.fullmatch(r"[0-9a-fx.]+", h):  # short and hex forms such as 0x7f.0.0.1 or 2130706433
        with contextlib.suppress(OSError):
            ip = ipaddress.ip_address(socket.inet_aton(h))
    if ip is None:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def server_time(r: Response) -> str:
    """The archive's own clock (Date header) as a timestamp; the local clock only as a fallback."""
    with contextlib.suppress(TypeError, ValueError):
        return email.utils.parsedate_to_datetime(r.headers.get("date", "")).astimezone(UTC).strftime("%Y%m%d%H%M%S")
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def cmd_save(args: argparse.Namespace, note) -> dict[str, Any]:
    url, _ = parse_target(args.url)
    host = urllib.parse.urlsplit(url).hostname or ""
    if private_host(host):
        raise Failure(EXIT_BAD_INPUT, f"{host} is not a public web address", "only public pages can be archived")
    keys = spn_keys()
    timeout = max(args.timeout, 120.0)
    if keys:
        # Save Page Now 2 with the user's archive.org S3 keys. Never follow a redirect with the keys attached.
        auth = {"Accept": "application/json", "Authorization": f"LOW {keys[0]}:{keys[1]}"}
        data = urllib.parse.urlencode({"url": url, "skip_first_archive": "1"}).encode()
        r = request(f"{WEB}/save", timeout, method="POST", data=data, follow=False, note=note, headers=auth)
        job = None
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            job = json.loads(r.body)
        if r.status != 200 or not isinstance(job, dict) or not isinstance(job.get("job_id"), str):
            msg = job.get("message") if isinstance(job, dict) else None
            raise Failure(EXIT_SPN, f"Save Page Now refused the request: {msg or f'HTTP {r.status}'}",
                          "check the keys; the site may exclude archiving, or the daily limit is reached")
        deadline = time.monotonic() + args.wait
        while time.monotonic() < deadline:
            time.sleep(4)
            s = request(f"{WEB}/save/status/{urllib.parse.quote(job['job_id'], safe='')}", args.timeout,
                        follow=False, note=note, headers=auth)
            st = None
            with contextlib.suppress(json.JSONDecodeError, ValueError):
                st = json.loads(s.body)
            if not isinstance(st, dict):
                continue
            if st.get("status") == "success":
                ts = str(st.get("timestamp", ""))
                if not re.fullmatch(r"\d{14}", ts):
                    raise Failure(EXIT_SPN, "Save Page Now reported success without a capture time",
                                  "check in a minute with: wayback.py check URL")
                snap = bare_snapshot(ts, str(st.get("original_url") or url))
                return {"url": url, "saved": True, "snapshot": snap, "method": "spn2"}
            if st.get("status") == "error":
                raise Failure(EXIT_SPN, f"Save Page Now failed: {st.get('message') or st.get('status_ext')}",
                              "the site may block the archive's crawler or exclude archiving")
        raise Failure(EXIT_SPN, f"the save did not finish within {args.wait:.0f} s (job {job['job_id']})",
                      "it may still complete; look later with: wayback.py check URL")
    # Anonymous Save Page Now: one request; the answer redirects to, or names, the new capture.
    r = request(f"{WEB}/save/{url}", timeout, method="GET", follow=False, note=note, limit=2_000_000)
    started = server_time(r)
    for header in ("location", "content-location"):
        loc = r.headers.get(header) or ""
        m = re.search(r"/web/(\d{14})/(.+)$", loc)
        if m and same_page(m.group(2), url):
            ts, original = m.group(1), m.group(2)
            fresh = ts >= started[:12]  # within the minute the archive says it answered
            if not fresh:
                note(f"archive.org returned an existing capture from {iso(ts)}: a recent copy already exists")
            return {"url": url, "saved": fresh, "snapshot": bare_snapshot(ts, original), "method": "anonymous"}
    if r.status in (401, 403):
        raise Failure(EXIT_SPN, f"Save Page Now needs an archive.org account for this request (HTTP {r.status})",
                      "set WAYBACK_ACCESS_KEY and WAYBACK_SECRET_KEY (archive.org/account/s3.php), or save it in a "
                      f"browser: {WEB}/save")
    if r.status == 429:
        raise Failure(EXIT_RATE, "Save Page Now rate limit", "wait several minutes; do not loop")
    if r.status >= 500 and r.status != 520:
        raise Failure(EXIT_NETWORK, f"archive.org error (HTTP {r.status})", "retry later")
    text = decode(r.body or b"", r.headers)
    if r.status == 520 or re.search(r"(?i)has been excluded|is not available for archiving|robots\.txt", text[:50000]):
        raise Failure(EXIT_SPN, "the site cannot be archived (excluded, or blocked by robots.txt)", "nothing to do from here")
    raise Failure(EXIT_SPN, f"Save Page Now did not confirm a capture (HTTP {r.status})",
                  "the save may still be in progress; check in a minute with: wayback.py check URL")


# ------------------------------------------------------------------ output

def table(payload: dict[str, Any], command: str) -> str:
    lines: list[str] = []

    def snap_line(label: str, s: dict[str, Any] | None) -> None:
        if s:
            extra = " · ".join(x for x in (str(s["status"]) if s.get("status") else "", s.get("mimetype") or "") if x)
            lines.append(f"{label:<8} {s['datetime'][:19].replace('T', ' ')}" + (f"  ({extra})" if extra else ""))
            lines.append(f"{'':<8} {s['archive_url']}")

    if command == "check":
        state = "archived" if payload["archived"] else (
            f"archived only as error pages ({payload['error_captures']})" if payload["error_captures"] else "not archived")
        lines.append(f"# {payload['url']}: {state}")
        snap_line("first", payload["first"])
        snap_line("latest", payload["latest"])
        snap_line("closest", payload["closest"])
        snap_line("error", payload.get("latest_error"))
        if payload["archived"] or payload["error_captures"]:
            lines.append(f"all:     {payload['calendar_url']}")
    elif command == "list":
        order = " (in URL order)" if payload["order"] == "url" else ""
        lines.append(f"# {payload['count']} snapshot(s) of {payload['url']}" +
                     (f" ({payload['match']})" if payload["match"] != "exact" else "") + order)
        for s in payload["snapshots"]:
            extra = " ".join(x for x in (str(s["status"] or "-"), s.get("mimetype") or "",
                                         f"{s['length']} B" if s.get("length") else "") if x)
            lines.append(f"{s['datetime'][:19].replace('T', ' ')}  {extra:<28}  {s['archive_url']}")
    elif command == "get":
        s = payload["snapshot"]
        lines.append(f"# capture {s['datetime']} of {payload['url']}")
        lines.append(f"# {s['archive_url']}")
        if payload.get("saved_to"):
            lines.append(f"# saved {payload['bytes']} bytes to {payload['saved_to']}")
        if payload.get("title"):
            lines.append(f"# title: {payload['title']}")
        if payload.get("text") is not None:
            lines += ["", payload["text"]]
    elif command == "save":
        s = payload["snapshot"]
        lines.append(f"# {'saved' if payload['saved'] else 'already archived'}: {payload['url']}")
        lines.append(f"{s['datetime'][:19].replace('T', ' ')}  {s['archive_url']}")
    return "\n".join(lines) + "\n"


def render(payload: dict[str, Any], command: str, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if fmt == "urls":
        if command == "list":
            if payload.get("match") != "exact":
                seen: list[str] = []
                for s in payload["snapshots"]:
                    if s["original"] not in seen:
                        seen.append(s["original"])
                return "".join(u + "\n" for u in seen)
            return "".join(s["archive_url"] + "\n" for s in payload["snapshots"])
        snaps = [x for x in (payload.get("closest"), payload.get("latest"), payload.get("snapshot")) if x]
        return "".join(s["archive_url"] + "\n" for s in snaps[:1])
    return table(payload, command)


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-f", "--format", choices=["table", "json", "urls"], default="table",
                        help="table (default), json (every field), urls (archive links, one per line)")
    common.add_argument("--timeout", type=float, default=45.0, help="network timeout in seconds (default 45)")
    common.add_argument("-q", "--quiet", action="store_true", help="no note: lines")
    c = sub.add_parser("check", parents=[common], help="is the URL archived: first and latest good capture")
    c.add_argument("url", help="a URL, or an archive.org link (its capture time becomes --at)")
    c.add_argument("--at", metavar="DATE", help="also the capture closest to DATE (YYYY[-MM[-DD]] or a timestamp)")
    li = sub.add_parser("list", parents=[common], help="list captures from the archive's index")
    li.add_argument("url")
    li.add_argument("--from", dest="since", metavar="DATE", help="captured on or after DATE (its start)")
    li.add_argument("--to", dest="until", metavar="DATE", help="captured on or before DATE (its whole period)")
    li.add_argument("-n", "--limit", type=int, default=50, help="rows to return (default 50, max 10000)")
    li.add_argument("--newest", action="store_true", help="the newest captures first (exact URL only)")
    li.add_argument("--status", metavar="CODE", help="only this HTTP status, e.g. 200")
    li.add_argument("--mime", metavar="TYPE", help="only this MIME type, e.g. text/html or application/pdf")
    li.add_argument("--changes", action="store_true", help="only captures whose content differs from the previous one")
    li.add_argument("--match", choices=["exact", "prefix", "host", "domain"], default="exact",
                    help="exact URL (default), every URL under a path (prefix), the whole host, or with subdomains")
    li.add_argument("--unique", action="store_true", help="with --match: one row per archived URL")
    li.add_argument("--revisits", action="store_true", help="include revisit records (unchanged re-captures)")
    g = sub.add_parser("get", parents=[common], help="fetch a capture: readable text, or the original bytes")
    g.add_argument("url", help="a URL, or an archive.org link (its capture time becomes --at)")
    g.add_argument("--at", metavar="DATE", help="the good capture closest to DATE (default: the latest)")
    g.add_argument("--text", action="store_true", help="print readable text (the default without -o)")
    g.add_argument("--raw", action="store_true", help="print the original HTML instead of text")
    g.add_argument("-o", "--output", metavar="FILE", help="save the original bytes (HTML, PDF, image…) to FILE")
    s = sub.add_parser("save", parents=[common], help="Save Page Now: archive the page as it is now (public)")
    s.add_argument("url")
    s.add_argument("--wait", type=float, default=90.0, help="with keys: seconds to wait for the capture (default 90)")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = make_parser().parse_args(argv)
    note = (lambda m: None) if args.quiet else (lambda m: sys.stderr.write(f"note: {safe(m)}\n"))

    def fail(code: int, message: str, hint: str = "") -> int:
        sys.stderr.write(f"error: {safe(message)}" + (f" — {hint}" if hint else "") + "\n")
        return code

    if not 1 <= args.timeout <= 600:
        return fail(EXIT_BAD_INPUT, "--timeout must be between 1 and 600 seconds")
    if args.command == "list" and not 1 <= args.limit <= 10000:
        return fail(EXIT_BAD_INPUT, "--limit must be between 1 and 10000")
    if args.command == "save" and not 10 <= args.wait <= 600:
        return fail(EXIT_BAD_INPUT, "--wait must be between 10 and 600 seconds")
    try:
        payload = {"check": cmd_check, "list": cmd_list, "get": cmd_get, "save": cmd_save}[args.command](args, note)
    except Failure as e:
        return fail(e.code, str(e), e.hint)
    except KeyboardInterrupt:
        return fail(130, "interrupted")
    except Exception as e:  # noqa: BLE001 - every exit code has a meaning; a crash must not pass as one of them
        return fail(EXIT_OTHER, f"unexpected {type(e).__name__}: {e}", "a bug; report it with the command line")
    try:
        sys.stdout.write(render(payload, args.command, args.format))
        sys.stdout.flush()
    except BrokenPipeError:
        with contextlib.suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    if args.command == "check" and not payload["archived"]:
        if payload["error_captures"]:
            return fail(EXIT_NONE, f"{payload['url']} is archived only as error pages",
                        "the page was already gone when captured; try the site's home page or list HOST --match host")
        return fail(EXIT_NONE, f"{payload['url']} is not archived", "try the URL without query parameters, "
                    "with or without www., or list the site: wayback.py list HOST --match host")
    if args.command == "list" and not payload["snapshots"]:
        return fail(EXIT_NONE, "no snapshots match", "loosen --from/--to/--status/--mime, or try --match prefix")
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("error: interrupted\n")
        sys.exit(130)
    except BrokenPipeError as e:
        try:
            sys.stdout.flush()
        except BrokenPipeError:  # the reader closed stdout (`| head`): stop quietly, as a pipe expects
            try:
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            except OSError:
                pass
            sys.exit(0)
        sys.stderr.write(f"error: unexpected BrokenPipeError: {e} — a bug; report it with the command line\n")
        sys.exit(EXIT_OTHER)
    except Exception as e:  # noqa: BLE001 - a crash exits 7, never 1 or another code that has a meaning
        sys.stderr.write(f"error: unexpected {type(e).__name__}: {(str(e).splitlines() or [''])[0][:300]} "
                         "— a bug; report it with the command line\n")
        sys.exit(EXIT_OTHER)
