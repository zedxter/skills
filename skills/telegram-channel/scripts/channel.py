#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["beautifulsoup4>=4.12,<5"]
# ///
"""Read a public Telegram channel without an account: Telegram's own web preview at https://t.me/s/<channel>
(20 posts per page, paged back with ?before=<id>). Text, date, views, media placeholders, links, post URL.

    uv run channel.py @telegram                          # the latest 20 posts, newest first
    uv run channel.py durov -n 60 --after 2026-01-01     # pages back until 60 posts or the date
    uv run channel.py @telegram --search "bots"          # Telegram's own search inside the channel
    uv run channel.py https://t.me/telegram/441          # one post (or: @telegram --post 441)
    uv run channel.py @telegram --links -n 40            # URLs mentioned in posts, one per line
    uv run channel.py @telegram -f markdown -o feed.md   # markdown file; -f json for every field

Exit codes:
    0 ok · 1 no posts matched · 2 bad input / missing dependency · 3 channel not public or not found ·
    4 blocked or rate-limited · 6 network error · 7 the preview markup changed and the parser failed ·
    130 interrupted
"""

import argparse
import contextlib
import http.client
import json
import math
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

EXIT_OK, EXIT_EMPTY, EXIT_BAD_INPUT, EXIT_PRIVATE, EXIT_BLOCKED, EXIT_NETWORK, EXIT_MARKUP = 0, 1, 2, 3, 4, 6, 7
EXIT_BUG = 7  # an unexpected error; shares 7 with the documented code, never 1

try:
    from bs4 import BeautifulSoup, NavigableString, Tag
except ImportError:
    sys.stderr.write("error: missing dependency beautifulsoup4; run with `uv run channel.py …` or `pip install beautifulsoup4`\n")
    sys.exit(EXIT_BAD_INPUT)

USER_AGENT = "utility-belt-telegram-channel/1.0 (+https://github.com/zedxter/utility-belt)"
BASE = "https://t.me"
PAGE_DELAY = 1.5  # seconds between page requests; the preview has no published limit, so stay polite
MAX_PAGE_BYTES = 5_000_000
MAX_PAGE_POSTS = 100  # a real page holds 20
DIGITS = re.compile(r"[0-9]{1,15}")  # str.isdigit() accepts '²', which int() rejects
INVISIBLE = "\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff"  # zero-width and bidi controls that reorder text
CONTROL = re.compile(rf"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f{INVISIBLE}]")
TG_HOSTS = {"t.me", "www.t.me", "telegram.me", "www.telegram.me", "telegram.dog", "www.telegram.dog"}
USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,31}")

# Every class name the parser depends on. When Telegram changes the preview, this is the place to fix.
SEL = {
    "history": "tgme_channel_history",
    "message": "tgme_widget_message",          # the element carrying data-post="<channel>/<id>"
    "no_posts": "tme_no_messages_found",
    "title": "tgme_channel_info_header_title",
    "username": "tgme_channel_info_header_username",
    "description": "tgme_channel_info_description",
    "counter": "tgme_channel_info_counter",
    "counter_value": "counter_value",
    "counter_type": "counter_type",
    "verified": "verified-icon",
    "more": "tme_messages_more",               # carries data-before / data-after
    "service": "service_message",
    "bubble": "tgme_widget_message_bubble",
    "text": "tgme_widget_message_text",
    "date": "tgme_widget_message_date",
    "views": "tgme_widget_message_views",
    "meta": "tgme_widget_message_meta",
    "signature": "tgme_widget_message_from_author",
    "forwarded": "tgme_widget_message_forwarded_from",
    "forwarded_name": "tgme_widget_message_forwarded_from_name",
    "reply": "tgme_widget_message_reply",
    "author_name": "tgme_widget_message_author_name",
    "via_bot": "tgme_widget_message_via_bot",
    "preview": "tgme_widget_message_link_preview",
    "preview_site": "link_preview_site_name",
    "preview_title": "link_preview_title",
    "preview_description": "link_preview_description",
    "photo": "tgme_widget_message_photo_wrap",
    "video": "tgme_widget_message_video_player",
    "video_duration": "message_video_duration",
    "roundvideo": "tgme_widget_message_roundvideo_player",
    "voice": "tgme_widget_message_voice",
    "voice_duration": "tgme_widget_message_voice_duration",
    "document": "tgme_widget_message_document_wrap",
    "document_title": "tgme_widget_message_document_title",
    "document_extra": "tgme_widget_message_document_extra",
    "audio_icon": "audio",
    "poll": "tgme_widget_message_poll",
    "poll_question": "tgme_widget_message_poll_question",
    "poll_type": "tgme_widget_message_poll_type",
    "poll_option": "tgme_widget_message_poll_option",
    "poll_option_text": "tgme_widget_message_poll_option_text",
    "poll_option_percent": "tgme_widget_message_poll_option_percent",
    "sticker": ("tgme_widget_message_sticker", "tgme_widget_message_tgsticker", "tgme_widget_message_videosticker"),
    "location": "tgme_widget_message_location_wrap",
    "contact": "tgme_widget_message_contact",
    "contact_name": "tgme_widget_message_contact_name",
    "unsupported": "message_media_not_supported",
    "emoji": ("emoji", "tg-emoji"),
}


