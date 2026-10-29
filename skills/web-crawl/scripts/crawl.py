#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["curl_cffi>=0.17,<1", "trafilatura>=2.3,<3", "lxml_html_clean", "pypdf>=5,<7"]
# ///
"""Crawl a site (or map its URLs) and collect every page's main content.

Breadth-first from one or more start URLs, staying on the same site, bounded by
depth, page count and wall-clock time, honouring robots.txt, seeded from
sitemap.xml when present. Each page is fetched and extracted by the web-extract
skill (same TLS fingerprint, block detection, PDF support and optional headless
rendering), so install that skill next to this one.

    uv run crawl.py https://docs.example.com/ -o docs.md            # one Markdown file
    uv run crawl.py https://docs.example.com/ --out-dir pages/      # one file per page + index.json
    uv run crawl.py https://docs.example.com/ -f jsonl -o pages.jsonl
    uv run crawl.py https://example.com/ --map                      # just the URLs

Exit codes (the start URL decides; later pages only appear in `failed`):
    0 at least one page collected (or mapped) · 1 nothing collected
    · 2 bad input / missing dependency or web-extract · 3 start URL HTTP error
    · 4 start URL blocked / robots.txt disallows it · 6 start URL network error · 7 other
"""

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import re
import sys
import threading
import time
import urllib.robotparser
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

EXIT_OK, EXIT_EMPTY, EXIT_BAD_INPUT, EXIT_HTTP, EXIT_BLOCKED, EXIT_NOT_HTML, EXIT_NETWORK, EXIT_OTHER = range(8)
MAX_SITEMAPS, MAX_SITEMAP_URLS = 20, 50_000
STDOUT_CAP = 60_000
SKIP_EXT = re.compile(r"\.(?:png|jpe?g|gif|webp|svg|ico|bmp|tiff?|mp[34]|m4[av]|webm|ogg|wav|flac|avi|mov|mkv|zip|tar|gz|tgz|bz2|7z|rar|"
                      r"exe|dmg|pkg|deb|rpm|msi|apk|iso|woff2?|ttf|otf|eot|css|js|mjs|map|json|xml|rss|atom|csv|xlsx?|docx?|pptx?|epub)$", re.I)
TRACKING_PARAMS = re.compile(r"^(?:utm_\w+|fbclid|gclid|yclid|mc_[ce]id|ref_src|igshid|_ga)$", re.I)


def load_web_extract() -> Any:
    """Import the web-extract skill's extract.py as a module (sibling skill, ~/.claude/skills, or $WEB_EXTRACT_SCRIPT)."""
    here = Path(__file__).resolve()
    candidates = [Path(os.environ["WEB_EXTRACT_SCRIPT"])] if os.environ.get("WEB_EXTRACT_SCRIPT") else []
    candidates += [here.parent.parent.parent / "web-extract" / "scripts" / "extract.py",  # skills/<name>/scripts layout
                   here.parent / "extract.py"]
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


# ------------------------------------------------------------------ url rules

def canonical(url: str) -> str:
    """Normalise a URL for deduplication: lower-case scheme/host, no fragment, no tracking params,
    default port and trailing `index.html` dropped, trailing slash kept as given.

    >>> canonical("HTTPS://Docs.Example.com:443/a/index.html?utm_source=x&b=1#top")
    'https://docs.example.com/a/?b=1'
    >>> canonical("http://example.com") , canonical("https://example.com/a/b?z=1&a=2")
    ('http://example.com/', 'https://example.com/a/b?a=2&z=1')
    """
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url
    host = (p.hostname or "").lower()
    if p.port and not ((p.scheme == "http" and p.port == 80) or (p.scheme == "https" and p.port == 443)):
        host = f"{host}:{p.port}"
    path = re.sub(r"/index\.html?$", "/", p.path) or "/"
    query = "&".join(sorted(kv for kv in p.query.split("&") if kv and not TRACKING_PARAMS.match(kv.split("=", 1)[0])))
    return urlunsplit((p.scheme.lower(), host, path, query, ""))


