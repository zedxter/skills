#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Wikipedia in any language and Wikidata without a key, through the official REST and Action APIs with a
User-Agent that follows Wikimedia's policy (an empty one gets HTTP 403). Standard library only.

    uv run wiki.py search "mercury" -n 5                   # titles with short descriptions
    uv run wiki.py summary "Mercury (planet)"              # lead, description, revision id for citing
    uv run wiki.py summary https://ru.wikipedia.org/wiki/Москва
    uv run wiki.py text Berlin --lang de --sections        # the article's sections
    uv run wiki.py text Berlin --lang de --section Geschichte
    uv run wiki.py infobox "Marie Curie"                   # the infobox as label: value (best effort)
    uv run wiki.py entity Q64 --langs ru,de                # Wikidata facts and the article in other languages
    uv run wiki.py entity "Marie Curie" --props P569,P570,P166
    uv run wiki.py links "Alan Turing" -n 50               # articles the page links to

Exit codes:
    0 done · 1 not found, or no results · 2 bad input (also: no Wikipedia in that language) · 3 a disambiguation
    page (its options are printed) · 4 rate-limited or blocked · 6 network error or Wikimedia down · 7 an unexpected
    answer or failure · 130 interrupted
"""

import argparse
import contextlib
import html
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

EXIT_OK, EXIT_NOT_FOUND, EXIT_BAD_INPUT, EXIT_DISAMBIG, EXIT_RATE, EXIT_NETWORK, EXIT_OTHER = 0, 1, 2, 3, 4, 6, 7

# Wikimedia's User-Agent policy: identify the tool and give a way to contact its maintainers; never a browser's.
UA = "utility-belt-wiki-lookup/1.0 (+https://github.com/zedxter/utility-belt)"
WIKIDATA = "https://www.wikidata.org/w/api.php"
INTERVAL = 0.1  # seconds between requests: one at a time, as the API etiquette asks
LANG_RE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]{2,12})*|simple")
LANG_ALIAS = {"nb": "no"}                        # nb.wikipedia.org is no.wikipedia.org
SITE_ID = {"be-tarask": "be_x_old"}             # Wikidata site ids that do not follow the language code
SITE_LANG = {v: k for k, v in SITE_ID.items()}
FORBIDDEN = re.compile(r"[<>\[\]{}|]")            # characters a title cannot contain
LICENSE = "CC BY-SA 4.0"
# Shown by `entity` unless --props says otherwise: what people usually want to know about an item.
DEFAULT_PROPS = ["P31", "P279", "P17", "P131", "P36", "P625", "P571", "P576", "P569", "P570", "P19", "P20", "P27",
                 "P106", "P39", "P69", "P108", "P166", "P50", "P57", "P170", "P175", "P577", "P495", "P112", "P159",
                 "P452", "P1128", "P1082", "P2046", "P2044", "P1448", "P1705", "P37", "P38", "P35", "P6", "P856"]
NOT_WIKIPEDIA = {"commonswiki", "specieswiki", "metawiki", "mediawikiwiki", "wikidatawiki", "wikimaniawiki",
                 "outreachwiki", "incubatorwiki", "foundationwiki", "wikifunctionswiki", "sourceswiki", "testwiki",
                 "test2wiki", "testwikidatawiki", "labswiki", "betawikiversity", "donatewiki", "loginwiki"}


class Failure(Exception):
    def __init__(self, code: int, message: str, hint: str = "", payload: Any = None) -> None:
        super().__init__(message)
        self.code, self.hint, self.payload = code, hint, payload


# ------------------------------------------------------------------ text

def safe(s: Any) -> str:
    """One printable line: no control, C1 or bidi-override characters, whitespace collapsed."""
    return " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f‪-‮⁦-⁩]", " ", str(s if s is not None else "")).split())


def safe_block(s: str) -> str:
    """Multi-line text with the same characters removed, line breaks kept."""
    lines = [re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]", " ", ln).rstrip() for ln in s.split("\n")]
    return "\n".join(lines)


def strip_tags(s: str | None) -> str:
    return " ".join(html.unescape(re.sub(r"<[^<>]*>", "", s or "")).split())


def title_of(s: str) -> str:
    return " ".join(s.replace("_", " ").split())


def page_url(lang: str, title: str) -> str:
    return f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe="()_,:'!*-.~")


def site_id(lang: str) -> str:
    return SITE_ID.get(lang, lang.replace("-", "_")) + "wiki"


def site_lang(site: str) -> str:
    code = site[:-4]
    return SITE_LANG.get(code, code.replace("_", "-"))


def permalink(lang: str, title: str, revid: Any) -> str | None:
    if not revid:
        return None
    return f"https://{lang}.wikipedia.org/w/index.php?" + urllib.parse.urlencode({"title": title.replace(" ", "_"),
                                                                                 "oldid": revid})


def anchor_text(frag: str) -> str:
    """A URL fragment as a heading: percent-encoding, and MediaWiki's old '.D0.98'-style encoding, undone."""
    if re.search(r"\.[0-9A-F]{2}", frag) and "%" not in frag:
        with contextlib.suppress(UnicodeDecodeError):
            frag = urllib.parse.unquote(re.sub(r"\.([0-9A-F]{2})", r"%\1", frag), errors="strict")
    return title_of(urllib.parse.unquote(frag))