class Failure(Exception):
    def __init__(self, code: int, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.hint = code, hint


# ------------------------------------------------------------------ helpers

def clean(value: Any) -> str:
    """One safe line: control characters dropped, whitespace collapsed.

    >>> clean("Hi\\n# fake\\x1b[31m  there"), clean(None)
    ('Hi # fake[31m there', '')
    """
    return " ".join(CONTROL.sub("", str(value or "")).split())


def clean_block(value: str) -> str:
    """Multi-line text: control characters dropped, line ends trimmed, at most one blank line in a row. Leading
    spaces stay (code indentation); render_text has already collapsed the spaces in running text.

    >>> clean_block("a b\\n\\n\\n\\n    c d \\x07\\n")
    'a b\\n\\n    c d'
    """
    lines = [line.rstrip() for line in CONTROL.sub("", value.replace("\r", "")).split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def parse_views(value: str) -> int | None:
    """Telegram shows views rounded: '845', '83.5K', '1.62M'.

    >>> parse_views("845"), parse_views("83.5K"), parse_views("1.62M"), parse_views(""), parse_views("1,2K")
    (845, 83500, 1620000, None, 1200)
    """
    m = re.fullmatch(r"([0-9.,]{1,15})\s*([KMB]?)", value.strip(), re.I)
    if not m:
        return None
    try:
        number = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    number *= {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2).upper()]
    return round(number) if math.isfinite(number) else None


def parse_duration(value: str) -> int | None:
    """'0:06' → 6, '1:02:03' → 3723.

    >>> parse_duration("0:06"), parse_duration("1:02:03"), parse_duration("x")
    (6, 3723, None)
    """
    parts = value.strip().split(":")
    if not 2 <= len(parts) <= 3 or not all(DIGITS.fullmatch(p) for p in parts):
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return seconds


def fmt_duration(sec: int | None) -> str:
    if sec is None:
        return ""
    h, rest = divmod(sec, 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60}:{rest % 60:02d}"


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"bad date {value!r}; use YYYY-MM-DD") from None


def parse_target(value: str) -> tuple[str, int | None]:
    """'@name', 'name', 't.me/name', 'https://t.me/s/name', 'https://t.me/name/123' → (name, post id or None).

    >>> parse_target("@telegram"), parse_target("https://t.me/s/durov"), parse_target("t.me/telegram/441?single")
    (('telegram', None), ('durov', None), ('telegram', 441))
    >>> parse_target("https://t.me/+AbCd")
    Traceback (most recent call last):
    ...
    channel.Failure: invite links point to private chats; only public channels can be read
    """
    s = value.strip()
    if not s:
        raise Failure(EXIT_BAD_INPUT, "empty channel", "pass @name, a name or a t.me link")
    if s.startswith("@"):
        s = s[1:]
    elif "/" in s or "." in s:
        try:
            url = urllib.parse.urlsplit(s if "://" in s else "https://" + s)
            host = (url.hostname or "").lower()
        except ValueError:
            raise Failure(EXIT_BAD_INPUT, f"not a valid link: {value!r}") from None
        if host not in TG_HOSTS:
            raise Failure(EXIT_BAD_INPUT, f"not a Telegram link: {value!r}", "pass @name or a https://t.me/<name> link")
        parts = [p for p in url.path.split("/") if p]
        if parts and parts[0] == "s":
            parts = parts[1:]
        if not parts:
            raise Failure(EXIT_BAD_INPUT, f"no channel name in {value!r}")
        if parts[0].startswith("+") or parts[0] in ("joinchat", "c"):
            raise Failure(EXIT_PRIVATE, "invite links point to private chats; only public channels can be read")
        if len(parts) > 1:
            if not DIGITS.fullmatch(parts[1]):
                raise Failure(EXIT_BAD_INPUT, f"{parts[1]!r} in {value!r} is not a post number")
            return check_name(parts[0]), int(parts[1])
        s = parts[0]
    return check_name(s), None


def check_name(name: str) -> str:
    if not USERNAME.fullmatch(name):
        raise Failure(EXIT_BAD_INPUT, f"bad channel name {name!r}",
                      "a public channel name is 3–32 letters, digits or underscores and starts with a letter")
    return name


# ------------------------------------------------------------------ network

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # a redirect from /s/<name> means "no public preview"
        return None


OPENER = urllib.request.build_opener(NoRedirect)
_last_request = 0.0


