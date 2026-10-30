#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["feedparser>=6.0,<7"]
# ///
"""Find podcasts and episodes without an API key: Apple's iTunes Search API for the directory, the show's
own RSS feed for the episode list (title, date, duration, audio URL, description).

    uv run podcast.py search "history of science"              # shows: name, author, episodes, genre, feed URL
    uv run podcast.py search "sam altman" --episodes           # episodes across all podcasts, with audio URLs
    uv run podcast.py episodes "Lex Fridman Podcast" -n 5      # a show's newest episodes (name, feed, Apple URL or id)
    uv run podcast.py episodes https://lexfridman.com/feed/podcast/ --grep "math|physics" --min-minutes 60
    uv run podcast.py episodes FEED -n 1 -f urls               # audio URL only, for transcribe-audio

Exit codes:
    0 ok · 1 no results after filters · 2 bad input / missing dependency · 4 rate-limited or blocked by
    Apple or the feed host · 5 feed or show not found (404/410, unknown id) · 6 network error · 7 feed
    unparsable or not a podcast feed, or an unexpected error (a bug) · 130 interrupted
"""

import argparse
import contextlib
import email.utils
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

EXIT_OK, EXIT_EMPTY, EXIT_BAD_INPUT, EXIT_BLOCKED, EXIT_NOT_FOUND, EXIT_NETWORK, EXIT_FEED = 0, 1, 2, 4, 5, 6, 7
EXIT_BUG = 7  # an unexpected error; shares 7 with the documented code, never 1

try:
    import feedparser
except ImportError:
    sys.stderr.write("error: missing dependency feedparser; run with `uv run podcast.py …` or `pip install feedparser`\n")
    sys.exit(EXIT_BAD_INPUT)

USER_AGENT = "zedxter-skills-podcast-search/1.0 (+https://github.com/zedxter/skills)"
ITUNES = "https://itunes.apple.com"
ITUNES_PER_MINUTE = 18  # Apple allows ~20 calls/min per IP and answers 403 beyond; stay under it across runs
MAX_FEED_BYTES = 40_000_000
DESCRIPTION_CHARS = 4000


class Failure(Exception):
    def __init__(self, code: int, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.hint = code, hint


# ------------------------------------------------------------------ helpers

def clean(value: Any) -> str:
    """Feed/directory text as one safe line: control characters dropped, whitespace collapsed.

    >>> clean("Ep 1\\n# fake line\\x1b[31m"), clean(None)
    ('Ep 1 # fake line[31m', '')
    """
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", str(value or ""))
    return " ".join(s.split())


def html_to_text(value: str | None) -> str:
    """Show notes are HTML; keep the text, paragraph and line breaks become spaces.

    >>> html_to_text("<p>Guest: <b>Ann</b> &amp; Bob</p><ul><li>AI</li></ul>")
    'Guest: Ann & Bob AI'
    """
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", value or "", flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return clean(html.unescape(s))


def parse_duration(value: Any) -> int | None:
    """itunes:duration in any of its shapes → seconds.

    >>> [parse_duration(x) for x in ("3:42:40", "62:07", "3725", "1:02:07.5", "", None, "n/a", "PT1H2M")]
    [13360, 3727, 3725, 3727, None, None, None, 3720]
    """
    s = str(value or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return int(float(s))
    if re.fullmatch(r"\d+(?::\d{1,2}){1,2}(?:\.\d+)?", s):
        total = 0.0
        for part in s.split(":"):
            total = total * 60 + float(part)
        return int(total)
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s, re.I)
    if m and any(m.groups()):
        h, mi, se = (int(x or 0) for x in m.groups())
        return h * 3600 + mi * 60 + se
    return None


def fmt_duration(sec: int | None) -> str:
    """
    >>> fmt_duration(13360), fmt_duration(59), fmt_duration(None)
    ('3:42:40', '0:59', '—')
    """
    if sec is None:
        return "—"
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"bad date {value!r}; use YYYY-MM-DD")


