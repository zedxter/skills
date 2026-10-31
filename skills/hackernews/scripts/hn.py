#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Hacker News without a key: search stories and comments (Algolia HN Search), the front page, newest, best, Ask,
Show and job lists (the official Firebase API), a story with its whole comment tree, and a user's profile and recent
posts. Standard library only.

    uv run hn.py search "rust async" --min-points 100 -n 10
    uv run hn.py search "sqlite" --type comment --since 7d --sort date
    uv run hn.py top -n 20                          # the front page, in order
    uv run hn.py item 8863 --max-comments 50        # a story with its top comments, threaded
    uv run hn.py item https://news.ycombinator.com/item?id=8863 -f markdown -o thread.md
    uv run hn.py user pg
    uv run hn.py top -n 10 -f urls                  # article links, one per line, for web-extract

Exit codes:
    0 done · 1 no results, or the item or user does not exist · 2 bad input · 4 rate-limited · 6 network error or the
    service down · 7 an unexpected answer or failure · 130 interrupted
"""

import argparse
import contextlib
import datetime as dt
import html
import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

EXIT_OK, EXIT_EMPTY, EXIT_BAD_INPUT, EXIT_RATE, EXIT_NETWORK, EXIT_OTHER = 0, 1, 2, 4, 6, 7

UA = "utility-belt-hackernews/1.0 (+https://github.com/zedxter/utility-belt)"
ALGOLIA = "https://hn.algolia.com/api/v1"
FIREBASE = "https://hacker-news.firebaseio.com/v0"
HN = "https://news.ycombinator.com"
LISTS = {"top": "topstories", "new": "newstories", "best": "beststories", "ask": "askstories",
         "show": "showstories", "job": "jobstories"}
TYPES = {"story": "story", "comment": "comment", "ask": "ask_hn", "show": "show_hn", "poll": "poll", "job": "job"}
ALGOLIA_INTERVAL = 0.4    # HN Search allows 10,000 requests an hour per IP; this stays under 9,000
WORKERS = 8               # parallel Firebase item reads
LIST_MAX = {"top": 500, "new": 500, "best": 200, "ask": 200, "show": 200, "job": 200}


class Failure(Exception):
    def __init__(self, code: int, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.hint = code, hint


def safe(s: Any) -> str:
    """One printable line: no control, C1 or bidi-override characters, whitespace collapsed."""
    return " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f‪-‮⁦-⁩]", " ", str(s if s is not None else "")).split())


def safe_block(s: str) -> str:
    return "\n".join(re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]", " ", ln).rstrip()
                     for ln in s.split("\n"))


TOKEN = re.compile(r"<pre><code>(.*?)</code></pre>|<a\s[^>]*?href=\"([^\"]*)\"[^>]*>(.*?)</a>|<p>|</?(?:i|em)>|<[^>]+>",
                   re.S | re.I)


def text_of(h: str | None) -> str:
    r"""HN's comment HTML as plain text: paragraphs kept, links as their URL (or "text (URL)"), code blocks indented,
    italics as *...*. Every piece is decoded exactly once, so "&amp;region=" in a link stays "&region=".

    >>> text_of('a<p>see <a href="https:&#x2F;&#x2F;x.org&#x2F;s?q=1&amp;region=us">https:&#x2F;&#x2F;x.org&#x2F;s?q=1...</a>')
    'a\n\nsee https://x.org/s?q=1&region=us'
    """
    if not h:
        return ""
    out, pos = [], 0
    for m in TOKEN.finditer(h):
        out.append(html.unescape(h[pos:m.start()]))
        tok = m[0].lower()
        if m[1] is not None:
            code = html.unescape(m[1]).strip("\n")
            out.append("\n\n" + "\n".join("    " + ln for ln in code.split("\n")) + "\n\n")
        elif m[2] is not None:
            href = html.unescape(m[2])
            label = html.unescape(re.sub(r"<[^>]+>", "", m[3])).strip()
            short = label.rstrip(".…")
            url_like = re.match(r"^(https?://|www\.)", short, re.I)
            same = url_like and re.sub(r"^https?://", "", href).startswith(re.sub(r"^https?://", "", short))
            out.append(href if not label or same else f"{label} ({href})")
        elif tok == "<p>":
            out.append("\n\n")
        elif re.fullmatch(r"</?(i|em)>", tok):
            out.append("*")
        pos = m.end()
    out.append(html.unescape(h[pos:]))
    s = re.sub(r"\n{3,}", "\n\n", "".join(out).replace("\r", ""))
    return s.strip("\n").rstrip()


def clean_link(u: str) -> str:
    """A URL without the sentence punctuation old HN comments glued onto it ("…investors.html),")."""
    u = u.rstrip(".,;:!?'\"")
    while u.endswith(")") and u.count(")") > u.count("("):
        u = u[:-1].rstrip(".,;:!?")
    return u


def links_of(h: str | None) -> list[str]:
    """The links of an HN text, from its <a href> tags (HN turns every URL in a comment into one)."""
    return [clean_link(html.unescape(m[2])) for m in TOKEN.finditer(h or "") if m[2]]


def when(ts: Any) -> str | None:
    if isinstance(ts, (int, float)):
        return dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(ts, str) and ts:
        return ts.replace(".000Z", "Z")
    return None


def ago(iso: str | None) -> str:
    if not iso:
        return ""
    with contextlib.suppress(ValueError):
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        secs = (dt.datetime.now(dt.UTC) - t).total_seconds()
        for unit, size in (("y", 365 * 86400), ("mo", 30 * 86400), ("d", 86400), ("h", 3600), ("m", 60)):
            if secs >= size:
                return f"{int(secs // size)}{unit} ago"
        return "just now"
    return iso


def item_url(i: Any) -> str:
    return f"{HN}/item?id={i}"


# ------------------------------------------------------------------ network

class Net:
    def __init__(self, timeout: float, note) -> None:
        self.timeout, self.note = timeout, note
        self.lock = threading.Lock()
        self.last_algolia = 0.0

    def get(self, url: str) -> Any:
        """Parsed JSON; None for a 404. 429 is retried once, then exit 4; 5xx and network errors are retried once,
        then exit 6."""
        host = urllib.parse.urlsplit(url).hostname or ""
        for attempt in (1, 2):
            if host.endswith("algolia.com"):
                with self.lock:
                    wait = self.last_algolia + ALGOLIA_INTERVAL - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                    self.last_algolia = time.monotonic()
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read(100_000_001)
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code == 429:
                    ra = (e.headers or {}).get("Retry-After", "")
                    secs = int(ra) if ra.isdigit() else 10
                    if attempt == 1 and secs <= 30:
                        self.note(f"{host} rate limit; waiting {secs} s once")
                        time.sleep(secs)
                        continue
                    raise Failure(EXIT_RATE, f"{host} rate limit (HTTP 429)", "wait a minute; do not loop") from None
                if e.code >= 500 and attempt == 1:
                    time.sleep(2)
                    continue
                if e.code >= 500:
                    raise Failure(EXIT_NETWORK, f"{host} is not answering (HTTP {e.code})", "try again later") from None
                if e.code == 400:
                    raise Failure(EXIT_BAD_INPUT, f"{host} rejected the request (HTTP 400)",
                                  "simplify the query or the filters") from None
                raise Failure(EXIT_OTHER, f"{host} answered HTTP {e.code}", "try again later") from None
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
                why = getattr(e, "reason", e) or type(e).__name__
                if "CERTIFICATE_VERIFY_FAILED" in str(why):
                    raise Failure(EXIT_NETWORK, f"TLS certificate verification failed ({why})",
                                  "on a python.org macOS build run 'Install Certificates.command', or use uv's "
                                  "managed Python") from None
                if attempt == 2:
                    raise Failure(EXIT_NETWORK, f"{host}: {why}", "check the connection (HTTPS_PROXY is honoured)") from None
                time.sleep(1)
        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise Failure(EXIT_OTHER, f"{host} answered something that is not JSON", "try again later") from None


# ------------------------------------------------------------------ records

def story_record(h: dict[str, Any], source: str = "algolia") -> dict[str, Any]:
    """A story or comment from an Algolia hit or a Firebase item, in one shape."""
    if source == "firebase":
        sid = h.get("id")
        kind = h.get("type")
        title = h.get("title")
        tags = []
        if kind == "story" and isinstance(title, str):
            tags = ["ask_hn"] if title.startswith("Ask HN") else ["show_hn"] if title.startswith("Show HN") else []
        return {"id": sid, "type": kind, "title": title, "url": h.get("url"), "author": h.get("by"),
                "points": h.get("score"), "comments": h.get("descendants"), "created": when(h.get("time")),
                "text": text_of(h.get("text")) or None, "hn_url": item_url(sid), "story_id": None,
                "story_title": None, "parent_id": h.get("parent"), "tags": tags,
                "dead": bool(h.get("dead")), "deleted": bool(h.get("deleted"))}
    tags = [t for t in h.get("_tags") or [] if t in ("story", "comment", "ask_hn", "show_hn", "poll", "job", "front_page")]
    kind = "comment" if "comment" in tags else "job" if "job" in tags else "poll" if "poll" in tags else "story"
    sid = h.get("objectID") or h.get("id")
    sid = int(sid) if str(sid).isdigit() else sid
    return {"id": sid, "type": kind, "title": h.get("title"), "url": h.get("url"), "author": h.get("author"),
            "points": h.get("points"), "comments": h.get("num_comments"), "created": when(h.get("created_at")),
            "text": text_of(h.get("comment_text") or h.get("story_text")) or None, "hn_url": item_url(sid),
            "story_id": h.get("story_id"), "story_title": h.get("story_title"), "parent_id": h.get("parent_id"),
            "tags": [t for t in tags if t != kind], "dead": False, "deleted": False}


# ------------------------------------------------------------------ commands

def since_epoch(s: str, flag: str = "--since") -> int:
    """'24h', '7d', '4w', '6m' (months), '1y', or a date YYYY-MM-DD → Unix time."""
    now = dt.datetime.now(dt.UTC)
    if flag == "--since" and (m := re.fullmatch(r"(\d{1,5})\s*(h|d|w|m|y)", s.strip().lower())):
        n, unit = int(m[1]), m[2]
        try:
            delta = {"h": dt.timedelta(hours=n), "d": dt.timedelta(days=n), "w": dt.timedelta(weeks=n),
                     "m": dt.timedelta(days=30 * n), "y": dt.timedelta(days=365 * n)}[unit]
            return max(0, int((now - delta).timestamp()))
        except (OverflowError, ValueError):
            raise Failure(EXIT_BAD_INPUT, f"{flag} {s!r} is too far back", "Hacker News started in 2006") from None
    try:
        d = dt.date.fromisoformat(s.strip())
    except ValueError:
        raise Failure(EXIT_BAD_INPUT, f"{flag} {s!r} is not " + ("a period or a date" if flag == "--since" else "a date"),
                      "use 24h, 7d, 4w, 6m, 1y or YYYY-MM-DD" if flag == "--since" else "use YYYY-MM-DD") from None
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.UTC).timestamp())


def cmd_search(net: Net, args: argparse.Namespace) -> dict[str, Any]:
    tags = [TYPES[args.type or "story"]]
    if args.author:
        tags.append(f"author_{args.author}")
    filters = []
    if args.since:
        filters.append(f"created_at_i>={since_epoch(args.since)}")
    if args.until:
        filters.append(f"created_at_i<{since_epoch(args.until, '--until') + 86400}")
    if args.min_points:
        filters.append(f"points>={args.min_points}")
    if args.min_comments:
        filters.append(f"num_comments>={args.min_comments}")
    params: dict[str, Any] = {"query": args.query, "tags": ",".join(tags), "hitsPerPage": args.n}
    if filters:
        params["numericFilters"] = ",".join(filters)
    endpoint = "search_by_date" if args.sort == "date" else "search"
    d = net.get(f"{ALGOLIA}/{endpoint}?{urllib.parse.urlencode(params)}")
    hits = d.get("hits") if isinstance(d, dict) else None
    if not isinstance(hits, list):
        raise Failure(EXIT_OTHER, "HN Search answered without a result list", "try again later")
    results = [story_record(h) for h in hits if isinstance(h, dict)]
    if not results:
        raise Failure(EXIT_EMPTY, f"no results for {args.query!r}" if args.query else "no results",
                      "fewer words, a longer --since, a lower --min-points, or another --type")
    return {"kind": "search", "query": args.query, "sort": args.sort, "type": args.type or "story",
            "total": d.get("nbHits"), "results": results}


def firebase_items(net: Net, ids: list[int]) -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        items = list(ex.map(lambda i: net.get(f"{FIREBASE}/item/{i}.json"), ids))
    return [story_record(it, "firebase") for it in items if isinstance(it, dict) and not it.get("deleted")]


def cmd_list(net: Net, args: argparse.Namespace) -> dict[str, Any]:
    ids = net.get(f"{FIREBASE}/{LISTS[args.command]}.json")
    if not isinstance(ids, list):
        raise Failure(EXIT_OTHER, "Hacker News answered without a list", "try again later")
    results = firebase_items(net, [i for i in ids if isinstance(i, int)][:args.n])
    if not results:
        raise Failure(EXIT_EMPTY, f"the {args.command} list is empty", "try again later")
    for rank, r in enumerate(results, 1):
        r["rank"] = rank
    return {"kind": "list", "list": args.command, "results": results}


def parse_item_id(s: str) -> int:
    s = s.strip()
    if s.isdigit():
        return int(s)
    m = re.match(r"^(?:https?://)?news\.ycombinator\.com/item\?(?:.*&)?id=(\d+)", s, re.I)
    if m:
        return int(m[1])
    raise Failure(EXIT_BAD_INPUT, f"{s!r} is not a Hacker News item id or link",
                  "use 8863 or https://news.ycombinator.com/item?id=8863")


def from_firebase(it: dict[str, Any]) -> dict[str, Any]:
    """A Firebase item in Algolia's tree shape (children filled in by the caller)."""
    return {"id": it.get("id"), "type": it.get("type"), "title": it.get("title"), "url": it.get("url"),
            "author": it.get("by"), "points": it.get("score"), "created_at": when(it.get("time")),
            "text": it.get("text"), "parent_id": it.get("parent"), "children": [], "_kids": it.get("kids") or [],
            "_dead": bool(it.get("dead") or it.get("deleted"))}