def http_get(url: str, timeout: float, note) -> str:
    """Page HTML. Spaces requests PAGE_DELAY apart; one retry after a dropped connection, which some networks
    produce on t.me intermittently."""
    global _last_request
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html",
                                               "Accept-Language": "en-US,en;q=0.8"})
    for attempt in (1, 2):
        wait = _last_request + PAGE_DELAY - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            with OPENER.open(req, timeout=timeout) as resp:
                expected = resp.headers.get("Content-Length", "")
                charset = resp.headers.get_content_charset() or "utf-8"
                deadline = time.monotonic() + 2 * timeout  # timeout is per socket read; bound a slow drip too
                chunks: list[bytes] = []
                size = 0
                while size <= MAX_PAGE_BYTES and (chunk := resp.read(65536)):
                    chunks.append(chunk)
                    size += len(chunk)
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"page still downloading after {2 * timeout:.0f} s")
                data = b"".join(chunks)
            if len(data) > MAX_PAGE_BYTES:
                raise Failure(EXIT_MARKUP, f"{url}: page larger than {MAX_PAGE_BYTES // 1_000_000} MB",
                              "not a normal channel preview; report it rather than retrying")
            if DIGITS.fullmatch(expected) and len(data) < int(expected):
                # urllib returns a short body without an error when the connection drops mid-page
                raise ConnectionError(f"connection closed after {len(data)} of {expected} bytes")
            try:
                return data.decode(charset, errors="replace")
            except LookupError:  # unknown or non-text codec in Content-Type; t.me serves UTF-8
                return data.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                raise Failure(EXIT_PRIVATE, "no public preview for this channel",
                              "it does not exist, is private, is a group, user or bot, or its owner turned the preview off; "
                              "check the name") from None
            if e.code == 404:
                raise Failure(EXIT_PRIVATE, f"{url}: HTTP 404", "check the channel name") from None
            if e.code in (403, 429):
                raise Failure(EXIT_BLOCKED, f"{url}: HTTP {e.code}",
                              "rate-limited or blocked; wait several minutes, do not retry in a loop") from None
            if e.code < 500:
                raise Failure(EXIT_NETWORK, f"{url}: HTTP {e.code}", "unexpected answer from t.me; check the link") from None
            if attempt == 2:
                raise Failure(EXIT_NETWORK, f"{url}: HTTP {e.code}", "Telegram server error; one retry later is fine") from None
        except (OSError, http.client.HTTPException) as e:  # URLError, timeouts, resets, TLS, bad status lines
            reason = getattr(e, "reason", e)
            if "CERTIFICATE_VERIFY_FAILED" in str(reason):
                raise Failure(EXIT_NETWORK, f"{url}: TLS certificate verification failed ({reason})",
                              "retrying will not help; on a python.org macOS build run 'Install Certificates.command', "
                              "or use uv's managed Python; a proxy may be intercepting TLS") from None
            if attempt == 2:
                raise Failure(EXIT_NETWORK, f"{url}: {reason}",
                              "check the connection (HTTPS_PROXY is honoured); t.me is blocked or throttled on some "
                              "networks") from None
        note("connection problem, retrying once")
        time.sleep(2)
    raise AssertionError("unreachable")


# ------------------------------------------------------------------ parsing

def has_class(tag: Tag, names: str | tuple[str, ...]) -> bool:
    classes = tag.get("class") or []
    return any(n in classes for n in ((names,) if isinstance(names, str) else names))


def first(root: Tag, name: str) -> Tag | None:
    return root.find(class_=SEL[name])


def text_of(tag: Tag | None) -> str:
    return clean(tag.get_text(" ")) if tag else ""


def background_url(tag: Tag | None) -> str | None:
    m = re.search(r"""background-image:\s*url\(\s*['"]?([^'"()\s]+)""", str(tag.get("style", ""))[:4000] if tag else "")
    if not m or m.group(1).startswith("data:"):
        return None
    return absolute(m.group(1))


def absolute(href: str | None) -> str | None:
    """A printable absolute URL, or None. URLs reach every output format, so control, space and bidi characters
    reject the link and Markdown-breaking characters are percent-encoded.

    >>> absolute("//cdn.example/a (1).jpg"), absolute("https://x.example/a\\n 2. [9]"), absolute("https://x.example/(a)<b>")
    (None, None, 'https://x.example/%28a%29%3Cb%3E')
    """
    if not href:
        return None
    href = href.strip()
    if re.search(rf"[\x00-\x20\x7f-\x9f{INVISIBLE}]", href):
        return None
    href = href.replace("(", "%28").replace(")", "%29").replace("<", "%3C").replace(">", "%3E")
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("?"):
        return None  # hashtag and cashtag links: an in-channel search, not a URL anyone posted
    if href.startswith("/"):
        return urllib.parse.urljoin(BASE, href)
    return href