def parse_target(target: str, lang: str | None) -> tuple[str, str, str | None]:
    """(lang, title, section) from a title ('Berlin', 'Berlin#Klima', 'fr:Paris') or a Wikipedia URL (desktop or
    mobile, /wiki/ or ?title=, with a #section)."""
    t = target.strip()
    m = re.match(r"^(?:https?://)?([a-z0-9-]+)(?:\.m)?\.wikipedia\.org/(.*)$", t, re.I)
    if not m:
        if re.match(r"^(?:https?://|www\.)", t, re.I):
            raise Failure(EXIT_BAD_INPUT, f"{target!r} is not a Wikipedia URL",
                          "give an article title, or a link like https://en.wikipedia.org/wiki/Title")
        title, _, frag = t.partition("#")
        if (iw := re.match(r"^([a-z]{2,3}(?:-[a-z0-9]{2,12})*|simple):(\S.*)$", title)) and LANG_RE.fullmatch(iw[1]):
            lang, title = iw[1], iw[2]  # an interlanguage prefix: fr:Paris
        url_lang, section = LANG_ALIAS.get(lang or "en", lang or "en"), anchor_text(frag) if frag else None
    else:
        url_lang, rest = LANG_ALIAS.get(m[1].lower(), m[1].lower()), m[2]
        path, _, frag = rest.partition("#")
        path, _, query = path.partition("?")
        q = urllib.parse.parse_qs(query)
        if q.get("oldid") or q.get("diff"):
            raise Failure(EXIT_BAD_INPUT, f"{target!r} points to an old revision or a diff",
                          "this tool reads the current version; open that link directly, or drop oldid= to "
                          "read the article as it is now")
        title = urllib.parse.unquote(path[5:]) if path.startswith("wiki/") else (q.get("title") or [""])[0]
        if not title:
            raise Failure(EXIT_BAD_INPUT, f"no article title in {target!r}",
                          "use a link like https://en.wikipedia.org/wiki/Title")
        section = anchor_text(frag) if frag else None
    title = title_of(title)
    if not title:
        raise Failure(EXIT_BAD_INPUT, "the title is empty")
    if FORBIDDEN.search(title):
        raise Failure(EXIT_BAD_INPUT, f"{title!r} contains a character no Wikipedia title can have (<>[]{{}}|)",
                      "pass one title at a time, without markup")
    return url_lang, title, section


# ------------------------------------------------------------------ network

class Net:
    def __init__(self, timeout: float, note) -> None:
        self.timeout, self.note, self.last = timeout, note, 0.0

    def get(self, url: str) -> Any:
        """Parsed JSON. 404 → None. 429/503 retried once, then exit 4/6; 403 is a block (exit 4)."""
        host = urllib.parse.urlsplit(url).hostname or ""
        for attempt in (1, 2):
            wait = self.last + INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                                       "Accept-Encoding": "identity"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read(30_000_001)
                    length = resp.headers.get("Content-Length", "")
                if length.isdigit() and len(body) < int(length):  # the connection dropped mid-answer
                    raise ConnectionError(f"answer cut off after {len(body)} of {length} bytes")
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code in (429, 503):
                    ra = (e.headers or {}).get("Retry-After", "")
                    secs = int(ra) if ra.isdigit() else 5
                    if attempt == 1 and secs <= 30:
                        self.note(f"{host} answered HTTP {e.code}; waiting {secs} s once")
                        time.sleep(secs)
                        continue
                    wait_hint = f"it asks to wait {secs} s" if ra.isdigit() else "wait a minute"
                    if e.code == 429:
                        raise Failure(EXIT_RATE, f"{host} rate limit (HTTP 429)", f"{wait_hint}; do not loop") from None
                    raise Failure(EXIT_NETWORK, f"{host} is busy (HTTP 503)", f"{wait_hint}, then retry once") from None
                if e.code == 403:
                    text = ""
                    with contextlib.suppress(Exception):
                        text = e.read(2000).decode("utf-8", "replace")
                    why = "the User-Agent policy" if "user-agent" in text.lower() else "Wikimedia"
                    raise Failure(EXIT_RATE, f"{host} blocked the request (HTTP 403, {why})",
                                  "this network may be blocked; wait, or try from another connection") from None
                if e.code >= 500:
                    if attempt == 1:
                        time.sleep(2)
                        continue
                    raise Failure(EXIT_NETWORK, f"{host} is not answering (HTTP {e.code})", "try again later") from None
                if e.code == 400:
                    raise Failure(EXIT_BAD_INPUT, f"{host} rejected the request (HTTP 400)",
                                  "check the title or query for characters a title cannot contain") from None
                raise Failure(EXIT_OTHER, f"{host} answered HTTP {e.code}", "try again later") from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                reason = getattr(e, "reason", e)
                if isinstance(reason, socket.gaierror) and host.endswith(".wikipedia.org") and host != "en.wikipedia.org":
                    if resolves("en.wikipedia.org"):
                        lang = host.split(".")[0]
                        raise Failure(EXIT_BAD_INPUT, f"there is no Wikipedia in language {lang!r}",
                                      "check --lang: en, de, ru, zh, simple...") from None
                if "CERTIFICATE_VERIFY_FAILED" in str(reason):
                    raise Failure(EXIT_NETWORK, f"TLS certificate verification failed ({reason})",
                                  "on a python.org macOS build run 'Install Certificates.command', or use uv's "
                                  "managed Python") from None
                if attempt == 2:
                    raise Failure(EXIT_NETWORK, f"{host}: {reason}", "check the connection (HTTPS_PROXY is honoured)") from None
                time.sleep(1)
            finally:
                self.last = time.monotonic()
        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise Failure(EXIT_OTHER, f"{host} answered something that is not JSON", "try again later") from None

    def api(self, lang: str, **params: Any) -> dict[str, Any]:
        """Action API of a language edition (lang 'wikidata' for Wikidata); API errors become Failures."""
        base = WIKIDATA if lang == "wikidata" else f"https://{lang}.wikipedia.org/w/api.php"
        params = {"format": "json", "formatversion": 2, "errorformat": "plaintext", **params}
        d = self.get(f"{base}?{urllib.parse.urlencode(params)}")
        if not isinstance(d, dict):
            raise Failure(EXIT_OTHER, "the API answered without a result", "try again later")
        errors = d.get("errors") or ([d["error"]] if isinstance(d.get("error"), dict) else [])
        if errors:
            e = errors[0]
            code, text = e.get("code", "?"), e.get("text") or e.get("info") or ""
            if code in ("ratelimited", "maxlag"):
                raise Failure(EXIT_RATE, f"Wikimedia: {text or code}", "wait a minute; do not loop")
            if code in ("invalidtitle", "badvalue", "paramempty", "invalid-entity-id",
                        "param-illegal", "toomanyvalues"):
                raise Failure(EXIT_BAD_INPUT, f"Wikimedia rejected the request: {text or code}", "check the input")
            if code == "missingtitle":
                raise Failure(EXIT_NOT_FOUND, f"not found: {text or code}", "try `search` first")
            if code == "no-such-entity":
                raise Failure(EXIT_NOT_FOUND, f"not found: {text or code}", "check the Q-id, or pass an article title")
            raise Failure(EXIT_OTHER, f"Wikimedia API error {code}: {text}", "try again later")
        return d


def resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ pages

def page_info(net: Net, lang: str, title: str, extract: bool = False) -> dict[str, Any]:
    """Resolved title (redirects followed), page id, revision, Wikidata item, disambiguation flag; with extract=True
    also the article's plain text with == headings ==."""
    props = "revisions|info|pageprops" + ("|extracts" if extract else "")
    params: dict[str, Any] = {"action": "query", "titles": title, "redirects": 1, "prop": props,
                              "rvprop": "ids|timestamp", "inprop": "url", "ppprop": "wikibase_item|disambiguation"}
    if extract:
        params.update(explaintext=1, exsectionformat="wiki")
    d = net.api(lang, **params)
    q = d.get("query") or {}
    pages = q.get("pages") or []
    if q.get("interwiki"):
        raise Failure(EXIT_BAD_INPUT, f"{title!r} is a link to another wiki ({q['interwiki'][0].get('iw')})",
                      "for another language pass --lang (or fr:Title); other wikis are not supported")
    if not pages:
        raise Failure(EXIT_OTHER, "the API answered without a page", "try again later")
    p = pages[0]
    if p.get("invalid"):
        why = p.get("invalidreason")
        why = why.get("text") or why.get("code") if isinstance(why, dict) else why
        raise Failure(EXIT_BAD_INPUT, f"{title!r} is not a valid title" + (f": {why}" if why else ""), "fix the title")
    if p.get("missing"):
        raise Failure(EXIT_NOT_FOUND, f"no article {title!r} on {lang}.wikipedia.org",
                      f"try `search {title!r} --lang {lang}`, or another --lang")
    redirect = next((r for r in q.get("redirects") or []), {})
    rev = (p.get("revisions") or [{}])[0]
    return {"lang": lang, "title": p.get("title"), "pageid": p.get("pageid"), "url": page_url(lang, p.get("title", "")),
            "revision": rev.get("revid"), "timestamp": rev.get("timestamp"),
            "permalink": permalink(lang, p.get("title", ""), rev.get("revid")),
            "wikibase_item": (p.get("pageprops") or {}).get("wikibase_item"),
            "disambiguation": "disambiguation" in (p.get("pageprops") or {}),
            "redirected_from": redirect.get("from"), "fragment": redirect.get("tofragment"),
            "extract": p.get("extract")}


