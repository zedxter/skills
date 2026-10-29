#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["curl_cffi>=0.17,<1", "trafilatura>=2.3,<3", "lxml_html_clean", "pypdf>=5,<7", "cssselect>=1.2"]
# ///
"""Scrape structured records from web pages: CSS/XPath fields, JSON-LD objects or tables.

Fetching, block detection and optional headless rendering come from the
web-extract skill (install it next to this one). One record per --item match
(or per page), fields by selector, pagination via --next, detail pages via
--follow; output JSON, JSONL, CSV or a Markdown table. --probe lists the
repeating blocks of a page so the right --item selector can be chosen.

    uv run scrape.py URL --probe
    uv run scrape.py URL --item ".product" --field name="h2" --field price=".price|number" --field url="a@href"
    uv run scrape.py URL --item ".product" --field ... --next "a.next" --max-pages 10 -f csv -o items.csv
    uv run scrape.py URL --item "article" --field title=h2 --field url="h2 a@href" --follow url --detail-field body=".post|text"
    uv run scrape.py URL --json-ld Product            # schema.org objects as records
    uv run scrape.py URL --table 1                    # the page's first data table as records

Field syntax:  name=SELECTOR[@attr][|modifier…]
    SELECTOR   CSS by default, or `xpath:…`; relative to the item (or the page)
    @attr      attribute instead of text (href/src are made absolute); `@html` = inner HTML
    |text      collapsed text (default) · |html inner HTML · |number first number (1 234,50 → 1234.5)
    |int       integer · |list all matches as a list · |date first ISO/European date → YYYY-MM-DD
    |join      all matches joined with ", " · |raw text without whitespace collapsing

Exit codes: 0 records found · 1 no records matched · 2 bad input / missing web-extract
    · 3 start URL HTTP error · 4 blocked · 5 not HTML · 6 network · 7 other
"""

import argparse
import csv
import datetime
import importlib.util
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