def render_text(node: Tag, markdown: bool) -> str:
    """A post's HTML as plain text or Markdown. Line breaks are kept, running whitespace is collapsed, code keeps
    its indentation; in Markdown links become [text](url).

    >>> from bs4 import BeautifulSoup as B
    >>> clean_block(render_text(B("<b>Bold </b>and <i>it</i><br/> next<b> x</b> <pre>def f():<br/>    return 1</pre>", "html.parser"), True))
    '**Bold** and *it*\\nnext **x**\\n```\\ndef f():\\n    return 1\\n```'
    """
    state = {"last": "\n"}  # the last character produced anywhere, so nested elements know where a line starts

    def note_out(s: str) -> str:
        if s:
            state["last"] = s[-1]
        return s

    def block(s: str, after_newline: bool) -> str:
        """Put a block on lines of its own without doubling the newline before it."""
        return note_out(("" if after_newline else "\n") + s.strip("\n") + "\n")

    def wrap(marker: str, inner: str) -> str:
        """Emphasis keeps outer spaces outside the markers: '**Bold** next', not '**Bold **next'."""
        core = inner.strip()
        if not core:
            return inner
        lead, trail = inner[: len(inner) - len(inner.lstrip())], inner[len(inner.rstrip()):]
        return f"{lead}{marker}{core}{marker}{trail}"

    def walk(n: Any, depth: int, pre: bool, code: bool = False) -> str:
        if isinstance(n, NavigableString):
            s = str(n)
            line_start = state["last"] == "\n"
            if not pre:
                s = re.sub(r"\s+", " ", s)
                if state["last"] in ("\n", " "):
                    s = s.lstrip(" ")
            if markdown and not code:
                s = md_escape(s, line_start)
            return note_out(s)
        if not isinstance(n, Tag):
            return ""
        name = n.name
        if name == "br":
            return note_out("\n")
        if has_class(n, SEL["emoji"]) or name == "tg-emoji":
            return note_out(n.get_text())
        if depth > MAX_DEPTH:  # absurdly deep markup: flatten instead of recursing further
            return note_out(clean(n.get_text(" ")))
        at_start = state["last"] == "\n"
        code_block = not pre and (name == "pre" or (name == "code" and n.find(["pre", "br"]) is not None))
        if code_block:
            state["last"] = "\n"
        in_code = code or code_block or name == "code"
        inner = "".join(walk(c, depth + 1, pre or code_block, in_code) for c in n.children)
        if code_block:
            body = inner.strip("\n")
            fence = "`" * max(3, max((len(r) for r in re.findall(r"`+", body)), default=0) + 1)
            return block(f"{fence}\n{body}\n{fence}" if markdown else body, at_start)
        if name in ("blockquote", "div", "p"):
            if markdown and name == "blockquote":
                return block("\n".join("> " + line for line in inner.strip("\n").split("\n")), at_start)
            return block(inner, at_start)
        if not markdown or pre:
            return inner
        if name in ("b", "strong"):
            return wrap("**", inner)
        if name in ("i", "em"):
            return wrap("*", inner)
        if name in ("s", "del"):
            return wrap("~~", inner)
        if name == "code" and inner:
            ticks = "`" * (max((len(r) for r in re.findall(r"`+", inner)), default=0) + 1)
            pad = " " if inner.startswith("`") or inner.endswith("`") else ""
            return f"{ticks}{pad}{inner}{pad}{ticks}"
        if name == "a":
            href = absolute(n.get("href"))
            if href and href.startswith(("http://", "https://")) and inner.strip() and n.get_text().strip() != n.get("href", "").strip():
                return f"[{inner}]({href})"
        return inner

    return "".join(walk(c, 0, False) for c in node.children)


MAX_DEPTH = 60  # real posts nest a few levels


def md_escape(s: str, line_start: bool) -> str:
    """Post text as literal Markdown: a post must not be able to fake a heading, a list, a link, a code fence or
    raw HTML in -f markdown output.

    >>> md_escape("## [1](https://evil) <img> a_b *c* `d`", True)
    '\\\\#\\\\# \\\\[1\\\\](https://evil) \\\\<img> a\\\\_b \\\\*c\\\\* \\\\`d\\\\`'
    >>> md_escape("1. one", True), md_escape("1. one", False)
    ('1\\\\. one', '1. one')
    """
    s = re.sub(r"([\\`*_\[\]<#~|])", r"\\\1", s)
    if line_start:
        s = re.sub(r"^(\s*)([>+-])", r"\1\\\2", s)
        s = re.sub(r"^(\s*[0-9]+)([.)])", r"\1\\\2", s)
    return s


def post_links(msg: Tag, text_node: Tag | None) -> list[str]:
    links: list[str] = []
    anchors = list(text_node.find_all("a")) if text_node else []
    preview = first(msg, "preview")
    for a in anchors + ([preview] if preview else []):
        href = absolute(a.get("href"))
        if href and href.startswith(("http://", "https://")) and href not in links:
            links.append(href)
    return links