def apple_id(value: str) -> str | None:
    """Apple Podcasts show id from a podcasts.apple.com / itunes.apple.com URL, `id123…` or a bare number.

    >>> apple_id("https://podcasts.apple.com/us/podcast/lex-fridman-podcast/id1434243584?i=1000649593201")
    '1434243584'
    >>> apple_id("id1434243584"), apple_id("1434243584"), apple_id("https://lexfridman.com/feed/podcast/"), apple_id("Lex")
    ('1434243584', '1434243584', None, None)
    """
    s = value.strip()
    if re.fullmatch(r"(?:id)?\d{5,12}", s):
        return s.removeprefix("id")
    p = urllib.parse.urlsplit(s)
    if (p.hostname or "").endswith(("podcasts.apple.com", "itunes.apple.com")):
        m = re.search(r"/id(\d+)", p.path)
        return m.group(1) if m else None
    return None


def is_url(value: str) -> bool:
    p = urllib.parse.urlsplit(value.strip())
    return p.scheme.lower() in ("http", "https") and bool(p.hostname)


# ------------------------------------------------------------------ network

def throttle_file() -> Path:
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_CACHE_HOME")
    return Path(base or Path.home() / ".cache") / "zedxter-skills" / "itunes-calls.json"


@contextlib.contextmanager
def locked(path: Path):
    """Exclusive lock on `path` (created) for the read-modify-write of the throttle file; POSIX and Windows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as fh:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            for _ in range(100):  # LK_NBLCK retries; msvcrt has no blocking lock without a 10 s timeout
                try:
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


def throttle(note) -> None:
    """Keep this machine under ITUNES_PER_MINUTE calls to Apple, across concurrent runs: each call reserves a
    time slot in a small locked cache file (a slot may lie in the future), then waits for it."""
    path = throttle_file()
    try:
        with locked(path.with_suffix(".lock")):
            now = time.time()
            try:
                calls = sorted(t for t in json.loads(path.read_text()) if t > now - 60)
            except (OSError, ValueError, TypeError):
                calls = []
            slot = now
            if len(calls) >= ITUNES_PER_MINUTE:
                # the N-th most recent call leaves the 60 s window at +60; from then on there is room again
                slot = max(now, calls[-ITUNES_PER_MINUTE] + 60.5)
            calls.append(slot)
            path.write_text(json.dumps(calls))
    except OSError:
        slot = time.time()  # throttling is best effort; an unwritable cache must not block searching
    wait = slot - time.time()
    if wait > 0:
        note(f"pausing {wait:.0f} s to stay under Apple's ~20 requests/minute limit")
        time.sleep(wait)


def http_get(url: str, timeout: float, limit: int, what: str) -> tuple[bytes, dict[str, str]]:
    """Body and lower-cased headers; the Content-Type charset matters for feeds without an XML encoding."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(limit + 1)
            headers = {k.lower(): v for k, v in resp.headers.items()}
            expected = headers.get("content-length", "")
            if expected.isdigit() and len(data) < int(expected) <= limit:
                raise Failure(EXIT_NETWORK, f"{what}: connection closed after {len(data)} of {expected} bytes",
                              "the download was cut off; one retry later is fine")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise Failure(EXIT_BLOCKED, f"{what}: HTTP {e.code}",
                          "rate-limited or blocked; wait a few minutes before retrying, do not loop")
        if e.code in (404, 410):
            raise Failure(EXIT_NOT_FOUND, f"{what}: HTTP {e.code}", "the feed moved or the show ended; search the show again for its current feed")
        raise Failure(EXIT_NETWORK, f"{what}: HTTP {e.code}", "server error; one retry later is fine")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        reason = getattr(e, "reason", e)
        if "CERTIFICATE_VERIFY_FAILED" in str(reason):
            raise Failure(EXIT_NETWORK, f"{what}: TLS certificate verification failed ({reason})",
                          "retrying will not help; on a python.org macOS build run 'Install Certificates.command', "
                          "or run with uv's managed Python (uv run --python-preference only-managed); a proxy may be intercepting TLS")
        raise Failure(EXIT_NETWORK, f"{what}: {reason}", "check the connection (HTTPS_PROXY is honoured); one retry later is fine")
    if len(data) > limit:
        raise Failure(EXIT_FEED, f"{what}: larger than {limit // 1_000_000} MB", "not a normal podcast feed")
    if headers.get("content-encoding", "").lower() == "gzip" or data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError) as e:
            raise Failure(EXIT_FEED, f"{what}: broken gzip body ({e})")
        headers.pop("content-encoding", None)
        if len(data) > limit:
            raise Failure(EXIT_FEED, f"{what}: larger than {limit // 1_000_000} MB", "not a normal podcast feed")
    return data, headers


