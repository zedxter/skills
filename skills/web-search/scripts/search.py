#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["ddgs>=9,<10", "curl_cffi>=0.17,<1", "trafilatura>=2.3,<3", "lxml_html_clean", "pypdf>=5,<7"]
# ///
"""Search the web without an API key and, optionally, read the top results.

Results come from the `ddgs` metasearch library (DuckDuckGo, Bing, Brave,
Google, Yahoo, Wikipedia … backends, chosen automatically) or from a SearXNG
instance the user runs (--searx URL). --read N fetches the first N results
through the web-extract skill and appends each page's main content, so one call
answers "search and tell me what they say".

    uv run search.py "trafilatura vs readability"
    uv run search.py "uv python package manager" -n 5 --time m --site docs.astral.sh
    uv run search.py "openai devday" --news --time d
    uv run search.py "best static site generators 2026" --read 3 -f markdown
    uv run search.py "query" -f json

Exit codes: 0 results · 1 no results · 2 bad input / missing dependency
    · 4 search engine rate-limited or blocked · 6 network / timeout · 7 other
"""

import argparse
import importlib.util
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

EXIT_OK, EXIT_EMPTY, EXIT_BAD_INPUT, EXIT_HTTP, EXIT_BLOCKED, EXIT_NOT_HTML, EXIT_NETWORK, EXIT_OTHER = range(8)
TRACKING_PARAMS = re.compile(r"^(?:utm_\w+|fbclid|gclid|yclid|mc_[ce]id|ref_src|igshid|_ga|ved|usg|sa|ei)$", re.I)
TIME_MAP = {"d": "d", "day": "d", "w": "w", "week": "w", "m": "m", "month": "m", "y": "y", "year": "y"}
BACKENDS = ["auto", "duckduckgo", "bing", "brave", "google", "yahoo", "wikipedia", "yandex", "mojeek", "grokipedia"]
NEWS_BACKENDS = ("auto", "duckduckgo", "bing", "yahoo")  # ddgs's news category; others silently fall back to auto
REGION_DEFAULT = "us-en"
AUTO_FALLBACK_TEXT = ("bing", "duckduckgo")  # tried in order when ddgs "auto" finds nothing (text search only)  # ddgs's own default; "wt-wt" breaks the wikipedia/google/bing legs of ddgs 9
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
READ_CHARS_DEFAULT = 3000


def clean_url(url: str) -> str:
    """Drop fragments and tracking parameters; keep everything else as the engine gave it.
    Unparseable URLs come back empty (and are dropped by normalise()).

    >>> clean_url("https://a.example/p?utm_source=x&id=2#frag")
    'https://a.example/p?id=2'
    >>> clean_url("http://[::1/x")
    ''
    """
    try:
        p = urlsplit(url.strip())
        p.hostname  # noqa: B018 — raises ValueError on a malformed netloc / port
        p.port  # noqa: B018
    except ValueError:
        return ""
    query = "&".join(kv for kv in p.query.split("&") if kv and not TRACKING_PARAMS.match(kv.split("=", 1)[0]))
    return urlunsplit((p.scheme, p.netloc, p.path, query, ""))


def host_of(url: str) -> str:
    """
    >>> host_of("https://WWW.Example.com/x"), host_of("http://[::1/x")
    ('example.com', '')
    """
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def clean_text(value: Any) -> str | None:
    """Drop C0/C1 control characters (terminal escapes) and collapse whitespace.

    >>> clean_text("Admin \\x1b[31mRED\\x1b[0m  console\\n"), clean_text("  ")
    ('Admin [31mRED[0m console', None)
    """
    s = " ".join(CONTROL_CHARS.sub("", str(value or "")).split())
    return s or None


def build_query(args: argparse.Namespace) -> str:
    q = " ".join(args.query).strip()
    for s in args.site or []:
        q += f" site:{s}"
    for s in args.exclude_site or []:
        q += f" -site:{s}"
    return q