def parse_media(msg: Tag) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    for ph in msg.find_all(class_=SEL["photo"]):
        media.append({"type": "photo", "url": absolute(ph.get("href")), "image": background_url(ph)})
    for v in msg.find_all(class_=(SEL["video"], SEL["roundvideo"])):
        d = v.find(class_=SEL["video_duration"])
        media.append({"type": "round_video" if has_class(v, SEL["roundvideo"]) else "video",
                      "url": absolute(v.get("href")), "duration": parse_duration(text_of(d)) if d else None})
    for _ in msg.find_all("audio", class_=SEL["voice"]):
        d = msg.find(class_=SEL["voice_duration"])
        media.append({"type": "voice", "duration": parse_duration(text_of(d)) if d else None})
    for doc in msg.find_all(class_=SEL["document"]):
        kind = "audio" if doc.find(class_=SEL["audio_icon"]) else "document"
        media.append({"type": kind, "url": absolute(doc.get("href")), "title": text_of(doc.find(class_=SEL["document_title"])),
                      "extra": text_of(doc.find(class_=SEL["document_extra"]))})
    poll = first(msg, "poll")
    if poll:
        media.append({"type": "poll", "question": text_of(poll.find(class_=SEL["poll_question"])),
                      "kind": text_of(poll.find(class_=SEL["poll_type"])),
                      "options": [{"text": text_of(o.find(class_=SEL["poll_option_text"])),
                                   "percent": text_of(o.find(class_=SEL["poll_option_percent"]))}
                                  for o in poll.find_all(class_=SEL["poll_option"])]})
    if msg.find(class_=SEL["sticker"]):
        media.append({"type": "sticker"})
    loc = first(msg, "location")
    if loc:
        media.append({"type": "location", "url": absolute(loc.get("href"))})
    contact = first(msg, "contact")
    if contact:  # the name only: a phone number is personal data and not needed to read a channel
        media.append({"type": "contact", "name": text_of(contact.find(class_=SEL["contact_name"]))})
    return media


def parse_post(msg: Tag, channel: str) -> dict[str, Any]:
    pid = msg.get("data-post", "")
    m = re.fullmatch(r"([^/]+)/([0-9]{1,15})", str(pid))
    if not m:
        raise Failure(EXIT_MARKUP, f"unexpected data-post value {pid!r}", "the preview markup changed; report it")
    bubble = msg.find(class_=SEL["bubble"]) or msg
    text_node = bubble.find(class_=SEL["text"], recursive=False) or next(
        (t for t in bubble.find_all(class_=SEL["text"]) if not t.find_parent(class_=(SEL["reply"], SEL["preview"]))), None)
    date_a = msg.find("a", class_=SEL["date"])
    when = date_a.find("time") if date_a else None
    published = None
    if when and when.get("datetime"):
        try:
            published = datetime.fromisoformat(str(when["datetime"])).astimezone(UTC)
        except ValueError:
            published = None
    views = msg.find(class_=SEL["views"])
    meta = msg.find(class_=SEL["meta"])
    post: dict[str, Any] = {
        "id": int(m.group(2)),
        "url": (absolute(date_a.get("href")) if date_a else None) or f"{BASE}/{channel}/{m.group(2)}",
        "date": published.isoformat().replace("+00:00", "Z") if published else None,
        "views": parse_views(text_of(views)) if views else None,
        "views_text": text_of(views) or None,
        "author": text_of(msg.find(class_=SEL["signature"])) or None,
        "edited": bool(meta and re.search(r"\bedited\b", meta.get_text(" "))),
        "service": has_class(msg, SEL["service"]),
        "text": clean_block(render_text(text_node, False)) if text_node else "",
        "markdown": clean_block(render_text(text_node, True)) if text_node else "",
        "media": parse_media(msg),
        "links": post_links(msg, text_node),
        "forwarded_from": None,
        "reply_to": None,
        "link_preview": None,
    }
    if not post["text"] and not post["media"] and first(msg, "unsupported"):
        # Every post carries a hidden "open Telegram to view" block; it only means something when nothing else shows
        post["media"].append({"type": "unsupported"})
    fwd = msg.find(class_=SEL["forwarded"])
    if fwd:
        name = fwd.find(class_=SEL["forwarded_name"])
        post["forwarded_from"] = {"name": text_of(name) or text_of(fwd), "url": absolute(name.get("href")) if name else None}
    reply = msg.find(class_=SEL["reply"])
    if reply:
        href = absolute(reply.get("href"))
        rid = re.search(r"/(\d+)(?:\?|$)", href or "")
        post["reply_to"] = {"id": int(rid.group(1)) if rid else None, "url": href,
                            "author": text_of(reply.find(class_=SEL["author_name"])) or None,
                            "text": text_of(reply.find(class_=SEL["text"]))[:300]}
    preview = first(msg, "preview")
    if preview:
        post["link_preview"] = {"url": absolute(preview.get("href")), "site": text_of(preview.find(class_=SEL["preview_site"])),
                                "title": text_of(preview.find(class_=SEL["preview_title"])),
                                "description": text_of(preview.find(class_=SEL["preview_description"]))[:500]}
    post["_published"] = published
    return post