def itunes(path: str, params: dict[str, Any], args: argparse.Namespace, note) -> list[dict[str, Any]]:
    throttle(note)
    url = f"{ITUNES}/{path}?{urllib.parse.urlencode(params)}"
    data, _ = http_get(url, args.timeout, 5_000_000, "Apple Podcasts directory")
    try:
        return json.loads(data.decode("utf-8")).get("results", [])
    except ValueError:
        raise Failure(EXIT_BLOCKED, "Apple Podcasts directory returned a non-JSON page", "probably throttled; wait a few minutes")


# ------------------------------------------------------------------ records

def show_record(r: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "show", "id": r.get("collectionId"), "title": clean(r.get("collectionName")),
            "author": clean(r.get("artistName")), "episodes": r.get("trackCount"),
            "genre": clean(r.get("primaryGenreName")) or None, "latest": (r.get("releaseDate") or "")[:10] or None,
            "feed_url": r.get("feedUrl"), "apple_url": (r.get("collectionViewUrl") or "").split("?")[0] or None,
            "explicit": r.get("collectionExplicitness") == "explicit"}


def directory_episode(r: dict[str, Any]) -> dict[str, Any]:
    ms = r.get("trackTimeMillis")
    return {"kind": "episode", "show": clean(r.get("collectionName")), "title": clean(r.get("trackName")),
            "published": (r.get("releaseDate") or "")[:10] or None, "duration": int(ms / 1000) if ms else None,
            "audio_url": r.get("episodeUrl"), "audio_type": r.get("episodeContentType"),
            "url": (r.get("trackViewUrl") or "").split("&uo=")[0] or None, "feed_url": r.get("feedUrl"),
            "guid": r.get("episodeGuid"), "description": clean(r.get("description") or r.get("shortDescription"))[:DESCRIPTION_CHARS],
            "transcript_url": None}


MEDIA_EXT = re.compile(r"\.(mp3|m4a|aac|ogg|oga|opus|mp4|m4v|wav|flac|webm)(\?|$)", re.I)


def pick_media(e: Any) -> dict[str, Any] | None:
    """The episode's audio/video among its enclosures and <media:content> items; never an image or a page."""
    candidates = [{"href": enc.get("href"), "type": enc.get("type") or "", "length": enc.get("length")}
                  for enc in e.get("enclosures", [])]
    candidates += [{"href": m.get("url"), "type": m.get("type") or "", "length": m.get("filesize") or m.get("fileSize")}
                   for m in e.get("media_content", []) or []]
    candidates = [c for c in candidates if c["href"]]
    for c in candidates:
        if c["type"].startswith(("audio", "video")) or (MEDIA_EXT.search(c["href"]) and not c["type"].startswith(("image", "text"))):
            return c
    return None


def feed_date(e: Any) -> tuple[str | None, float]:
    """(date as the show published it, in its own time zone; sortable timestamp).

    >>> feed_date({"published": "Tue, 05 Mar 2024 18:00:00 -0800"})
    ('2024-03-05', 1709690400.0)
    """
    raw = e.get("published") or e.get("updated")
    if raw:
        try:
            moment = email.utils.parsedate_to_datetime(raw)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
            return moment.date().isoformat(), moment.timestamp()
        except (TypeError, ValueError, IndexError):
            pass
    parsed = e.get("published_parsed") or e.get("updated_parsed")  # Atom/ISO dates, normalised to UTC by feedparser
    if parsed:
        moment = datetime(*parsed[:6], tzinfo=UTC)
        return moment.date().isoformat(), moment.timestamp()
    return None, 0.0


