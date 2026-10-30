#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["curl_cffi>=0.17,<1", "trafilatura>=2.3,<3", "lxml_html_clean", "pypdf>=5,<7"]
# ///
"""Fetch a web page and print its main content as Markdown, text, JSON or HTML.

Fetches with curl_cffi (browser-grade TLS fingerprint, honest User-Agent) and
extracts the main content with trafilatura; PDFs are read with pypdf. When the
page is a JavaScript shell it is rendered in a local headless Chromium
(optional Playwright dependency, --render); when the live page is blocked, gone
or unreachable it falls back to the newest Wayback Machine snapshot; with --jina
it may, as a last resort, ask the third-party r.jina.ai reader.
Works on macOS, Linux and Windows.

Run with uv (installs the dependencies automatically):
    uv run extract.py URL [URL ...] [options]
    uv run --with playwright extract.py URL --render always   # after: uv run --with playwright playwright install chromium-headless-shell
Or with plain Python after `pip install curl_cffi trafilatura lxml_html_clean pypdf`:
    python3 extract.py URL [URL ...] [options]

Exit codes:
    0 ok · 1 nothing extracted · 2 bad input / missing dependency
    · 3 HTTP error (not a bot wall) · 4 blocked / bot challenge / rate limit
    · 5 content is not HTML · 6 network · 7 other
"""

import argparse
import difflib
import email.utils
import hashlib
import html
import io
import ipaddress
import json
import logging
import os
import re
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

PIP_HINT = "pip install curl_cffi trafilatura lxml_html_clean pypdf, or run with uv run"
try:
    from curl_cffi import CurlOpt, requests
    from curl_cffi.requests import exceptions as cx
except ImportError as e:
    sys.stderr.write(f"error: missing dependency curl_cffi ({e}); {PIP_HINT}\n")
    sys.exit(2)
try:
    import lxml.etree
    import lxml.html
    import trafilatura
    from trafilatura.utils import detect_encoding, is_dubious_html, load_html
    from trafilatura.xpaths import BODY_XPATH
except ImportError as e:  # also raised when pip < 23.3 skipped the lxml[html_clean] extra
    sys.stderr.write(f"error: missing dependency trafilatura ({e}); {PIP_HINT}\n")
    sys.exit(2)

EXIT_OK, EXIT_EMPTY, EXIT_BAD_INPUT, EXIT_HTTP, EXIT_BLOCKED, EXIT_NOT_HTML, EXIT_NETWORK, EXIT_OTHER = range(8)

UA = "zedxter-skills-web-extract/1.0 (+https://github.com/zedxter/skills)"
MAX_BODY_BYTES = 10_000_000  # decompressed; below trafilatura's MAX_FILE_SIZE (20 MB)
MAX_REDIRECTS = 10
MARKER_SCAN_BYTES = 65_536
MAX_LINKS = 500
DEFAULT_MAX_CHARS = 60_000
CDX_URL = "https://web.archive.org/cdx/search/cdx"
JINA_URL = "https://r.jina.ai/"
PLAYWRIGHT_HINT = ("the optional headless browser is not available — run the script with `uv run --with playwright` "
                   "(or `pip install playwright`) and once `playwright install chromium-headless-shell` (~100 MB)")
PLAYWRIGHT_HEADFUL_HINT = ("--headful needs the full Chromium build, not the headless shell — run once "
                           "`uv run --with playwright playwright install chromium` (full browser, needed for --headful)")
RENDER_BLOCKED_RESOURCES = {"image", "media", "font"}  # never needed for the DOM; skipped in headless renders
RENDER_HUMAN_WAIT = 120.0  # --headful: minimum seconds to wait for a human to pass a bot check or log in
ACTION_KINDS = {"wait": (int, float), "wait_for": str, "click": str, "type": str, "press": str, "scroll": int}
MAX_PDF_PAGES = 500
MAX_TABLES, MAX_TABLE_ROWS, MAX_JSON_LD = 50, 500, 20
MAX_DIFF_CHARS = 20_000
PLAIN_HEADERS = {  # sent only with --impersonate none (browser profiles bring their own)
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
# Lower-case substrings that identify a bot-challenge page, by vendor. The only
# table to edit when a new wall appears. Only the VERIFIED_VENDORS (signatures
# seen on real interstitials) may block an HTTP 200/202 on their own; every other
# vendor substring blocks only on 403/503, and "generic" never blocks a 200.
# Deliberately absent: "/cdn-cgi/challenge-platform/" — Cloudflare injects that
# JS-detections beacon (…/scripts/jsd/main.js) into every normal page of sites
# with Bot Fight Mode; real interstitials carry _cf_chl_opt / "Just a moment".
CHALLENGE_MARKERS: dict[str, list[str]] = {
    "cloudflare": ["_cf_chl_opt", "<title>just a moment", "cf-browser-verification", "cf_chl_rt_tk",
                   "attention required! | cloudflare"],
    "anubis": ["anubis_challenge", "within.website/x/cmd/anubis", "/.within.website/",
               "making sure you're not a bot"],
    "akamai": ["/_sec/cp_challenge/", "ak_bmsc"],
    "imperva": ["_incapsula_resource", "incapsula incident id"],
    "datadome": ["captcha-delivery.com", "datadome"],
    "perimeterx": ["px-captcha", "_pxhd"],
    "kasada": ["/149e9513-01fa-4fb0-aad4-566afd725d1b/"],
    "awswaf": ["awswaf", "challenge.compact.js"],
    "vercel": ["vercel security checkpoint"],
    "generic": ["g-recaptcha", "h-captcha", "cf-turnstile", "enable javascript and cookies to continue",
                "are you a human", "are you a robot", "not a robot", "access denied", "validatecaptcha",
                "/checkpoint/continue"],
}
VERIFIED_VENDORS = {"cloudflare", "anubis"}
HTML_TYPES = {"text/html", "application/xhtml+xml", "application/xml", "text/xml",
              "application/rss+xml", "application/atom+xml"}
TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "application/json", "application/ld+json",
              "application/javascript", "text/javascript", "application/x-ndjson", "text/x-python"}
BINARY_MAGIC = (b"%PDF-", b"\x89PNG", b"GIF8", b"\xff\xd8\xff", b"PK\x03\x04", b"\x1f\x8b")
FEED_RE = re.compile(rb"<(feed|rss|rdf:rdf)[\s>]", re.I)  # RSS/Atom/RDF: handed over as raw XML
# Boilerplate pruned before extraction (MediaWiki section-edit links, footnote
# markers and inline "[citation needed]" templates), by class name.
PRUNE_XPATH = ['//span[contains(@class,"mw-editsection")]', '//sup[contains(@class,"reference")]',
               '//sup[contains(@class,"Inline-Template")]']
MIN_SNAPSHOT_WORDS = 100  # an archived page with fewer words is a suspected interstitial
# A cookie/consent interstitial served as HTTP 200: separate consent host, consent path, or
# two distinct consent-button phrases in title + first lines (checked only under 300 words).
CONSENT_HOST_RE = re.compile(r"^(consent|cmp|privacy|cookies?|gdpr)\.", re.I)
CONSENT_PATH_RE = re.compile(r"collectconsent|/consent\b", re.I)
CONSENT_PHRASES = re.compile(
    r"accept all|reject all|alle (akzeptieren|ablehnen)|tout (accepter|refuser)|aceptar todo|rechazar todo"
    r"|manage (your )?(privacy|cookie|consent)? ?(settings|choices|preferences)|datenschutzeinstellungen verwalten"
    r"|we and our partners|wir und unsere partner|nous et nos partenaires|your privacy (is important|matters|choices)"
    r"|ihre privatsph[äa]re ist uns wichtig|before you continue|cookie-richtlinie|cookie policy", re.I)
WARNING_HINTS = {
    "low_coverage": "extraction is far below the page's paragraph text; try --recall, compare words with para_words, --raw to see what was dropped",
    "js_shell": "JavaScript app; body is a shell, try --render always (local headless browser), --links for a data URL or --jina",
    "links_pruned": "most of the page's anchors were outside the main content (link lists / index pages are removed as boilerplate); use --raw to see every href",
    "consent_wall": "cookie/consent interstitial, not the article — try --wayback or the user's browser",
    "truncated": "rerun with -o FILE to get everything", "size_limit": "body cut at 10 MB",
    "challenge_suspected": "page looks like a bot check", "archived_copy": "not the live page",
    "third_party": "content came from r.jina.ai", "links_truncated": f"first {MAX_LINKS} links kept",
    "redirected": "final host differs from the requested one; check # url",
    "downgraded": "https request ended on plain http", "feed": "RSS/Atom feed returned as raw XML",
    "pages_truncated": f"PDF text stopped after {MAX_PDF_PAGES} pages",
    "action_failed": "an --actions step failed (see the note: line); the page was read anyway",
    "human_timeout": "the bot check was still showing when the --headful wait ran out",
}
HEADER_WARNINGS = set(WARNING_HINTS) | {"short", "binary_saved"}
NO_RETRY_SUFFIX = "do not retry in a loop"


@dataclass
class Diag:
    code: int
    kind: str
    message: str
    status: int | None = None
    vendor: str | None = None
    retry_after: int | None = None
    meta: dict[str, Any] | None = None  # metadata recovered before an exit-1 verdict; not part of as_dict()

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "kind": self.kind, "status": self.status,
                "vendor": self.vendor, "message": self.message}


@dataclass
class Fetched:
    status: int
    headers: dict[str, str]
    url: str
    body: bytes
    truncated: bool = False
    redirected: bool = False
    downgraded: bool = False  # https request ended on plain http
    screenshot: str | None = None  # --screenshot path written by the browser render
    action_errors: list[str] = field(default_factory=list)  # failed --actions steps (browser render)
    human_timeout: bool = False  # --headful: the challenge never went away

    @property
    def ctype(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()


@dataclass
class Classification:
    error: Diag | None = None
    warnings: list[str] = field(default_factory=list)


QUIET = False


def note(msg: str) -> None:
    if not QUIET:
        sys.stderr.write(f"note: {msg}\n")


# ----------------------------------------------------------------- pure helpers

def normalize_url(arg: str) -> str | None:
    """Return an http(s) URL without fragment, or None when the argument is not one.

    >>> normalize_url("example.com/page#top")
    'https://example.com/page'
    >>> normalize_url("HTTP://Example.com")
    'http://example.com'
    >>> normalize_url("example.com:8080/path")
    'https://example.com:8080/path'
    >>> [normalize_url(a) for a in ("ftp://x", "", "notaurl", "http://", "a b.com", "javascript:alert(1)")]
    [None, None, None, None, None, None]
    >>> normalize_url("https://user:pw@example.com/x") is None  # credentials are never sent
    True
    """
    s = (arg or "").strip()
    if not s or any(c.isspace() for c in s):
        return None
    # "label:" is a scheme unless it is immediately followed by a port number (host:port form)
    if "://" not in s and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d{1,5}(?:[/?]|$))", s):
        if "." not in s.split("/", 1)[0]:
            return None
        s = "https://" + s
    try:
        parts = urlsplit(s)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            return None
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            return None
    except ValueError:
        return None
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def has_userinfo(arg: str) -> bool:
    """True when the argument carries user:password@ credentials.

    >>> has_userinfo("https://user:pw@example.com/x"), has_userinfo("example.com/a@b"), has_userinfo("x@y.com/")
    (True, False, True)
    """
    s = (arg or "").strip()
    s = s.split("://", 1)[1] if "://" in s else s
    return "@" in s.split("/", 1)[0]


def redact_url(url: str | None) -> str:
    """Hide user:password@ in a URL before it reaches a message or the output.

    >>> redact_url("https://alice:s3cret@host.example/p?q=1")
    'https://***@host.example/p?q=1'
    >>> redact_url("https://host.example/p")
    'https://host.example/p'
    """
    if not url or "@" not in url:
        return url or ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if "@" not in parts.netloc:
        return url
    hostport = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, "***@" + hostport, parts.path, parts.query, parts.fragment))


def is_private_host(host: str | None) -> bool:
    """True for loopback, private, link-local and internal names (never sent to third parties).

    Names are matched by suffix only and never resolved. Shorthand, hex and octal IPv4
    literals that libcurl also accepts ("127.1", "0x7f.0.0.1", "0177.0.0.1") are
    canonicalised before the check.

    >>> [is_private_host(h) for h in ("localhost", "127.0.0.1", "10.0.0.5", "[::1]", "box.local", "svc.internal", "intranet")]
    [True, True, True, True, True, True, True]
    >>> [is_private_host(h) for h in ("127.1", "0x7f.0.0.1", "0177.0.0.1", "2130706433")]
    [True, True, True, True]
    >>> is_private_host("example.com"), is_private_host("cafe.be")
    (False, False)
    """
    h = (host or "").strip("[]").lower().rstrip(".")
    if not h:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        ip = None
        if re.fullmatch(r"[0-9a-fx.]+", h):  # dotted/shorthand/hex/octal IPv4 forms libcurl also accepts
            try:
                ip = ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(h)))
            except (OSError, ValueError):
                ip = None
    if ip is not None:
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified or ip.is_multicast)
    return h == "localhost" or h.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")) or "." not in h