def parse_page(html: str, channel: str) -> dict[str, Any]:
    """{channel: {...}, posts: [... oldest first], before: id|None (older posts exist), empty: bool}."""
    soup = BeautifulSoup(html, "html.parser")
    history = soup.find(class_=SEL["history"])
    title = soup.find(class_=SEL["title"])
    if history is None or title is None:
        raise Failure(EXIT_MARKUP, "the channel page has no post list or channel header",
                      "Telegram changed the preview markup; report it, retrying will not help")
    counters = {}
    for c in soup.find_all(class_=SEL["counter"]):
        value, kind = c.find(class_=SEL["counter_value"]), c.find(class_=SEL["counter_type"])
        if value and kind:
            counters[text_of(kind)] = text_of(value)
    username = text_of(soup.find(class_=SEL["username"])).lstrip("@") or channel
    desc = soup.find(class_=SEL["description"])
    info = {"username": username, "title": text_of(title), "url": f"{BASE}/s/{username}",
            "description": clean_block(render_text(desc, False)) if desc else "",
            "verified": bool(soup.find(class_=SEL["verified"])), "counters": counters}
    messages = [m for m in soup.find_all(class_=SEL["message"])
                if m.get("data-post") and m.find_parent(class_=SEL["message"]) is None][:MAX_PAGE_POSTS]
    posts = [parse_post(m, username) for m in messages]
    if messages and all(p["_published"] is None for p in posts):
        raise Failure(EXIT_MARKUP, "posts were found but none has a date",
                      "Telegram changed the preview markup; report it, retrying will not help")
    more = soup.find(class_=SEL["more"], attrs={"data-before": True})
    before = int(more["data-before"]) if more and DIGITS.fullmatch(str(more["data-before"])) else None
    return {"channel": info, "posts": sorted(posts, key=lambda p: p["id"]), "before": before,
            "empty": not messages and soup.find(class_=SEL["no_posts"]) is not None}


# ------------------------------------------------------------------ commands

def page_url(channel: str, post: int | None = None, **params: Any) -> str:
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return f"{BASE}/s/{channel}" + (f"/{post}" if post else "") + (f"?{q}" if q else "")


def matches(post: dict[str, Any], args: argparse.Namespace, grep: re.Pattern[str] | None) -> bool:
    if post["service"]:
        return False
    if grep and not grep.search("\n".join([post["text"], *(m.get("title", "") for m in post["media"]),
                                           (post["link_preview"] or {}).get("title", "")])):
        return False
    day = post["_published"].date() if post["_published"] else None
    if (args.after or args.before) and day is None:
        return False
    return not (args.after and day < args.after) and not (args.before and day > args.before)


def cmd_list(channel: str, args: argparse.Namespace, note) -> dict[str, Any]:
    grep = re.compile(args.grep, re.I) if args.grep else None
    before = args.before_id
    posts: list[dict[str, Any]] = []
    info: dict[str, Any] = {}
    next_before: int | None = None
    hit_limit = False
    for page_no in range(1, args.max_pages + 1):
        page = parse_page(http_get(page_url(channel, q=args.search, before=before), args.timeout, note), channel)
        info = info or page["channel"]
        done = False
        for post in reversed(page["posts"]):  # newest first
            if before is not None and post["id"] >= before:
                continue
            if args.after and post["_published"] and post["_published"].date() < args.after:
                done = True  # everything further back is older still; next_before_id stays above this post
                break
            next_before = post["id"]
            if matches(post, args, grep):
                posts.append(post)
                if len(posts) >= args.limit:
                    done = hit_limit = True
                    break
        at_start = page["before"] is None or not page["posts"]
        if done:
            if hit_limit and at_start and page["posts"] and next_before == page["posts"][0]["id"]:
                next_before = None  # the limit fell exactly on the channel's first post
            break
        if at_start:
            next_before = None  # reached the channel's first post
            break
        older = min(page["before"], page["posts"][0]["id"])
        if before is not None and older >= before:
            next_before = None  # the page did not move back: treat it as the start rather than loop
            break
        before = older
        if page_no == args.max_pages:
            note(f"stopped after {args.max_pages} pages ({args.max_pages * 20} posts scanned); "
                 f"continue with --before-id {before} or raise --max-pages")
    else:
        next_before = before
    if hit_limit and args.after and next_before is not None:
        note(f"stopped at -n {args.limit} before reaching --after {args.after.isoformat()}; "
             f"raise -n or continue with --before-id {next_before}")
    return {"channel": info, "query": {"search": args.search, "grep": args.grep,
                                       "after": args.after.isoformat() if args.after else None,
                                       "before": args.before.isoformat() if args.before else None},
            "count": len(posts), "next_before_id": next_before, "posts": posts}


def cmd_post(channel: str, post_id: int, args: argparse.Namespace, note) -> dict[str, Any]:
    page = parse_page(http_get(page_url(channel, post_id), args.timeout, note), channel)
    found = [p for p in page["posts"] if p["id"] == post_id]
    if not found:  # an album is one post; its other photos and videos have numbers of their own
        found = [p for p in page["posts"] if any(re.search(rf"/{post_id}(?:\?|$)", m.get("url") or "") for m in p["media"])][:1]
    return {"channel": page["channel"], "query": {"post": post_id}, "count": len(found), "next_before_id": None,
            "posts": found}


# ------------------------------------------------------------------ output

