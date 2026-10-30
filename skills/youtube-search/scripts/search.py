#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["yt-dlp>=2025.1.1"]
# ///
"""Search YouTube without an API key and print results with date, views and duration.

Uses yt-dlp's search extractor (YouTube's internal search endpoint, same one the
website uses). Works on macOS, Linux and Windows.

Run with uv (installs the dependency automatically):
    uv run search.py "query" [options]
Or with plain Python after `pip install yt-dlp`:
    python3 search.py "query" [options]

Exit codes:
    0 ok · 1 no results after filtering · 2 bad input / missing dependency
    · 4 blocked by YouTube · 6 network error · 7 other error
"""

import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
from typing import Any

try:
    import yt_dlp
    from yt_dlp.utils import DownloadError, ExtractorError
except ImportError:
    sys.stderr.write(
        "Missing dependency: yt-dlp\n"
        "Install it with:  pip install yt-dlp\n"
        "or run this script with uv:  uv run search.py ...\n"
    )
    sys.exit(2)

EXIT_OK = 0
EXIT_NO_RESULTS = 1
EXIT_BAD_INPUT = 2
EXIT_BLOCKED = 4
EXIT_NETWORK = 6
EXIT_OTHER = 7

# YouTube search filter protobuf (the `sp=` URL parameter), field numbers:
SORT = {"relevance": 0, "rating": 1, "date": 2, "views": 3}
PERIOD = {"hour": 1, "today": 2, "week": 3, "month": 4, "year": 5}
TYPE = {"video": 1, "channel": 2, "playlist": 3, "movie": 4}
DURATION = {"short": 1, "long": 2, "medium": 3}  # <4 min, >20 min, 4-20 min
DURATION_BOUNDS = {"short": (0, 240), "medium": (240, 1200), "long": (1200, None)}


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field(num: int, value: int | bytes) -> bytes:
    if isinstance(value, int):
        return _varint((num << 3) | 0) + _varint(value)
    return _varint((num << 3) | 2) + _varint(len(value)) + value


def build_sp(sort: str, period: str | None, kind: str | None, duration: str | None) -> str | None:
    """Encode YouTube's search filter parameter. Returns None when nothing is set."""
    filters = b""
    if period:
        filters += _field(1, PERIOD[period])
    if kind:
        filters += _field(2, TYPE[kind])
    if duration:
        filters += _field(3, DURATION[duration])
    msg = b""
    if SORT[sort]:
        msg += _field(1, SORT[sort])
    if filters:
        msg += _field(2, filters)
    return base64.b64encode(msg).decode() if msg else None


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "live/?"
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_views(n: int | None) -> str:
    if n is None:
        return "?"
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= div:
            v = n / div
            return f"{v:.1f}{unit}" if v < 10 else f"{v:.0f}{unit}"
    return str(n)


def iso_date(ts: int | None) -> str | None:
    return dt.datetime.fromtimestamp(ts, dt.UTC).date().isoformat() if ts else None


# Granularity of YouTube's relative dates ("8 years ago" -> "year"), keyed by the
# timestamp yt-dlp derives from them. Filled by the hook installed below.
_DATE_PRECISION: dict[int, str] = {}
# Unit spellings YouTube uses in relative times, long and abbreviated ("1y ago",
# "2mo ago", "3w ago", "4h ago"). Mirrors yt-dlp's _RELATIVE_TIME_UNIT_MAP.
_RELATIVE_UNITS: dict[str, str] = {
    "year": "year", "yr": "year", "y": "year",
    "month": "month", "mo": "month",
    "week": "day", "wk": "day", "w": "day",
    "day": "day", "d": "day",
    "hour": "day", "hr": "day", "h": "day",
    "minute": "day", "min": "day",
    "second": "day", "sec": "day", "s": "day",
}
_RELATIVE_UNIT_RE = re.compile(
    r"(\d+)\s*(%s)s?\b" % "|".join(sorted(_RELATIVE_UNITS, key=len, reverse=True)),
    re.I,
)


def relative_precision(text: str) -> str | None:
    """Map YouTube's relative time text to the precision it actually conveys.

    Years and months are only known to the year/month; anything finer keeps the
    full date. YouTube uses both long and abbreviated units:

    >>> [relative_precision(t) for t in ("8 years ago", "1y ago", "Streamed 2y ago", "1 yr ago")]
    ['year', 'year', 'year', 'year']
    >>> [relative_precision(t) for t in ("3 months ago", "2mo ago", "11 mo ago")]
    ['month', 'month', 'month']
    >>> [relative_precision(t) for t in ("3w ago", "2 weeks ago", "5 days ago", "6d ago")]
    ['day', 'day', 'day', 'day']
    >>> [relative_precision(t) for t in ("4h ago", "7 min ago", "30s ago", "12 minutes ago")]
    ['day', 'day', 'day', 'day']
    >>> [relative_precision(t) for t in ("Premieres Oct 30, 2026", "", "today")]
    [None, None, None]
    """
    m = _RELATIVE_UNIT_RE.search(text or "")
    return _RELATIVE_UNITS[m.group(2).lower()] if m else None