def feed_episode(e: Any, show: str) -> dict[str, Any] | None:
    audio = pick_media(e)
    if audio is None:
        return None  # a blog post, an announcement, or an item with only an image
    published, stamp = feed_date(e)
    content = (e.get("content") or [{}])[0].get("value") if e.get("content") else None
    description = html_to_text(e.get("summary") or content)
    # <podcast:transcript url=… type=…/> (Podcasting 2.0); feedparser keeps the last one when there are several
    tag = e.get("podcast_transcript")
    transcript = tag.get("url") if isinstance(tag, dict) else None
    size = str(audio.get("length") or "")
    # "_ts" orders same-day episodes; keys starting with "_" never reach the output
    return {"kind": "episode", "show": show, "title": clean(e.get("title")), "published": published,
            "duration": parse_duration(e.get("itunes_duration")), "audio_url": audio.get("href"),
            "audio_type": audio.get("type") or None, "audio_bytes": int(size) if size.isdigit() and int(size) > 0 else None,
            # feedparser copies a non-URL <guid> into link when there is no <link>
            "url": e.get("link") if is_url(e.get("link") or "") else None, "guid": e.get("id"), "season": e.get("itunes_season"), "episode": e.get("itunes_episode"),
            "description": description[:DESCRIPTION_CHARS], "transcript_url": transcript, "_ts": stamp}