def firebase_subtrees(net: Net, ids: list[int], budget: int, depth: int) -> tuple[list[dict[str, Any]], bool]:
    """Comment subtrees read from Firebase breadth-first, at most budget comments and depth levels (0 = all).
    Dead and deleted comments are left out, as Algolia leaves them out. Returns (roots, cut short)."""
    roots: list[dict[str, Any]] = []
    level: list[tuple[dict[str, Any] | None, int]] = [(None, i) for i in ids]
    fetched, lvl = 0, 0
    while level:
        if fetched >= budget or (depth and lvl >= depth):
            return roots, True
        level = level[:budget - fetched]
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            items = list(ex.map(lambda pk: net.get(f"{FIREBASE}/item/{pk[1]}.json"), level))
        nxt = []
        for (parent, _), it in zip(level, items):
            if not isinstance(it, dict) or it.get("dead") or it.get("deleted"):
                continue
            node = from_firebase(it)
            (parent["children"] if parent else roots).append(node)
            nxt += [(node, k) for k in node["_kids"]]
        fetched += len(level)
        level, lvl = nxt, lvl + 1
    return roots, False


def annotate(node: dict[str, Any]) -> int:
    """Store each node's count of replies below it (iteratively: threads can be hundreds of levels deep)."""
    order, stack = [], [node]
    while stack:
        n = stack.pop()
        order.append(n)
        stack.extend(c for c in n.get("children") or [] if isinstance(c, dict))
    for n in reversed(order):
        n["_below"] = sum(1 + c["_below"] for c in n.get("children") or [] if isinstance(c, dict))
    return node["_below"]