def normalise(items: list[dict[str, Any]], news: bool) -> list[dict[str, Any]]:
    """Common result shape: {title, url, snippet, host, date?, source?}; deduped by cleaned URL."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in items:
        url = clean_url(str(it.get("href") or it.get("url") or ""))
        if not url.startswith(("http://", "https://")) or not host_of(url):
            continue
        key = url.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        rec: dict[str, Any] = {"title": clean_text(it.get("title")), "url": url,
                               "snippet": clean_text(it.get("body") or it.get("content") or it.get("snippet")),
                               "host": host_of(url)}
        if news:
            date = clean_text(it.get("date") or it.get("publishedDate")) or ""
            rec["date"] = date[:10] if re.match(r"\d{4}-\d{2}-\d{2}", date) else (date or None)
            rec["source"] = clean_text(it.get("source"))
        out.append(rec)
    return out


# -------------------------------------------------------------------- engines

def ddgs_engine(query: str, args: argparse.Namespace) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS
        from ddgs import exceptions as dx
    except ImportError as e:
        raise RuntimeError(f"missing dependency ddgs ({e}); pip install ddgs, or run with uv run") from e
    kwargs: dict[str, Any] = {"region": args.region or REGION_DEFAULT, "safesearch": args.safesearch, "max_results": args.max_results}
    if args.time:
        kwargs["timelimit"] = TIME_MAP[args.time]
    if args.backend != "auto":
        kwargs["backend"] = args.backend
    d = DDGS(proxy=args.proxy, timeout=args.timeout)
    try:
        found = d.news(query, **kwargs) if args.news else d.text(query, **kwargs)
    except dx.DDGSException as e:
        found = _map_ddgs_error(e, dx)  # raises EngineBlocked/EngineNetwork; [] only for "No results"
    if found or args.backend != "auto" or args.news:
        return found
    # ddgs "auto" samples a few providers and can land only on ones with nothing (e.g. wikipedia):
    # on a genuine "no results" ask the two big general indexes directly before giving up.
    for backend in AUTO_FALLBACK_TEXT:
        try:
            found = d.text(query, **{**kwargs, "backend": backend})
        except dx.DDGSException:
            continue
        if found:
            args.backend_used = backend
            return found
    return []


def _map_ddgs_error(e: Exception, dx: Any) -> list[dict[str, Any]]:
    """Translate a ddgs exception into EngineBlocked/EngineNetwork, [] for "no results", or re-raise."""
    try:
        raise e
    except dx.RatelimitException as e:
        raise EngineBlocked(f"search backend rate-limited: {str(e).splitlines()[0][:200]}") from e
    except dx.TimeoutException as e:
        raise EngineNetwork(f"search timed out: {str(e).splitlines()[0][:200]}") from e
    except dx.DDGSException as e:
        msg = str(e).splitlines()[0] if str(e) else type(e).__name__
        if "No results" in msg or "no results" in msg:
            return []
        if re.search(r"403|429|captcha|blocked|ratelimit", msg, re.I):
            raise EngineBlocked(f"search backend blocked the request: {msg[:200]}") from e
        if re.search(r"timeout|connect|resolve|network|ssl", msg, re.I):
            raise EngineNetwork(f"search network error: {msg[:200]}") from e
        raise


def searx_engine(query: str, args: argparse.Namespace) -> list[dict[str, Any]]:
    """SearXNG JSON API on the user's own instance (format=json must be enabled there)."""
    try:
        from curl_cffi import requests
    except ImportError as e:
        raise RuntimeError(f"missing dependency curl_cffi ({e}); pip install curl_cffi, or run with uv run") from e
    params = {"q": query, "format": "json", "pageno": 1, "safesearch": {"off": 0, "moderate": 1, "on": 2}[args.safesearch]}
    if args.time:
        params["time_range"] = {"d": "day", "w": "week", "m": "month", "y": "year"}[TIME_MAP[args.time]]
    if args.news:
        params["categories"] = "news"
    lang = args.region.split("-")[-1].lower() if args.region else None  # only when --region was given
    if lang and lang != "wt":
        params["language"] = lang
    base = args.searx.rstrip("/")
    try:
        r = requests.get(f"{base}/search?{urlencode(params)}", timeout=args.timeout, proxy=args.proxy, impersonate="chrome")
    except Exception as e:
        raise EngineNetwork(f"SearXNG unreachable: {str(e).splitlines()[0][:200]}") from e
    if r.status_code == 429 or r.status_code == 403:
        raise EngineBlocked(f"SearXNG answered HTTP {r.status_code} (rate limit, bot protection, or JSON format disabled)")
    if r.status_code != 200:
        raise RuntimeError(f"SearXNG answered HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError as e:
        raise SearxConfigError("SearXNG returned no JSON (enable `formats: [html, json]` in its settings)") from e
    # SearXNG exposes no publisher field: news `source` is the result's host, like ddgs's publisher name
    return [{"title": x.get("title"), "href": x.get("url"), "body": x.get("content"), "date": x.get("publishedDate"),
             "source": host_of(str(x.get("url") or "")) or None}
            for x in data.get("results", []) if isinstance(x, dict)][: args.max_results]


class EngineBlocked(RuntimeError):
    pass


class EngineNetwork(RuntimeError):
    pass


class SearxConfigError(RuntimeError):
    pass


# -------------------------------------------------------------------- reading

def load_web_extract() -> Any | None:
    here = Path(__file__).resolve()
    candidates = [Path(os.environ["WEB_EXTRACT_SCRIPT"])] if os.environ.get("WEB_EXTRACT_SCRIPT") else []
    candidates += [here.parent.parent.parent / "web-extract" / "scripts" / "extract.py", here.parent / "extract.py"]
    # The skill folders agents install into: the agentskills.io layout first, then agents' own folders.
    for root in (Path.cwd() / ".agents" / "skills", Path.home() / ".agents" / "skills",
                 Path.home() / ".claude" / "skills", Path.cwd() / ".claude" / "skills",  # agent-dirs
                 Path.home() / ".codex" / "skills", Path.home() / ".gemini" / "skills",  # agent-dirs
                 Path.home() / ".config" / "opencode" / "skills"):  # agent-dirs
        candidates.append(root / "web-extract" / "scripts" / "extract.py")
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("web_extract", path)
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            sys.modules["web_extract"] = mod
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            return mod
    return None


def read_results(results: list[dict[str, Any]], args: argparse.Namespace, wx: Any) -> None:
    """Fetch the first --read results through web-extract and attach `content` (capped) + `read` status."""
    ns = argparse.Namespace(format="text" if args.format == "text" else "markdown", out=None, links=False, raw=False, no_header=True,
                            max_chars=0, comments=False, recall=False, precision=False, timeout=args.timeout, impersonate="chrome",
                            proxy=args.proxy, wayback=False, no_wayback=False, jina=False, quiet=args.quiet, render=args.render, headful=False,
                            profile=None, actions=None, action_steps=[], render_wait=0.0, screenshot=None, structured=False, track=False)
    wx.QUIET = True
    session = wx.make_session(ns)
    todo = results[: args.read]

    def one(r: dict[str, Any]) -> None:
        # result URLs come from a third party: never let them point --read at the user's own network
        if wx.is_private_host(urlsplit(r["url"]).hostname or ""):
            r["content"] = None
            r["read"] = {"ok": False, "error": {"code": 2, "kind": "private_host", "status": None, "vendor": None,
                                                    "message": "result points at a private/local host; not fetched"}}
            return
        res = wx.process(session, r["url"], ns)
        if res["ok"]:
            content = res["content"] or ""
            cut = len(content) > args.read_chars
            r["content"] = content[: args.read_chars].rstrip() + (" […]" if cut else "")
            r["read"] = {"ok": True, "source": res["source"], "words": res["words"], "truncated": cut, "title": res.get("title"),
                         "date": res.get("date"), "warnings": res["warnings"]}
        else:
            r["content"] = None
            r["read"] = {"ok": False, "error": res["error"]}

    # web-extract prints an `error:` line per failed page; the failure is already in `read.error`, so -q silences it
    saved_stderr = sys.stderr
    if args.quiet:
        sys.stderr = io.StringIO()
    try:
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(todo)))) as pool:
            list(pool.map(one, todo))
    finally:
        sys.stderr = saved_stderr