def parse_feed(data: bytes, url: str, headers: dict[str, str] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    d = feedparser.parse(data, response_headers=headers or {})
    if not d.version:  # feedparser recognised no RSS/Atom flavour: an HTML page, JSON, an error page
        reason = clean(getattr(d, "bozo_exception", "")) or "no items"
        raise Failure(EXIT_FEED, f"not a readable RSS/Atom feed: {url} ({reason[:160]})",
                      "pass the show's RSS URL, an Apple Podcasts URL/id, or a show name")
    title = clean(d.feed.get("title"))
    show = {"title": title, "author": clean(d.feed.get("author") or d.feed.get("itunes_author")) or None,
            "language": d.feed.get("language"), "link": d.feed.get("link"), "feed_url": url,
            "description": html_to_text(d.feed.get("summary") or d.feed.get("subtitle"))[:1000]}
    episodes = [ep for ep in (feed_episode(e, title) for e in d.entries) if ep]
    if d.entries and not episodes:
        raise Failure(EXIT_FEED, f"the feed has items but no audio or video: {url}", "this is a blog/news feed, not a podcast")
    return show, episodes


# ------------------------------------------------------------------ commands

def filter_episodes(episodes: list[dict[str, Any]], args: argparse.Namespace, by_date: bool = True) -> list[dict[str, Any]]:
    """Apply --after/--before/--grep/--min-minutes/--max-minutes; sort by date (newest first unless --oldest) when
    `by_date`, else keep the incoming (relevance) order."""
    pattern = re.compile(args.grep, re.I) if args.grep else None
    out = []
    for ep in episodes:
        day = ep.get("published")
        if args.after and (not day or day < args.after.isoformat()):
            continue
        if args.before and (not day or day > args.before.isoformat()):
            continue
        if pattern and not pattern.search(f"{ep['title']}\n{ep.get('description') or ''}"):
            continue
        dur = ep.get("duration")
        if args.min_minutes is not None and (dur is None or dur < args.min_minutes * 60):
            continue
        if args.max_minutes is not None and (dur is None or dur > args.max_minutes * 60):
            continue
        out.append(ep)
    if by_date:
        out.sort(key=lambda ep: (ep.get("published") or "", ep.get("_ts") or 0.0), reverse=not args.oldest)
    return out


def resolve_feed(target: str, args: argparse.Namespace, note) -> tuple[str, dict[str, Any] | None]:
    """FEED URL, Apple URL/id or a show name → (feed URL, directory record or None)."""
    ident = apple_id(target)
    if ident:
        results = itunes("lookup", {"id": ident, "entity": "podcast"}, args, note)
        shows = [r for r in results if r.get("kind") == "podcast" or r.get("wrapperType") == "collection"]
        if not shows:
            raise Failure(EXIT_NOT_FOUND, f"no Apple Podcasts show with id {ident}", "check the URL, or search by name")
    elif is_url(target):
        return target.strip(), None
    else:
        shows = itunes("search", {"term": target, "media": "podcast", "entity": "podcast", "limit": 5, "country": args.country},
                       args, note)
        if not shows:
            raise Failure(EXIT_EMPTY, f"no podcast found for {target!r}", "check the spelling, try --country, or use `search`")
        exact = [s for s in shows if clean(s.get("collectionName")).casefold() == target.strip().casefold()]
        if not exact:
            note(f"using the best match {clean(shows[0].get('collectionName'))!r} by {clean(shows[0].get('artistName'))}; "
                 "pass its feed URL or Apple URL to be exact")
        shows = exact or shows
    record = show_record(shows[0])
    if not record["feed_url"]:
        raise Failure(EXIT_NOT_FOUND, f"{record['title']!r} has no public RSS feed",
                      "Apple-exclusive or paid shows (Apple Podcasts Subscriptions) cannot be read")
    return record["feed_url"], record


def cmd_search(args: argparse.Namespace, note) -> dict[str, Any]:
    query = " ".join(args.query).strip()
    if not query:
        raise Failure(EXIT_BAD_INPUT, "empty query")
    episode_filters = [flag for flag, v in (("--after", args.after), ("--before", args.before), ("--grep", args.grep),
                       ("--min-minutes", args.min_minutes), ("--max-minutes", args.max_minutes), ("--oldest", args.oldest or None))
                       if v is not None]
    if episode_filters and not args.episodes:
        raise Failure(EXIT_BAD_INPUT, f"{', '.join(episode_filters)} filter episodes, not shows",
                      "add --episodes, or list a show's episodes with `episodes SHOW`")
    entity = "podcastEpisode" if args.episodes else "podcast"
    # The directory ranks by relevance and has no date sort; fetch more when filters will drop results.
    filtering = any(v is not None for v in (args.after, args.before, args.grep, args.min_minutes, args.max_minutes)) \
        or args.sort == "date" or args.oldest
    limit = min(200, max(args.limit * (4 if filtering else 1), args.limit))
    rows = itunes("search", {"term": query, "media": "podcast", "entity": entity, "limit": limit, "country": args.country}, args, note)
    if args.episodes:
        results = [directory_episode(r) for r in rows if r.get("kind") == "podcast-episode"]
        if filtering:
            results = filter_episodes(results, args, by_date=args.sort == "date" or args.oldest)
    else:
        results = [show_record(r) for r in rows if r.get("kind") == "podcast" or r.get("wrapperType") == "track"]
        if args.sort == "date":
            results.sort(key=lambda s: s.get("latest") or "", reverse=True)
    return {"query": query, "country": args.country, "results": results[: args.limit]}


def cmd_episodes(args: argparse.Namespace, note) -> dict[str, Any]:
    target = " ".join(args.show).strip()
    if not target:
        raise Failure(EXIT_BAD_INPUT, "no show given", "pass a feed URL, an Apple Podcasts URL/id or a show name")
    feed_url, record = resolve_feed(target, args, note)
    data, headers = http_get(feed_url, args.timeout, MAX_FEED_BYTES, f"feed {feed_url}")
    show, episodes = parse_feed(data, feed_url, headers)
    if record:
        show.update({"apple_id": record["id"], "apple_url": record["apple_url"], "genre": record["genre"]})
    total = len(episodes)
    episodes = filter_episodes(episodes, args)
    return {"show": show, "total_episodes": total, "results": episodes[: args.limit]}


# ------------------------------------------------------------------ output

def render(payload: dict[str, Any], fmt: str) -> str:
    results = payload["results"]
    if fmt == "json":
        public = {**payload, "results": [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]}
        return json.dumps(public, ensure_ascii=False, indent=2) + "\n"
    if fmt == "urls":
        key = "audio_url" if results and results[0]["kind"] == "episode" else "feed_url"
        return "".join(f"{r[key]}\n" for r in results if r.get(key))
    lines = []
    if "show" in payload:
        s = payload["show"]
        by = f" — {s['author']}" if s.get("author") else ""
        lines += [f"# {s['title']}{by} ({payload['total_episodes']} episodes in feed)", f"# feed: {s['feed_url']}", ""]
    else:
        lines += [f"# {len(results)} result(s) for: {payload['query']}", ""]
    for i, r in enumerate(results, 1):
        if r["kind"] == "show":
            meta = " · ".join(x for x in (r["author"], f"{r['episodes']} episodes" if r["episodes"] else "", r["genre"] or "",
                                          f"latest {r['latest']}" if r["latest"] else "") if x)
            lines += [f"{i:2}. {r['title']}", f"    {meta}", f"    feed: {r['feed_url'] or '— (no public feed)'}",
                      f"    {r['apple_url'] or ''}".rstrip()]
        else:
            show = f"{r['show']} · " if "show" not in payload else ""
            lines += [f"{i:2}. {r['title']}", f"    {show}{r['published'] or 'undated'} · {fmt_duration(r['duration'])}"
                      + (" · transcript in feed" if r.get("transcript_url") else ""),
                      f"    audio: {r['audio_url']}"]
            if r.get("description"):
                desc = r["description"]
                lines.append(f"    {desc[:200]}{'…' if len(desc) > 200 else ''}")
    return "\n".join(lines).rstrip() + "\n"


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-n", "--limit", type=int, default=10, help="results to print (default 10, max 200)")
    common.add_argument("-f", "--format", choices=["table", "json", "urls"], default="table",
                        help="table (default), json (every field), urls (audio URLs for episodes, feed URLs for shows)")
    common.add_argument("--country", default="us", help="Apple store country, 2 letters (default us); e.g. ru, de, gb for local shows")
    common.add_argument("--after", metavar="YYYY-MM-DD", help="episodes published on or after this date")
    common.add_argument("--before", metavar="YYYY-MM-DD", help="episodes published on or before this date")
    common.add_argument("--grep", metavar="REGEX", help="episodes whose title or description matches (case-insensitive)")
    common.add_argument("--min-minutes", type=float, help="episodes at least this long")
    common.add_argument("--max-minutes", type=float, help="episodes at most this long")
    common.add_argument("--oldest", action="store_true", help="oldest episodes first (default newest first)")
    common.add_argument("--timeout", type=float, default=30.0, help="network timeout in seconds (default 30)")
    common.add_argument("-q", "--quiet", action="store_true", help="no note: lines")
    s = sub.add_parser("search", parents=[common], help="search the Apple Podcasts directory for shows (or --episodes)")
    s.add_argument("query", nargs="+", help="search terms")
    s.add_argument("--episodes", action="store_true", help="search episodes across all podcasts instead of shows")
    s.add_argument("--sort", choices=["relevance", "date"], default="relevance", help="relevance (default) or date (newest first)")
    e = sub.add_parser("episodes", parents=[common], help="list a show's episodes from its RSS feed")
    e.add_argument("show", nargs="+", help="RSS feed URL, Apple Podcasts URL or id, or a show name")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = make_parser().parse_args(argv)
    note = (lambda m: None) if args.quiet else (lambda m: sys.stderr.write(f"note: {m}\n"))

    def fail(code: int, message: str, hint: str = "") -> int:
        sys.stderr.write(f"error: {message}" + (f" — {hint}" if hint else "") + "\n")
        return code

    try:
        args.after = parse_date(args.after) if args.after else None
        args.before = parse_date(args.before) if args.before else None
    except ValueError as e:
        return fail(EXIT_BAD_INPUT, str(e))
    if not 1 <= args.limit <= 200:
        return fail(EXIT_BAD_INPUT, "--limit must be between 1 and 200")
    if not re.fullmatch(r"[A-Za-z]{2}", args.country):
        return fail(EXIT_BAD_INPUT, f"bad --country {args.country!r}", "use a two-letter store code such as us, gb, de, ru")
    args.country = args.country.lower()
    if args.grep:
        try:
            re.compile(args.grep)
        except re.error as e:
            return fail(EXIT_BAD_INPUT, f"bad --grep pattern: {e}")
    try:
        payload = cmd_search(args, note) if args.command == "search" else cmd_episodes(args, note)
    except Failure as e:
        return fail(e.code, str(e), e.hint)
    except KeyboardInterrupt:
        return fail(130, "interrupted")
    if not payload["results"]:
        what = "episodes" if args.command == "episodes" or getattr(args, "episodes", False) else "shows"
        if args.format == "json":
            sys.stdout.write(render(payload, "json"))
        return fail(EXIT_EMPTY, f"no {what} match", "loosen --after/--grep/--min-minutes, or broaden the query")
    sys.stdout.write(render(payload, args.format))
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
        sys.exit(EXIT_BUG)
    except Exception as e:  # noqa: BLE001 - a crash exits 7, never 1 or another code that has a meaning
        sys.stderr.write(f"error: unexpected {type(e).__name__}: {(str(e).splitlines() or [''])[0][:300]} "
                         "— a bug; report it with the command line\n")
        sys.exit(EXIT_BUG)