def media_label(m: dict[str, Any]) -> str:
    kind = m["type"].replace("_", " ")
    if m.get("duration") is not None:
        return f"{kind} {fmt_duration(m['duration'])}"
    if m["type"] in ("document", "audio"):
        return f"{kind}: {m['title']}" + (f" ({m['extra']})" if m.get("extra") else "")
    if m["type"] == "poll":
        opts = " / ".join(f"{o['text']} {o['percent']}".strip() for o in m["options"])
        return f"poll: {m['question']}" + (f" — {opts}" if opts else "")
    if m["type"] == "contact":
        return f"contact: {m['name']}"
    if m["type"] == "unsupported":
        return "media only visible in the Telegram app"
    return kind


def media_summary(media: list[dict[str, Any]]) -> list[str]:
    """Albums collapse: three photos → 'photo ×3'."""
    labels: list[str] = []
    for m in media:
        label = media_label(m)
        if labels and labels[-1].split(" ×")[0] == label and m["type"] in ("photo", "sticker"):
            n = int(labels[-1].split(" ×")[1]) + 1 if " ×" in labels[-1] else 2
            labels[-1] = f"{label} ×{n}"
        else:
            labels.append(label)
    return labels


def header_line(p: dict[str, Any]) -> str:
    when = p["_published"].strftime("%Y-%m-%d %H:%M UTC") if p["_published"] else "undated"
    bits = [when]
    if p["views_text"]:
        bits.append(f"{p['views_text']} views")
    if p["author"]:
        bits.append(f"by {p['author']}")
    if p["edited"]:
        bits.append("edited")
    return " · ".join(bits)


def outside_links(links: list[str], username: str) -> list[str]:
    """Links that leave the channel: a channel's own post series ("1 • 2 • More") would bury the articles.

    >>> outside_links(["https://t.me/chan/12", "https://t.me/s/Chan/3", "https://t.me/other/5", "https://x.org/a"], "chan")
    ['https://t.me/other/5', 'https://x.org/a']
    """
    own = re.compile(rf"https?://(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/(?:s/)?{re.escape(username)}(?:[/?#]|$)", re.I)
    return [u for u in links if not own.match(u)]


def render(payload: dict[str, Any], fmt: str, links_only: bool) -> str:
    ch, posts = payload["channel"], payload["posts"]
    if links_only:
        rows = [(url, p) for p in posts for url in outside_links(p["links"], ch["username"])]
        if fmt == "json":
            return json.dumps([{"url": u, "post": p["url"], "date": p["date"]} for u, p in rows], ensure_ascii=False, indent=2) + "\n"
        if fmt == "markdown":
            return "".join(f"- <{u}> — [post {p['id']}]({p['url']})\n" for u, p in rows)
        seen: list[str] = []
        for u, _ in rows:
            if u not in seen:
                seen.append(u)
        return "".join(u + "\n" for u in seen)
    if fmt == "json":
        out = {**payload, "posts": [{k: v for k, v in p.items() if not k.startswith("_")} for p in posts]}
        return json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    subs = ch.get("counters", {}).get("subscribers")
    if fmt == "markdown":
        lines = [f"# {md_escape(ch['title'], False)} (@{ch['username']})", ""]
        if ch["description"]:
            lines += ["> " + md_escape(line, True) for line in ch["description"].split("\n")] + [""]
        lines += [f"<{ch['url']}>" + (f" · {subs} subscribers" if subs else ""), ""]
        for p in posts:
            lines += [f"## [{p['id']}]({p['url']}) · {md_escape(header_line(p), False)}", ""]
            extras = media_summary(p["media"])
            if p["forwarded_from"]:
                extras.insert(0, f"forwarded from {p['forwarded_from']['name']}")
            if p["reply_to"]:
                extras.insert(0, f"reply to {p['reply_to']['url']}")
            if extras:
                lines += ["*\\[" + md_escape("; ".join(extras), False) + "\\]*", ""]
            if p["markdown"]:
                lines += [p["markdown"], ""]
            lp = p["link_preview"]
            if lp and lp["url"] and lp["url"] not in p["markdown"]:
                label = md_escape(lp["title"], False) or lp["url"]
                lines += [f"> {md_escape(lp['site'], False) or 'link'}: [{label}]({lp['url']})", ""]
        return "\n".join(lines).rstrip() + "\n"
    lines = [f"# {ch['title']} (@{ch['username']})" + (" ✔" if ch["verified"] else "") + (f" — {subs} subscribers" if subs else ""),
             f"# {ch['url']}"]
    if payload.get("next_before_id"):
        lines.append(f"# older: --before-id {payload['next_before_id']}")
    for i, p in enumerate(posts, 1):
        lines += ["", f"{i:>2}. [{p['id']}] {header_line(p)}", f"    {p['url']}"]
        if p["forwarded_from"]:
            lines.append(f"    forwarded from {p['forwarded_from']['name']}")
        if p["reply_to"]:
            lines.append(f"    reply to {p['reply_to']['url']}")
        for label in media_summary(p["media"]):
            lines.append(f"    [{label}]")
        for line in p["text"].split("\n") if p["text"] else []:
            lines.append(f"    {line}".rstrip())
        if p["links"]:
            lines.append("    links: " + " ".join(p["links"]))
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ CLI