def approx_date(ts: int | None, precision: str | None) -> str | None:
    """Render a timestamp with no more precision than the source string had.

    year -> "2018", month -> "2026-08", anything else -> "2026-10-20".
    """
    if not ts:
        return None
    d = dt.datetime.fromtimestamp(ts, dt.UTC).date()
    if precision == "year":
        return f"{d.year:04d}"
    if precision == "month":
        return f"{d.year:04d}-{d.month:02d}"
    return d.isoformat()


def install_date_precision_hook() -> None:
    """Record the unit of each "N <unit> ago" string yt-dlp turns into a timestamp.

    yt-dlp's flat search entries only carry the derived timestamp (today minus N
    years, rounded to a day), which looks far more precise than "8 years ago".
    Wrapping the parser is the only place the original text is visible. If the
    private method disappears in a future yt-dlp, dates fall back to full
    YYYY-MM-DD, as before.
    """
    try:
        from yt_dlp.extractor.youtube import YoutubeBaseInfoExtractor as cls
    except ImportError:
        return
    orig = getattr(cls, "_parse_time_text", None)
    if orig is None or getattr(orig, "_records_precision", False):
        return

    def hooked(self, text, *args, **kwargs):
        ts = orig(self, text, *args, **kwargs)
        if ts is not None and text:
            precision = relative_precision(text)
            if precision:
                _DATE_PRECISION[int(ts)] = precision
        return ts

    hooked._records_precision = True  # type: ignore[attr-defined]
    cls._parse_time_text = hooked


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}")


def channel_search_url(channel: str, query: str) -> str:
    """Build an in-channel search URL from @handle, channel id or channel URL."""
    from urllib.parse import quote

    c = channel.strip().rstrip("/")
    if c.startswith("http"):
        base = c.split("?")[0]
        base = re.sub(r"/(videos|shorts|streams|playlists|about|search)$", "", base)
    elif c.startswith("@"):
        base = f"https://www.youtube.com/{c}"
    elif re.fullmatch(r"UC[\w-]{22}", c):
        base = f"https://www.youtube.com/channel/{c}"
    else:
        base = f"https://www.youtube.com/@{c}"
    return f"{base}/search?query={quote(query)}"


class _QuietLogger:
    """Swallow yt-dlp chatter; errors are reported by this script itself."""

    def debug(self, msg: str) -> None: ...
    def info(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


def ydl_opts(limit: int, proxy: str | None, flat: bool = True) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _QuietLogger(),
        "skip_download": True,
        "extract_flat": "in_playlist" if flat else False,
        "playlistend": limit,
        # Turns "2 years ago" into an approximate timestamp on flat entries.
        "extractor_args": {"youtubetab": {"approximate_date": ["true"]}},
    }
    if proxy:
        opts["proxy"] = proxy
    return opts


def normalize(entry: dict[str, Any]) -> dict[str, Any]:
    vid = entry.get("id")
    kind = "video"
    url = entry.get("url") or entry.get("webpage_url") or ""
    if entry.get("ie_key") == "YoutubeTab" or "/channel/" in url or "/@" in url:
        kind = "channel"
    if "list=" in url or (vid and vid.startswith(("PL", "UU", "OL", "RD"))):
        kind = "playlist"
    if kind == "video" and vid:
        url = f"https://www.youtube.com/watch?v={vid}"
    ts = entry.get("timestamp") or entry.get("release_timestamp")
    exact = entry.get("upload_date")  # YYYYMMDD, only in --details mode
    if exact:
        published, precision = f"{exact[:4]}-{exact[4:6]}-{exact[6:]}", "day"
    else:
        precision = _DATE_PRECISION.get(ts, "day") if ts else None
        published = approx_date(ts, precision)
    return {
        "kind": kind,
        "id": vid,
        "url": url,
        "title": entry.get("title") or "",
        "channel": entry.get("channel") or entry.get("uploader") or "",
        "channel_url": entry.get("channel_url") or entry.get("uploader_url"),
        "duration": entry.get("duration"),
        "view_count": entry.get("view_count"),
        "published": published,
        "published_exact": bool(exact),
        "published_precision": precision,
        "live": entry.get("live_status") in ("is_live", "is_upcoming"),
        "timestamp": ts,
    }


def dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop repeated videos, keeping the first occurrence of each id.

    YouTube's search pages repeat videos across the over-fetched pool
    (`--sort date` fetches up to 3x `-n`), so one id can appear twice.

    >>> [e["id"] for e in dedupe([{"id": "a"}, {"id": "b"}, {"id": "a"}, {"id": None}, {"id": None}])]
    ['a', 'b', None, None]
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        vid = it.get("id")
        if vid:
            if vid in seen:
                continue
            seen.add(vid)
        out.append(it)
    return out


def published_date(item: dict[str, Any]) -> dt.date | None:
    """Best available publish date for filtering (exact if known, else yt-dlp's estimate)."""
    if item["published_exact"] and item["published"]:
        return dt.date.fromisoformat(item["published"])
    ts = item.get("timestamp")
    return dt.datetime.fromtimestamp(ts, dt.UTC).date() if ts else None


def enrich(item: dict[str, Any], proxy: str | None) -> dict[str, Any]:
    """Fetch full metadata for one video (exact date, likes, captions, description)."""
    if item["kind"] != "video" or not item["id"]:
        return item
    try:
        with yt_dlp.YoutubeDL(ydl_opts(1, proxy, flat=False)) as y:
            info = y.extract_info(item["url"], download=False)
    except Exception as e:  # keep the flat entry, note the failure
        first = str(e).splitlines()[0]
        first = re.sub(r"^ERROR: \[youtube\] [\w-]+: ", "", first)
        if "not a bot" in first.lower():
            first = "YouTube bot check on this IP (datacenter/VPN); exact date, likes and captions need a residential IP or --proxy"
        item["details_error"] = first[:200]
        return item
    exact = info.get("upload_date")
    if exact:
        item["published"] = f"{exact[:4]}-{exact[4:6]}-{exact[6:]}"
        item["published_exact"] = True
        item["published_precision"] = "day"
    for k in ("duration", "view_count", "like_count", "comment_count", "channel", "channel_url", "channel_follower_count", "categories", "tags", "chapters"):
        if info.get(k) is not None:
            item[k] = info[k]
    desc = (info.get("description") or "").strip()
    item["description"] = desc[:1500] + ("…" if len(desc) > 1500 else "")
    item["captions_manual"] = sorted((info.get("subtitles") or {}).keys())
    item["captions_auto"] = sorted(
        k for k in (info.get("automatic_captions") or {}).keys() if "-" not in k or k.count("-") == 1
    )[:30]
    return item