EXIT_OK, EXIT_EMPTY, EXIT_BAD_INPUT, EXIT_HTTP, EXIT_BLOCKED, EXIT_NOT_HTML, EXIT_NETWORK, EXIT_OTHER = range(8)
MAX_RECORDS_DEFAULT = 1000
MAX_DETAILS = 200
MODIFIERS = {"text", "html", "number", "int", "list", "date", "join", "raw"}
URL_ATTRS = {"href", "src", "action", "data-src", "data-href", "poster", "content"}
NUM_RE = re.compile(r"[-+]?\d[\d\s\u00a0.,']*\d|\d")
DATE_RES = [re.compile(r"\b(\d{4})-(\d{2})-(\d{2})"), re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b"),
            re.compile(r"\b(\d{4})/(\d{2})/(\d{2})\b")]
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WORD_DATE_RES = [re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"),  # March 14, 1879
                 re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\.?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")]  # 14 March 1879


def load_web_extract() -> Any:
    here = Path(__file__).resolve()
    candidates = [Path(os.environ["WEB_EXTRACT_SCRIPT"])] if os.environ.get("WEB_EXTRACT_SCRIPT") else []
    candidates += [here.parent.parent.parent / "web-extract" / "scripts" / "extract.py", here.parent / "extract.py"]
    for root in (Path.home() / ".claude" / "skills", Path.cwd() / ".claude" / "skills", Path.home() / ".codex" / "skills",
                 Path.home() / ".gemini" / "skills", Path.home() / ".config" / "opencode" / "skills", Path.cwd() / ".agents" / "skills"):
        candidates.append(root / "web-extract" / "scripts" / "extract.py")
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("web_extract", path)
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            sys.modules["web_extract"] = mod
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            return mod
    sys.stderr.write("error: the web-extract skill was not found (looked next to this skill, in ~/.claude/skills and other agent skill dirs); "
                     "install it from the same repository or set WEB_EXTRACT_SCRIPT=/path/to/extract.py\n")
    sys.exit(EXIT_BAD_INPUT)


wx = load_web_extract()
try:
    from lxml.cssselect import CSSSelector, SelectorError
except ImportError as e:
    sys.stderr.write(f"error: missing dependency cssselect ({e}); pip install cssselect, or run with uv run\n")
    sys.exit(EXIT_BAD_INPUT)


# ------------------------------------------------------------------ selectors

class BadSelector(ValueError):
    """A selector that compiled but fails when evaluated (e.g. an undefined XPath namespace prefix)."""


class Field:
    """A parsed `name=SELECTOR[@attr][|mod…]` specification."""

    def __init__(self, spec: str, json_mode: bool = False) -> None:
        if "=" not in spec:
            raise ValueError(f"field {spec!r}: expected name=SELECTOR")
        self.name, rest = spec.split("=", 1)
        self.name = self.name.strip()
        if not re.fullmatch(r"[A-Za-z_][\w.-]*", self.name):
            raise ValueError(f"field {spec!r}: bad name {self.name!r}")
        parts = rest.split("|")
        sel, self.mods = parts[0].strip(), [m.strip().lower() for m in parts[1:] if m.strip()]
        bad = [m for m in self.mods if m not in MODIFIERS]
        if bad:
            raise ValueError(f"field {self.name}: unknown modifier {bad[0]!r} (known: {', '.join(sorted(MODIFIERS))})")
        self.attr: str | None = None
        if json_mode:  # a dotted JSON path (`offers.price`, `image.0`, `@id`): kept verbatim, never compiled
            if not sel:
                raise ValueError(f"field {self.name}: empty JSON-LD path")
            self.raw_selector, self.selector = sel, None
            return
        m = re.search(r"@([\w:-]+)$", sel)
        if m and not sel.endswith("]") and not sel.lower().startswith("xpath:"):  # `a@href`; XPath keeps its own `@attr`
            self.attr, sel = m.group(1), sel[: m.start()].strip()
        if sel == ".":
            sel = ""
        self.raw_selector = sel
        self.selector = compile_selector(sel) if sel else None  # None = the item (or page) itself

    def extract(self, node: Any, base_url: str) -> Any:
        nodes = self.selector(node) if self.selector is not None else [node]
        want_all = "list" in self.mods or "join" in self.mods
        values: list[Any] = []
        for n in nodes if want_all else nodes[:1]:
            v = node_value(n, self.attr, self.mods, base_url)
            if v is not None and v != "":
                values.append(v)
        if "join" in self.mods:
            return ", ".join(str(v) for v in values) if values else None
        if "list" in self.mods:
            return values
        return values[0] if values else None


def compile_selector(sel: str):
    """CSS (default) or `xpath:…` → callable(node) -> list of nodes/strings."""
    if sel.lower().startswith("xpath:"):
        expr = sel[6:].strip()
        try:
            xp = wx.lxml.etree.XPath(expr)
        except wx.lxml.etree.XPathError as e:
            raise ValueError(f"bad XPath {expr!r}: {e}") from e

        def run_xpath(node: Any) -> list[Any]:
            try:
                return _as_list(xp(node))
            except wx.lxml.etree.XPathError as e:
                raise BadSelector(f"bad XPath {expr!r}: {e}") from e
        return run_xpath
    try:
        css = CSSSelector(sel, translator="html")
    except (SelectorError, Exception) as e:  # cssselect raises several classes
        raise ValueError(f"bad CSS selector {sel!r}: {e}") from e
    return lambda node: _as_list(css(node))


def _as_list(res: Any) -> list[Any]:
    if isinstance(res, list):
        return res
    return [res] if res not in (None, "") else []


def collapse(text: str) -> str:
    return " ".join(text.split())


def node_value(n: Any, attr: str | None, mods: list[str], base_url: str) -> Any:
    """Text/attribute/HTML of a matched node (or a string from XPath), with modifiers applied."""
    if isinstance(n, (str, bytes)) or not hasattr(n, "tag"):  # XPath string/attribute result
        s = n.decode("utf-8", "replace") if isinstance(n, bytes) else str(n)
        return apply_mods(s, mods, base_url, attr_name=None)
    if attr == "html" or (attr is None and "html" in mods):
        inner = (n.text or "") + "".join(wx.lxml.html.tostring(c, encoding="unicode", with_tail=True) for c in n)
        return inner.strip()
    if attr:
        v = n.get(attr)
        if v is None:
            return None
        v = v.strip()
        if attr in URL_ATTRS and v and not v.startswith(("data:", "javascript:", "#")) and (attr != "content" or re.match(r"^(https?:)?//|^/", v)):
            v = urljoin(base_url, v)
        return apply_mods(v, mods, base_url, attr_name=attr)
    return apply_mods(n.text_content() if not isinstance(n, wx.lxml.etree._Comment) else "", mods, base_url, attr_name=None)


def apply_mods(s: str, mods: list[str], base_url: str, attr_name: str | None) -> Any:
    if "raw" not in mods:
        s = collapse(s)
    if "number" in mods or "int" in mods:
        m = NUM_RE.search(s)
        if not m:
            return None
        raw = m.group(0).replace("\u00a0", "").replace(" ", "").replace("'", "")
        if "," in raw and "." in raw:
            raw = raw.replace(",", "") if raw.rfind(".") > raw.rfind(",") else raw.replace(".", "").replace(",", ".")
        elif "," in raw:
            raw = raw.replace(",", ".") if re.search(r",\d{1,2}$", raw) else raw.replace(",", "")
        elif raw.count(".") > 1:
            raw = raw.replace(".", "")
        try:
            num = float(raw)
        except ValueError:
            return None
        if "int" in mods:
            return int(num)
        return int(num) if num.is_integer() and "." not in raw else num
    if "date" in mods:
        for i, rx in enumerate(DATE_RES):
            for m in rx.finditer(s):
                a, b, c = (int(x) for x in m.groups())
                if i == 1:  # d.m.yyyy / d/m/yyyy; a slash date that only works month-first (12/31/2023) is read as US
                    y, mo, d = (c, a, b) if "/" in m.group(0) and a <= 12 < b else (c, b, a)
                else:
                    y, mo, d = a, b, c
                try:
                    return datetime.date(y, mo, d).isoformat()
                except ValueError:
                    continue
        for i, rx in enumerate(WORD_DATE_RES):
            for m in rx.finditer(s):
                month, day = (m.group(1), m.group(2)) if i == 0 else (m.group(2), m.group(1))
                mi = MONTHS.get(month[:3].lower())
                if mi:
                    try:
                        return datetime.date(int(m.group(3)), mi, int(day)).isoformat()
                    except ValueError:
                        continue
        return None
    return s if s != "" else None


# --------------------------------------------------------------------- probe

def probe(tree: Any, limit: int = 15) -> list[dict[str, Any]]:
    """Repeating blocks: groups of 3+ sibling elements sharing tag + classes, ranked by count × text."""
    groups: dict[tuple, list[Any]] = {}
    for parent in tree.iter():
        if not isinstance(parent.tag, str):
            continue
        for child in parent:
            if not isinstance(child.tag, str) or child.tag in ("script", "style", "br", "hr", "option", "tr", "td", "th", "span", "b", "i", "em", "strong"):
                continue
            classes = tuple(sorted(c for c in (child.get("class") or "").split() if not re.search(r"\d{3,}|^(?:is|has)-|active|selected", c)))
            groups.setdefault((parent, child.tag, classes), []).append(child)
    rows: list[dict[str, Any]] = []
    for (parent, tag, classes), nodes in groups.items():
        if len(nodes) < 3:
            continue
        texts = [collapse(n.text_content()) for n in nodes]
        avg = sum(len(t) for t in texts) / len(texts)
        if avg < 15:
            continue
        sel = tag + "".join(f".{c}" for c in classes[:2])
        ptag = parent.tag + (f"#{parent.get('id')}" if parent.get("id") else "".join(f".{c}" for c in (parent.get("class") or "").split()[:1]))
        links = sum(1 for n in nodes[:5] for _ in n.iter("a"))
        rows.append({"selector": sel, "parent": ptag, "count": len(nodes), "avg_chars": round(avg),
                     "links_per_item": round(links / min(5, len(nodes)), 1), "sample": texts[0][:160],
                     "children": sorted({c.tag + "".join(f".{x}" for x in (c.get("class") or "").split()[:1]) for c in nodes[0] if isinstance(c.tag, str)})[:8]})
    rows.sort(key=lambda r: r["count"] * min(r["avg_chars"], 400), reverse=True)
    return rows[:limit]


# ------------------------------------------------------------------- scraping

class Scraper:
    def __init__(self, args: argparse.Namespace, fields: list[Field], detail_fields: list[Field]) -> None:
        self.args = args
        self.fields, self.detail_fields = fields, detail_fields
        self.session = wx.make_session(args)
        self.timeout = float(args.timeout)
        self.item = compile_selector(args.item) if args.item else None
        self.next_sel = compile_selector(args.next) if args.next else None
        self.pages_fetched = 0
        self.allow_private = False  # set per start URL in run()
        self.origin: dict[int, tuple[str, str]] = {}  # id(record) → (start URL, listing page URL)
        self.diag: dict[str, Any] = {"pages": [], "item_matches": 0, "field_hits": {f.name: 0 for f in fields},
                                     "detail_field_hits": {f.name: 0 for f in detail_fields}, "details_fetched": 0, "details_failed": 0,
                                     "rendered": 0, "notes": []}
        self.ns = argparse.Namespace(timeout=args.timeout, impersonate=args.impersonate, proxy=args.proxy, render=args.render,
                                     headful=False, profile=None, actions=None, action_steps=getattr(args, "action_steps", []),
                                     render_wait=args.render_wait, screenshot=None, raw=False)

    def get(self, url: str, render: bool = False, deadline: float | None = None) -> tuple[Any, wx.Diag | None]:
        """Fetch one page (HTTP or browser) → (Fetched, None) or (None, Diag). Block detection applied.

        With a deadline the request timeout shrinks to the remaining --max-time budget (at least 1 s)."""
        left = None if deadline is None else deadline - time.monotonic()
        timeout = self.timeout if left is None else max(1.0, min(self.timeout, left))
        if render:
            f = wx.render_fetch(url, argparse.Namespace(**{**vars(self.ns), "timeout": timeout}), self.ns.action_steps)
        else:  # no retry when a second attempt would cross the --max-time deadline
            f = wx.fetch(self.session, url, timeout=timeout, attempts=1 if left is not None and left < 2 * timeout else 2)
        if isinstance(f, wx.Diag):
            return None, f
        cls = wx.classify(f.status, f.headers, f.body[:wx.MARKER_SCAN_BYTES], f.url, len(f.body))
        if cls.error:
            return None, cls.error
        if f.status >= 400:
            return None, wx.Diag(EXIT_HTTP, "http_error", f"HTTP {f.status} at {wx.redact_url(f.url)}", f.status)
        if wx.gate(f.ctype, f.body[:4096]) != "html":
            return None, wx.Diag(EXIT_NOT_HTML, "non_html", f"content is {f.ctype or 'binary'}, not HTML, at {wx.redact_url(f.url)}", f.status)
        self.pages_fetched += 1
        if render:
            self.diag["rendered"] += 1
        return f, None

    def parse(self, f: Any) -> Any:
        tree = wx.load_html(f.body)
        if tree is None:
            try:
                tree = wx.lxml.html.fromstring(f.body or b"<html></html>")
            except (wx.lxml.etree.ParserError, ValueError):  # comment- or whitespace-only body
                tree = wx.lxml.html.fromstring(b"<html></html>")
        return tree

    def records_from_tree(self, tree: Any, page_url: str) -> list[dict[str, Any]]:
        a = self.args
        if a.json_ld is not None:
            return self.json_ld_records(tree, page_url)
        if a.table is not None:
            return self.table_records(tree, page_url)
        nodes = self.item(tree) if self.item else [tree]
        nodes = [n for n in nodes if hasattr(n, "tag")]
        self.diag["item_matches"] += len(nodes)
        out = []
        for n in nodes:
            rec: dict[str, Any] = {}
            for fld in self.fields:
                v = fld.extract(n, page_url)
                if v not in (None, [], ""):
                    self.diag["field_hits"][fld.name] += 1
                rec[fld.name] = v
            if not self.fields:  # --item alone: the text of each item
                rec["text"] = collapse(n.text_content())[:2000]
            if any(v not in (None, [], "") for v in rec.values()):
                out.append(rec)
        return out

    def json_ld_records(self, tree: Any, page_url: str) -> list[dict[str, Any]]:
        want = (self.args.json_ld or "").strip().lower()
        out: list[dict[str, Any]] = []

        def walk(obj: Any) -> None:
            if isinstance(obj, list):
                for x in obj:
                    walk(x)
            elif isinstance(obj, dict):
                types = obj.get("@type")
                types = [types] if isinstance(types, str) else (types or [])
                if types and (not want or any(str(t).lower().rsplit("/", 1)[-1] == want for t in types)):
                    out.append(obj)
                    if want:
                        return
                for k, v in obj.items():
                    if k in ("@graph", "itemListElement", "mainEntity", "hasPart", "item", "offers", "review"):
                        walk(v)
                    elif isinstance(v, (dict, list)) and not want:
                        pass
        for script in tree.iter("script"):
            if (script.get("type") or "").strip().lower() != "application/ld+json":
                continue
            text = re.sub(r"^\s*(?:<!--|//<!\[CDATA\[)|(?:-->|//\]\]>)\s*$", "", (script.text or "").strip())
            try:
                walk(json.loads(text, strict=False))
            except ValueError:
                continue
        self.diag["item_matches"] += len(out)
        if self.fields:  # optional: pick keys with name=key (dotted path) from each object
            picked = []
            for obj in out:
                rec = {f.name: json_value(dig(obj, f.raw_selector), f.mods, page_url) for f in self.fields}
                for f in self.fields:
                    if rec[f.name] not in (None, "", []):
                        self.diag["field_hits"][f.name] += 1
                picked.append(rec)
            return picked
        return out

    def table_records(self, tree: Any, page_url: str) -> list[dict[str, Any]]:
        tables = []
        for t in tree.iter("table"):
            rows, header = [], None
            for tr in t.iter("tr"):
                cells = [c for c in tr if c.tag in ("td", "th")]
                if not cells:
                    continue
                vals = [collapse(c.text_content()) for c in cells]
                if header is None and not rows and all(c.tag == "th" for c in cells):
                    header = vals
                else:
                    rows.append(vals)
            if len(rows) >= 1 and max((len(r) for r in rows), default=0) >= 2:
                tables.append((header, rows))
        sel = self.args.table
        chosen = tables if sel == "all" else tables[int(sel) - 1: int(sel)] if 0 < int(sel) <= len(tables) else []
        if not chosen and tables:
            self.diag["notes"].append(f"table {sel} not found; the page has {len(tables)} data table(s)")
        out = []
        for ti, (header, rows) in enumerate(chosen, 1):
            if header is None:
                header = [f"col{i + 1}" for i in range(max(len(r) for r in rows))]
            keys = [h or f"col{i + 1}" for i, h in enumerate(header)]
            for r in rows:
                rec = {keys[i] if i < len(keys) else f"col{i + 1}": v for i, v in enumerate(r)}
                if sel == "all":
                    rec["_table"] = ti
                out.append(rec)
        self.diag["item_matches"] += len(out)
        return out

    def next_url(self, tree: Any, page_url: str) -> str | None:
        cands: list[Any] = []
        if self.next_sel is not None:
            cands = self.next_sel(tree)
        else:
            cands = tree.xpath("//link[translate(@rel,'NEXT','next')='next']/@href | //a[translate(@rel,'NEXT','next')='next']/@href")
            if not cands:
                cands = tree.xpath("//a[contains(translate(normalize-space(.),'NEXT→»›','next→»›'),'next') or normalize-space(.)='→' or normalize-space(.)='»' or normalize-space(.)='›']/@href")
        for c in cands:
            href = c if isinstance(c, str) else c.get("href")
            if not href or href.strip().startswith(("#", "javascript:")):
                continue
            u, why = safe_link(page_url, href, self.allow_private)
            if u is None:  # note it and try the next candidate
                self.diag["notes"].append(f"next link {href.strip()[:120]!r} not followed: {why}")
                continue
            return u
        return None

    def run(self, starts: list[str]) -> tuple[list[dict[str, Any]], wx.Diag | None]:
        a = self.args
        records: list[dict[str, Any]] = []
        seen_pages: set[str] = set()
        deadline = time.monotonic() + float(a.max_time)
        start_error: wx.Diag | None = None
        for start in starts:
            url: str | None = start
            page_no = 0
            self.allow_private = wx.is_private_host(urlsplit(start).hostname)  # a public start never leads to local hosts
            while url and url not in seen_pages and page_no < a.max_pages and len(records) < a.limit and time.monotonic() < deadline:
                seen_pages.add(url)
                page_no += 1
                f, err = self.get(url, render=(a.render == "always"), deadline=deadline)
                if err:
                    if url == start and not records:
                        start_error = start_error or err
                    self.diag["pages"].append({"url": url, "ok": False, "error": err.as_dict()})
                    wx.note(f"page {page_no} {url}: {err.message}")
                    break
                tree = self.parse(f)
                recs = self.records_from_tree(tree, f.url)
                if not recs and a.render == "auto" and wx.page_signals(f.body).dynamic and wx.playwright_api()[0] is not None:
                    wx.note("no matches in the HTTP body and the page looks client-rendered; rendering it in a headless browser")
                    rf, rerr = self.get(url, render=True, deadline=deadline)
                    if rf is not None:
                        f, tree = rf, self.parse(rf)
                        recs = self.records_from_tree(tree, f.url)
                for r in recs:
                    self.origin[id(r)] = (start, f.url)  # for --follow: relative URLs and the private-host rule
                    if not a.no_meta:
                        r["_page"] = f.url
                records.extend(recs)
                self.diag["pages"].append({"url": url, "ok": True, "status": f.status, "records": len(recs)})
                wx.note(f"page {page_no}: {len(recs)} record(s) from {wx.redact_url(f.url)}")
                if a.next or a.max_pages > 1:
                    url = self.next_url(tree, f.url)
                    if url is None:
                        break
                else:
                    url = None
        if a.unique:
            seen_keys: set[str] = set()
            deduped = []
            for r in records:
                k = json.dumps(r.get(a.unique), ensure_ascii=False, sort_keys=True)
                if k in seen_keys and r.get(a.unique) not in (None, ""):
                    continue
                seen_keys.add(k)
                deduped.append(r)
            if len(deduped) != len(records):
                self.diag["notes"].append(f"unique {a.unique}: {len(records) - len(deduped)} duplicate(s) dropped")
            records = deduped
        records = records[: a.limit]
        if a.follow and self.detail_fields and records:
            self.fetch_details(records, deadline)
        return records, start_error

    def fetch_details(self, records: list[dict[str, Any]], deadline: float) -> None:
        a = self.args
        by_url: dict[str, list[int]] = {}  # one fetch per distinct URL, its result copied to every record that has it
        for i, r in enumerate(records[:MAX_DETAILS]):
            v = r.get(a.follow)
            if not isinstance(v, str) or not v.strip():
                continue
            start, page = self.origin.get(id(r), ("", ""))
            u, why = safe_link(page or v, v, wx.is_private_host(urlsplit(start).hostname) if start else False)
            if u is None:
                self.diag["details_failed"] += 1
                r["_detail_error"] = {"kind": "unsafe_url", "message": f"{v.strip()[:120]!r} not fetched: {why}"}
                continue
            by_url.setdefault(u, []).append(i)
        if len(records) > MAX_DETAILS:
            self.diag["notes"].append(f"details limited to the first {MAX_DETAILS} records")

        def one(url: str) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
            if time.monotonic() > deadline:
                return url, None, {"kind": "time_budget", "message": "time budget exhausted"}
            f, err = self.get(url, render=(a.render == "always"), deadline=deadline)
            if err:
                return url, None, {"kind": err.kind, "message": err.message}
            tree = self.parse(f)
            return url, {fld.name: fld.extract(tree, f.url) for fld in self.detail_fields}, None

        with ThreadPoolExecutor(max_workers=max(1, a.concurrency)) as pool:
            for url, rec, err in pool.map(one, list(by_url)):
                for i in by_url[url]:
                    if rec is None:
                        self.diag["details_failed"] += 1
                        records[i]["_detail_error"] = err
                        continue
                    self.diag["details_fetched"] += 1
                    for k, v in rec.items():
                        if v not in (None, "", []):
                            self.diag["detail_field_hits"][k] += 1
                        if records[i].get(k) in (None, "", []):  # listing values win over detail values
                            records[i][k] = v


def safe_link(base: str, href: str, allow_private: bool) -> tuple[str | None, str]:
    """Absolute http(s) URL for a page-supplied link, or (None, reason) for other schemes and,
    unless the run started on a private host, for loopback/LAN/internal hosts. Judged by host name
    or IP literal only: names are not resolved, so a public name pointing at a private address passes."""
    u = urljoin(base, href.strip())
    ps = urlsplit(u)
    if ps.scheme not in ("http", "https") or not ps.hostname:
        return None, "not an http(s) URL"
    if not allow_private and wx.is_private_host(ps.hostname):
        return None, "points at a private/local host"
    return u, ""


def json_value(v: Any, mods: list[str], base_url: str) -> Any:
    """A JSON-LD value with the text modifiers applied to scalars (`|number`, `|date`, …)."""
    mods = [m for m in mods if m not in ("list", "join", "html", "text")]
    if not mods or isinstance(v, (dict, list, bool)) or v is None:
        return v
    if isinstance(v, (int, float)) and ("number" in mods or "int" in mods):  # already numeric: no string round-trip
        return int(v) if "int" in mods else v
    return apply_mods(str(v), mods, base_url, attr_name=None)


def dig(obj: Any, path: str) -> Any:
    """Dotted path into a JSON object (`offers.price`, `image.0`)."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


# --------------------------------------------------------------------- output

def to_csv(records: list[dict[str, Any]]) -> str:
    keys: list[str] = []
    for r in records:
        for k in r:
            if k not in keys:
                keys.append(k)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in records:
        w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else ("" if v is None else v)) for k, v in r.items()})
    return buf.getvalue()


def to_markdown(records: list[dict[str, Any]]) -> str:
    keys: list[str] = []
    for r in records:
        for k in r:
            if k not in keys and not k.startswith("_"):
                keys.append(k)
    if not keys:
        return ""
    def cell(v: Any) -> str:
        s = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else ("" if v is None else str(v))
        return s.replace("|", "\\|").replace("\n", " ")[:200]
    lines = ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    lines += ["| " + " | ".join(cell(r.get(k)) for k in keys) + " |" for r in records]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("urls", nargs="+", metavar="URL", help="listing or detail page(s)")
    src = p.add_argument_group("what makes a record")
    src.add_argument("--item", metavar="SELECTOR", help="CSS/xpath: selector of the repeating container; one record per match (default: the page)")
    src.add_argument("--field", action="append", default=[], metavar="NAME=SEL", help="field spec, repeatable (see syntax above)")
    src.add_argument("--json-ld", metavar="TYPE", nargs="?", const="", help="records from JSON-LD objects of this schema.org @type (empty = every typed object); --field NAME=dotted.path picks keys")
    src.add_argument("--table", metavar="N|all", help="records from the page's N-th data table (1-based) or every table (`_table` column)")
    src.add_argument("--probe", action="store_true", help="list the page's repeating blocks (candidate --item selectors) and leave")
    nav = p.add_argument_group("pagination and detail pages")
    nav.add_argument("--next", metavar="SELECTOR", help="selector of the next-page link (default: rel=next, then a link reading next/→/»)")
    nav.add_argument("--max-pages", type=int, default=None, help="listing pages to follow (default 1; with --next default 20)")
    nav.add_argument("--follow", metavar="FIELD", help="open the URL in FIELD of every record and add --detail-field values")
    nav.add_argument("--detail-field", action="append", default=[], metavar="NAME=SEL", help="field taken from the followed detail page")
    nav.add_argument("--limit", type=int, default=MAX_RECORDS_DEFAULT, help=f"max records (default {MAX_RECORDS_DEFAULT})")
    nav.add_argument("--unique", metavar="FIELD", help="drop records whose FIELD repeats")
    nav.add_argument("--max-time", type=float, default=300.0, metavar="S", help="wall-clock budget (default 300)")
    nav.add_argument("--concurrency", type=int, default=4, help="parallel detail-page fetches (default 4)")
    out = p.add_argument_group("output")
    out.add_argument("-f", "--format", choices=["json", "jsonl", "csv", "markdown"], default="json")
    out.add_argument("-o", "--out", metavar="FILE")
    out.add_argument("--no-meta", action="store_true", help="omit the `_page` column")
    br = p.add_argument_group("fetching (via web-extract)")
    br.add_argument("--render", choices=["off", "auto", "always"], default="auto",
                    help="headless Chromium (needs Playwright): auto = when nothing matched on a client-rendered page, always = every page")
    br.add_argument("--actions", metavar="JSON", help="browser steps before reading each page (web-extract syntax), with --render")
    br.add_argument("--render-wait", type=float, default=0.0, metavar="S")
    br.add_argument("--timeout", type=float, default=30.0, metavar="S")
    br.add_argument("--impersonate", choices=["chrome", "firefox", "safari", "edge", "none"], default="chrome")
    br.add_argument("--proxy", metavar="URL")
    p.add_argument("-q", "--quiet", action="store_true")
    args = p.parse_args(argv)
    wx.QUIET = args.quiet

    if args.max_pages is None:
        args.max_pages = 20 if args.next else 1
    if args.max_pages < 1 or args.limit < 1 or args.max_time <= 0 or args.timeout <= 0 or args.concurrency < 1:
        sys.stderr.write("error: --max-pages, --limit, --concurrency ≥ 1; --max-time, --timeout > 0\n")
        return EXIT_BAD_INPUT
    if sum(x is not None for x in (args.json_ld, args.table)) + bool(args.item) > 1:
        sys.stderr.write("error: use one of --item, --json-ld, --table\n")
        return EXIT_BAD_INPUT
    if args.table is not None and args.table != "all" and not (args.table.isdigit() and int(args.table) >= 1):
        sys.stderr.write("error: --table takes a 1-based number or `all`\n")
        return EXIT_BAD_INPUT
    if args.follow and not args.detail_field:
        sys.stderr.write("error: --follow needs at least one --detail-field\n")
        return EXIT_BAD_INPUT
    if args.render == "off" and (args.actions or args.render_wait):
        sys.stderr.write("error: --actions/--render-wait need --render auto or always\n")
        return EXIT_BAD_INPUT
    steps = wx.parse_actions(args.actions)
    if isinstance(steps, str):
        sys.stderr.write(f"error: {steps}\n")
        return EXIT_BAD_INPUT
    args.action_steps = steps
    if args.render == "always" and wx.playwright_api()[0] is None:
        sys.stderr.write(f"error: {wx.PLAYWRIGHT_HINT}\n")
        return EXIT_BAD_INPUT
    starts = []
    for raw in args.urls:
        u = wx.normalize_url(raw)
        if u is None:
            sys.stderr.write(f"error: not an http(s) URL: {raw}\n")
            return EXIT_BAD_INPUT
        starts.append(u)
    try:
        fields = [Field(s, json_mode=args.json_ld is not None) for s in args.field]
        detail_fields = [Field(s) for s in args.detail_field]
        if args.follow and fields and args.follow not in {f.name for f in fields}:
            raise ValueError(f"--follow {args.follow!r} is not one of the fields")
        scraper = Scraper(args, fields, detail_fields)
    except ValueError as e:
        sys.stderr.write(f"error: {e}\n")
        return EXIT_BAD_INPUT
    if not args.probe and not args.item and not fields and args.json_ld is None and args.table is None:
        sys.stderr.write("error: say what a record is: --item/--field, --json-ld, --table, or --probe to explore\n")
        return EXIT_BAD_INPUT

    try:
        return scrape_main(args, scraper, starts, fields)
    except BadSelector as e:
        sys.stderr.write(f"error: {e}\n")
        return EXIT_BAD_INPUT
    except Exception as e:  # anything unexpected: one error line, exit 7
        sys.stderr.write(f"error: {type(e).__name__}: {e}\n")
        return EXIT_OTHER


def scrape_main(args: argparse.Namespace, scraper: Scraper, starts: list[str], fields: list[Field]) -> int:
    if args.probe:
        f, err = scraper.get(starts[0], render=(args.render == "always"))
        if err:
            sys.stderr.write(f"error: {err.message}\n")
            return err.code
        tree = scraper.parse(f)
        rows = probe(tree)
        if not rows and args.render == "auto" and wx.page_signals(f.body).dynamic and wx.playwright_api()[0] is not None:
            rf, rerr = scraper.get(starts[0], render=True)
            if rf is not None:
                tree, rows = scraper.parse(rf), probe(scraper.parse(rf))
        tables = sum(1 for _ in tree.iter("table"))
        ld = [s for s in tree.iter("script") if (s.get("type") or "").lower().strip() == "application/ld+json"]
        report = {"url": starts[0], "repeating_blocks": rows, "tables": tables, "json_ld_scripts": len(ld),
                  "hint": "pick a block, then --item '<parent> <selector>' with --field for its children; --table N for tables; --json-ld TYPE for schema.org data"}
        if args.format == "json":
            sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        else:
            sys.stdout.write(f"# probe: {starts[0]}\n# tables: {tables} · json-ld scripts: {len(ld)}\n\n")
            for r in rows:
                sys.stdout.write(f"{r['count']:>4} × {r['selector']}  (in {r['parent']}; ~{r['avg_chars']} chars, {r['links_per_item']} links/item)\n"
                                 f"       children: {', '.join(r['children'])}\n       sample: {r['sample']}\n")
            if not rows:
                sys.stdout.write("no repeating blocks with text found (client-rendered page? try --render always, or --table/--json-ld)\n")
        return EXIT_OK if rows or tables or ld else EXIT_EMPTY

    records, start_error = scraper.run(starts)
    d = scraper.diag
    summary = (f"{len(records)} record(s) from {sum(1 for pg in d['pages'] if pg['ok'])} page(s)"
               + (f", {d['rendered']} rendered" if d["rendered"] else "")
               + (f"; details {d['details_fetched']} ok / {d['details_failed']} failed" if args.follow else ""))
    if fields and args.table is None:
        misses = [f"{n} 0/{d['item_matches']}" for n, h in d["field_hits"].items() if h == 0]
        partial = [f"{n} {h}/{d['item_matches']}" for n, h in d["field_hits"].items() if 0 < h < d["item_matches"]]
        if misses:
            d["notes"].append("fields never matched: " + ", ".join(misses) + " (check the selector against web-extract -f html or --raw)")
        if partial:
            d["notes"].append("fields partially matched: " + ", ".join(partial))
    wx.note(summary)
    for n in d["notes"]:
        wx.note(n)
    code = EXIT_OK
    if not records:
        if start_error is not None:
            sys.stderr.write(f"error: {start_error.message}\n")
            code = start_error.code
        else:
            what = f"--item {args.item!r} matched {d['item_matches']} element(s)" if args.item else "nothing matched"
            sys.stderr.write(f"error: no records: {what}; use --probe to find the repeating block, web-extract -f html to inspect, "
                             f"or --render always for client-rendered pages\n")
            code = EXIT_EMPTY
    if args.format == "json":
        text = json.dumps({"count": len(records), "records": records, "pages": d["pages"], "stats": {k: v for k, v in d.items() if k != "pages"}},
                          ensure_ascii=False, indent=2) + "\n"
    elif args.format == "jsonl":
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    elif args.format == "csv":
        text = to_csv(records)
    else:
        text = to_markdown(records)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        sys.stdout.write(f"saved: {args.out} ({len(records)} records)\n")
    else:
        sys.stdout.write(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