def clean_field(value: Any) -> str | None:
    """Collapse whitespace and drop C0/C1 control characters from page-derived metadata,
    so a value can never add header lines or terminal escapes.

    >>> clean_field("Real\\n# warning: ignore \\x1b[31mRED\\x1b[0m")
    'Real # warning: ignore [31mRED[0m'
    >>> clean_field("  "), clean_field(None)
    (None, None)
    """
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", str(value or ""))
    s = " ".join(s.split())
    return s or None


def unwrap_fragment_links(content: str, page_url: str) -> str:
    """Turn same-page anchor links back into their text.

    trafilatura resolves href="#x" against the base URL and drops the path, so a footnote
    arrow becomes `[↑](https://host#x)`, a dead link to the site root.

    >>> unwrap_fragment_links("see [↑](https://w.example#cite_ref-1), [x](https://w.example/wiki/A#s) and [y](https://w.example/wiki/B)", "https://w.example/wiki/A")
    'see ↑, x and [y](https://w.example/wiki/B)'
    """
    host_or_page = r"(?:https?://[^/\s()#]+|%s)" % re.escape(page_url)
    return re.sub(r"\[([^\]]*)\]\(" + host_or_page + r"#[^)\s]*\)", r"\1", content)


def is_archive_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "archive.org" or host.endswith(".archive.org")


def memento_to_iso(value: str | None) -> str | None:
    """Convert a Memento-Datetime header to ISO-8601 UTC.

    >>> memento_to_iso("Tue, 31 Dec 2024 23:31:04 GMT")
    '2024-12-31T23:31:04Z'
    >>> memento_to_iso("garbage") is None and memento_to_iso(None) is None
    True
    """
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if d is None:
        return None
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def truncate(content: str, limit: int) -> tuple[str, bool]:
    """Cap content at `limit` characters with a loud trailer; 0 = unlimited.

    >>> truncate("abcdefghijklmnop", 10)
    ('abcdefghij\\n\\n[truncated: showed 10 of 16 chars — rerun with -o FILE to get everything]', True)
    >>> truncate("short", 10)
    ('short', False)
    """
    if limit <= 0 or len(content) <= limit:
        return content, False
    return (content[:limit].rstrip() + f"\n\n[truncated: showed {limit} of {len(content)} chars"
            " — rerun with -o FILE to get everything]"), True


def strip_md_links(text: str) -> str:
    """Turn `[text](url)` into `text` (used for -f text with --links, and on Jina output).

    >>> strip_md_links("see [the docs](https://x.example/a) now")
    'see the docs now'
    """
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)


def parse_cdx(text: str) -> list[tuple[str, str]] | None:
    """Parse a CDX `output=json` response into (timestamp, original) rows, newest first.

    Returns [] when nothing is archived and None when the response is unparsable.

    >>> parse_cdx('[["timestamp","original"],["20261029040627","https://example.com/"]]')
    [('20261029040627', 'https://example.com/')]
    >>> parse_cdx("[]"), parse_cdx('[["timestamp","original"]]'), parse_cdx("<html>oops")
    ([], [], None)
    """
    try:
        rows = json.loads(text)
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    out = [(str(r[0]), str(r[1])) for r in rows[1:] if isinstance(r, list) and len(r) >= 2 and str(r[0]).isdigit()]
    out.sort(key=lambda r: r[0], reverse=True)
    return out


def coverage_of(words: int, body_words: int | None, para_words: int | None = None) -> tuple[float | None, list[str]]:
    """Ratio of extracted to visible words plus the warnings it implies.

    `low_coverage` fires when the ratio is under 0.3 on a page with over 300 visible words,
    or when the extraction holds under 60 % of the page's paragraph text (`para_words`:
    words in <p> elements of 8+ words, 300 or more): the signature of an article body
    split around an ad slot or figure of which only the first part survived.

    >>> coverage_of(4043, 4514)
    (0.9, [])
    >>> coverage_of(60, 900)
    (0.07, ['low_coverage'])
    >>> coverage_of(10, 0)
    (None, [])
    >>> coverage_of(26, 25)
    (1.0, [])
    >>> coverage_of(306, 1000, 549)
    (0.31, ['low_coverage'])
    >>> coverage_of(655, 919, 352)
    (0.71, [])
    """
    if not body_words:
        return None, []
    cov = round(min(words / body_words, 1.0), 2)
    low = (cov < 0.3 and body_words > 300) or ((para_words or 0) >= 300 and words < 0.6 * (para_words or 0))
    return cov, (["low_coverage"] if low else [])


def sniff(head: bytes) -> str:
    """Classify a body by content: 'binary', 'markup' or 'text'."""
    h = head.lstrip(b"\xef\xbb\xbf \t\r\n")[:512]
    if h.startswith(BINARY_MAGIC):
        return "binary"
    if h.startswith(b"<"):
        return "markup"
    return "text"


def gate(ctype: str, head: bytes) -> str:
    """Decide how a response body is handled: 'html', 'text' (passthrough) or 'binary'.

    >>> gate("application/pdf", b"%PDF-1.4"), gate("text/plain", b"hello"), gate("text/html", b"%PDF-1.4")
    ('binary', 'text', 'binary')
    >>> gate("text/html", b'{"a":1}'), gate("", b"<html><body>x</body></html>"), gate("image/png", b"")
    ('text', 'html', 'binary')
    >>> gate("text/html", b"<!--" + b"x" * 600 + b"--><html>")  # markup is trusted even without "html" up front
    'html'
    >>> gate("application/atom+xml", b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">')
    'text'
    """
    kind = sniff(head)
    if kind == "binary":
        return "binary"
    if ctype in HTML_TYPES or ctype.endswith("+xml"):
        if is_feed(head):
            return "text"
        if kind == "markup":  # body starts with '<' (after BOM/whitespace): trust the declared type
            return "html"
        return "text" if is_dubious_html(head[:4096].decode("utf-8", "replace")) else "html"
    if ctype in TEXT_TYPES or ctype.startswith("text/"):
        return "text"
    if ctype in ("", "application/octet-stream", "binary/octet-stream"):
        if kind == "markup":
            return "text" if is_feed(head) else "html"
        return "text" if ctype == "" else "binary"
    return "binary"


def is_feed(head: bytes) -> bool:
    """True when the first bytes look like an RSS/Atom/RDF feed document."""
    return FEED_RE.search(head[:4096]) is not None