def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("channel", help="@name, name, or a t.me link (a post link reads that one post)")
    p.add_argument("-n", "--limit", type=int, help="posts to return (default 20 = one page; with --after, everything "
                   "back to that date up to 1000; max 1000)")
    p.add_argument("--post", type=int, metavar="ID", help="read one post by its number")
    p.add_argument("--search", metavar="WORDS", help="Telegram's own search inside the channel (whole words)")
    p.add_argument("--grep", metavar="REGEX", help="keep posts whose text matches (case-insensitive)")
    p.add_argument("--after", metavar="YYYY-MM-DD", help="posts on or after this date (UTC)")
    p.add_argument("--before", metavar="YYYY-MM-DD", help="posts on or before this date (UTC)")
    p.add_argument("--before-id", type=int, metavar="ID", help="start below this post number (continue paging)")
    p.add_argument("--max-pages", type=int, default=25, help="pages of 20 posts to scan at most (default 25)")
    p.add_argument("--links", action="store_true", help="print the URLs found in posts instead of the posts")
    p.add_argument("-f", "--format", choices=["table", "markdown", "json"], default="table",
                   help="table (default), markdown, json (every field)")
    p.add_argument("-o", "--output", metavar="FILE", help="write to FILE (UTF-8) instead of stdout")
    p.add_argument("--timeout", type=float, default=30.0, help="network timeout in seconds (default 30)")
    p.add_argument("-q", "--quiet", action="store_true", help="no note: lines")
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
    if args.after and args.before and args.after > args.before:
        return fail(EXIT_BAD_INPUT, "--after is later than --before")
    if args.limit is None:
        args.limit = 1000 if args.after else 20
    if not 1 <= args.limit <= 1000:
        return fail(EXIT_BAD_INPUT, "--limit must be between 1 and 1000")
    if not (math.isfinite(args.timeout) and 1 <= args.timeout <= 300):
        return fail(EXIT_BAD_INPUT, "--timeout must be between 1 and 300 seconds")
    if not 1 <= args.max_pages <= 200:
        return fail(EXIT_BAD_INPUT, "--max-pages must be between 1 and 200")
    for flag in ("post", "before_id"):
        if getattr(args, flag) is not None and getattr(args, flag) < 1:
            return fail(EXIT_BAD_INPUT, f"--{flag.replace('_', '-')} must be a positive post number")
    if args.grep:
        try:
            re.compile(args.grep)
        except re.error as e:
            return fail(EXIT_BAD_INPUT, f"bad --grep pattern: {e}")
    if args.search is not None and not args.search.strip():
        return fail(EXIT_BAD_INPUT, "empty --search")
    try:
        channel, post_id = parse_target(args.channel)
        if args.post is not None:
            if post_id is not None and post_id != args.post:
                return fail(EXIT_BAD_INPUT, f"the link names post {post_id} but --post says {args.post}")
            post_id = args.post
        if post_id is not None and (args.search or args.grep or args.after or args.before or args.before_id):
            return fail(EXIT_BAD_INPUT, "a single post takes no --search, --grep, --after, --before or --before-id")
        payload = cmd_post(channel, post_id, args, note) if post_id is not None else cmd_list(channel, args, note)
        text = render(payload, args.format, args.links)
    except Failure as e:
        return fail(e.code, str(e), e.hint)
    except KeyboardInterrupt:
        return fail(130, "interrupted")
    except Exception as e:  # noqa: BLE001 - exit 1 must keep meaning "nothing matched", never a crash
        return fail(EXIT_MARKUP, f"unexpected {type(e).__name__}: {e}",
                    "a bug or a markup change the parser does not handle; report it with the command line")
    if payload["posts"] or args.format == "json":
        if args.output:  # an empty run never overwrites an earlier file with nothing
            try:
                Path(args.output).write_text(text, encoding="utf-8")
            except OSError as e:
                return fail(EXIT_BAD_INPUT, f"cannot write {args.output}: {e.strerror or e}")
            what = f"{len(text.splitlines())} lines of links" if args.links else f"{payload['count']} posts"
            note(f"wrote {what} to {args.output}")
        else:
            try:
                sys.stdout.write(text)
                sys.stdout.flush()
            except BrokenPipeError:  # `| head` closed the pipe: the reader has what it wanted
                with contextlib.suppress(OSError):
                    os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
                return EXIT_OK
    if not payload["posts"]:
        if post_id is not None:
            return fail(EXIT_EMPTY, f"post {post_id} is not in the public preview", "it was deleted, or the number is wrong")
        return fail(EXIT_EMPTY, "no posts match", "loosen --grep/--search/--after/--before, or raise --max-pages")
    if args.links and not any(outside_links(p["links"], payload["channel"]["username"]) for p in payload["posts"]):
        note("these posts link nowhere outside the channel")
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