class OptionsParser(HTMLParser):
    """List items of a disambiguation page in order: the item's text and the title its first article link names."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, Any]] = []
        self.open: list[dict[str, Any]] = []      # list items being read (nested lists nest)
        self.skip = 0
        self.stack: list[bool] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID:
            return
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        hidden = tag in ("style", "script", "sup") or any(c in ("navbox", "noprint", "mw-editsection", "metadata",
                                                                "sistersitebox", "hatnote") for c in cls)
        self.stack.append(hidden)
        self.skip += hidden
        if self.skip:
            return
        if tag == "li":
            item = {"title": None, "parts": []}
            self.items.append(item)
            self.open.append(item)
        elif tag == "a" and self.open and self.open[-1]["title"] is None:
            href, title = a.get("href") or "", a.get("title") or ""
            if href.startswith("/wiki/") and title and ":" not in href[6:] and "new" not in cls:
                self.open[-1]["title"] = title

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID or not self.stack:
            return
        if tag == "li" and self.open and not self.skip:
            self.open.pop()
        self.skip -= self.stack.pop()

    def handle_data(self, data: str) -> None:
        if self.open and not self.skip:
            self.open[-1]["parts"].append(data)

    def options(self) -> list[dict[str, str]]:
        out = []
        for it in self.items:
            line = " ".join("".join(it["parts"]).split())
            if it["title"] and line:
                out.append({"title": it["title"], "line": line.split("\n")[0][:300]})
        return out


MAX_OPTIONS = 300


def disambiguation(net: Net, info: dict[str, Any]) -> Failure:
    d = net.api(info["lang"], action="parse", page=info["title"], prop="text", disablelimitreport=1,
                disableeditsection=1, redirects=1)
    p = OptionsParser()
    p.feed(((d.get("parse") or {}).get("text")) or "")
    options = p.options()
    return Failure(EXIT_DISAMBIG, f"{info['title']!r} is a disambiguation page on {info['lang']}.wikipedia.org "
                                  f"({len(options)} options)", "pick the meaning and ask again with its title",
                   payload={"disambiguation": True, "title": info["title"], "lang": info["lang"], "url": info["url"],
                            "total": len(options), "truncated": len(options) > MAX_OPTIONS,
                            "options": options[:MAX_OPTIONS]})


# ------------------------------------------------------------------ commands

def cmd_search(net: Net, args: argparse.Namespace) -> dict[str, Any]:
    url = f"https://{args.lang}.wikipedia.org/w/rest.php/v1/search/page?" + urllib.parse.urlencode(
        {"q": args.query, "limit": args.n})
    d = net.get(url)
    pages = (d or {}).get("pages") if isinstance(d, dict) else None
    if not isinstance(pages, list):
        raise Failure(EXIT_OTHER, "search answered without a result list", "try again later")
    results = [{"title": p.get("title"), "description": p.get("description"), "excerpt": strip_tags(p.get("excerpt")),
                "url": page_url(args.lang, p.get("title") or ""), "pageid": p.get("id")}
               for p in pages if isinstance(p, dict) and p.get("title")]
    if not results:
        raise Failure(EXIT_NOT_FOUND, f"no results for {args.query!r} on {args.lang}.wikipedia.org",
                      "try other words, or another --lang")
    return {"lang": args.lang, "query": args.query, "results": results}


def cmd_summary(net: Net, args: argparse.Namespace) -> dict[str, Any]:
    lang, title, _ = parse_target(args.target, args.lang)
    d = net.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/"
                + urllib.parse.quote(title.replace(" ", "_"), safe=""))
    if d is None:
        raise Failure(EXIT_NOT_FOUND, f"no article {title!r} on {lang}.wikipedia.org",
                      f"try `search {title!r} --lang {lang}`, or another --lang")
    if not isinstance(d, dict) or not d.get("title"):
        raise Failure(EXIT_OTHER, "the summary answer has no title", "try `text` instead")
    canonical = title_of((d.get("titles") or {}).get("canonical") or d["title"])
    info = {"lang": lang, "title": canonical, "url": page_url(lang, canonical)}
    if d.get("type") == "disambiguation":
        raise disambiguation(net, info)
    return {"lang": lang, "title": canonical, "description": d.get("description"), "extract": d.get("extract"),
            "url": info["url"], "revision": int(d["revision"]) if str(d.get("revision", "")).isdigit() else None,
            "timestamp": d.get("timestamp"),
            "permalink": permalink(lang, canonical, d.get("revision")),
            "wikibase_item": d.get("wikibase_item"),
            "redirected_from": title if title[:1].upper() + title[1:] != canonical else None,
            "thumbnail": (d.get("thumbnail") or {}).get("source"),
            "coordinates": d.get("coordinates"), "license": LICENSE}


def sections_of(text: str) -> list[dict[str, Any]]:
    """[{title, level, text}] from TextExtracts plain text; level 1 is the lead."""
    out = [{"title": "(lead)", "level": 1, "text": []}]
    for ln in text.split("\n"):
        m = re.fullmatch(r"(={2,6})\s*(.*?)\s*\1", ln.strip())
        if m:
            out.append({"title": m[2], "level": len(m[1]), "text": []})
        else:
            out[-1]["text"].append(ln)
    for s in out:
        s["text"] = "\n".join(s["text"]).strip()
    # Lists, tables and references are not in TextExtracts' plain text: drop headings left with nothing under them
    # (from the end, so a parent whose children all went is seen empty too).
    keep: list[dict[str, Any]] = []
    for s in reversed(out):
        has_children = bool(keep) and keep[-1]["level"] > s["level"]
        if s["text"] or has_children or s["level"] == 1:
            keep.append(s)
    keep.reverse()
    for i, s in enumerate(keep):
        end = next((j for j in range(i + 1, len(keep)) if keep[j]["level"] <= s["level"]), len(keep))
        s["total"] = sum(len(x["text"]) for x in keep[i:end]) if s["level"] > 1 else len(s["text"])
    return keep


def find_section(sections: list[dict[str, Any]], name: str) -> list[dict[str, Any]] | None:
    """The named section and its subsections: exact (case-insensitive) title first, then a unique prefix."""
    want = name.casefold().strip()
    idx = next((i for i, s in enumerate(sections) if s["title"].casefold() == want), None)
    if idx is None:
        hits = [i for i, s in enumerate(sections) if s["title"].casefold().startswith(want)]
        idx = hits[0] if len(hits) == 1 else None
    if idx is None:
        return None
    level = sections[idx]["level"]
    end = next((j for j in range(idx + 1, len(sections)) if sections[j]["level"] <= level), len(sections))
    return sections[idx:end]


def markdown_of(sections: list[dict[str, Any]], plain: bool = False) -> str:
    parts = []
    for s in sections:
        if s["level"] > 1:
            parts.append(s["title"] if plain else "#" * s["level"] + " " + s["title"])
        if s["text"]:
            parts.append(s["text"])
    return "\n\n".join(parts)


def cmd_text(net: Net, args: argparse.Namespace) -> dict[str, Any]:
    lang, title, frag = parse_target(args.target, args.lang)
    info = page_info(net, lang, title, extract=True)
    text = info.pop("extract") or ""
    if info["disambiguation"]:
        raise disambiguation(net, info)
    sections = sections_of(text)
    out = {k: info[k] for k in ("lang", "title", "url", "revision", "timestamp", "permalink", "wikibase_item",
                                 "redirected_from")}
    out["sections"] = [{"title": s["title"], "level": s["level"], "chars": len(s["text"]), "total_chars": s["total"]}
                       for s in sections]
    out["license"] = LICENSE
    name = args.section or frag or info.get("fragment")
    if args.sections:
        out["text"] = None
        return out
    if name:
        part = find_section(sections, name)
        if part is None:
            raise Failure(EXIT_NOT_FOUND, f"no section {name!r} in {info['title']!r}",
                          "run with --sections to list them; sections: "
                          + ", ".join(s["title"] for s in sections[1:30]))
        out["section"] = part[0]["title"]
        sections = part
    text = markdown_of(sections, plain=args.format == "text")
    out["chars"], out["truncated"] = len(text), False
    limit = args.max_chars if args.max_chars is not None else (0 if args.output else 40_000)
    if limit and len(text) > limit:
        kept = []
        for s in sections:  # whole sections while they fit; the first one is cut if it alone is too long
            part = markdown_of(kept + [s], plain=args.format == "text")
            if len(part) > limit:
                break
            kept.append(s)
        while kept and not kept[-1]["text"]:  # never end on a heading whose text did not fit
            kept.pop()
        text = markdown_of(kept, plain=args.format == "text") if kept else text[:limit].rsplit("\n", 1)[0]
        out["truncated"] = True
    out["text"] = text
    return out


VOID = {"br", "img", "hr", "meta", "link", "input", "wbr", "area", "base", "col", "embed", "source", "track"}


class InfoboxParser(HTMLParser):
    """Rows of the first infobox, a table or a div (French Infobox v3) whose class starts with 'infobox':
    (label, value) pairs of visible text."""
    SKIP = {"style", "script", "sup", "math", "annotation"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0            # open tables inside the infobox (a table infobox counts itself)
        self.box: str | None = None  # the infobox element's tag, once found
        self.box_open = 0         # nesting of that tag inside the infobox
        self.done = False
        self.rows: list[list[tuple[str, str]]] = []
        self.cell: list[str] | None = None
        self.cell_tag = ""
        self.skip = 0             # inside an element whose text is not shown
        self.stack: list[tuple[str, bool]] = []
        self.template = None

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        hidden = tag in self.SKIP and (tag != "sup" or "reference" in cls) \
            or "display:none" in (a.get("style") or "").replace(" ", "") \
            or any(c in ("noprint", "mw-editsection", "reference", "sortkey") for c in cls)
        if tag not in VOID:
            self.stack.append((tag, hidden))
            if hidden:
                self.skip += 1
        if self.done:
            return
        if self.box is None:
            if tag in ("table", "div") and any(c.startswith("infobox") for c in cls):
                self.box, self.box_open, self.template = tag, 1, a.get("data-name")
                self.depth = 1 if tag == "table" else 0
            return
        if tag == self.box:
            self.box_open += 1
        if tag == "table":
            self.depth += 1
            return
        if not self.depth:
            return
        if self.depth > 1 and tag == "tr" and self.cell is not None:  # rows of a table inside a cell
            self.cell.append("; ")
        if tag == "tr" and self.depth == 1:
            self.rows.append([])
        elif tag in ("th", "td") and self.depth == 1 and self.rows:
            self.cell, self.cell_tag = [], tag
        elif tag in ("br", "li", "p", "div") and self.cell is not None and not self.skip:
            self.cell.append("; ")

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID or all(t != tag for t, _ in self.stack):
            return  # a stray end tag: nothing to close
        while self.stack:
            t, hidden = self.stack.pop()
            if hidden:
                self.skip -= 1
            if t == tag:
                break
        if self.done or self.box is None:
            return
        if tag == self.box:
            self.box_open -= 1
            if self.box_open == 0:
                self.done = True
                return
        if tag == "table":
            self.depth -= 1
        elif tag in ("th", "td") and self.depth == 1 and self.cell is not None and self.rows:
            text = " ".join(re.sub(r"[\u200b-\u200d\u2060\ufeff]", "", "".join(self.cell)).split())
            text = re.sub(r"\s*;(\s*;)*\s*", "; ", text).strip("; ")
            self.rows[-1].append((self.cell_tag, text))
            self.cell = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None and not self.skip:
            self.cell.append(data)

    def fields(self) -> list[dict[str, str]]:
        out, seen = [], set()
        for row in self.rows:
            cells = [(t, v) for t, v in row if v]
            if len(cells) == 2:
                label, value = cells[0][1], cells[1][1]
            elif len(cells) > 2 and cells[0][0] == "th":
                label, value = cells[0][1], "; ".join(v for _, v in cells[1:])
            else:
                continue
            label = label.rstrip(":").strip()
            if label and value and (label, value) not in seen:
                seen.add((label, value))
                out.append({"label": label, "value": value})
        return out


def cmd_infobox(net: Net, args: argparse.Namespace) -> dict[str, Any]:
    lang, title, _ = parse_target(args.target, args.lang)
    info = page_info(net, lang, title)
    if info["disambiguation"]:
        raise disambiguation(net, info)
    d = net.api(lang, action="parse", page=info["title"], prop="text", section=0, disablelimitreport=1,
                disableeditsection=1)
    p = InfoboxParser()
    p.feed(((d.get("parse") or {}).get("text")) or "")
    fields = p.fields()
    if not fields:
        raise Failure(EXIT_NOT_FOUND, f"{info['title']!r} has no infobox that could be read",
                      f"use `entity {info['title']!r} --lang {lang}` for structured facts from Wikidata")
    return {**{k: info[k] for k in ("lang", "title", "url", "revision", "timestamp", "permalink", "wikibase_item")},
            "template": p.template, "fields": fields, "license": LICENSE}


def cmd_links(net: Net, args: argparse.Namespace) -> dict[str, Any]:
    lang, title, _ = parse_target(args.target, args.lang)
    info = page_info(net, lang, title)
    links: list[str] = []
    cont: dict[str, Any] = {}
    while len(links) < args.n:
        d = net.api(lang, action="query", titles=info["title"], prop="links", plnamespace=0,
                    pllimit=min(500, args.n - len(links)), **cont)
        pages = (d.get("query") or {}).get("pages") or [{}]
        links += [l["title"] for l in pages[0].get("links") or [] if l.get("title")]
        if "continue" not in d:
            break
        cont = d["continue"]
    if not links:
        raise Failure(EXIT_NOT_FOUND, f"{info['title']!r} links to no articles", "")
    return {**{k: info[k] for k in ("lang", "title", "url", "revision")},
            "links": [{"title": t, "url": page_url(lang, t)} for t in links[:args.n]],
            "complete": len(links) <= args.n and "continue" not in d}


# ------------------------------------------------------------------ Wikidata

def entity_id(net: Net, target: str, lang: str | None) -> str:
    t = target.strip()
    if m := re.fullmatch(r"(?:https?://(?:www\.|m\.)?wikidata\.org/(?:wiki/(?:Property:|Lexeme:|Special:EntityPage/)?"
                         r"|entity/))?([QqPpLl]\d{1,12})(?:#.*)?", t):
        return m[1].upper()
    plang, title, _ = parse_target(t, lang)
    d = net.api("wikidata", action="wbgetentities", sites=site_id(plang), titles=title,
                normalize=1, props="info")
    ents = d.get("entities") or {}
    eid = next((k for k in ents if not k.startswith("-")), None)
    if not eid:
        raise Failure(EXIT_NOT_FOUND, f"no Wikidata item for the {plang} article {title!r}",
                      f"check the title with `search {title!r} --lang {plang}`")
    return eid


def labels(net: Net, ids: list[str], lang: str) -> dict[str, str]:
    out: dict[str, str] = {}
    ids = list(dict.fromkeys(i for i in ids if re.fullmatch(r"[QPL]\d+", i)))
    for i in range(0, len(ids), 50):
        d = net.api("wikidata", action="wbgetentities", ids="|".join(ids[i:i + 50]), props="labels",
                    languages=f"{lang}|mul|en", languagefallback=1)
        for k, e in (d.get("entities") or {}).items():
            ls = e.get("labels") or {}
            v = next((ls[x]["value"] for x in (lang, "mul", "en") if x in ls), None) or \
                next((x["value"] for x in ls.values()), None)
            if v:
                out[k] = v
    return out


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def fmt_time(v: dict[str, Any]) -> str:
    t, prec = v.get("time", ""), v.get("precision", 11)
    m = re.match(r"([+-])0*(\d+)-(\d\d)-(\d\d)", t)
    if not m:
        return t
    sign, y, mo, d = m[1], int(m[2]), m[3], m[4]
    era = " BCE" if sign == "-" else ""
    if prec >= 11:
        s = f"{y:04d}-{mo}-{d}"
    elif prec == 10:
        s = f"{y:04d}-{mo}"
    elif prec == 9:
        s = f"{y}"
    elif prec == 8:
        s = f"{y // 10 * 10}s"
    elif prec == 7:
        s = f"{ordinal((y - 1) // 100 + 1)} century"
    elif prec == 6:
        s = f"{ordinal((y - 1) // 1000 + 1)} millennium"
    else:  # ten thousand years and coarser: geological and cosmological dates
        s = f"{y:,} years ago" if sign == "-" else f"{y:,}"
        era = ""
    if v.get("calendarmodel", "").endswith("Q1985786") and prec >= 9:
        s += " (Julian)"
    return s + era


def fmt_amount(a: str) -> str:
    sign, a = ("-", a[1:]) if a.startswith("-") else ("", a.lstrip("+"))
    whole, dot, frac = a.partition(".")
    if not whole.isdigit() or (frac and not frac.isdigit()):
        return sign + a
    return f"{sign}{int(whole):,}{dot}{frac}"


def snak_value(snak: dict[str, Any], names: dict[str, str]) -> str | None:
    kind = snak.get("snaktype")
    if kind == "somevalue":
        return "unknown value"
    if kind == "novalue":
        return "none"
    dv = (snak.get("datavalue") or {})
    v, t = dv.get("value"), dv.get("type")
    if t == "wikibase-entityid":
        eid = v.get("id") or f"Q{v.get('numeric-id')}"
        return names.get(eid, eid)
    if t == "time":
        return fmt_time(v)
    if t == "quantity":
        unit = (v.get("unit") or "1").rsplit("/", 1)[-1]
        return fmt_amount(v.get("amount", "")) + ("" if unit == "1" else " " + names.get(unit, unit))
    if t == "globecoordinate":
        return f"{v.get('latitude'):.6g}, {v.get('longitude'):.6g}"
    if t == "monolingualtext":
        return f"{v.get('text')} ({v.get('language')})"
    if t == "string":
        if snak.get("datatype") == "commonsMedia":
            return "https://commons.wikimedia.org/wiki/File:" + urllib.parse.quote(v.replace(" ", "_"))
        return str(v)
    return None


def referenced_ids(claims: dict[str, list[dict[str, Any]]]) -> list[str]:
    ids = []
    for prop, stmts in claims.items():
        ids.append(prop)
        for s in stmts:
            for snak in [s.get("mainsnak") or {}] + [q for qs in (s.get("qualifiers") or {}).values() for q in qs]:
                dv = (snak.get("datavalue") or {})
                if dv.get("type") == "wikibase-entityid":
                    ids.append(dv["value"].get("id") or f"Q{dv['value'].get('numeric-id')}")
                elif dv.get("type") == "quantity":
                    ids.append((dv["value"].get("unit") or "1").rsplit("/", 1)[-1])
                ids.append(snak.get("property", ""))
    return ids


def statement_values(stmts: list[dict[str, Any]], names: dict[str, str], limit: int = 12) -> tuple[list[str], int]:
    """Best-ranked values with their time qualifiers (preferred statements if any, else normal ones), and how many
    more were left out."""
    best = [s for s in stmts if s.get("rank") == "preferred"] or [s for s in stmts if s.get("rank") == "normal"]
    out = []
    for s in best[:limit]:
        v = snak_value(s.get("mainsnak") or {}, names)
        if v is None:
            continue
        q = s.get("qualifiers") or {}
        when = {p: snak_value(q[p][0], names) for p in ("P585", "P580", "P582") if q.get(p)}
        if when.get("P585"):  # 'as of' for a measured value, plain for an event (an award in 1911)
            quantity = ((s.get("mainsnak") or {}).get("datavalue") or {}).get("type") == "quantity"
            v += f" (as of {when['P585']})" if quantity else f" ({when['P585']})"
        elif when.get("P580") or when.get("P582"):
            v += f" ({when.get('P580') or '…'} – {when.get('P582') or ''})".replace(" )", ")")
        out.append(v)
    return out, max(0, len(best) - limit)


def cmd_entity(net: Net, args: argparse.Namespace) -> dict[str, Any]:
    lang = args.lang
    eid = entity_id(net, args.target, args.lang)
    if args.lang is None:
        lang = parse_target(args.target, None)[0] if not re.match(r"^\s*(?:https?://\S*wikidata\.org/|[QPLqpl]\d)", args.target) else "en"
    d = net.api("wikidata", action="wbgetentities", ids=eid, props="labels|descriptions|aliases|claims|sitelinks/urls|info",
                languages=f"{lang}|mul|en", languagefallback=1)
    e = (d.get("entities") or {}).get(eid) or next(iter((d.get("entities") or {}).values()), {})
    if not e or "missing" in e:
        raise Failure(EXIT_NOT_FOUND, f"no Wikidata entity {eid}", "check the id")
    eid = e.get("id", eid)
    pick = lambda m: next((m[x]["value"] for x in (lang, "mul", "en") if x in m), None)  # noqa: E731
    claims = e.get("claims") or {}
    if args.props == "all":
        props = list(claims)
    elif args.props:
        props = [p.strip().upper() for p in args.props.split(",") if p.strip()]
    else:
        props = [p for p in DEFAULT_PROPS if p in claims]
    chosen = {p: claims[p] for p in props if p in claims}
    names = labels(net, referenced_ids(chosen), lang)
    links = e.get("sitelinks") or {}
    wikis = {k: v for k, v in links.items() if re.fullmatch(r"[a-z_]+wiki", k) and k not in NOT_WIKIPEDIA}
    kinds = {((st.get("mainsnak") or {}).get("datavalue") or {}).get("value", {}).get("id") for st in claims.get("P31", [])}
    if "Q4167410" in kinds:  # a Wikimedia disambiguation page: its meanings are articles of their own
        page = wikis.get(site_id(lang)) or wikis.get("enwiki")
        if page:
            plang = site_lang(next(k for k, v in wikis.items() if v is page))
            raise disambiguation(net, {"lang": plang, "title": page["title"], "url": page_url(plang, page["title"])})
        raise Failure(EXIT_DISAMBIG, f"{eid} is a disambiguation item", "search for the meaning you want")
    limit = 50 if args.props else 12
    facts = []
    for p in props:
        if p not in claims:
            continue
        values, more = statement_values(claims[p], names, limit)
        if values:
            facts.append({"property": p, "label": names.get(p, p), "values": values, "more": more})
    if args.langs == "all":
        want = list(wikis)
    else:
        codes = [c.strip().lower() for c in (args.langs.split(",") if args.langs else [lang, "en"]) if c.strip()]
        want = [site_id(LANG_ALIAS.get(c, c)) for c in dict.fromkeys(codes)]
    articles = [{"lang": site_lang(k), "title": wikis[k]["title"], "url": wikis[k].get("url")
                 or page_url(site_lang(k), wikis[k]["title"])} for k in want if k in wikis]
    return {"id": eid, "url": f"https://www.wikidata.org/wiki/{eid}", "revision": e.get("lastrevid"),
            "label": pick(e.get("labels") or {}), "description": pick(e.get("descriptions") or {}),
            "aliases": [a["value"] for a in (e.get("aliases") or {}).get(lang, [])][:10],
            "facts": facts, "articles": articles, "wikipedia_count": len(wikis), "license": "CC0 1.0"}


# ------------------------------------------------------------------ output

def render(command: str, d: dict[str, Any], args: argparse.Namespace) -> str:
    if args.format == "json":
        return json.dumps(d, ensure_ascii=False, indent=2) + "\n"
    lines: list[str] = []
    if d.get("disambiguation"):
        lines.append(f"# {safe(d['title'])}: disambiguation page on {d['lang']}.wikipedia.org"
                     + (f", first {len(d['options'])} of {d['total']} options" if d.get("truncated") else ""))
        lines += [f"- {safe(o['line'])}  → {safe(o['title'])}" for o in d["options"]]
        lines.append(safe(d["url"]))
        return "\n".join(lines) + "\n"
    cite = lambda: " · ".join(x for x in (  # noqa: E731
        safe(d.get("url")), f"revision {d['revision']}" if d.get("revision") else "",
        safe(d.get("timestamp")) if d.get("timestamp") else "", f"Wikidata {d['wikibase_item']}" if d.get("wikibase_item") else "",
        d.get("license", "")) if x)
    if command == "search":
        lines.append(f"# {safe(d['query'])} · {d['lang']}.wikipedia.org · {len(d['results'])} results")
        for i, r in enumerate(d["results"], 1):
            lines.append(f"{i}. {safe(r['title'])}" + (f" — {safe(r['description'])}" if r.get("description") else ""))
            lines.append(f"   {safe(r['url'])}")
            if r.get("excerpt"):
                lines.append(f"   {safe(r['excerpt'])}")
    elif command == "summary":
        lines.append(f"# {safe(d['title'])}")
        if d.get("redirected_from"):
            lines.append(f"(redirected from {safe(d['redirected_from'])})")
        if d.get("description"):
            lines.append(safe(d["description"]))
        lines += ["", safe_block(d.get("extract") or ""), "", cite()]
        if d.get("permalink"):
            lines.append(f"permalink: {safe(d['permalink'])}")
    elif command == "text":
        if args.sections:
            lines.append(f"# {safe(d['title'])}: sections")
            for s in d["sections"]:
                size = f"{s['total_chars']:,} chars" + (" with subsections" if s["total_chars"] != s["chars"] else "")
                lines.append("  " * (s["level"] - 1) + f"- {safe(s['title'])} ({size})")
        else:
            head = d["title"] + (f" — {d['section']}" if d.get("section") else "")
            lines += [("" if args.format == "text" else "# ") + safe(head), "", safe_block(d["text"] or "")]
            if d.get("truncated"):
                lines += ["", f"[cut at {len(d['text']):,} of {d['chars']:,} characters: use --sections and --section "
                              "NAME for one part, or --max-chars 0 / -o FILE for all of it]"]
        lines += ["", cite()]
        if d.get("permalink"):
            lines.append(f"permalink: {safe(d['permalink'])}")
    elif command == "infobox":
        lines.append(f"# {safe(d['title'])}: infobox" + (f" ({safe(d['template'])})" if d.get("template") else ""))
        lines += [f"{safe(f['label'])}: {safe(f['value'])}" for f in d["fields"]]
        lines += ["", cite()]
        if d.get("permalink"):
            lines.append(f"permalink: {safe(d['permalink'])}")
    elif command == "links":
        lines.append(f"# {safe(d['title'])}: {len(d['links'])} linked articles" + ("" if d["complete"] else " (first)"))
        lines += [safe(l["title"]) for l in d["links"]]
    elif command == "entity":
        lines.append(f"# {safe(d['label'] or d['id'])} ({d['id']})")
        if d.get("description"):
            lines.append(safe(d["description"]))
        if d.get("aliases"):
            lines.append("also: " + safe("; ".join(d["aliases"])))
        lines.append("")
        for f in d["facts"]:
            more = f" (+{f['more']} more: --props {f['property']})" if f.get("more") else ""
            lines.append(f"{safe(f['label'])} ({f['property']}): {safe('; '.join(f['values']))}{more}")
        if d["articles"]:
            lines.append("")
            lines += [f"{a['lang']}: {safe(a['title'])} — {safe(a['url'])}" for a in d["articles"]]
        lines += ["", f"{d['url']} · revision {d['revision']} · in {d['wikipedia_count']} Wikipedias · {d['license']}"]
    return "\n".join(lines).rstrip() + "\n"


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wiki.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser, fmts: list[str]) -> None:
        sp.add_argument("--lang", help="Wikipedia language edition: en, ru, de, fr, zh, simple... (default en, "
                                       "or the language of a Wikipedia URL)")
        sp.add_argument("-f", "--format", choices=fmts, default=fmts[0], help=f"output format (default {fmts[0]})")
        sp.add_argument("-o", "--output", metavar="FILE", help="write to FILE instead of stdout")
        sp.add_argument("--timeout", type=float, default=30.0, help="network timeout in seconds (default 30)")
        sp.add_argument("-q", "--quiet", action="store_true", help="no notes on stderr")

    s = sub.add_parser("search", help="find articles by words")
    s.add_argument("query", nargs="+")
    s.add_argument("-n", type=int, default=10, help="results, 1-100 (default 10)")
    common(s, ["table", "json"])
    s = sub.add_parser("summary", help="the lead of an article, with its revision for citing")
    s.add_argument("target", help="article title or Wikipedia URL")
    common(s, ["table", "json"])
    s = sub.add_parser("text", help="the article's plain text as Markdown, or one section")
    s.add_argument("target", help="article title or Wikipedia URL (a #fragment selects that section)")
    s.add_argument("--sections", action="store_true", help="list the sections only")
    s.add_argument("--section", metavar="NAME", help="only this section and its subsections")
    s.add_argument("--max-chars", type=int, metavar="N",
                   help="cut longer text at a section boundary (default 40000, none with -o; 0 = no limit)")
    common(s, ["markdown", "text", "json"])
    s = sub.add_parser("infobox", help="the infobox as label: value pairs (best effort)")
    s.add_argument("target", help="article title or Wikipedia URL")
    common(s, ["table", "json"])
    s = sub.add_parser("links", help="articles the page links to")
    s.add_argument("target", help="article title or Wikipedia URL")
    s.add_argument("-n", type=int, default=200, help="links to return, 1-5000 (default 200)")
    common(s, ["table", "json"])
    s = sub.add_parser("entity", help="Wikidata facts and the article in other languages")
    s.add_argument("target", help="Q-id (Q64), Wikidata URL, or an article title or Wikipedia URL")
    s.add_argument("--props", help="comma-separated P-ids (P569,P570), or 'all' (default: a common set)")
    s.add_argument("--langs", help="comma-separated languages for the article links, or 'all' (default: --lang and en)")
    common(s, ["table", "json"])
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
    if args.lang is not None:
        args.lang = args.lang.strip().lower()
        if not LANG_RE.fullmatch(args.lang):
            return fail(EXIT_BAD_INPUT, f"--lang {args.lang!r} is not a language code", "use en, de, ru, zh-yue, simple...")
    if args.command == "search":
        args.query = " ".join(" ".join(args.query).split())
        args.lang = args.lang or "en"
        if not args.query:
            return fail(EXIT_BAD_INPUT, "the query is empty")
        if not 1 <= args.n <= 100:
            return fail(EXIT_BAD_INPUT, "-n must be between 1 and 100")
    elif args.command == "links" and not 1 <= args.n <= 5000:
        return fail(EXIT_BAD_INPUT, "-n must be between 1 and 5000")
    elif args.command != "search" and not args.target.strip():
        return fail(EXIT_BAD_INPUT, "the title is empty")
    if getattr(args, "max_chars", None) is not None and args.max_chars < 0:
        return fail(EXIT_BAD_INPUT, "--max-chars must be 0 or more")
    if args.lang:
        args.lang = LANG_ALIAS.get(args.lang, args.lang)
    net = Net(args.timeout, note)
    run = {"search": cmd_search, "summary": cmd_summary, "text": cmd_text, "infobox": cmd_infobox,
           "links": cmd_links, "entity": cmd_entity}[args.command]
    code = EXIT_OK
    try:
        data = run(net, args)
    except Failure as e:
        if not e.payload:
            return fail(e.code, str(e), e.hint)
        data, code = e.payload, e.code
        fail(e.code, str(e), e.hint)
    if data.get("redirected_from"):
        note(f"{data['redirected_from']!r} redirects to {data['title']!r}")
    text = render(args.command, data, args)
    if data.get("truncated"):
        note(f"text cut at {len(data['text']):,} of {data['chars']:,} characters; use --section NAME for one part")
    if args.output:
        try:
            with open(os.path.expanduser(args.output), "w", encoding="utf-8") as fh:
                fh.write(text)
            return code
        except OSError as e:
            sys.stdout.write(text)
            return fail(EXIT_BAD_INPUT, f"cannot write {args.output}: {e.strerror or e}", "the output went to stdout")
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except BrokenPipeError:
        with contextlib.suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return code


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
    except Exception as e:  # noqa: BLE001 - exit 1 means "not found"; a crash must not look like that
        sys.stderr.write(f"error: unexpected {type(e).__name__}: {(str(e).splitlines() or [''])[0][:300]} "
                         "— a bug; report it with the command line\n")
        sys.exit(EXIT_OTHER)