def feed_title(text: str) -> str | None:
    """First <title> of a feed document (channel/feed level), unescaped.

    >>> feed_title('<?xml version="1.0"?><feed><title>Simon&#39;s Weblog</title><entry><title>x</title></entry></feed>')
    "Simon's Weblog"
    """
    m = re.search(r"<title[^>]*>(.*?)</title>", text[:8192], re.I | re.S)
    if not m:
        return None
    return clean_field(html.unescape(re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", m.group(1), flags=re.S)))


def decode_text(body: bytes, headers: dict[str, str]) -> str:
    """Decode a passthrough/--raw body: header charset, in-document declaration, UTF-8, detection, cp1252.

    >>> decode_text("<meta charset='windows-1251'><title>Привет</title>".encode("cp1251"), {"content-type": "text/html"})
    "<meta charset='windows-1251'><title>Привет</title>"
    >>> decode_text("héllo".encode("utf-8"), {})
    'héllo'
    """
    cands: list[str] = []
    m = re.search(r"charset=([\w.-]+)", headers.get("content-type", ""), re.I)
    if m:
        cands.append(m.group(1))
    m2 = re.search(rb"""<meta[^>]+charset=["']?\s*([\w.-]+)|<\?xml[^>]+encoding=["']([\w.-]+)""", body[:2048], re.I)
    if m2:
        cands.append((m2.group(1) or m2.group(2)).decode("ascii", "ignore"))
    cands.append("utf-8")
    if len(body) >= 256:  # charset detection is noise on tiny bodies
        try:
            cands.extend(detect_encoding(body)[:2])
        except Exception:
            pass
    cands.append("cp1252")
    seen: set[str] = set()
    for enc in cands:
        key = enc.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")


def retry_after_of(headers: dict[str, str]) -> int | None:
    v = headers.get("retry-after", "").strip()
    return int(v) if v.isdigit() else None


# -------------------------------------------------------------- block detection

def classify(status: int, headers: dict[str, str], head: bytes, final_url: str = "",
             body_len: int | None = None) -> Classification:
    """Decide whether a response is a bot wall / HTTP error before extraction.

    Headers first, status second, body markers last and only under a size guard,
    so a long article that merely talks about Cloudflare is never "blocked".
    Only verified vendor signatures (cloudflare, anubis) may block a 200/202;
    unverified vendors block on 403/503 only and otherwise merely raise the
    `challenge_suspected` warning.

    >>> classify(200, {}, b"<html><head><title>Community</title><script>var u='/cdn-cgi/challenge-platform/scripts/jsd/main.js'</script></head><body>" + b"<p>real content</p>" * 50 + b"</body></html>").error is None
    True
    >>> classify(200, {}, b"<html><head><title>Shop</title><script src='https://js.datadome.co/tags.js'></script></head><body>" + b"<p>real content</p>" * 50 + b"</body></html>").error is None
    True
    >>> c = classify(403, {}, b"<html><script src='https://js.datadome.co/tags.js'></script>Please verify</html>")
    >>> (c.error.code, c.error.kind, c.error.vendor)
    (4, 'bot_check', 'datadome')
    """
    headers = {k.lower(): v for k, v in headers.items()}
    body_len = len(head) if body_len is None else body_len
    text = html.unescape(head[:MARKER_SCAN_BYTES].decode("utf-8", "replace")).lower()
    hits = [v for v, marks in CHALLENGE_MARKERS.items() if any(m in text for m in marks)]
    strong = [v for v in hits if v in VERIFIED_VENDORS]
    where = f" at {redact_url(final_url)}" if final_url else ""

    def blocked(kind: str, vendor: str, detail: str = "") -> Classification:
        label = {"cloudflare_challenge": "cloudflare challenge", "anubis_challenge": "anubis challenge"}.get(kind, f"{vendor} bot check")
        return Classification(Diag(EXIT_BLOCKED, kind, f"blocked by {label} (HTTP {status}{detail}){where} — a browser-only check; {NO_RETRY_SUFFIX}", status, vendor))

    if headers.get("cf-mitigated", "").lower() == "challenge":
        return blocked("cloudflare_challenge", "cloudflare", ", cf-mitigated: challenge")
    if status == 202 and "anubis" in hits:
        return blocked("anubis_challenge", "anubis")
    if status == 429:
        ra = retry_after_of(headers)
        detail = f", Retry-After: {ra}s" if ra is not None else ""
        return Classification(Diag(EXIT_BLOCKED, "rate_limited", f"rate limited (HTTP 429{detail}){where} — {NO_RETRY_SUFFIX}", status, None, ra))
    if status == 401:
        return Classification(Diag(EXIT_HTTP, "unauthorized", f"HTTP 401 (authentication required){where}", status))
    if status in (403, 503) and "cloudflare" in headers.get("server", "").lower() and "cloudflare" in hits:
        return blocked("cloudflare_challenge", "cloudflare", ", server: cloudflare")
    if status in (403, 503, 202, 200) and body_len <= MARKER_SCAN_BYTES and (strong or (hits and status in (403, 503))):
        vendor = "cloudflare" if "cloudflare" in strong else "anubis" if "anubis" in strong else (strong or hits)[0]
        kind = {"cloudflare": "cloudflare_challenge", "anubis": "anubis_challenge"}.get(vendor, "bot_check")
        return blocked(kind, vendor)
    if status == 403:
        extra = "; page body was returned, --raw shows it" if body_len > 8192 and sniff(head) == "markup" else ""
        return Classification(Diag(EXIT_BLOCKED, "forbidden", f"HTTP 403 (forbidden — bot wall without a challenge page, or a permission/geo restriction{extra}){where} — {NO_RETRY_SUFFIX}", status))
    if status in (404, 410):
        return Classification(Diag(EXIT_HTTP, "not_found", f"HTTP {status} (not found){where}", status))
    if 400 <= status < 500:
        return Classification(Diag(EXIT_HTTP, "http_error", f"HTTP {status}{where}", status))
    if status >= 500:
        return Classification(Diag(EXIT_HTTP, "server_error", f"HTTP {status} (server error){where}", status, None, retry_after_of(headers)))
    if status < 200 or status >= 400:
        return Classification(Diag(EXIT_HTTP, "http_error", f"HTTP {status}{where}", status))
    return Classification(None, ["challenge_suspected"] if hits and body_len < 8192 else [])


# ------------------------------------------------------------------- fetching

def make_session(args: argparse.Namespace) -> Any:
    kwargs: dict[str, Any] = dict(headers={"User-Agent": UA}, timeout=float(args.timeout),
                                  allow_redirects=True, max_redirects=MAX_REDIRECTS)
    if args.impersonate != "none":
        kwargs["impersonate"] = args.impersonate
    else:
        kwargs["headers"].update(PLAIN_HEADERS)
    if args.proxy:
        kwargs["proxy"] = args.proxy
    kwargs["curl_options"] = {
        CurlOpt.REDIR_PROTOCOLS_STR: b"http,https",  # libcurl's default also follows redirects to ftp:
        # hard libcurl backstop over the largest per-call timeout (Jina, 60 s); the per-attempt
        # wall-clock budget is enforced in fetch(), this only guards against a stuck transfer
        CurlOpt.TIMEOUT_MS: int(max(float(args.timeout), 60.0) * 1000),
    }
    return requests.Session(**kwargs)


def fetch(session: Any, url: str, *, timeout: float, attempts: int = 2, headers: dict[str, str] | None = None) -> Fetched | Diag:
    """One GET with the body cap, redirect bookkeeping, exception mapping and a bounded retry.

    `timeout` is a wall-clock budget per attempt: with stream=True curl_cffi maps it to a
    connect + low-speed (1 B/s) limit only, so a slowly dripping body is cut here by a
    monotonic deadline checked after every chunk.
    """
    host = urlsplit(url).hostname or url
    for attempt in range(1, attempts + 1):
        diag: Diag | None = None
        try:
            deadline = time.monotonic() + timeout
            r = session.get(url, stream=True, timeout=timeout, headers=headers)
            body, truncated = bytearray(), False
            try:
                for chunk in r.iter_content():
                    body += chunk
                    if len(body) > MAX_BODY_BYTES:
                        truncated = True
                        break
                    if time.monotonic() > deadline:
                        raise cx.Timeout(f"body still arriving after {timeout:g}s")
            finally:
                r.close()
            hdrs = {str(k).lower(): str(v) for k, v in r.headers.items()}
            final = str(r.url or url)
            final_host = (urlsplit(final).hostname or "").lower()
            if not is_private_host(host) and not is_archive_url(url) and is_private_host(final_host):
                # a public page redirected into localhost / private space: never output that body
                return Diag(EXIT_BLOCKED, "redirect_to_private",
                            f"redirected from {host} to private/local host {final_host} ({redact_url(final)}); "
                            f"body discarded — {NO_RETRY_SUFFIX}", r.status_code)
            f = Fetched(r.status_code, hdrs, final, bytes(body[:MAX_BODY_BYTES]), truncated,
                        final_host != (urlsplit(url).hostname or "").lower(),
                        urlsplit(url).scheme == "https" and urlsplit(final).scheme == "http")
            if attempt < attempts and f.status in (500, 502, 503, 504) and classify(f.status, hdrs, f.body[:MARKER_SCAN_BYTES], final, len(f.body)).error.code != EXIT_BLOCKED:
                note(f"retrying (HTTP {f.status})")
                time.sleep(2)
                continue
            ra = retry_after_of(hdrs)
            if attempt < attempts and f.status == 429 and ra is not None and ra <= 10:
                note(f"retrying (HTTP 429, Retry-After {ra}s)")
                time.sleep(ra)
                continue
            return f
        except cx.DNSError:
            return Diag(EXIT_NETWORK, "dns", f"DNS lookup failed for host {host}")
        except cx.Timeout:
            diag = Diag(EXIT_NETWORK, "timeout", f"timed out (limit {timeout:g}s per attempt, {attempts} attempt{'s' if attempts != 1 else ''}) fetching {redact_url(url)}")
        except (cx.CertificateVerifyError, cx.SSLError):
            return Diag(EXIT_NETWORK, "tls", f"TLS certificate verification failed for {host}")
        except cx.TooManyRedirects:
            return Diag(EXIT_NETWORK, "redirects", f"more than {MAX_REDIRECTS} redirects fetching {redact_url(url)}")
        except cx.ImpersonateError as e:
            return Diag(EXIT_BAD_INPUT, "bad_input", f"impersonation profile not supported: {e}")
        except (cx.InvalidSchema, cx.InvalidURL) as e:  # libcurl refused a redirect to file:/ftp:/… (deterministic, not exit 7)
            m = re.search(r'Protocol "([^"]+)" is disabled', str(e))
            first = (str(e).splitlines() or [type(e).__name__])[0].split(" See https://curl.se")[0]
            what = f"redirected to a non-http(s) URL ({m.group(1)}:); refused" if m else f"invalid URL after redirect: {first}"
            return Diag(EXIT_NETWORK, "redirects", f"{what} fetching {redact_url(url)}")
        except (cx.ConnectionError, cx.IncompleteRead, cx.ChunkedEncodingError) as e:
            first = (str(e).splitlines() or [type(e).__name__])[0].split(" See https://curl.se")[0]
            diag = Diag(EXIT_NETWORK, "connection", f"connection failed: {first}")
        except Exception as e:  # other RequestException / CurlError / internal
            return Diag(EXIT_OTHER, "other", f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}".rstrip(": "))
        if attempt < attempts:
            note(f"retrying ({diag.kind})")
            if diag.kind != "timeout":
                time.sleep(2)
            continue
        return diag
    return Diag(EXIT_OTHER, "other", "no attempts made")  # unreachable


# -------------------------------------------------------------------- browser

def playwright_api() -> tuple[Any, str | None]:
    """The Playwright sync API module, or (None, reason) when the optional dependency is missing."""
    try:
        import playwright.sync_api as pw
    except ImportError:
        return None, PLAYWRIGHT_HINT
    return pw, None


def parse_actions(text: str | None) -> list[dict[str, Any]] | str:
    """Validate --actions JSON: a list of single-step objects. Returns the list, or an error message.

    >>> parse_actions('[{"click": "#more"}, {"type": "input[name=q]", "text": "cats"}, {"press": "Enter"}, {"wait_for": ".results"}, {"wait": 1.5}, {"scroll": 3}]')[5]
    {'scroll': 3}
    >>> parse_actions('{"wait": 2}'), parse_actions(None)
    ([{'wait': 2}], [])
    >>> parse_actions('[{"hover": "x"}]'), parse_actions('[{"type": "input"}]'), parse_actions('[{"scroll": 99}]'), parse_actions('nope')[:9]
    ('--actions step 1: unknown step "hover" (known: click, press, scroll, type, wait, wait_for)', '--actions step 1: "type" needs a "text" string', '--actions step 1: "scroll" must be an integer 1-50', '--actions')
    """
    if not text:
        return []
    try:
        steps = json.loads(text)
    except ValueError as e:
        return f"--actions is not valid JSON: {e}"
    if isinstance(steps, dict):
        steps = [steps]
    if not isinstance(steps, list):
        return "--actions must be a JSON list of step objects"
    out: list[dict[str, Any]] = []
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not step:
            return f"--actions step {i}: must be a non-empty object"
        kinds = [k for k in step if k != "text"]
        if len(kinds) != 1 or kinds[0] not in ACTION_KINDS:
            name = kinds[0] if len(kinds) == 1 else ", ".join(kinds)
            return f'--actions step {i}: unknown step "{name}" (known: {", ".join(sorted(ACTION_KINDS))})'
        kind, val = kinds[0], step[kinds[0]]
        if kind == "wait":
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not 0 <= val <= 120:
                return f'--actions step {i}: "wait" must be seconds between 0 and 120'
        elif kind == "scroll":
            if isinstance(val, bool) or not isinstance(val, int) or not 1 <= val <= 50:
                return f'--actions step {i}: "scroll" must be an integer 1-50'
        elif not isinstance(val, str) or not val.strip():
            return f'--actions step {i}: "{kind}" needs a non-empty selector/key string'
        if kind == "type" and not isinstance(step.get("text"), str):
            return f'--actions step {i}: "type" needs a "text" string'
        out.append({kind: val, "text": step["text"]} if kind == "type" else {kind: val})
    return out


def browser_error(e: Exception, url: str, timeout: float, headful: bool = False) -> Diag:
    """Map a Playwright/Chromium failure onto the script's exit codes.

    >>> browser_error(Exception("Executable doesn't exist at /x/chromium-1200/chrome-linux/chrome"), "https://a.example/", 5).message.endswith("for --headful)")
    True
    >>> "headless-shell" in browser_error(Exception("Executable doesn't exist at /x/chromium_headless_shell-1200/x"), "https://a.example/", 5).message
    True
    >>> browser_error(Exception("Page.goto: Download is starting"), "https://a.example/a.pdf", 5).kind
    'non_html'
    """
    msg = (str(e).splitlines() or [type(e).__name__])[0].strip()
    m = re.search(r"net::(ERR_[A-Z_0-9]+)", str(e))
    code = m.group(1) if m else ""
    if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
        full = "chromium-" in str(e) and "headless_shell" not in str(e)  # path of the full build, not the shell
        return Diag(EXIT_BAD_INPUT, "bad_input",
                    f"browser not installed: {PLAYWRIGHT_HEADFUL_HINT if headful or full else PLAYWRIGHT_HINT}")
    if "shared libraries" in str(e) or "missing dependencies" in str(e):
        return Diag(EXIT_BAD_INPUT, "bad_input", "browser cannot start, system libraries are missing: run `playwright install-deps chromium` (administrator rights)")
    if code == "ERR_NAME_NOT_RESOLVED":
        return Diag(EXIT_NETWORK, "dns", f"DNS lookup failed for host {urlsplit(url).hostname}")
    if code.startswith(("ERR_CERT", "ERR_SSL")):
        return Diag(EXIT_NETWORK, "tls", f"TLS error in the browser for {urlsplit(url).hostname} ({code})")
    if code == "ERR_TOO_MANY_REDIRECTS":
        return Diag(EXIT_NETWORK, "redirects", f"too many redirects (browser) fetching {redact_url(url)}")
    if code == "ERR_CONNECTION_TIMED_OUT" or type(e).__name__ == "TimeoutError" or "Timeout" in msg:
        return Diag(EXIT_NETWORK, "timeout", f"browser timed out after {timeout:g}s loading {redact_url(url)}")
    if code.startswith("ERR_CONNECTION") or code in ("ERR_ADDRESS_UNREACHABLE", "ERR_NETWORK_CHANGED", "ERR_EMPTY_RESPONSE",
                                                     "ERR_INTERNET_DISCONNECTED", "ERR_PROXY_CONNECTION_FAILED", "ERR_TUNNEL_CONNECTION_FAILED"):
        return Diag(EXIT_NETWORK, "connection", f"browser connection failed ({code}) for {redact_url(url)}")
    if code == "ERR_ABORTED" or "Download is starting" in str(e):
        return Diag(EXIT_NOT_HTML, "non_html", f"browser navigation was aborted at {redact_url(url)} (a download such as a PDF?) — fetch it without --render")
    return Diag(EXIT_OTHER, "other", f"browser: {msg[:240]}")


def browser_proxy(proxy: str) -> dict[str, str]:
    """Playwright proxy settings from a --proxy URL: Chromium takes credentials only as separate fields.

    >>> browser_proxy("127.0.0.1:3128")
    {'server': 'http://127.0.0.1:3128'}
    >>> browser_proxy("socks5://[::1]:1080")
    {'server': 'socks5://[::1]:1080'}
    >>> browser_proxy("http://alice:s%40cret@proxy.example:8080")
    {'server': 'http://proxy.example:8080', 'username': 'alice', 'password': 's@cret'}
    """
    if "://" not in proxy:
        proxy = "http://" + proxy
    pu = urlsplit(proxy)
    if "@" not in pu.netloc:
        return {"server": proxy}
    userinfo, hostport = pu.netloc.rsplit("@", 1)
    user, _, password = userinfo.partition(":")
    return {"server": urlunsplit((pu.scheme, hostport, pu.path, "", "")).rstrip("/"),
            "username": unquote(user), "password": unquote(password)}


def run_action(page: Any, step: dict[str, Any], ms: int) -> None:
    kind = next(k for k in step if k != "text")
    val = step[kind]
    if kind == "wait":
        page.wait_for_timeout(int(float(val) * 1000))
    elif kind == "wait_for":
        page.wait_for_selector(val, timeout=ms)
    elif kind == "click":
        page.click(val, timeout=ms)
    elif kind == "type":
        page.fill(val, step["text"], timeout=ms)
    elif kind == "press":
        page.keyboard.press(val)
    elif kind == "scroll":
        for _ in range(int(val)):  # lazy-loading pages: scroll to the bottom and give the loader time
            page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            page.wait_for_timeout(600)


def render_fetch(url: str, args: argparse.Namespace, actions: list[dict[str, Any]]) -> Fetched | Diag:
    """GET through a real Chromium (headless unless --headful): JavaScript runs, --actions are performed,
    the DOM is returned as HTML. Images, media and fonts are not loaded in headless mode.

    The browser identifies itself: headless renders append the project token to Chromium's own
    `HeadlessChrome` User-Agent; --headful keeps the stock User-Agent because a human is present.
    """
    pw, why = playwright_api()
    if pw is None:
        return Diag(EXIT_BAD_INPUT, "bad_input", why or PLAYWRIGHT_HINT)
    timeout = float(args.timeout)
    ms = int(timeout * 1000)
    host = (urlsplit(url).hostname or "").lower()
    main: dict[str, Any] = {}  # latest main-frame navigation response (changes when a human passes a check)
    extra = Fetched(0, {}, url, b"")  # carrier for screenshot / action bookkeeping

    def on_response(resp: Any) -> None:
        try:
            req = resp.request
            if req.is_navigation_request() and req.frame == req.frame.page.main_frame:
                main["status"], main["headers"] = resp.status, {str(k).lower(): str(v) for k, v in resp.headers.items()}
                main["response"] = resp
        except Exception:
            pass

    def on_route(route: Any) -> None:
        try:
            if route.request.resource_type in RENDER_BLOCKED_RESOURCES:
                route.abort()
            else:
                route.continue_()
        except Exception:
            pass

    def dom() -> bytes:
        return page.content().encode("utf-8")

    try:
        with pw.sync_playwright() as p:
            launch: dict[str, Any] = {"headless": not args.headful}
            if args.proxy:
                launch["proxy"] = browser_proxy(args.proxy)
            ctx_opts: dict[str, Any] = {"viewport": {"width": 1280, "height": 900}, "locale": "en-US"}
            browser = None
            if args.profile:
                if not args.headful:  # persistent contexts take user_agent too; read the stock one from a throwaway browser
                    probe_browser = p.chromium.launch(**launch)
                    try:
                        stock_ua = probe_browser.new_page().evaluate("navigator.userAgent")
                    finally:
                        probe_browser.close()
                    ctx_opts["user_agent"] = f"{stock_ua} {UA}"
                ctx = p.chromium.launch_persistent_context(args.profile, **launch, **ctx_opts)
            else:
                browser = p.chromium.launch(**launch)
                if not args.headful:
                    probe = browser.new_page()
                    stock_ua = probe.evaluate("navigator.userAgent")
                    probe.close()
                    ctx_opts["user_agent"] = f"{stock_ua} {UA}"
                ctx = browser.new_context(**ctx_opts)
            try:
                page = ctx.new_page()
                if not args.headful:
                    page.route("**/*", on_route)
                page.on("response", on_response)
                resp = page.goto(url, wait_until="domcontentloaded", timeout=ms)
                for state, budget in (("load", ms), ("networkidle", int(min(10.0, timeout / 3) * 1000))):
                    try:
                        page.wait_for_load_state(state, timeout=budget)
                    except pw.TimeoutError:
                        pass
                if resp is None:
                    return Diag(EXIT_OTHER, "other", f"browser produced no response for {redact_url(url)}")
                status = main.get("status", resp.status)
                headers = main.get("headers") or {str(k).lower(): str(v) for k, v in resp.headers.items()}
                if args.headful:  # human in the loop: wait while the page is a bot check / consent / login interstitial
                    deadline = time.monotonic() + max(timeout, RENDER_HUMAN_WAIT)
                    told = False
                    while True:
                        try:
                            body = dom()
                            cls = classify(status, headers, body[:MARKER_SCAN_BYTES], page.url, len(body))
                            # only what a human can pass: bot walls / logins, not 404s or 5xx
                            stuck = ((cls.error is not None and (cls.error.code == EXIT_BLOCKED or cls.error.kind == "unauthorized"))
                                     or "challenge_suspected" in cls.warnings)
                        except pw.Error:  # navigating right now
                            stuck = True
                        if not stuck:
                            break
                        if time.monotonic() > deadline:
                            extra.human_timeout = True
                            break
                        if not told:
                            note(f"bot check or interstitial detected — pass it in the browser window (waiting up to {max(timeout, RENDER_HUMAN_WAIT):g}s)")
                            told = True
                        page.wait_for_timeout(1000)
                        status, headers = main.get("status", status), main.get("headers", headers)
                for step in actions:
                    try:
                        run_action(page, step, ms)
                    except pw.Error as e:
                        what = (str(e).splitlines() or [type(e).__name__])[0][:160]
                        shown = {**step, "text": f"<{len(step['text'])} chars>"} if "text" in step else step  # never echo typed text
                        extra.action_errors.append(f"{json.dumps(shown, ensure_ascii=False)}: {what}")
                        note(f"browser action failed: {extra.action_errors[-1]}")
                if args.render_wait:
                    page.wait_for_timeout(int(args.render_wait * 1000))
                if args.screenshot:
                    try:
                        Path(args.screenshot).parent.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=args.screenshot, full_page=True)
                        extra.screenshot = str(Path(args.screenshot))
                    except (OSError, pw.Error) as e:
                        extra.action_errors.append(f"screenshot: {(str(e).splitlines() or ['error'])[0][:160]}")
                        note(f"screenshot failed: {extra.action_errors[-1]}")
                status, headers = main.get("status", status), main.get("headers", headers)
                final = page.url
                body = dom()
                mctype = headers.get("content-type", "text/html").split(";")[0].strip().lower() or "text/html"
                if gate(mctype, body[:4096]) != "html":  # JSON / text / XML: Chromium wraps it in a viewer page; keep the server's bytes
                    try:
                        raw = (main.get("response") or resp).body()
                        body, raw_ctype = raw, headers.get("content-type", mctype)
                    except pw.Error:
                        raw_ctype = None
                else:
                    raw_ctype = None
            finally:
                try:
                    ctx.close()
                finally:
                    if browser is not None:
                        browser.close()
    except Exception as e:  # Playwright errors, Chromium net:: errors, launch failures
        return browser_error(e, url, timeout, headful=bool(args.headful))
    final_host = (urlsplit(final).hostname or "").lower()
    if not is_private_host(host) and not is_archive_url(url) and is_private_host(final_host):
        return Diag(EXIT_BLOCKED, "redirect_to_private",
                    f"redirected from {host} to private/local host {final_host} ({redact_url(final)}); body discarded — {NO_RETRY_SUFFIX}", status)
    ctype = headers.get("content-type", "text/html").split(";")[0].strip() or "text/html"
    if raw_ctype is not None:
        headers["content-type"] = raw_ctype  # the server's bytes, with the server's charset
    else:
        headers["content-type"] = f"{ctype}; charset=utf-8"  # page.content() is always serialised as UTF-8
    f = Fetched(status, headers, final, body[:MAX_BODY_BYTES], len(body) > MAX_BODY_BYTES,
                final_host != host, urlsplit(url).scheme == "https" and urlsplit(final).scheme == "http")
    f.screenshot, f.action_errors, f.human_timeout = extra.screenshot, extra.action_errors, extra.human_timeout
    return f


# ------------------------------------------------------------------------ pdf

def pdf_date(value: Any) -> str | None:
    """PDF `D:YYYYMMDD…` date string to ISO date.

    >>> pdf_date("D:20240315120000+00'00'"), pdf_date("20240315"), pdf_date(None), pdf_date("D:2024")
    ('2024-03-15', '2024-03-15', None, None)
    """
    m = re.match(r"(?:D:)?(\d{4})(\d{2})(\d{2})", str(value or ""))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def pdf_extract(body: bytes, url: str) -> dict[str, Any] | Diag:
    """Text layer of a PDF via pypdf: pages joined with `--- page N ---` markers, metadata when present."""
    try:
        from pypdf import PdfReader
        logging.getLogger("pypdf").setLevel(logging.ERROR)  # no stray "EOF marker not found" lines on stderr
    except ImportError:
        return Diag(EXIT_NOT_HTML, "non_html", f"content is a PDF but pypdf is not installed; {PIP_HINT}")
    try:
        reader = PdfReader(io.BytesIO(body))
        if reader.is_encrypted:
            try:
                opened = reader.decrypt("")
            except Exception:
                opened = 0
            if not opened:
                return Diag(EXIT_NOT_HTML, "non_html", f"PDF at {redact_url(url)} is password-protected; save it with --raw -o FILE")
        pages = len(reader.pages)
        parts: list[str] = []
        for i in range(min(pages, MAX_PDF_PAGES)):
            try:
                text = reader.pages[i].extract_text() or ""
            except Exception:
                text = ""
            text = re.sub(r"[ \t]+\n", "\n", text).strip()
            if text:
                parts.append(f"--- page {i + 1} ---\n\n{text}" if pages > 1 else text)
        meta = reader.metadata
    except Exception as e:
        return Diag(EXIT_NOT_HTML, "non_html", f"PDF at {redact_url(url)} could not be parsed ({type(e).__name__}); save it with --raw -o FILE")
    content = re.sub(r"\n{3,}", "\n\n", "\n\n".join(parts)).strip()
    words = len(content.split())
    if not words:
        return Diag(EXIT_EMPTY, "nothing_extracted",
                    f"PDF at {redact_url(url)} has no text layer ({pages} page{'s' if pages != 1 else ''}; scanned images?) — "
                    "OCR is outside this skill; --raw -o FILE saves the file")
    get = (lambda k: meta.get(k) if meta else None)
    path_name = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1] or None
    return {"title": clean_field(get("/Title")) or path_name, "author": clean_field(get("/Author")),
            "date": pdf_date(get("/CreationDate")) or pdf_date(get("/ModDate")), "description": clean_field(get("/Subject")),
            "content": content, "content_format": "text", "words": words, "chars": len(content), "pages": pages,
            "body_words": None, "para_words": None, "coverage": None,
            "warnings": ["pages_truncated"] if pages > MAX_PDF_PAGES else [], "links": None}