def site_key(host: str, subdomains: bool) -> str:
    """The comparable part of a host: registrable-ish domain when subdomains are allowed (`www.` always stripped)."""
    h = (host or "").lower().removeprefix("www.")
    if subdomains:
        parts = h.split(".")
        if len(parts) > 2 and parts[-2] in ("co", "com", "org", "net", "ac", "gov", "edu") and len(parts[-1]) == 2:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:]) if len(parts) > 2 else h
    return h


def page_links(body: bytes, base_url: str) -> list[str]:
    """Every absolute http(s) link target in the page (`<a href>`, `<area href>`, `<link rel=next/prev>`)."""
    try:
        tree = wx.lxml.html.fromstring(body)
    except Exception:
        return []
    base = base_url
    for b in tree.iter("base"):
        if b.get("href"):
            base = urljoin(base_url, b.get("href"))
            break
    out: list[str] = []
    seen: set[str] = set()
    for el in tree.iter("a", "area", "link"):
        href = (el.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        if el.tag == "link" and not {"next", "prev", "alternate"} & set((el.get("rel") or "").lower().split()):
            continue
        if el.tag == "link" and (el.get("type") or "").lower() not in ("", "text/html"):
            continue
        try:
            absu = urljoin(base, href)
            p = urlsplit(absu)
        except ValueError:
            continue
        if p.scheme not in ("http", "https") or not p.hostname:
            continue
        c = canonical(absu)
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def sitemap_urls(session: Any, start: str, robots_sitemaps: list[str], timeout: float) -> tuple[list[str], list[str]]:
    """URLs from robots.txt-declared sitemaps or /sitemap.xml (sitemap indexes followed, .gz accepted). Returns (urls, notes)."""
    p = urlsplit(start)
    queue = deque(robots_sitemaps or [f"{p.scheme}://{p.netloc}/sitemap.xml"])
    seen: set[str] = set()
    urls: list[str] = []
    notes: list[str] = []
    while queue and len(seen) < MAX_SITEMAPS and len(urls) < MAX_SITEMAP_URLS:
        sm = queue.popleft()
        if sm in seen:
            continue
        seen.add(sm)
        f = wx.fetch(session, sm, timeout=timeout, attempts=1)
        if isinstance(f, wx.Diag) or f.status != 200:
            notes.append(f"sitemap {sm}: {f.message if isinstance(f, wx.Diag) else 'HTTP ' + str(f.status)}")
            continue
        body = f.body
        if sm.endswith(".gz") or body[:2] == b"\x1f\x8b":
            try:
                body = gzip.decompress(body)
            except OSError:
                notes.append(f"sitemap {sm}: not gzip")
                continue
        try:
            tree = wx.lxml.etree.fromstring(body, parser=wx.lxml.etree.XMLParser(recover=True, huge_tree=True))
        except Exception:
            tree = None
        if tree is None:
            notes.append(f"sitemap {sm}: unparsable")
            continue
        tag = wx.lxml.etree.QName(tree).localname.lower()
        locs = [el.text.strip() for el in tree.iter() if wx.lxml.etree.QName(el).localname.lower() == "loc" and el.text and el.text.strip()]
        if tag == "sitemapindex":
            queue.extend(locs)
        else:
            urls.extend(locs)
    return urls[:MAX_SITEMAP_URLS], notes


# ---------------------------------------------------------------------- crawl

class Crawler:
    def __init__(self, args: argparse.Namespace, starts: list[str]) -> None:
        self.args = args
        self.starts = starts
        self.session = wx.make_session(args)
        self.timeout = float(args.timeout)
        self.subdomains = args.allow_subdomains
        self.sites = {site_key(urlsplit(u).hostname or "", self.subdomains) for u in starts}
        self.include = [re.compile(x) for x in (args.include or [])]
        self.exclude = [re.compile(x) for x in (args.exclude or [])]
        self.robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.robots_sitemaps: dict[str, list[str]] = {}
        self.crawl_delay = float(args.delay)
        self.lock = threading.Lock()
        self.last_request = 0.0
        self.seen: set[str] = set()
        self.pages: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []
        self.skipped: dict[str, int] = {}
        self.skipped_examples: dict[str, list[str]] = {}
        self.content_hashes: set[str] = set()
        self.notes: list[str] = []
        self.deadline = time.monotonic() + float(args.max_time)
        self.extract_ns = argparse.Namespace(
            format="markdown" if args.format != "text" else "text", out=None, links=args.links, raw=False, no_header=True,
            max_chars=0, comments=False, recall=False, precision=False, timeout=args.timeout, impersonate=args.impersonate,
            proxy=args.proxy, wayback=False, no_wayback=True, jina=False, quiet=True, render=args.render, headful=False,
            profile=None, actions=None, action_steps=[], render_wait=0.0, screenshot=None, structured=args.structured, track=False)

    # robots ---------------------------------------------------------------
    def robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        with self.lock:
            if origin in self.robots:
                return self.robots[origin]
        rp: urllib.robotparser.RobotFileParser | None = None
        sitemaps: list[str] = []
        if not self.args.no_robots:
            f = wx.fetch(self.session, origin + "/robots.txt", timeout=self.timeout, attempts=1)
            if isinstance(f, wx.Fetched) and f.status == 200 and wx.gate(f.ctype, f.body[:4096]) != "binary":
                text = f.body[:512_000].decode("utf-8", "replace")
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(text.splitlines())
                sitemaps = [ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.lower().startswith("sitemap:")]
                delay = rp.crawl_delay(wx.UA.split("/")[0]) or rp.crawl_delay("*")
                if delay:
                    self.crawl_delay = max(self.crawl_delay, min(float(delay), 10.0))
            elif isinstance(f, wx.Fetched) and f.status in (401, 403):
                rp = urllib.robotparser.RobotFileParser()
                rp.disallow_all = True
                self.notes.append(f"robots.txt answered HTTP {f.status}: treated as disallow-all (RFC 9309)")
        with self.lock:
            self.robots[origin] = rp
            self.robots_sitemaps[origin] = sitemaps
        return rp

    def allowed_by_robots(self, url: str) -> bool:
        rp = self.robots_for(url)
        if rp is None:
            return True
        return rp.can_fetch(wx.UA.split("/")[0], url) and rp.can_fetch(wx.UA, url)

    # filtering ------------------------------------------------------------
    def skip(self, reason: str, url: str) -> None:
        with self.lock:
            self.skipped[reason] = self.skipped.get(reason, 0) + 1
            ex = self.skipped_examples.setdefault(reason, [])
            if len(ex) < 3:
                ex.append(url)

    def eligible(self, url: str, depth: int) -> str | None:
        """None when the URL should be fetched, else the skip reason."""
        p = urlsplit(url)
        if site_key(p.hostname or "", self.subdomains) not in self.sites:
            return "external"
        if SKIP_EXT.search(p.path):
            return "binary_extension"
        if self.exclude and any(r.search(url) for r in self.exclude):
            return "excluded"
        if self.include and not any(r.search(url) for r in self.include):
            return "not_included"
        if depth > self.args.depth:
            return "too_deep"
        return None

    # fetching -------------------------------------------------------------
    def politeness(self) -> None:
        if self.crawl_delay <= 0:
            return
        with self.lock:
            wait = self.last_request + self.crawl_delay - time.monotonic()
            self.last_request = max(time.monotonic(), self.last_request + self.crawl_delay)
        if wait > 0:
            time.sleep(wait)

    def fetch_page(self, url: str, depth: int, parent: str | None) -> dict[str, Any]:
        """Fetch + extract one page through web-extract; returns a crawl record (ok or failed) with discovered links."""
        rec: dict[str, Any] = {"url": url, "depth": depth, "parent": parent, "links": [], "ok": False}
        if not self.allowed_by_robots(url):
            rec["error"] = {"code": EXIT_BLOCKED, "kind": "robots_disallow", "message": "disallowed by robots.txt"}
            return rec
        self.politeness()
        a = self.extract_ns
        f = wx.render_fetch(url, a, []) if a.render == "always" else wx.fetch(self.session, url, timeout=self.timeout)
        if isinstance(f, wx.Diag):
            rec["error"] = f.as_dict()
            return rec
        result = wx.new_result(url)
        result["final_url"], result["status"], result["content_type"] = wx.redact_url(f.url), f.status, f.ctype or None
        cls = wx.classify(f.status, f.headers, f.body[:wx.MARKER_SCAN_BYTES], f.url, len(f.body))
        if cls.error:
            rec["error"] = cls.error.as_dict()
            rec["status"] = f.status
            return rec
        body = f.body
        if self.args.map_only:
            rec.update(ok=True, status=f.status, final_url=wx.redact_url(f.url), title=wx.html_title(body))
        else:
            err = wx.handle_body(f, result, a, "live")
            if err and err.code == EXIT_EMPTY and a.render == "auto" and wx.gate(f.ctype, body[:4096]) == "html":
                pw, _ = wx.playwright_api()
                if pw is not None:
                    rf = wx.render_fetch(url, a, [])
                    if isinstance(rf, wx.Fetched) and not wx.classify(rf.status, rf.headers, rf.body[:wx.MARKER_SCAN_BYTES], rf.url, len(rf.body)).error:
                        trial = wx.new_result(url)
                        trial["final_url"], trial["status"], trial["content_type"] = wx.redact_url(rf.url), rf.status, rf.ctype or None
                        if wx.handle_body(rf, trial, a, "browser") is None:
                            result, err, body = trial, None, rf.body
            if err:
                rec["error"] = err.as_dict()
                rec["status"] = f.status
                rec["title"] = result.get("title")
            else:
                result = wx.finish(result, a)
                h = hashlib.sha1((result["content"] or "").encode("utf-8")).hexdigest()
                with self.lock:
                    dup = h in self.content_hashes and bool(result["content"])
                    self.content_hashes.add(h)
                if dup:
                    rec["error"] = {"code": 0, "kind": "duplicate_content", "message": "same text as an earlier page"}
                else:
                    rec.update(ok=True, **{k: result[k] for k in ("final_url", "status", "source", "content_type", "title", "author", "date",
                                                                   "description", "language", "content", "content_format", "words",
                                                                   "pages", "coverage", "warnings")})
                    if a.links:
                        rec["content_links"] = result["links"]
                    if a.structured:
                        rec["structured"] = result["structured"]
        if wx.gate(f.ctype, body[:4096]) == "html":
            rec["links"] = page_links(body, f.url)
        rec["final_url"] = wx.redact_url(f.url)
        return rec

    # main loop ------------------------------------------------------------
    def run(self) -> int:
        """BFS with a thread pool; returns the start URL's exit code (0 when something was collected)."""
        args = self.args
        frontier: deque[tuple[str, int, str | None]] = deque()
        for u in self.starts:
            c = canonical(u)
            self.seen.add(c)
            frontier.append((c, 0, None))
        if not args.no_sitemap:
            for u in self.starts:
                self.robots_for(u)
                origin = f"{urlsplit(u).scheme}://{urlsplit(u).netloc}"
                urls, notes = sitemap_urls(self.session, u, self.robots_sitemaps.get(origin, []), self.timeout)
                self.notes.extend(notes)
                added = 0
                for su in urls:
                    c = canonical(su)
                    if c in self.seen:
                        continue
                    reason = self.eligible(c, 1)
                    if reason:
                        self.skip(reason, c)
                        continue
                    self.seen.add(c)
                    frontier.append((c, 1, "sitemap"))
                    added += 1
                if urls:
                    self.notes.append(f"sitemap: {len(urls)} URLs listed, {added} queued")
        start_code = EXIT_OK
        in_flight: dict[Any, tuple[str, int, str | None]] = {}
        stop_reason: str | None = None
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            while (frontier or in_flight) and stop_reason is None:
                while frontier and len(in_flight) < max(1, args.concurrency) and len(self.pages) + len(in_flight) < args.max_pages:
                    url, depth, parent = frontier.popleft()
                    in_flight[pool.submit(self.fetch_page, url, depth, parent)] = (url, depth, parent)
                if not in_flight:
                    if frontier:
                        stop_reason = "max_pages"
                    break
                done = next(as_completed(list(in_flight)))
                url, depth, parent = in_flight.pop(done)
                try:
                    rec = done.result()
                except Exception as e:  # keep the crawl alive; the start URL's failure still sets the exit code
                    rec = {"url": url, "depth": depth, "parent": parent, "links": [], "ok": False,
                           "error": {"code": EXIT_OTHER, "kind": "other", "message": f"{type(e).__name__}: {e}"}}
                if rec["ok"]:
                    self.pages.append(rec)
                    wx.note(f"[{len(self.pages)}/{args.max_pages}] d{depth} {rec.get('words', '-')}w {rec['url']}")
                else:
                    err = rec.get("error") or {}
                    if err.get("kind") == "duplicate_content":
                        self.skip("duplicate_content", url)
                    else:
                        self.failed.append({k: rec.get(k) for k in ("url", "depth", "parent", "status", "error")})
                        wx.note(f"failed d{depth} {url}: {err.get('message', '?')}")
                    if depth == 0 and parent is None and err.get("kind") != "duplicate_content":
                        start_code = err.get("code") or EXIT_OTHER
                if rec["ok"] or (rec.get("error") or {}).get("kind") == "duplicate_content":
                    for link in rec.get("links", []):
                        if link in self.seen:
                            continue
                        reason = self.eligible(link, depth + 1)
                        if reason:
                            self.skip(reason, link)
                            continue
                        self.seen.add(link)
                        frontier.append((link, depth + 1, url))
                if time.monotonic() > self.deadline:
                    stop_reason = "max_time"
                if len(self.pages) >= args.max_pages and not in_flight:
                    stop_reason = "max_pages" if frontier else None
                    break
            for fut in in_flight:  # a stop condition hit: do not wait for the whole queue, but keep what finished
                fut.cancel()
        if stop_reason:
            self.notes.append(f"stopped: {stop_reason} ({len(frontier)} URLs still queued)")
        self.pages.sort(key=lambda r: (r["depth"], r["url"]))
        if not self.pages:
            return start_code if start_code != EXIT_OK else EXIT_EMPTY
        return EXIT_OK


# --------------------------------------------------------------------- output

def slug_for(url: str) -> str:
    p = urlsplit(url)
    path = re.sub(r"[^A-Za-z0-9._-]+", "-", (p.path.strip("/") or "index") + (("-" + p.query) if p.query else "")).strip("-")
    return (path[:120] or "index") + ".md"


def page_markdown(r: dict[str, Any]) -> str:
    head = [f"## {wx.clean_field(r.get('title')) or r['url']}", "", f"<!-- url: {r.get('final_url') or r['url']} · depth {r['depth']} · "
            f"{r.get('words', 0)} words · source {r.get('source')}" + (f" · date {r['date']}" if r.get("date") else "") + " -->"]
    if r.get("warnings"):
        head.append(f"<!-- warnings: {', '.join(r['warnings'])} -->")
    return "\n".join(head) + "\n\n" + (r.get("content") or "")


def write_outputs(cr: Crawler, args: argparse.Namespace, code: int) -> None:
    stats = {"pages": len(cr.pages), "failed": len(cr.failed), "skipped": cr.skipped, "skipped_examples": cr.skipped_examples,
             "words": sum(r.get("words") or 0 for r in cr.pages), "notes": cr.notes, "exit_code": code,
             "elapsed_s": round(float(args.max_time) - max(0.0, cr.deadline - time.monotonic()), 1)}
    if args.map_only:
        urls = [r.get("final_url") or r["url"] for r in cr.pages]
        if args.format in ("json", "jsonl"):
            text = json.dumps({"start": cr.starts, "count": len(urls), "urls": urls,
                               "titles": {r["url"]: r.get("title") for r in cr.pages}, "failed": cr.failed, "stats": stats},
                              ensure_ascii=False, indent=2) + "\n"
        else:
            text = "\n".join(urls) + ("\n" if urls else "")
        emit(text, args)
        return
    if args.out_dir:
        d = Path(args.out_dir)
        d.mkdir(parents=True, exist_ok=True)
        index = []
        used: set[str] = set()
        for r in cr.pages:
            name = slug_for(r["url"])
            while name in used:
                name = name[:-3] + "-" + hashlib.sha1(r["url"].encode()).hexdigest()[:6] + ".md"
            used.add(name)
            (d / name).write_text("\n".join(wx.header_lines({**r, "status": r.get("status"), "content_format": r.get("content_format", "markdown"),
                                                             "language": r.get("language"), "live_error": None, "snapshot": None})) + "\n\n"
                                  + (r.get("content") or "") + "\n", encoding="utf-8", newline="\n")
            index.append({"file": name, **{k: r.get(k) for k in ("url", "final_url", "title", "date", "words", "depth", "parent", "source", "warnings")}})
        (d / "index.json").write_text(json.dumps({"start": cr.starts, "pages": index, "failed": cr.failed, "stats": stats},
                                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sys.stdout.write(f"saved: {len(cr.pages)} pages to {d} (index.json, {stats['words']} words, {len(cr.failed)} failed)\n")
        return
    if args.format == "json":
        pages = [{k: v for k, v in r.items() if k != "links"} for r in cr.pages]
        text = json.dumps({"start": cr.starts, "count": len(pages), "pages": pages, "failed": cr.failed, "stats": stats}, ensure_ascii=False, indent=2) + "\n"
    elif args.format == "jsonl":
        text = "".join(json.dumps({k: v for k, v in r.items() if k != "links"}, ensure_ascii=False) + "\n" for r in cr.pages)
        text += json.dumps({"stats": stats, "failed": cr.failed}, ensure_ascii=False) + "\n"
    else:
        parts = [f"# Crawl of {', '.join(cr.starts)}", "",
                 f"<!-- {len(cr.pages)} pages · {stats['words']} words · {len(cr.failed)} failed · skipped {json.dumps(cr.skipped)} -->",
                 "", "## Pages", ""] + [f"- [{wx.clean_field(r.get('title')) or r['url']}]({r.get('final_url') or r['url']}) — {r.get('words', 0)} words" for r in cr.pages]
        if cr.failed:
            parts += ["", "## Failed", ""] + [f"- {f['url']} — {f['error']['kind']}: {f['error']['message']}" for f in cr.failed]
        text = "\n".join(parts) + "\n\n---\n\n" + "\n\n---\n\n".join(page_markdown(r) for r in cr.pages) + "\n"
    emit(text, args)


def emit(text: str, args: argparse.Namespace) -> None:
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        sys.stdout.write(f"saved: {args.out} ({len(text)} chars)\n")
    elif args.format not in ("json", "jsonl") and len(text) > STDOUT_CAP and not args.map_only:
        cut = text[:STDOUT_CAP].rsplit("\n", 1)[0]
        sys.stdout.write(cut + f"\n\n[truncated: showed {len(cut)} of {len(text)} chars — rerun with -o FILE or --out-dir DIR]\n")
        wx.note(f"output truncated to {STDOUT_CAP} chars ({len(text)} total) — rerun with -o FILE or --out-dir DIR")
    else:
        sys.stdout.write(text)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("urls", nargs="+", metavar="URL", help="start URL(s); the crawl stays on their site(s)")
    p.add_argument("--depth", type=int, default=2, help="link depth from the start URL (default 2; sitemap URLs count as depth 1)")
    p.add_argument("--max-pages", type=int, default=50, help="stop after this many collected pages (default 50)")
    p.add_argument("--max-time", type=float, default=300.0, metavar="S", help="wall-clock budget in seconds (default 300)")
    p.add_argument("--include", action="append", metavar="REGEX", help="only URLs matching (repeatable; any match)")
    p.add_argument("--exclude", action="append", metavar="REGEX", help="skip URLs matching (repeatable; checked first)")
    p.add_argument("--allow-subdomains", action="store_true", help="follow links to other subdomains of the same domain")
    p.add_argument("--no-sitemap", action="store_true", help="do not seed the frontier from sitemap.xml")
    p.add_argument("--no-robots", action="store_true", help="ignore robots.txt (only when the user owns the site or asked explicitly)")
    p.add_argument("--delay", type=float, default=0.0, metavar="S", help="minimum seconds between requests (robots Crawl-delay raises it, max 10)")
    p.add_argument("--concurrency", type=int, default=4, help="parallel fetches (default 4)")
    p.add_argument("--map", dest="map_only", action="store_true", help="only discover URLs (and titles); no extraction")
    p.add_argument("-f", "--format", choices=["markdown", "text", "json", "jsonl"], default="markdown")
    p.add_argument("-o", "--out", metavar="FILE", help="write everything to one file")
    p.add_argument("--out-dir", metavar="DIR", help="write one Markdown file per page plus index.json")
    p.add_argument("--links", action="store_true", help="keep in-content links in each page's Markdown / `content_links`")
    p.add_argument("--structured", action="store_true", help="add web-extract's `structured` (JSON-LD, OpenGraph, tables) per page (JSON formats)")
    p.add_argument("--render", choices=["off", "auto", "always"], default="off",
                   help="headless Chromium per page via web-extract (needs Playwright): auto = only JavaScript shells, always = every page (slow)")
    p.add_argument("--timeout", type=float, default=30.0, metavar="S", help="per request (default 30)")
    p.add_argument("--impersonate", choices=["chrome", "firefox", "safari", "edge", "none"], default="chrome")
    p.add_argument("--proxy", metavar="URL")
    p.add_argument("-q", "--quiet", action="store_true", help="suppress per-page note: progress lines")
    args = p.parse_args(argv)
    wx.QUIET = args.quiet
    if args.depth < 0 or args.max_pages < 1 or args.max_time <= 0 or args.timeout <= 0 or args.concurrency < 1 or args.delay < 0:
        sys.stderr.write("error: --depth ≥ 0, --max-pages ≥ 1, --max-time, --timeout > 0, --concurrency ≥ 1, --delay ≥ 0\n")
        return EXIT_BAD_INPUT
    if args.out and args.out_dir:
        sys.stderr.write("error: use either -o FILE or --out-dir DIR\n")
        return EXIT_BAD_INPUT
    starts: list[str] = []
    for raw in args.urls:
        u = wx.normalize_url(raw)
        if u is None:
            sys.stderr.write(f"error: not an http(s) URL: {raw}\n")
            return EXIT_BAD_INPUT
        starts.append(u)
    try:
        for rx in (args.include or []) + (args.exclude or []):
            re.compile(rx)
    except re.error as e:
        sys.stderr.write(f"error: bad regex {e.pattern!r}: {e}\n")
        return EXIT_BAD_INPUT
    if args.render != "off" and wx.playwright_api()[0] is None:
        sys.stderr.write(f"error: --render needs Playwright: {wx.PLAYWRIGHT_HINT}\n")
        return EXIT_BAD_INPUT
    try:
        cr = Crawler(args, starts)
        code = cr.run()
    except KeyboardInterrupt:
        sys.stderr.write("error: interrupted\n")
        return EXIT_OTHER
    for n in cr.notes:
        wx.note(n)
    if code != EXIT_OK and cr.failed and cr.failed[0]["depth"] == 0:
        e = cr.failed[0]["error"]
        sys.stderr.write(f"error: start URL failed ({e['kind']}): {e['message']}\n")
    elif code == EXIT_EMPTY:
        sys.stderr.write("error: no page could be collected\n")
    write_outputs(cr, args, code)
    return code


if __name__ == "__main__":
    sys.exit(main())