def prune(roots: list[dict[str, Any]], depth: int, budget: int) -> tuple[list[dict[str, Any]], int]:
    """Comments depth-first, each top comment with its replies before the next, down to depth (0 = all), at most
    budget (0 = all). Returns (comments, shown)."""
    out: list[dict[str, Any]] = []
    shown = 0
    stack = [(c, out, 0) for c in reversed(roots)]
    while stack:
        node, into, level = stack.pop()
        if budget and shown >= budget:
            break
        shown += 1
        rec = {"id": node.get("id"), "author": node.get("author"), "created": when(node.get("created_at")),
               "text": text_of(node.get("text")), "links": links_of(node.get("text")),
               "reply_count": node.get("_below", 0), "replies": []}
        if not node.get("author") and not node.get("text"):
            rec["text"], rec["deleted"] = "[deleted]", True
        into.append(rec)
        if not depth or level + 1 < depth:
            kids = [c for c in node.get("children") or [] if isinstance(c, dict) and c.get("type", "comment") == "comment"]
            stack.extend((c, rec["replies"], level + 1) for c in reversed(kids))
    return out, shown


def cmd_item(net: Net, args: argparse.Namespace) -> dict[str, Any]:
    iid = parse_item_id(args.target)
    fb = net.get(f"{FIREBASE}/item/{iid}.json")
    tree = net.get(f"{ALGOLIA}/items/{iid}")
    if not isinstance(tree, dict) and not isinstance(fb, dict):
        raise Failure(EXIT_EMPTY, f"Hacker News has no item {iid}", "check the id")
    budget = args.max_comments or 0
    note_lag = None
    if not isinstance(tree, dict):  # very new, or not indexed: Firebase only
        tree = from_firebase(fb)
        tree["children"], _ = firebase_subtrees(net, tree["_kids"], budget or 5000, args.depth)
    elif isinstance(fb, dict) and fb.get("kids"):
        # Algolia indexes a little behind the site; fetch top-level comments it does not have yet.
        have = {c.get("id") for c in tree.get("children") or [] if isinstance(c, dict)}
        missing = [k for k in fb["kids"] if k not in have]
        if missing:
            extra, _ = firebase_subtrees(net, missing, max(budget, 100) if budget else 500, args.depth)
            tree["children"] = (tree.get("children") or []) + extra
            note_lag = len(extra)  # dead and deleted ones are skipped, as Algolia skips them
    if isinstance(fb, dict) and isinstance(fb.get("kids"), list):
        # Algolia lists replies oldest first; Hacker News ranks the top level. Use its order.
        rank = {k: n for n, k in enumerate(fb["kids"])}
        tree["children"] = sorted(tree.get("children") or [], key=lambda c: rank.get(c.get("id"), len(rank)))
    kind = tree.get("type") or (fb or {}).get("type")
    head = {"id": tree.get("id"), "type": kind, "title": tree.get("title"), "url": tree.get("url"),
            "author": tree.get("author"), "points": tree.get("points"), "created": when(tree.get("created_at")),
            "text": text_of(tree.get("text")) or None, "links": links_of(tree.get("text")),
            "hn_url": item_url(tree.get("id")), "parent_id": tree.get("parent_id"), "story_id": tree.get("story_id")}
    if kind == "comment" and head["story_id"]:
        params = {"tags": f"story_{head['story_id']}", "hitsPerPage": 1}
        story = net.get(f"{ALGOLIA}/search?{urllib.parse.urlencode(params)}")
        hit = ((story or {}).get("hits") or [None])[0] if isinstance(story, dict) else None
        head["story_title"] = hit.get("title") if isinstance(hit, dict) else None
    if kind == "poll" and isinstance(fb, dict) and fb.get("parts"):
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            parts = list(ex.map(lambda i: net.get(f"{FIREBASE}/item/{i}.json"), fb["parts"]))
        head["poll"] = [{"text": text_of(p.get("text")), "points": p.get("score")} for p in parts if isinstance(p, dict)]
    roots = [c for c in tree.get("children") or [] if isinstance(c, dict) and c.get("type", "comment") == "comment"]
    counted = sum(1 + annotate(c) for c in roots)
    total = max(counted, (fb or {}).get("descendants") or 0) if kind != "comment" else counted
    comments, shown = prune(roots, args.depth, budget)
    head["comments"] = total
    out = {"kind": "item", "item": head, "comments": comments, "shown": shown, "total": total,
           "truncated": shown < total}
    if note_lag:
        out["note"] = f"{note_lag} top-level comments were not yet in HN Search and were read from Hacker News directly"
    if total > counted:
        out["note"] = (f"{total - counted} recent comments are not available yet "
                       "(HN Search indexes a little behind the site)")
    return out