# ----------------------------------------------------------------- structured

def structured_data(body: bytes, final_url: str) -> dict[str, Any]:
    """Machine-readable parts of a page: JSON-LD objects, OpenGraph/Twitter cards, a few <meta>/<link>
    values and every data table (2+ rows, 2+ columns) as header + rows. Page text is untrusted data."""
    out: dict[str, Any] = {"json_ld": [], "opengraph": {}, "meta": {}, "tables": [], "truncated": False}
    try:
        tree = load_html(body)
    except Exception:
        tree = None
    if tree is None:
        return out
    for script in tree.iter("script"):
        if (script.get("type") or "").strip().lower() != "application/ld+json":
            continue
        text = re.sub(r"^\s*(?:<!--|//<!\[CDATA\[)|(?:-->|//\]\]>)\s*$", "", (script.text or "").strip())
        try:
            obj = json.loads(text, strict=False)
        except ValueError:
            continue
        for item in obj if isinstance(obj, list) else [obj]:
            if len(out["json_ld"]) >= MAX_JSON_LD:
                out["truncated"] = True
                break
            out["json_ld"].append(item)
    for m in tree.iter("meta"):
        key = (m.get("property") or m.get("name") or "").strip().lower()
        value = m.get("content")
        if not key or value is None:
            continue
        if key.startswith(("og:", "article:", "twitter:", "fb:", "al:")):
            out["opengraph"].setdefault(key, clean_field(value))
        elif key in ("description", "keywords", "author", "robots", "generator", "application-name") or key.startswith(("citation_", "dc.")):
            out["meta"].setdefault(key, clean_field(value))
    for link in tree.iter("link"):
        rel = (link.get("rel") or "").strip().lower()
        if rel in ("canonical", "alternate") and link.get("href"):
            if rel == "canonical":
                out["meta"]["canonical"] = urljoin(final_url, link.get("href"))
            elif (link.get("type") or "").lower() in ("application/rss+xml", "application/atom+xml", "application/json+oembed"):
                out["meta"].setdefault("feeds", []).append(urljoin(final_url, link.get("href")))
    for table in tree.iter("table"):
        rows: list[list[str]] = []
        header: list[str] | None = None
        for tr in table.iter("tr"):
            cells = [c for c in tr if c.tag in ("td", "th")]
            if not cells:
                continue
            values = [clean_field("".join(c.itertext())) or "" for c in cells]
            if header is None and not rows and all(c.tag == "th" for c in cells):
                header = values
            else:
                rows.append(values)
        if len(rows) + (1 if header else 0) < 2 or max((len(r) for r in rows), default=0) < 2:
            continue
        cap = table.find("caption")
        out["tables"].append({"caption": clean_field("".join(cap.itertext())) if cap is not None else None,
                              "headers": header, "rows": rows[:MAX_TABLE_ROWS], "row_count": len(rows),
                              "truncated": len(rows) > MAX_TABLE_ROWS})
        if len(out["tables"]) >= MAX_TABLES:
            out["truncated"] = True
            break
    return out