# --------------------------------------------------------------------- output

def render_markdown(results: list[dict[str, Any]], query: str, args: argparse.Namespace, engine: str) -> str:
    lines = [f"# search: {query}", f"# engine: {engine} · {len(results)} results" + (f" · {args.time} window" if args.time else "")
             + (" · news" if args.news else "") + (f" · read {min(args.read, len(results))}" if args.read else ""), ""]
    for i, r in enumerate(results, 1):
        head = f"{i}. **{r['title'] or r['url']}**  \n   {r['url']}"
        if args.news:
            head += f"  \n   {r.get('date') or '—'} · {r.get('source') or r['host']}"
        lines.append(head)
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
        rd = r.get("read")
        if rd is not None:
            if rd["ok"]:
                src = rd["source"] + (f", {rd['words']} words" if rd.get("words") else "")
                lines += ["", f"   > read: {src}" + (f" · warnings: {', '.join(rd['warnings'])}" if rd.get("warnings") else ""), "",
                          "\n".join("   " + ln for ln in (r.get("content") or "").splitlines())]
            else:
                lines += ["", f"   > read failed: {rd['error']['kind']} — {rd['error']['message']}"]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None, engine: Callable[[str, argparse.Namespace], list[dict[str, Any]]] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query", nargs="+", help="search terms (operators like site:, quotes and - are passed through)")
    p.add_argument("-n", "--max-results", type=int, default=10, help="results to return (default 10, max 50)")
    p.add_argument("--news", action="store_true", help="news search (adds date and source)")
    p.add_argument("--time", choices=sorted(TIME_MAP), help="only results from the last day/week/month/year (d/w/m/y)")
    p.add_argument("--region", metavar="XX-xx",
                   help=f"region/language such as us-en, de-de, ru-ru (default {REGION_DEFAULT}; with --searx: the instance's default language)")
    p.add_argument("--site", action="append", metavar="HOST", help="restrict to a site (repeatable)")
    p.add_argument("--exclude-site", action="append", metavar="HOST", help="exclude a site (repeatable)")
    p.add_argument("--backend", choices=BACKENDS, default="auto", help="search backend (default auto: ddgs rotates several)")
    p.add_argument("--searx", metavar="URL", help="use this SearXNG instance's JSON API instead of ddgs")
    p.add_argument("--safesearch", choices=["off", "moderate", "on"], default="moderate")
    p.add_argument("--read", type=int, default=0, metavar="N", help="also fetch the first N results with web-extract and include their content")
    p.add_argument("--read-chars", type=int, default=READ_CHARS_DEFAULT, metavar="C", help=f"content cap per read result (default {READ_CHARS_DEFAULT})")
    p.add_argument("--render", choices=["off", "auto", "always"], default="auto", help="web-extract rendering mode for --read")
    p.add_argument("-f", "--format", choices=["markdown", "text", "json", "jsonl"], default="markdown")
    p.add_argument("-o", "--out", metavar="FILE")
    p.add_argument("--timeout", type=float, default=15.0, metavar="S", help="per request (default 15)")
    p.add_argument("--proxy", metavar="URL")
    p.add_argument("-q", "--quiet", action="store_true")
    args = p.parse_args(argv)
    if not (1 <= args.max_results <= 50) or args.read < 0 or args.read_chars < 100 or args.timeout <= 0:
        sys.stderr.write("error: -n 1..50, --read ≥ 0, --read-chars ≥ 100, --timeout > 0\n")
        return EXIT_BAD_INPUT
    if args.region is not None and not re.fullmatch(r"[a-z]{2}-[a-z]{2}", args.region, re.I):
        sys.stderr.write("error: --region must look like us-en, de-de or wt-wt (country-language)\n")
        return EXIT_BAD_INPUT
    if args.news and not args.searx and args.backend not in NEWS_BACKENDS:
        sys.stderr.write(f"error: --news supports --backend {', '.join(NEWS_BACKENDS)} only\n")
        return EXIT_BAD_INPUT
    if args.searx and not re.match(r"^https?://", args.searx):
        sys.stderr.write("error: --searx needs an http(s) URL of your SearXNG instance\n")
        return EXIT_BAD_INPUT
    query = build_query(args)
    if not query:
        sys.stderr.write("error: empty query\n")
        return EXIT_BAD_INPUT
    wx = None
    if args.read:
        wx = load_web_extract()
        if wx is None:
            sys.stderr.write("error: --read needs the web-extract skill installed alongside (or WEB_EXTRACT_SCRIPT=/path/to/extract.py)\n")
            return EXIT_BAD_INPUT
    engine = engine or (searx_engine if args.searx else ddgs_engine)
    engine_name = f"searxng {urlsplit(args.searx).hostname}" if args.searx else f"ddgs/{args.backend}"
    t0 = time.monotonic()
    try:
        raw = engine(query, args)
    except EngineBlocked as e:
        sys.stderr.write(f"error: {e} — wait a minute, try --backend bing|brave|google, or --searx YOUR_INSTANCE; do not retry in a loop\n")
        return EXIT_BLOCKED
    except EngineNetwork as e:
        sys.stderr.write(f"error: {e} — check connectivity or raise --timeout\n")
        return EXIT_NETWORK
    except SearxConfigError as e:
        sys.stderr.write(f"error: {e}\n")
        return EXIT_BAD_INPUT
    except RuntimeError as e:
        sys.stderr.write(f"error: {e}\n")
        return EXIT_BAD_INPUT if "missing dependency" in str(e) else EXIT_OTHER
    except Exception as e:
        sys.stderr.write(f"error: search failed: {type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}\n")
        return EXIT_OTHER
    if getattr(args, "backend_used", None):
        engine_name = f"ddgs/auto→{args.backend_used}"
    results = normalise(raw if isinstance(raw, list) else [], args.news)
    if args.site:  # engines sometimes ignore site: — enforce it
        wanted = {s.lower().removeprefix("www.") for s in args.site}
        results = [r for r in results if any(r["host"] == w or r["host"].endswith("." + w) for w in wanted)]
    if args.exclude_site:
        banned = {s.lower().removeprefix("www.") for s in args.exclude_site}
        results = [r for r in results if not any(r["host"] == b or r["host"].endswith("." + b) for b in banned)]
    results = results[: args.max_results]
    if not args.quiet:
        sys.stderr.write(f"note: {len(results)} result(s) from {engine_name} in {time.monotonic() - t0:.1f}s\n")
    if results and args.read and wx is not None:
        read_results(results, args, wx)
        if not args.quiet:
            ok = sum(1 for r in results if r.get("read", {}).get("ok"))
            sys.stderr.write(f"note: read {ok}/{min(args.read, len(results))} result page(s) with web-extract\n")
    code = EXIT_OK if results else EXIT_EMPTY
    if not results:
        sys.stderr.write("error: no results — broaden the query, drop --time/--site, or try --backend bing|brave\n")
    if args.format == "json":
        text = json.dumps({"query": query, "engine": engine_name, "count": len(results), "results": results}, ensure_ascii=False, indent=2) + "\n"
    elif args.format == "jsonl":
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results)
    else:
        text = render_markdown(results, query, args, engine_name)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        sys.stdout.write(f"saved: {args.out} ({len(results)} results)\n")
    else:
        sys.stdout.write(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