def cmd_user(net: Net, args: argparse.Namespace) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", args.name):
        raise Failure(EXIT_BAD_INPUT, f"{args.name!r} is not a Hacker News username", "letters, digits, - and _")
    u = net.get(f"{FIREBASE}/user/{args.name}.json")
    if not isinstance(u, dict):
        raise Failure(EXIT_EMPTY, f"Hacker News has no user {args.name!r}", "usernames are case-sensitive")
    params = {"tags": f"author_{u.get('id', args.name)}", "hitsPerPage": args.n}
    d = net.get(f"{ALGOLIA}/search_by_date?{urllib.parse.urlencode(params)}")
    recent = [story_record(h) for h in ((d or {}).get("hits") or []) if isinstance(h, dict)] if isinstance(d, dict) else []
    return {"kind": "user", "user": {"id": u.get("id"), "karma": u.get("karma"), "created": when(u.get("created")),
                                      "about": text_of(u.get("about")) or None,
                                      "submissions": len(u.get("submitted") or []),
                                      "profile_url": f"{HN}/user?id={u.get('id')}"},
            "recent": recent}


# ------------------------------------------------------------------ output

def link_of(r: dict[str, Any]) -> str:
    return r.get("url") or r.get("hn_url") or ""


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def md_text(s: str) -> str:
    """Text safe to put in a Markdown file: no raw HTML, no link syntax an author could smuggle in."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("[", "\\[").replace("]", "\\]")


def story_lines(r: dict[str, Any], n: int | None, md: bool) -> list[str]:
    title = r.get("title") or (f"comment on: {r['story_title']}" if r.get("story_title") else "comment")
    meta = [plural(r["points"], "point") if isinstance(r.get("points"), int) else "", safe(r.get("author") or ""),
            ago(r.get("created")), plural(r["comments"], "comment") if isinstance(r.get("comments"), int) else ""]
    meta = " · ".join(m for m in meta if m)
    prefix = f"{n}. " if n else ""
    if md:
        url = safe(link_of(r)).replace(")", "%29").replace(" ", "%20")
        title_md = re.sub(r"([\\`*_\[\]<>|])", r"\\\1", safe(title))
        out = [f"{prefix}[{title_md}]({url}) — {md_text(meta)} · [discuss]({r['hn_url']})"]
    else:
        out = [f"{prefix}{safe(title)}", f"   {meta}", f"   {safe(link_of(r))}"]
        if r.get("url"):
            out.append(f"   {r['hn_url']}")
    if r.get("type") == "comment" and r.get("text"):
        excerpt = safe(r["text"])
        excerpt = excerpt[:280] + "…" if len(excerpt) > 280 else excerpt
        out.append(f"   > {md_text(excerpt)}" if md else f"   {excerpt}")
    return out


def comment_lines(c: dict[str, Any], level: int, md: bool) -> list[str]:
    """A comment and its replies, indented by level (an explicit stack: threads can be very deep)."""
    out: list[str] = []
    stack = [(c, level)]
    while stack:
        node, lvl = stack.pop()
        pad = "  " * lvl
        bits = [safe(node.get("author") or "[deleted]"), ago(node.get("created"))]
        if node.get("replies") == [] and node.get("reply_count"):
            n = node["reply_count"]
            bits.append(f"{n} more {'reply' if n == 1 else 'replies'} not shown")
        head = " · ".join(b for b in bits if b)
        text = safe_block(node.get("text") or "")
        if md:
            text = md_text(text)
        out.append(f"{pad}- **{md_text(head)}**" if md else f"{pad}- {head}")
        out += [pad + "  " + ln if ln else "" for ln in text.split("\n")]
        stack.extend((r, lvl + 1) for r in reversed(node["replies"]))
    return out


def all_links(comments: list[dict[str, Any]]) -> list[str]:
    links, stack = [], list(reversed(comments))
    while stack:
        c = stack.pop()
        links += c.get("links") or []
        stack.extend(reversed(c["replies"]))
    return links


def render(d: dict[str, Any], args: argparse.Namespace) -> str:
    if args.format == "json":
        return json.dumps(d, ensure_ascii=False, indent=2) + "\n"
    if args.format == "urls":
        if d["kind"] == "item":
            urls = [d["item"].get("url")] + d["item"].get("links", []) + all_links(d["comments"])
        elif d["kind"] == "user":
            urls = [link_of(r) for r in d["recent"] if r["type"] != "comment"]
        else:
            urls = [link_of(r) for r in d["results"]]
        return "".join(f"{u}\n" for u in dict.fromkeys(u for u in urls if u))
    md = args.format == "markdown"
    esc = md_text if md else (lambda x: x)
    lines: list[str] = []
    if d["kind"] in ("search", "list"):
        title = (f"# Hacker News search: {safe(d['query']) or '(any words)'} ({d['sort']}, {d['type']}) · "
                 f"{d['total']} matches"
                 if d["kind"] == "search" else f"# Hacker News: {d['list']}")
        lines += [esc(title), ""]
        for n, r in enumerate(d["results"], 1):
            lines += story_lines(r, n, md)
            if not md:
                lines.append("")
    elif d["kind"] == "item":
        it = d["item"]
        if it["type"] == "comment":
            lines.append("# " + esc(f"Comment by {safe(it.get('author'))}" + (f" on: {safe(it.get('story_title'))}"
                                                                              if it.get("story_title") else "")))
        else:
            lines.append("# " + esc(safe(it.get("title"))))
        meta = [plural(it["points"], "point") if isinstance(it.get("points"), int) else "", safe(it.get("author") or ""),
                (it.get("created") or "")[:10], plural(d["total"], "comment")]
        lines.append(esc(" · ".join(m for m in meta if m)))
        if it.get("url"):
            lines.append(safe(it["url"]))
        lines.append(it["hn_url"])
        if it.get("text"):
            lines += ["", esc(safe_block(it["text"]))]
        for opt in it.get("poll") or []:
            lines.append(esc(f"- {safe(opt['text'])}: {plural(opt['points'] or 0, 'point')}"))
        lines.append("")
        for c in d["comments"]:
            lines += comment_lines(c, 0, md)
        if d["truncated"]:
            lines += ["", f"[{d['shown']} of {d['total']} comments shown: raise --max-comments (0 = all) or --depth, "
                          "or save with -o FILE]"]
    else:
        u = d["user"]
        lines += ["# " + esc(safe(u["id"])), f"karma {u['karma']} · joined {(u['created'] or '')[:10]} · "
                                              f"{plural(u['submissions'], 'submission')} · {u['profile_url']}"]
        if u.get("about"):
            lines += ["", esc(safe_block(u["about"]))]
        if d["recent"]:
            lines += ["", "recent:"]
            for n, r in enumerate(d["recent"], 1):
                lines += story_lines(r, n, md)
    return "\n".join(lines).rstrip() + "\n"


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hn.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser, fmts: list[str]) -> None:
        sp.add_argument("-f", "--format", choices=fmts, default=fmts[0], help=f"output format (default {fmts[0]})")
        sp.add_argument("-o", "--output", metavar="FILE", help="write to FILE instead of stdout")
        sp.add_argument("--timeout", type=float, default=30.0, help="network timeout in seconds (default 30)")
        sp.add_argument("-q", "--quiet", action="store_true", help="no notes on stderr")

    s = sub.add_parser("search", help="search stories or comments (Algolia HN Search)")
    s.add_argument("query", nargs="*", help="words, a link or a domain (may be empty with --author or --since)")
    s.add_argument("--sort", choices=["relevance", "date"], default="relevance",
                   help="relevance (default; weighs points and comments) or date (newest first)")
    s.add_argument("--type", choices=list(TYPES), help="story (default), comment, ask, show, poll or job")
    s.add_argument("--since", help="only newer than: 24h, 7d, 4w, 6m, 1y or YYYY-MM-DD")
    s.add_argument("--until", help="only older than this date, YYYY-MM-DD (inclusive)")
    s.add_argument("--min-points", type=int, help="at least N points")
    s.add_argument("--min-comments", type=int, help="at least N comments")
    s.add_argument("--author", help="only posts by this user")
    s.add_argument("-n", type=int, default=20, help="results, 1-200 (default 20)")
    common(s, ["table", "markdown", "json", "urls"])
    for name, what in (("top", "the front page"), ("new", "newest stories"), ("best", "best recent stories"),
                       ("ask", "Ask HN"), ("show", "Show HN"), ("job", "job posts")):
        s = sub.add_parser(name, help=what + ", in Hacker News's order")
        s.add_argument("-n", type=int, default=30, help=f"stories, 1-{LIST_MAX[name]} (default 30)")
        common(s, ["table", "markdown", "json", "urls"])
    s = sub.add_parser("item", help="a story (or comment) with its comment tree")
    s.add_argument("target", help="item id or news.ycombinator.com/item?id=... link")
    s.add_argument("--max-comments", type=int, default=200, help="comments shown, 0 = all (default 200)")
    s.add_argument("--depth", type=int, default=0, help="reply levels shown, 0 = all (default)")
    common(s, ["table", "markdown", "json", "urls"])
    s = sub.add_parser("user", help="a user's karma, about and recent posts")
    s.add_argument("name")
    s.add_argument("-n", type=int, default=10, help="recent posts, 0-100 (default 10)")
    common(s, ["table", "markdown", "json", "urls"])
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = make_parser().parse_args(argv)
    note = (lambda m: None) if args.quiet else (lambda m: sys.stderr.write(f"note: {safe(m)}\n"))

    def fail(code: int, message: str, hint: str = "") -> int:
        sys.stderr.write(f"error: {safe(message)}" + (f" — {safe(hint)}" if hint else "") + "\n")
        return code

    if not 1 <= args.timeout <= 300:
        return fail(EXIT_BAD_INPUT, "--timeout must be between 1 and 300 seconds")
    if args.command == "search":
        args.query = " ".join(" ".join(args.query).split())
        if not args.query and not (args.author or args.since):
            return fail(EXIT_BAD_INPUT, "the query is empty", "give words, or filter with --author or --since")
        if args.author and not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", args.author):
            return fail(EXIT_BAD_INPUT, f"--author {args.author!r} is not a username")
        if (args.min_points or 0) < 0 or (args.min_comments or 0) < 0:
            return fail(EXIT_BAD_INPUT, "--min-points and --min-comments must be 0 or more")
        if args.type == "comment" and (args.min_points or args.min_comments):
            return fail(EXIT_BAD_INPUT, "comments have no points or comment counts in HN Search",
                        "drop --min-points/--min-comments with --type comment")
    if args.command == "search" and not 1 <= args.n <= 200:
        return fail(EXIT_BAD_INPUT, "-n must be between 1 and 200")
    if args.command in LISTS and not 1 <= args.n <= LIST_MAX[args.command]:
        return fail(EXIT_BAD_INPUT, f"-n must be between 1 and {LIST_MAX[args.command]} for {args.command}")
    if args.command == "user" and not 0 <= args.n <= 100:
        return fail(EXIT_BAD_INPUT, "-n must be between 0 and 100")
    if args.command == "item" and (args.max_comments < 0 or args.depth < 0):
        return fail(EXIT_BAD_INPUT, "--max-comments and --depth must be 0 or more")
    net = Net(args.timeout, note)
    run = {"search": cmd_search, "item": cmd_item, "user": cmd_user}.get(args.command, cmd_list)
    try:
        data = run(net, args)
    except Failure as e:
        return fail(e.code, str(e), e.hint)
    if data["kind"] == "item" and data.get("note"):
        note(data["note"])
    if data["kind"] == "item" and data["truncated"]:
        note(f"{data['shown']} of {data['total']} comments shown; --max-comments 0 for all, or -o FILE")
    text = render(data, args)
    if args.output:
        try:
            with open(os.path.expanduser(args.output), "w", encoding="utf-8") as fh:
                fh.write(text)
            return EXIT_OK
        except OSError as e:
            sys.stdout.write(text)
            return fail(EXIT_BAD_INPUT, f"cannot write {args.output}: {e.strerror or e}", "the output went to stdout")
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except BrokenPipeError:
        with contextlib.suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
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
    except Exception as e:  # noqa: BLE001 - exit 1 means "no results"; a crash must not look like that
        sys.stderr.write(f"error: unexpected {type(e).__name__}: {(str(e).splitlines() or [''])[0][:300]} "
                         "— a bug; report it with the command line\n")
        sys.exit(EXIT_OTHER)