def render_table(items: list[dict[str, Any]], query: str) -> str:
    lines = [f"# {len(items)} result(s) for: {query}", ""]
    for i, it in enumerate(items, 1):
        if it["kind"] == "video":
            date = it["published"] or "?"
            if date != "?" and not it["published_exact"]:
                date = "~" + date
            meta = " · ".join(
                x for x in (
                    it["channel"],
                    "LIVE" if it["live"] else fmt_duration(it["duration"]),
                    f"{fmt_views(it['view_count'])} views",
                    date,
                ) if x
            )
            lines.append(f"{i:>2}. {it['title']}")
            lines.append(f"    {it['url']}  |  {meta}")
            if "captions_manual" in it:
                caps = ", ".join(it["captions_manual"]) or "none"
                auto = "yes" if it.get("captions_auto") else "no"
                likes = fmt_views(it.get("like_count"))
                lines.append(f"    captions: {caps} (auto: {auto}) · likes: {likes}")
                if it.get("description"):
                    first = it["description"].splitlines()[0][:160]
                    lines.append(f"    {first}")
            if it.get("details_error"):
                lines.append(f"    details unavailable: {it['details_error']}")
        else:
            lines.append(f"{i:>2}. [{it['kind']}] {it['title']}")
            lines.append(f"    {it['url']}" + (f"  |  {it['channel']}" if it["channel"] else ""))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("query", nargs="+", help="search terms")
    p.add_argument("-n", "--limit", type=int, default=10, help="number of results (default 10, max 100)")
    p.add_argument("--sort", choices=sorted(SORT), default="relevance",
                   help="relevance (default), date (newest first), views, rating")
    p.add_argument("--period", choices=list(PERIOD), help="uploaded within the last hour/today/week/month/year")
    p.add_argument("--duration", choices=list(DURATION), help="short <4 min, medium 4-20 min, long >20 min")
    p.add_argument("--type", dest="kind", choices=list(TYPE), help="result type (default: videos only)")
    p.add_argument("--channel", help="search inside one channel: @handle, channel URL or UC… id")
    p.add_argument("--min-views", type=int, help="drop results with fewer views")
    p.add_argument("--after", type=parse_date, metavar="YYYY-MM-DD", help="drop results published before this date")
    p.add_argument("--min-minutes", type=float, help="drop videos shorter than this")
    p.add_argument("--max-minutes", type=float, help="drop videos longer than this")
    p.add_argument("--details", action="store_true",
                   help="fetch full metadata per result: exact date, likes, description, caption languages (one extra request each)")
    p.add_argument("-f", "--format", choices=["table", "json", "urls", "ids"], default="table")
    p.add_argument("--proxy", help="HTTP(S)/SOCKS proxy URL, useful when YouTube blocks your IP")
    args = p.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    query = " ".join(args.query).strip()
    if not query:
        sys.stderr.write("error: empty query\n")
        return EXIT_BAD_INPUT
    limit = max(1, min(args.limit, 100))

    client_side = bool(args.min_views or args.after or args.min_minutes or args.max_minutes)
    needs_resort = args.sort in ("date", "duration")
    # YouTube's internal API ignores sort-by-date, so over-fetch and sort locally.
    pool = min(100, max(limit * 3, 30)) if (needs_resort or client_side or args.sort == "date") else limit

    if args.channel:
        url = channel_search_url(args.channel, query)
    else:
        from urllib.parse import quote_plus

        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        sp = build_sp(args.sort, args.period, args.kind, args.duration)
        if sp:
            url += f"&sp={quote_plus(sp)}"

    install_date_precision_hook()
    try:
        with yt_dlp.YoutubeDL(ydl_opts(pool, args.proxy)) as y:
            info = y.extract_info(url, download=False)
    except (DownloadError, ExtractorError) as e:
        msg = str(e)
        low = msg.lower()
        if "sign in to confirm" in low or "not a bot" in low or "429" in low or "blocked" in low:
            sys.stderr.write(
                "error: YouTube blocked the request (common on cloud/datacenter IPs or after many requests).\n"
                "Wait a bit and retry, run from a residential network, or pass --proxy URL\n"
            )
            return EXIT_BLOCKED
        if "unable to download" in low or "timed out" in low or "name resolution" in low or "connection" in low:
            sys.stderr.write(f"error: network failure while contacting YouTube: {msg.splitlines()[0]}\n")
            return EXIT_NETWORK
        if "unsupported url" in low or "not a valid url" in low or "no suitable extractor" in low:
            sys.stderr.write(f"error: bad channel or query: {msg.splitlines()[0]}\n")
            return EXIT_BAD_INPUT
        sys.stderr.write(f"error: {msg.splitlines()[0]}\n")
        return EXIT_OTHER
    except OSError as e:
        sys.stderr.write(f"error: network failure while contacting YouTube: {e}\n")
        return EXIT_NETWORK

    entries = dedupe([normalize(e) for e in (info.get("entries") or []) if e])
    if args.kind in (None, "video") and not args.channel:
        entries = [e for e in entries if e["kind"] == "video"]

    def keep(it: dict[str, Any]) -> bool:
        if it["kind"] != "video":
            return True
        if args.min_views is not None and (it["view_count"] or 0) < args.min_views:
            return False
        if args.after:
            d = published_date(it)
            if d is None or d < args.after:
                return False
        d = it["duration"]
        if args.min_minutes is not None and (d is None or d < args.min_minutes * 60):
            return False
        if args.max_minutes is not None and (d is None or d > args.max_minutes * 60):
            return False
        return True

    entries = [e for e in entries if keep(e)]
    if args.sort == "date":
        entries.sort(key=lambda e: e["timestamp"] or 0, reverse=True)
    elif args.sort == "views":
        entries.sort(key=lambda e: e["view_count"] or 0, reverse=True)
    entries = entries[:limit]

    if not entries:
        sys.stderr.write("no results (try fewer filters or a broader query)\n")
        return EXIT_NO_RESULTS

    if args.details:
        entries = [enrich(e, args.proxy) for e in entries]

    if args.format == "json":
        for e in entries:
            e.pop("timestamp", None)
        sys.stdout.write(json.dumps({"query": query, "results": entries}, ensure_ascii=False, indent=2) + "\n")
    elif args.format == "urls":
        sys.stdout.write("\n".join(e["url"] for e in entries) + "\n")
    elif args.format == "ids":
        sys.stdout.write("\n".join(e["id"] or "" for e in entries) + "\n")
    else:
        sys.stdout.write(render_table(entries, query))
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