# -------------------------------------------------------------------- tracking

def track_dir() -> Path:
    env = os.environ.get("WEB_EXTRACT_CACHE")
    if env:
        return Path(env) / "track"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "web-extract" / "track"


def track_changes(result: dict[str, Any]) -> None:
    """Compare `content` with the last tracked copy of this URL (local cache) and store the new one."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = track_dir() / (hashlib.sha256(result["url"].encode("utf-8")).hexdigest()[:32] + ".json")
    change: dict[str, Any] = {"status": "new", "previous": None, "diff": None, "diff_truncated": False,
                              "added": 0, "removed": 0, "store": str(path)}
    prev: Any = None
    try:
        prev = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = None
    content = result["content"] or ""
    if isinstance(prev, dict) and isinstance(prev.get("content"), str):
        change["previous"] = prev.get("fetched_at")
        if prev["content"] == content:
            change["status"] = "same"
        else:
            change["status"] = "changed"
            lines = list(difflib.unified_diff(prev["content"].splitlines(), content.splitlines(),
                                              f"previous ({prev.get('fetched_at')})", f"current ({now})", lineterm="", n=1))
            change["added"] = sum(1 for ln in lines[2:] if ln.startswith("+"))
            change["removed"] = sum(1 for ln in lines[2:] if ln.startswith("-"))
            diff = "\n".join(lines)
            if len(diff) > MAX_DIFF_CHARS:
                diff, change["diff_truncated"] = diff[:MAX_DIFF_CHARS].rsplit("\n", 1)[0] + "\n[diff truncated]", True
            change["diff"] = diff
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)  # the store holds full page text, maybe logged-in pages
        data = json.dumps({"url": result["url"], "fetched_at": now, "source": result["source"],
                           "final_url": result["final_url"], "content": content}, ensure_ascii=False).encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        for target, mode in ((path, 0o600), (path.parent, 0o700)):  # tighten stores created by older versions
            try:
                os.chmod(target, mode)
            except OSError:
                pass
    except OSError as e:
        note(f"cannot store the tracked copy at {path}: {e}")
    result["change"] = change


# ----------------------------------------------------------------- extraction

@dataclass
class Signals:
    """Cheap whole-page signals computed from the fetched HTML before extraction."""
    body_words: int | None = None  # visible words after dropping script/style/noscript/template
    shell: bool = False  # hard JS-shell signal: <noscript> asking for JavaScript or an empty #root/#app container
    heavy: bool = False  # soft signal: almost no visible text and mostly scripts (media/app sites)
    lang: str | None = None  # <html lang>, first two letters
    anchors: int = 0  # <a href> count in the whole page (before boilerplate removal)
    para_words: int = 0  # words in <p> elements holding 8+ words: the page's paragraph text
    script_ratio: float = 0.0  # bytes inside <script> / body bytes: high + few words = client-side rendering

    @property
    def dynamic(self) -> bool:
        """Likely rendered client-side: a shell, script-heavy, or mostly script with under 50 visible words."""
        return self.shell or self.heavy or ((self.body_words or 0) < 50 and self.script_ratio > 0.5)


def page_signals(body: bytes) -> Signals:
    """Whole-page signals: visible words, JS-shell hints, <html lang>, anchor count, paragraph words.

    The hard shell signal is a <noscript> asking for JavaScript or an empty #root/#app/#__next
    container; the soft one is a page with almost no visible text that is mostly scripts
    (media and app sites such as YouTube), where the metadata is still worth keeping.
    """
    try:
        tree = lxml.html.fromstring(body)
    except Exception:
        return Signals()
    lang = (tree.get("lang") or "").strip()[:2].lower() or None
    anchors = len(tree.xpath("//a[@href]"))
    shell = False
    for el in tree.iter("noscript"):
        if "javascript" in (el.text_content() or "").lower():
            shell = True
    for el in tree.xpath("//*[@id='root' or @id='app' or @id='__next' or @id='___gatsby']"):
        if not el.text_content().strip():
            shell = True
    script_bytes = sum(len(el.text or "") for el in tree.iter("script"))
    for el in tree.xpath("//script|//style|//noscript|//template"):
        el.drop_tree()
    words = len(tree.text_content().split())
    heavy = words < 50 and (len(body) > 200_000 or (len(body) > 0 and script_bytes > 0.9 * len(body)))
    para_words = 0
    for p in tree.iter("p"):
        n = len(p.text_content().split())
        if n >= 8:
            para_words += n
    return Signals(words, shell, heavy, lang, anchors, para_words, script_bytes / len(body) if body else 0.0)


def html_title(body: bytes) -> str | None:
    """<title> text as a last resort when trafilatura yields no document (bare JS shells)."""
    try:
        t = lxml.html.fromstring(body).findtext(".//title")
    except Exception:
        return None
    return (clean_field(t) or "")[:200] or None


def merge_split_body(tree: Any) -> int:
    """Merge same-class sibling containers that split an article around an ad slot or figure.

    trafilatura keeps only the first container matched by each body selector; a site that
    wraps the article in two sibling `div.post-content` blocks loses everything after the
    first one. Children of later same-class containers holding 50+ words are moved into
    the first. Returns the number of containers merged.
    """
    for expr in BODY_XPATH:
        matches = [m for m in expr(tree) if m is not None]
        if len(matches) < 2:
            continue
        first, cls = matches[0], matches[0].get("class")
        if not cls:
            continue
        extra = [m for m in matches[1:] if m.get("class") == cls
                 and first not in m.iterancestors() and m not in first.iterancestors()
                 and len(m.text_content().split()) >= 50]
        if not extra:
            continue
        for m in extra:
            for child in list(m):
                first.append(child)
            m.drop_tree()
        return len(extra)
    return 0


def prepare_tree(body: bytes) -> Any:
    """Parse the page (trafilatura's own loader, so its encoding detection runs) and fix two
    things its extractor gets wrong, returning a fresh tree per call (trafilatura mutates
    its input) or the bytes when parsing fails.

    1. Every <pre> is flattened to <pre><code>TEXT</code></pre>: syntax-highlighted blocks
       (many <span> children, e.g. GitHub's pl-* classes) fail trafilatura's code heuristic
       and would be rendered as escaped prose with the line breaks lost.
    2. Same-class sibling body containers are merged (see merge_split_body).
    """
    try:
        tree = load_html(body)
    except Exception:
        return body
    if tree is None:
        return body
    if re.search(rb"<pre[\s>]", body, re.I):
        for pre in tree.iter("pre"):
            text = pre.text_content()
            for child in list(pre):
                pre.remove(child)
            pre.text = None
            lxml.etree.SubElement(pre, "code").text = text
    try:
        merge_split_body(tree)
    except Exception:
        pass
    return tree


_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_DATE_LIKE = re.compile(rf"^(?:{_MONTH}|\d{{1,4}}|[\d./-]+)(?:[\s.,]+(?:{_MONTH}|\d{{1,4}}))*$", re.I)
_AUTHOR_SEP = re.compile(r"\s*(?:;|·|\||—|–)\s*")
_AUTHOR_JUNK = re.compile(r"\b(search results?|databases?|navigation|menu|export|log ?in|sign ?in|subscribe|share)\b", re.I)


def clean_author(author: Any, sitename: str | None = None) -> str | None:
    """Drop author strings that are obviously mis-detected page elements: navigation labels,
    date tokens fused into the byline ("Oct. 26, 2026 · Name"), the site name a publisher
    prepends ("Heise Online; Name") and bare abbreviations ("Oct").

    >>> clean_author("Jane Doe"), clean_author("Authority control databases"), clean_author("Author search results export search results as first")
    ('Jane Doe', None, None)
    >>> clean_author("Oct"), clean_author("Oct. 26, 2026 · Jakub Beránek"), clean_author("Heise Online; Dafina Maloku", "Heise Online")
    (None, 'Jakub Beránek', 'Dafina Maloku')
    >>> clean_author("May Smith"), clean_author("Li Wu"), clean_author("Bo"), clean_author("")
    ('May Smith', 'Li Wu', None, None)
    """
    a = clean_field(author) or ""
    if not a:
        return None
    site = (sitename or "").strip().lower()
    parts = [p.strip() for p in _AUTHOR_SEP.split(a)]
    parts = [p for p in parts if p and p.lower() != site and not _DATE_LIKE.match(p)]
    a = "; ".join(parts)
    if not a or len(a.split()) > 5 or len(a) > 80:
        return None
    if _AUTHOR_JUNK.search(a):
        return None
    if len(a) < 4 and " " not in a:
        return None
    return a


_MD_BLOCK_START = r"(?:[-+*]|\d{1,9}[.)])(?:[ \t]|$)|#{1,6}[ \t]|>|\||$"
# newline + source indentation whose next line is NOT a list item, heading, quote, table row or blank
_INDENT_RUN = re.compile(r"\n[ \t]+(?=\S)(?!" + _MD_BLOCK_START + r")", re.M)
_BARE_MARKER = re.compile(r"(?m)^([ \t]*(?:[-+*]|\d{1,9}[.)]))\n(?=\S)")
_FENCE_SPLIT = re.compile(r"(?ms)(^[ \t]*```.*?^[ \t]*```[ \t]*$)")
_MW_LABEL = re.compile(r"(?mi)^\\?\[(?:edit|citation needed|clarification needed|when\?|who\?|which\?)\\?\](?:\n\n?|\Z)")


def squash_html_whitespace(content: str) -> str:
    """Collapse source-HTML indentation that trafilatura's markdown emitter keeps inside
    inline-formatted runs (<b>, <a>, <li> with children): "bei deinem\\n     Einkauf" ->
    "bei deinem Einkauf", "-\\n    item" -> "- item", "**x**\\t\\ty" -> "**x** y".
    Fenced code blocks are untouched; the 2-space nesting indent of list items is kept.

    >>> squash_html_whitespace("-\\n    Sugar **x**\\n  -\\n   sub\\n```\\n\\tcode  x\\n```\\na\\n\\t\\tb")
    '- Sugar **x**\\n  - sub\\n```\\n\\tcode  x\\n```\\na b'
    >>> squash_html_whitespace("-\\nitem one\\n- two\\n\\n| a | b |\\n|---|---|\\n  > quote")
    '- item one\\n- two\\n\\n| a | b |\\n|---|---|\\n  > quote'
    """
    parts = _FENCE_SPLIT.split(content)
    for i in range(0, len(parts), 2):  # even indexes are prose, odd are fenced code
        p = "\n".join(re.sub(r"(?<=\S)[ \t]{2,}", " ", line.rstrip()) for line in parts[i].split("\n"))
        p = _INDENT_RUN.sub(" ", p)
        parts[i] = _BARE_MARKER.sub(r"\1 ", p)
    return "".join(parts)


def _md_blocks(content: str) -> list[str]:
    """Split markdown into blank-line-separated blocks, keeping a fenced code block whole."""
    blocks: list[str] = []
    cur: list[str] = []
    fence = False
    for line in content.split("\n"):
        if line.lstrip().startswith("```"):
            fence = not fence
            cur.append(line)
            continue
        if fence:
            cur.append(line)
            continue
        if not line.strip():
            if cur:
                blocks.append("\n".join(cur))
                cur = []
            continue
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def tidy_markdown(content: str) -> str:
    """Generic safety net after extraction: <sup>/<sub> tags left by trafilatura, MediaWiki
    edit/citation labels, paragraphs repeated verbatim (desktop+mobile markup) and headings
    with no body. Fenced code blocks are left alone; other bracketed lines are content.

    >>> tidy_markdown("websites.<sup>[1]</sup> more\\n\\n[edit]\\n\\ntext")
    'websites.^[1] more\\n\\ntext'
    >>> tidy_markdown("a\\n\\n\\\\[Applause\\\\]\\n\\n[Update: fixed]\\n\\nb")
    'a\\n\\n\\\\[Applause\\\\]\\n\\n[Update: fixed]\\n\\nb'
    >>> tidy_markdown("## Reviews\\n\\n## Specs\\n\\n### Weight\\n\\n8 oz\\n\\n## Related\\n")
    '## Specs\\n\\n### Weight\\n\\n8 oz'
    >>> tidy_markdown("Same paragraph repeated here for the mobile layout.\\n\\nSame paragraph repeated here for the mobile layout.")
    'Same paragraph repeated here for the mobile layout.'
    >>> tidy_markdown("- Yes\\n\\n- Yes\\n\\n## A\\n\\n### B\\n\\ntext\\n\\n```\\nx\\n\\n    y\\n```")
    '- Yes\\n\\n- Yes\\n\\n## A\\n\\n### B\\n\\ntext\\n\\n```\\nx\\n\\n    y\\n```'
    """
    content = re.sub(r"<sup>(.*?)</sup>", r"^\1", content, flags=re.S)
    content = re.sub(r"</?sub>", "", content)
    content = _MW_LABEL.sub("", content)
    seen: set[str] = set()
    kept: list[str] = []
    for b in _md_blocks(content):  # a paragraph repeated verbatim is responsive duplicate markup, not content
        if len(b) > 40 and not b.startswith("#") and "```" not in b:
            if b in seen:
                continue
            seen.add(b)
        kept.append(b)
    out: list[str] = []
    for i, b in enumerate(kept):  # a heading with no body before an equal-or-higher heading (or EOF) is an empty section
        m = re.match(r"^(#{1,6}) \S", b)
        if m and "\n" not in b:
            nxt = kept[i + 1] if i + 1 < len(kept) else None
            nm = re.match(r"^(#{1,6}) ", nxt) if nxt else None
            if nxt is None or (nm and len(nm.group(1)) <= len(m.group(1))):
                continue
        out.append(b)
    return "\n\n".join(out).strip()


def extract(body: bytes, final_url: str, args: argparse.Namespace) -> dict[str, Any] | Diag:
    """Run trafilatura on the bytes and return content + metadata, or a Diag (exit 1)."""
    fmt = args.format
    sig = page_signals(body)
    body_words, lang = sig.body_words, sig.lang
    try:
        page_url = urlsplit(final_url)._replace(fragment="").geturl() if final_url else ""
    except ValueError:
        page_url = ""
    warnings: list[str] = []

    def run(recall: bool):
        try:
            doc = trafilatura.bare_extraction(
                prepare_tree(body), url=final_url, with_metadata=True, include_comments=args.comments,
                include_tables=True, include_images=False, include_links=args.links,
                include_formatting=(fmt != "text"), favor_recall=recall, favor_precision=args.precision,
                prune_xpath=PRUNE_XPATH)
        except Exception as e:
            return None, Diag(EXIT_OTHER, "other", f"extraction failed: {type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}")
        text = "\n".join(line.rstrip() for line in (doc.text or "").splitlines()).strip() if doc else ""
        if fmt == "text":
            if args.links:
                text = strip_md_links(text)  # -f text is plain; links live only in the JSON `links` array
        else:
            text = squash_html_whitespace(text)
            if args.links and page_url:
                text = unwrap_fragment_links(text, page_url)
        return (doc, tidy_markdown(text)), None

    # favor_recall is not monotonic (it can return less), so the default pass always runs
    # first and the recall pass is kept only when it yields more words.
    out, err = run(False)
    if err:
        return err
    doc, content = out
    used_recall = False
    tiny = len(content) < 40 or len(content.split()) < 5
    if args.recall or tiny:
        out, err = run(True)
        if err:
            return err
        doc2, content2 = out
        if len(content2.split()) > len(content.split()):
            doc, content, used_recall = doc2, content2, True
            tiny = len(content) < 40 or len(content.split()) < 5
    words = len(content.split())
    meta = doc.as_dict() if doc else {}
    title = clean_field(meta.get("title"))
    sitename = clean_field(meta.get("sitename"))
    author = clean_author(meta.get("author"), sitename)
    if not content or (sig.shell and words < 50) or (tiny and (body_words or 0) >= 50):
        kind = "js_shell" if sig.shell else "nothing_extracted"
        what = {"js_shell": "page is a JavaScript shell (no server-rendered text)",
                "nothing_extracted": "no main content found in the HTML"}[kind]
        # keep what the HTML did carry so the agent can at least name the page
        return Diag(EXIT_EMPTY, kind, f"{what} at {redact_url(final_url)} — try --render always (local headless browser), --raw, --links, --wayback or --jina (third party)", None,
                    meta={"title": title or html_title(body), "author": author, "date": meta.get("date") or None,
                          "description": clean_field(meta.get("description")), "sitename": sitename, "language": lang,
                          "body_words": body_words, "para_words": sig.para_words})

    cov, cov_warn = coverage_of(words, body_words, sig.para_words)
    warnings += cov_warn
    if sig.heavy and words < 50:  # script-heavy page with footer-only text: metadata is real, the body is a shell
        warnings.append("js_shell")
    if words < 100:
        warnings.append("short")
    if words < 300:  # a consent interstitial served as 200: consent host/path, or two distinct consent phrases
        try:
            u = urlsplit(final_url or "")
            host_hit = CONSENT_HOST_RE.search(u.netloc or "") or CONSENT_PATH_RE.search(u.path or "")
        except ValueError:
            host_hit = None
        sample = (title or "") + "\n" + content[:1200]
        if host_hit or len({m.group(0).lower() for m in CONSENT_PHRASES.finditer(sample)}) >= 2:
            warnings.append("consent_wall")
    if meta.get("author") and author is None:
        warnings.append("author_dropped")
    links = None
    if args.links and doc is not None and doc.body is not None:
        seen: dict[str, str] = {}
        for ref in doc.body.iter("ref"):
            target = (ref.get("target") or "").strip()
            if not target or target.startswith(("javascript:", "mailto:", "#", "tel:")) or target in seen:
                continue
            try:
                tp = urlsplit(target)
            except ValueError:
                continue
            # trafilatura resolves href="#x" against the base URL and drops the path (https://host#x):
            # a same-page anchor in disguise. Skip those and self-links with a fragment (https://host/page#x).
            if tp.fragment and ((tp.path in ("", "/") and not tp.query) or tp._replace(fragment="").geturl() == page_url):
                continue
            text = "".join(ref.itertext()).strip()
            if not text or text in ("↑", "^"):
                continue
            seen[target] = text
        if len(seen) > MAX_LINKS:
            warnings.append("links_truncated")
        if sig.anchors >= 20 and len(seen) < max(5, sig.anchors // 10):
            warnings.append("links_pruned")  # the page is mostly a link list the extractor treated as boilerplate
        links = [{"url": u, "text": t} for u, t in list(seen.items())[:MAX_LINKS]]
    if fmt == "html":
        try:
            content = trafilatura.extract(
                prepare_tree(body), url=final_url, output_format="html", include_links=args.links, include_tables=True,
                include_comments=args.comments, favor_recall=used_recall, favor_precision=args.precision,
                prune_xpath=PRUNE_XPATH) or content
        except Exception:
            pass
    # "plain" marks -f text extraction; "text" is reserved for passthrough bodies (see passthrough()).
    return {
        "title": title, "author": author, "date": meta.get("date") or None,
        "description": clean_field(meta.get("description")), "sitename": sitename,
        "language": lang, "content": content, "content_format": "plain" if fmt == "text" else "markdown",
        "words": words, "chars": len(content), "body_words": body_words, "para_words": sig.para_words,
        "coverage": cov, "warnings": warnings, "links": links,
    }


# -------------------------------------------------------------------- results

RESULT_KEYS = ("ok", "url", "final_url", "status", "source", "snapshot", "snapshot_url", "content_type", "title",
               "author", "date", "description", "sitename", "language", "content", "content_format", "words",
               "chars", "body_words", "para_words", "coverage", "partial", "truncated", "warnings", "links",
               "pages", "structured", "change", "retry_after", "live_error", "fallback_errors", "error")
PARTIAL_WARNINGS = {"js_shell", "low_coverage", "size_limit", "truncated", "consent_wall"}


def new_result(url: str) -> dict[str, Any]:
    r: dict[str, Any] = {k: None for k in RESULT_KEYS}
    r.update(ok=False, url=url, content="", content_format="markdown", words=0, chars=0, partial=False,
             truncated=False, warnings=[], fallback_errors=[])
    return r


def fill(result: dict[str, Any], f: Fetched, data: dict[str, Any], source: str, extra_warnings: list[str]) -> None:
    result.update(data)
    result.update(ok=True, source=source, status=f.status, content_type=f.ctype or None, error=None)
    live_flags = (["redirected"] if f.redirected else []) + (["downgraded"] if f.downgraded else []) if source in ("live", "browser") else []
    browser_flags = (["action_failed"] if f.action_errors else []) + (["human_timeout"] if f.human_timeout else [])
    for w in extra_warnings + (["size_limit"] if f.truncated else []) + live_flags + browser_flags:
        if w not in result["warnings"]:
            result["warnings"].append(w)
    if f.screenshot:
        result["screenshot"] = f.screenshot
    if result["final_url"] is None:
        result["final_url"] = redact_url(f.url)


def passthrough(f: Fetched, raw: bool) -> dict[str, Any]:
    text = decode_text(f.body, f.headers)
    feed = not raw and is_feed(f.body[:4096])
    path_name = urlsplit(f.url).path.rstrip("/").rsplit("/", 1)[-1] or None
    title = None if raw else (feed_title(text) or path_name) if feed else path_name
    words = len(text.split())
    return {"title": title, "content": text, "content_format": "raw" if raw else "text",
            "words": words, "chars": len(text), "body_words": None, "para_words": None, "coverage": None,
            "warnings": (["feed"] if feed else []) + (["short"] if words < 100 and not raw else []), "links": None}


def binary_hint(ctype: str, head: bytes) -> str:
    """What to do with a non-HTML body, by media type (images have nothing to read)."""
    if ctype.startswith("image/"):
        return "an image has no text to extract; nothing to retry"
    if ctype == "application/pdf" or head.lstrip().startswith(b"%PDF-") or ctype.startswith(
            ("application/msword", "application/vnd.openxmlformats", "application/vnd.ms-", "application/vnd.oasis",
             "application/epub", "application/rtf")):
        return "the text layer could not be read; save it with --raw -o FILE and use a document tool"
    return "save it with --raw -o FILE if the bytes are needed; no text to extract"


def handle_body(f: Fetched, result: dict[str, Any], args: argparse.Namespace, source: str) -> Diag | None:
    """Content gate + extraction for a 2xx response. Fills `result` or returns a Diag."""
    kind = gate(f.ctype, f.body[:4096])
    if kind == "binary":
        if args.raw and args.out:
            path = Path(args.out)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f.body)
            except OSError as e:
                return Diag(EXIT_BAD_INPUT, "bad_input", f"cannot write {path}: {e}", f.status)
            fill(result, f, {"content": "", "content_format": "raw", "words": 0, "chars": 0, "body_words": None,
                             "coverage": None, "warnings": ["binary_saved"], "links": None}, source, [])
            result["saved_to"] = str(path)
            return None
        if not args.raw and (f.ctype == "application/pdf" or f.body[:1024].lstrip().startswith(b"%PDF-")):
            if f.truncated:  # pypdf needs the trailer at the end of the file; a cut PDF cannot be read
                return Diag(EXIT_NOT_HTML, "too_large",
                            f"PDF at {redact_url(f.url)} is larger than the {MAX_BODY_BYTES // 1_000_000} MB body cap; its text layer "
                            "cannot be read and --raw -o would save a truncated copy — download it with another tool", f.status)
            data = pdf_extract(f.body, f.url)
            if isinstance(data, Diag):
                data.status = f.status
                return data
            fill(result, f, data, source, [])
            return None
        return Diag(EXIT_NOT_HTML, "non_html", f"content is {f.ctype or 'binary'} ({len(f.body)} bytes), not HTML — {binary_hint(f.ctype, f.body[:64])}", f.status)
    if not f.body.strip(b"\xef\xbb\xbf \t\r\n"):  # 204/205 or an empty 200: "ok" with zero words would mislead
        what = f"HTTP {f.status} with an empty body" if f.status in (204, 205) or not f.body else "response body is only whitespace"
        return Diag(EXIT_EMPTY, "nothing_extracted", f"{what} at {redact_url(f.url)} — nothing to read; try --wayback or --jina (third party)", f.status)
    if args.raw or kind == "text":
        fill(result, f, passthrough(f, args.raw), source, [])
        return None
    data = extract(f.body, f.url, args)
    if args.structured:  # JSON-LD / OpenGraph survive even when the body is a JavaScript shell
        result["structured"] = structured_data(f.body, f.url)
    if isinstance(data, Diag):
        data.status = f.status
        if data.meta:
            result.update(data.meta)
        return data
    fill(result, f, data, source, [])
    return None


def try_wayback(session: Any, url: str, result: dict[str, Any], args: argparse.Namespace) -> tuple[bool, bool]:
    """Newest archived text/html 200 snapshot via CDX. Returns (got_content, archive_unreachable)."""
    timeout = max(float(args.timeout), 45.0)
    params = [("url", url), ("output", "json"), ("limit", "-3"), ("filter", "statuscode:200"), ("fl", "timestamp,original")]
    if not args.raw:
        params.insert(4, ("filter", "mimetype:text/html"))
    cdx = fetch(session, f"{CDX_URL}?{urlencode(params)}", timeout=timeout)
    if isinstance(cdx, Diag):
        result["fallback_errors"].append(f"wayback-cdx: {cdx.message}")
        return False, True
    candidates: list[tuple[str, str, Fetched | None]] = []  # (timestamp, original, prefetched snapshot)
    if cdx.status == 403:
        # archive.org refuses CDX lookups for some publishers' domains ("requires authorization")
        # although snapshots exist; the direct newest-capture redirect still works there.
        probe = fetch(session, f"https://web.archive.org/web/2id_/{url}", timeout=timeout)
        m = re.search(r"/web/(\d{14})id_/(.+)$", probe.url) if isinstance(probe, Fetched) else None
        if isinstance(probe, Fetched) and probe.status == 200 and m and probe.headers.get("memento-datetime"):
            candidates.append((m.group(1), m.group(2), probe))
        else:
            result["fallback_errors"].append("wayback-cdx: HTTP 403 (archive.org restricts CDX lookups for this domain; snapshot index unavailable)")
            return False, False
    elif cdx.status != 200:
        result["fallback_errors"].append(f"wayback-cdx: HTTP {cdx.status}")
        return False, cdx.status in (429, 502, 503, 504)
    else:
        rows = parse_cdx(cdx.body[:1_000_000].decode("utf-8", "replace"))
        if rows is None:
            result["fallback_errors"].append("wayback-cdx: unparsable response")
            return False, False
        if not rows:
            result["fallback_errors"].append("wayback-cdx: no snapshot")
            return False, False
        candidates = [(ts, original, None) for ts, original in rows[:3]]

    best: tuple[dict[str, Any], str] | None = None  # (marker-free but short snapshot, its fallback_errors entry)
    any_suspect = False
    for ts, original, snap in candidates:
        if snap is None:
            snap = fetch(session, f"https://web.archive.org/web/{ts}id_/{original}", timeout=timeout)
        if isinstance(snap, Diag):
            result["fallback_errors"].append(f"wayback-snapshot {ts}: {snap.message}")
            if snap.code == EXIT_NETWORK:
                return False, True
            continue
        if snap.status != 200:
            result["fallback_errors"].append(f"wayback-snapshot {ts}: HTTP {snap.status}")
            continue
        cls = classify(snap.status, {}, snap.body[:MARKER_SCAN_BYTES], original, len(snap.body))
        if cls.error:
            result["fallback_errors"].append(f"wayback-snapshot {ts}: blocked ({cls.error.vendor or cls.error.kind})")
            continue
        snap.url, snap.redirected, snap.downgraded = original, False, False
        trial = new_result(url)
        err = handle_body(snap, trial, args, "wayback")
        if err:
            result["fallback_errors"].append(f"wayback-snapshot {ts}: {err.kind}")
            continue
        trial["fallback_errors"] = result["fallback_errors"]
        trial["live_error"] = result["live_error"]
        trial["final_url"] = original
        trial["snapshot"] = memento_to_iso(snap.headers.get("memento-datetime")) or f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}T{ts[8:10]}:{ts[10:12]}:{ts[12:14]}Z"
        trial["snapshot_url"] = f"https://web.archive.org/web/{ts}/{original}"
        trial["warnings"] = ["archived_copy"] + [w for w in trial["warnings"] if w != "archived_copy"] + cls.warnings
        if not args.raw:
            # archived interstitials (soft bot checks, "continue shopping" pages) are tiny: skip them
            if "challenge_suspected" in cls.warnings:
                result["fallback_errors"].append(f"wayback-snapshot {ts}: challenge_suspected ({trial['words']} words)")
                any_suspect = True
                continue
            if trial["words"] < MIN_SNAPSHOT_WORDS:
                entry = f"wayback-snapshot {ts}: short ({trial['words']} words)"
                result["fallback_errors"].append(entry)
                if best is None or trial["words"] > best[0]["words"]:
                    best = (trial, entry)
                continue
        result.clear()
        result.update(trial)
        return True, False
    if best is not None:
        trial, entry = best
        result["fallback_errors"].remove(entry)  # the delivered snapshot did not fail (list is shared with trial)
        if any_suspect and "challenge_suspected" not in trial["warnings"]:
            trial["warnings"].append("challenge_suspected")
        result.clear()
        result.update(trial)
        return True, False
    return False, False


def try_jina(session: Any, url: str, result: dict[str, Any], args: argparse.Namespace) -> bool:
    note("asking r.jina.ai (third party) for " + redact_url(url))
    r = fetch(session, JINA_URL + quote(url, safe=":/?&=#%+@;,"), timeout=max(float(args.timeout), 60.0),
              attempts=1, headers={"Accept": "application/json"})
    if isinstance(r, Diag):
        result["fallback_errors"].append(f"jina: {r.message}")
        return False
    try:
        payload = json.loads(r.body.decode("utf-8", "replace"))
    except ValueError:
        payload = {}
    data = payload.get("data") if isinstance(payload, dict) else None
    if r.status != 200 or not isinstance(data, dict) or not data.get("content"):
        detail = (payload.get("name") or payload.get("message") or "").strip() if isinstance(payload, dict) else ""
        result["fallback_errors"].append(f"jina: HTTP {r.status}" + (f" {detail}" if detail else ""))
        return False
    content = data["content"].strip()
    if args.format == "text":
        content = strip_md_links(content)
    date = (data.get("publishedTime") or "")[:10] or None
    result.update(ok=True, source="jina", status=r.status, final_url=redact_url(data.get("url") or url), content_type="text/html",
                  title=clean_field(data.get("title")), description=clean_field(data.get("description")), date=date,
                  content=content, content_format="jina_markdown", words=len(content.split()), chars=len(content),
                  body_words=None, para_words=None, coverage=None, warnings=["third_party"], links=None, error=None)
    return True


def process(session: Any, url: str, args: argparse.Namespace) -> dict[str, Any]:
    """One URL end to end. Any unexpected exception becomes exit 7 so the JSON/exit-code contract holds."""
    try:
        return _process(session, url, args)
    except Exception as e:  # last-resort guard
        result = new_result(url)
        msg = f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}".rstrip(": ")
        result["error"] = Diag(EXIT_OTHER, "other", f"unexpected {msg}").as_dict()
        result["ok"] = False
        return finish(result, args)


def _process(session: Any, url: str, args: argparse.Namespace) -> dict[str, Any]:
    result = new_result(url)
    host = urlsplit(url).hostname
    private = is_private_host(host) or is_archive_url(url) or urlsplit(url).username is not None
    live: Diag | None = None
    actions = getattr(args, "action_steps", None) or []
    if args.wayback:
        live = None
    else:
        source = "browser" if args.render == "always" else "live"
        f = render_fetch(url, args, actions) if source == "browser" else fetch(session, url, timeout=float(args.timeout))
        if isinstance(f, Diag):
            live = f
        else:
            result["final_url"], result["status"], result["content_type"] = redact_url(f.url), f.status, f.ctype or None
            if f.redirected:
                note(f"redirected to another host: {redact_url(f.url)}")
            if f.downgraded:
                note(f"https request ended on plain http: {redact_url(f.url)}")
            cls = classify(f.status, f.headers, f.body[:MARKER_SCAN_BYTES], f.url, len(f.body))
            if cls.error:
                live = cls.error
                result["retry_after"] = cls.error.retry_after
            else:
                live = handle_body(f, result, args, source)
                if live and live.code == EXIT_EMPTY and "challenge_suspected" in cls.warnings:
                    live.kind = "challenge_suspected"
                if live is None:
                    result["warnings"] = cls.warnings + result["warnings"]
        # auto render: the HTML had no readable text (exit 1), or very little text next to a lot of script
        want_render = (args.render == "auto" and not args.raw and source == "live" and isinstance(f, Fetched)
                       and gate(f.ctype, f.body[:4096]) == "html"
                       and ((live is not None and live.code == EXIT_EMPTY)
                            or (live is None and (result["words"] or 0) < 50 and page_signals(f.body).dynamic)))
        skipped = [n for n, v in (("--actions", getattr(args, "actions", None)), ("--render-wait", getattr(args, "render_wait", 0)),
                                  ("--screenshot", getattr(args, "screenshot", None))) if v]
        if skipped and not want_render and args.render == "auto" and source == "live":
            had_text = live is None and isinstance(f, Fetched) and gate(f.ctype, f.body[:4096]) == "html"
            why = "the page had server-rendered text so it was not rendered" if had_text else "the page was not rendered"
            note(f"{', '.join(skipped)} not applied: {why} (pass --render always)")
        if want_render:
            pw, why = playwright_api()
            if pw is None:
                note(f"a headless browser could render this page; {why}")
            else:
                reason = live.kind if live is not None else f"only {result['words']} words beside heavy scripts"
                note(f"no server-rendered text ({reason}); rendering the page in a headless browser")
                rf = render_fetch(url, args, actions)
                if isinstance(rf, Diag):
                    result["fallback_errors"].append(f"browser: {rf.message}")
                else:
                    cls = classify(rf.status, rf.headers, rf.body[:MARKER_SCAN_BYTES], rf.url, len(rf.body))
                    trial = new_result(url)
                    trial["final_url"], trial["status"], trial["content_type"] = redact_url(rf.url), rf.status, rf.ctype or None
                    err = cls.error or handle_body(rf, trial, args, "browser")
                    if err:
                        result["fallback_errors"].append(f"browser: {err.kind}" + (f" (HTTP {rf.status})" if cls.error else ""))
                        if rf.screenshot:
                            result["screenshot"] = rf.screenshot
                    elif live is None and (trial["words"] or 0) <= (result["words"] or 0):
                        note(f"browser render did not add text ({trial['words']} words); keeping the HTTP result")
                    else:
                        if live is not None:
                            trial["live_error"] = live.as_dict()
                        trial["fallback_errors"] = result["fallback_errors"]
                        trial["warnings"] = cls.warnings + trial["warnings"]
                        result.clear()
                        result.update(trial)
                        return finish(result, args)
    if live is None and not args.wayback:
        return finish(result, args)

    result["live_error"] = live.as_dict() if live else None
    can_fallback = live is None or (live.code in (EXIT_HTTP, EXIT_BLOCKED, EXIT_NETWORK)
                                    and live.kind not in ("unauthorized", "redirect_to_private"))
    if can_fallback and private:
        if args.wayback:  # nothing to look up: the URL must not leave the machine
            result["error"] = Diag(EXIT_BAD_INPUT, "bad_input",
                                   f"--wayback cannot be used with private/local or archive host {host}; archive lookup skipped").as_dict()
            result["ok"] = False
            return finish(result, args)
        note(f"{host} is a private/local or archive host; Wayback and Jina fallbacks skipped")
        can_fallback = False
    error: Diag | None = live
    if can_fallback and not args.no_wayback:
        got, down = try_wayback(session, url, result, args)
        if got:
            le = result["live_error"]
            when = (result["snapshot"] or "")[:10]
            note((f"live fetch failed (HTTP {le['status']} {le['kind']})" if le and le["status"] else f"live fetch failed ({le['kind']})" if le else "live fetch skipped")
                 + f"; showing Wayback snapshot from {when}")
            return finish(result, args)
        if live is None:  # --wayback mode: the archive result is the verdict
            unusable = any(e.startswith("wayback-snapshot") for e in result["fallback_errors"])
            error = Diag(EXIT_NETWORK, "connection", f"archive.org unreachable: {result['fallback_errors'][-1]}") if down \
                else Diag(EXIT_HTTP, "no_snapshot", f"no {'usable ' if unusable else ''}archived snapshot for {redact_url(url)}")
    if args.jina and error is not None and (can_fallback or error.code == EXIT_EMPTY) and not private:
        if try_jina(session, url, result, args):
            note(f"live fetch failed ({error.kind}); showing r.jina.ai rendering (third party)")
            result["live_error"] = (live or error).as_dict()
            return finish(result, args)
    result["error"] = error.as_dict() if error else None
    result["ok"] = False
    return finish(result, args)


def finish(result: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if result["ok"] and result.get("content_format") != "raw" and (result.get("words") or 0) < 100 and "short" not in result["warnings"]:
        result["warnings"].append("short")  # whatever the source: live, archived, Jina or passthrough
    if result["ok"] and getattr(args, "track", False) and not result.get("saved_to"):
        track_changes(result)
    if result["ok"] and not args.out and args.max_chars > 0:
        result["content"], cut = truncate(result["content"], args.max_chars)
        if cut:
            result["truncated"] = True
            result["warnings"].append("truncated")
            note(f"output truncated to {args.max_chars} chars ({result['chars']} total) — rerun with -o FILE for everything")
    result["partial"] = bool(PARTIAL_WARNINGS & set(result["warnings"]))
    if not result["ok"]:
        result["source"], result["live_error"] = None, None
        if result["error"]:
            sys.stderr.write(f"error: {result['error']['message']}\n")
            if result["fallback_errors"]:
                note("fallbacks tried: " + "; ".join(result["fallback_errors"]))
    return result


# --------------------------------------------------------------------- output

def header_lines(r: dict[str, Any]) -> list[str]:
    dash = "—"
    le = r.get("live_error") or {}
    live_note = f" (live: {le.get('status') or ''} {le.get('kind', '')})".replace("(live:  ", "(live: ") if le else ""
    if r["source"] == "wayback":
        src = f"wayback snapshot {(r['snapshot'] or '')[:10]}{live_note}"
    elif r["source"] == "jina":
        src = f"jina (third party{live_note.replace(' (', '; ').rstrip(')')})" if le else "jina (third party)"
    elif r["source"] == "browser":
        src = f"browser · HTTP {r['status']}{live_note}"
    else:
        src = f"live · HTTP {r['status']}"
    if r["content_format"] in ("text", "raw") and r["content_type"]:  # passthrough bodies: show the media type
        src += f" · {r['content_type']}"
    lines = [f"# title: {clean_field(r['title']) or dash}", f"# url: {r['final_url'] or r['url']}",
             f"# source: {src} · {r['words']} words",
             f"# date: {r['date'] or dash} · author: {clean_field(r['author']) or dash} · lang: {r['language'] or dash}"]
    if r.get("pages"):
        lines[2] += f" · {r['pages']} page{'s' if r['pages'] != 1 else ''}"
    if r.get("screenshot"):
        lines.append(f"# screenshot: {r['screenshot']}")
    ch = r.get("change")
    if ch:
        if ch["status"] == "changed":
            lines.append(f"# change: changed since {ch['previous']} (+{ch['added']} / -{ch['removed']} lines; diff below)")
        elif ch["status"] == "same":
            lines.append(f"# change: same as {ch['previous']}")
        else:
            lines.append("# change: new (first tracked fetch)")
    for w in r["warnings"]:
        if w in HEADER_WARNINGS:
            lines.append(f"# warning: {w}" + (f" — {WARNING_HINTS[w]}" if w in WARNING_HINTS else ""))
    return lines


def render(r: dict[str, Any], args: argparse.Namespace) -> str:
    """Header + body for markdown/text; --raw and -f html are bare bodies (metadata lives in -f json).
    --structured appends the JSON-LD/OpenGraph/meta parts (tables are already in the body), --track the diff."""
    body = r["content"]
    if args.raw or args.format == "html" or r["content_format"] == "raw":
        return body
    st = r.get("structured")
    if st and (st["json_ld"] or st["opengraph"] or st["meta"]):
        part = {k: st[k] for k in ("json_ld", "opengraph", "meta") if st[k]}
        text = json.dumps(part, ensure_ascii=False, indent=2)
        if len(text) > MAX_DIFF_CHARS:
            text = text[:MAX_DIFF_CHARS] + "\n… [structured data truncated; use -f json]"
        body += f"\n\n## Structured data\n\n```json\n{text}\n```"
    ch = r.get("change")
    if ch and ch["status"] == "changed" and ch["diff"]:
        body += f"\n\n## Changes since {ch['previous']}\n\n```diff\n{ch['diff']}\n```"
    if args.no_header:
        return body
    return "\n".join(header_lines(r)) + "\n\n" + body


def main(argv: list[str] | None = None) -> int:
    global QUIET
    for stream in (sys.stdout, sys.stderr):  # before argparse: --help text contains non-ASCII; Windows consoles default to a legacy code page
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("urls", nargs="+", metavar="URL", help="one or more http(s) URLs (scheme optional)")
    p.add_argument("-f", "--format", choices=["markdown", "text", "json", "html"], default="markdown")
    p.add_argument("-o", "--out", metavar="FILE", help="write output to FILE (UTF-8) instead of stdout")
    p.add_argument("--links", action="store_true",
                   help="keep hyperlinks found in the main content (inline in markdown, `links` array in JSON); "
                        "link lists on index pages are pruned as boilerplate")
    p.add_argument("--raw", action="store_true", help="skip extraction and output the response body as-is")
    p.add_argument("--no-header", action="store_true", help="omit the # title/url/source/date header")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, metavar="N",
                   help=f"cap each result's content on stdout at N chars (default {DEFAULT_MAX_CHARS}, 0 = unlimited; ignored with -o)")
    p.add_argument("--comments", action="store_true", help="keep comment sections")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--recall", action="store_true", help="favor recall (more text, more noise)")
    g.add_argument("--precision", action="store_true", help="favor precision (less boilerplate)")
    p.add_argument("--timeout", type=float, default=30.0, metavar="S",
                   help="wall-clock timeout per request attempt in seconds (default 30); a timed-out live fetch is retried once")
    p.add_argument("--impersonate", choices=["chrome", "firefox", "safari", "edge", "none"], default="chrome",
                   help="TLS/HTTP2 fingerprint family (default chrome); none = plain libcurl")
    p.add_argument("--proxy", metavar="URL", help="http(s)/socks proxy URL")
    w = p.add_mutually_exclusive_group()
    w.add_argument("--wayback", action="store_true", help="skip the live fetch; read the newest archived snapshot")
    w.add_argument("--no-wayback", action="store_true", help="never contact archive.org")
    p.add_argument("--jina", action="store_true", help="opt-in last resort via r.jina.ai (sends the URL to a third party)")
    b = p.add_argument_group("browser rendering (optional Playwright + Chromium; see --help text of --render)")
    b.add_argument("--render", choices=["off", "auto", "always"], default="auto",
                   help="run the page in a local headless Chromium: auto (default) = only when the HTTP body has no readable text "
                        "and the browser is installed; always = fetch through the browser; off = never")
    b.add_argument("--headful", action="store_true",
                   help="show the browser window (implies --render always) so a human can pass a bot check, cookie wall or login; "
                        f"waits up to max(--timeout, {RENDER_HUMAN_WAIT:g}) s for the interstitial to go away")
    b.add_argument("--profile", metavar="DIR", help="persistent browser profile directory (cookies and logins survive between runs)")
    b.add_argument("--actions", metavar="JSON",
                   help='steps before reading the page, e.g. \'[{"click":"#more"},{"type":"input[name=q]","text":"x"},'
                        '{"press":"Enter"},{"wait_for":".results"},{"wait":2},{"scroll":3}]\'')
    b.add_argument("--render-wait", type=float, default=0.0, metavar="S", help="extra seconds to wait after the page settled (default 0)")
    b.add_argument("--screenshot", metavar="FILE", help="save a full-page PNG of the rendered page")
    p.add_argument("--structured", action="store_true",
                   help="also return JSON-LD, OpenGraph/Twitter cards, canonical/meta and data tables (`structured` in JSON; appended in markdown)")
    p.add_argument("--track", action="store_true",
                   help="compare with the last tracked fetch of each URL (local cache, $WEB_EXTRACT_CACHE or ~/.cache/web-extract) and report new/same/changed with a diff")
    p.add_argument("-q", "--quiet", action="store_true", help="suppress note: lines on stderr")
    args = p.parse_args(argv)

    QUIET = args.quiet
    if args.timeout <= 0:
        sys.stderr.write("error: --timeout must be positive\n")
        return EXIT_BAD_INPUT
    if args.headful:
        args.render = "always"
    browser_opts = [n for n, v in (("--headful", args.headful), ("--profile", args.profile), ("--actions", args.actions),
                                   ("--render-wait", args.render_wait), ("--screenshot", args.screenshot)) if v]
    if browser_opts and args.render == "off":
        sys.stderr.write(f"error: {', '.join(browser_opts)} {'needs' if len(browser_opts) == 1 else 'need'} --render auto or always\n")
        return EXIT_BAD_INPUT
    if args.render == "always" and args.wayback:
        sys.stderr.write("error: --render always and --wayback are mutually exclusive\n")
        return EXIT_BAD_INPUT
    if args.render_wait < 0 or args.render_wait > 120:
        sys.stderr.write("error: --render-wait must be between 0 and 120 seconds\n")
        return EXIT_BAD_INPUT
    steps = parse_actions(args.actions)
    if isinstance(steps, str):
        sys.stderr.write(f"error: {steps}\n")
        return EXIT_BAD_INPUT
    args.action_steps = steps
    if args.render == "always":
        pw, why = playwright_api()
        if pw is None:
            sys.stderr.write(f"error: {why}\n")
            return EXIT_BAD_INPUT
    urls: list[str] = []
    for raw in args.urls:
        u = normalize_url(raw)
        if u is None:
            if has_userinfo(raw):
                sys.stderr.write("error: credentials in URL are not supported (user:password@host); remove them — "
                                 "the script never sends authentication\n")
            else:
                sys.stderr.write(f"error: not an http(s) URL: {raw}\n")
            return EXIT_BAD_INPUT
        urls.append(u)
    if args.screenshot and len(urls) > 1:
        sys.stderr.write("error: --screenshot takes a single URL (one image file); run once per URL\n")
        return EXIT_BAD_INPUT
    if args.raw and args.out and len(urls) > 1:
        sys.stderr.write("error: --raw -o FILE takes a single URL (one file per body); run once per URL\n")
        return EXIT_BAD_INPUT
    out_path = Path(args.out) if args.out else None
    created_probe = False  # the writability probe created the file; removed again if nothing is written
    if out_path is not None:
        try:
            if out_path.is_dir():
                raise IsADirectoryError(f"{out_path} is a directory")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if out_path.exists():
                if not os.access(out_path, os.W_OK):
                    raise PermissionError(f"{out_path} is not writable")
            else:
                out_path.touch()
                created_probe = True
        except OSError as e:
            sys.stderr.write(f"error: cannot write {args.out}: {e}\n")
            return EXIT_BAD_INPUT
    try:
        session = make_session(args)
    except Exception as e:
        sys.stderr.write(f"error: cannot create HTTP session: {e}\n")
        return EXIT_BAD_INPUT

    results = [process(session, u, args) for u in urls]
    exit_code = next((r["error"]["code"] for r in results if not r["ok"] and r["error"]), EXIT_OK)

    if args.format == "json":
        text = json.dumps({"count": len(results), "results": results}, ensure_ascii=False, indent=2) + "\n"
    else:
        text = "\n\n---\n\n".join(render(r, args) for r in results if r["ok"] and not r.get("saved_to"))
        text = text + "\n" if text and not text.endswith("\n") else text
    if out_path is not None:
        if any(r.get("saved_to") for r in results):
            # handle_body() already wrote the binary body to out_path (single URL, --raw -o);
            # the report goes to stdout so it can never overwrite the file it describes
            if args.format == "json":
                sys.stdout.write(text)
            else:
                for r in results:
                    if r["ok"]:
                        sys.stdout.write(f"saved: {r['saved_to']} ({r['words']} words, source={r['source']})\n")
            return exit_code
        if args.format == "json" or text:  # never truncate an existing file with an empty result
            try:
                out_path.write_text(text, encoding="utf-8", newline="\n")
            except OSError as e:
                sys.stderr.write(f"error: cannot write {args.out}: {e}\n")
                return EXIT_BAD_INPUT
            for r in results:
                if r["ok"]:
                    sys.stdout.write(f"saved: {args.out} ({r['words']} words, source={r['source']})\n")
        else:
            if created_probe:
                out_path.unlink(missing_ok=True)
            note(f"nothing written to {args.out}")
    else:
        sys.stdout.write(text)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
