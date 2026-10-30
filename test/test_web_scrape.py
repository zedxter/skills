"""Tests for skills/web-scrape/scripts/scrape.py against an in-process HTTP server."""

import http.server
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "web-scrape" / "scripts" / "scrape.py"
sys.path.insert(0, str(SCRIPT.parent))
import scrape as sc  # noqa: E402


def listing(page_no: int, total: int = 2) -> bytes:
    items = "".join(
        f"""<li class="product" data-sku="SKU-{page_no}{i}">
              <h2><a href="/p/{page_no}{i}">Widget {page_no}{i}</a></h2>
              <span class="price">€ 1.{page_no}{i}9,50</span>
              <span class="rating">{'★' * (i % 5 + 1)}</span>
              <img src="/img/{page_no}{i}.png">
              <ul class="tags"><li>tag-a</li><li>tag-{i}</li></ul>
              <time>Published March {i + 1}, 2024</time>
            </li>""" for i in range(1, 4))
    nxt = f'<a class="next" href="/list?page={page_no + 1}">Next →</a>' if page_no < total else ""
    return f"""<html><head><title>Shop</title>
    <script type="application/ld+json">{{"@context":"https://schema.org","@graph":[{{"@type":"BreadcrumbList","name":"crumbs"}},
      {{"@type":"ItemList","itemListElement":[{{"@type":"Product","name":"LD Widget {page_no}","offers":{{"@type":"Offer","price":"9.99"}}}}]}}]}}</script>
    <script type="application/ld+json">{{"@type":"Product","@id":"#solo","name":"Solo Product","image":["/a.png","/b.png"],"offers":{{"price":"1.50"}}}}</script>
    </head><body><nav><a href="/">home</a><a href="/about">about</a><a href="/help">help</a></nav>
    <ul id="products">{items}</ul>
    <table><caption>Stock</caption><tr><th>SKU</th><th>Qty</th></tr><tr><td>A</td><td>10</td></tr><tr><td>B</td><td>0</td></tr></table>
    <table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>
    <div class="pager">{nxt}</div></body></html>""".encode()


def detail(sku: str) -> bytes:
    return f"""<html><body><article class="desc"><h1>Widget {sku}</h1><p>Long description for {sku} with details.</p></article>
    <span class="weight">Weight: 1,250 g</span><span class="stock">In stock: 42 units</span></body></html>""".encode()


DYNAMIC = b"""<html><head><title>App</title></head><body><div id="root"></div>
<script>document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("root").innerHTML = '<ul>' + [1, 2, 3].map(i => '<li class="product"><h2>JS Widget ' + i + '</h2></li>').join('') + '</ul>';
});</script></body></html>"""
HITS: dict[str, int] = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path
        HITS[path] = HITS.get(path, 0) + 1
        if path == "/dyn":
            return self._send(200, DYNAMIC)
        if path == "/slow":
            time.sleep(4)
            return self._send(200, detail("slow"))
        if path == "/badnext":
            return self._send(200, listing(1).replace(b'<div class="pager">', b'<div class="pager"><a class="nx" href="file:///etc/passwd">x</a>'
                                                                               b'<a class="nx" href="/list?page=2">2</a>'))
        if path == "/comment":
            return self._send(200, b"<!-- maintenance -->")
        if path in ("/", "/list", "/list?page=1"):
            return self._send(200, listing(1))
        if path == "/list?page=2":
            return self._send(200, listing(2))
        if path.startswith("/p/"):
            return self._send(200, detail(path[3:]))
        if path == "/empty":
            return self._send(200, b"<html><body><p>Nothing repeating here at all.</p></body></html>")
        if path == "/blocked":
            return self._send(403, b"<html><head><title>Just a moment...</title></head><body>_cf_chl_opt</body></html>")
        if path == "/pdf":
            return self._send(200, b"%PDF-1.4 x", "application/pdf")
        return self._send(404, b"<html><body>nope</body></html>")

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def run(*args, timeout=120, render="off"):
    p = subprocess.run([sys.executable, str(SCRIPT), *args, "--impersonate", "none", "--render", render], capture_output=True, text=True,
                       encoding="utf-8", timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def run_json(*args):
    rc, out, err = run(*args, "-f", "json")
    return rc, json.loads(out), err


# ------------------------------------------------------------------ unit level

def test_field_spec_parsing_and_modifiers():
    f = sc.Field("price=.price|number")
    assert f.name == "price" and f.mods == ["number"] and f.attr is None and f.raw_selector == ".price"
    f = sc.Field("url=h2 a@href")
    assert f.attr == "href" and f.raw_selector == "h2 a"
    f = sc.Field("sku=xpath:./@data-sku")
    assert f.raw_selector.startswith("xpath:")
    for bad in ("noequals", "1x=a", "a=b|bogus", "a=xpath:///", "a=div[", "a=:::"):
        with pytest.raises(ValueError):
            sc.Field(bad)
    am = sc.apply_mods
    assert am("€ 1.234,50", ["number"], "", None) == 1234.5 and am("1,234.56", ["number"], "", None) == 1234.56
    assert am("1 250 g", ["number"], "", None) == 1250 and am("12,5", ["number"], "", None) == 12.5 and am("1.234.567", ["int"], "", None) == 1234567
    assert am("no digits", ["number"], "", None) is None and am("$ 3", ["int"], "", None) == 3
    assert am("Published March 4, 2024", ["date"], "", None) == "2024-03-04" and am("on 14 March 1879.", ["date"], "", None) == "1879-03-14"
    assert am("31.12.2023", ["date"], "", None) == "2023-12-31" and am("2024-01-05T10:00", ["date"], "", None) == "2024-01-05" and am("none", ["date"], "", None) is None
    assert am("  a \n b ", [], "", None) == "a b" and am("  a \n b ", ["raw"], "", None) == "  a \n b "
    assert sc.dig({"offers": {"price": "9"}, "image": ["x", "y"]}, "offers.price") == "9" and sc.dig({"image": ["x", "y"]}, "image.1") == "y"
    assert sc.dig({"a": 1}, "a.b") is None
    # dates are validated; a slash date that only works month-first is US-ordered
    assert am("Posted 12/31/2023", ["date"], "", None) == "2023-12-31" and am("03/04/2024", ["date"], "", None) == "2024-04-03"
    assert am("99.99.2024", ["date"], "", None) is None and am("2024-13-45", ["date"], "", None) is None
    assert am("February 30, 2024", ["date"], "", None) is None
    # an empty selector is the item itself; JSON-LD paths are kept verbatim
    f = sc.Field("u=@href")
    assert f.selector is None and f.attr == "href"
    node = sc.wx.lxml.html.fromstring('<a href="/x">x</a>')
    assert f.extract(node, "https://e.com/") == "https://e.com/x"
    for spec, path in (("img=image.0", "image.0"), ("id=@id", "@id"), ("t=@type", "@type"), ("p=offers.price|number", "offers.price")):
        f = sc.Field(spec, json_mode=True)
        assert f.raw_selector == path and f.selector is None and f.attr is None
    # an XPath that fails only when evaluated is a selector error, not a traceback
    with pytest.raises(ValueError):
        sc.Field("a=xpath:.//svg:a").extract(node, "https://e.com/")


def test_json_ld_numbers_skip_string_round_trip():
    assert sc.json_value(1e-05, ["number"], "") == 1e-05 and sc.json_value(3.7, ["int"], "") == 3 and sc.json_value(12, ["number"], "") == 12
    assert sc.json_value(True, ["number"], "") is True and sc.json_value("€ 1,50", ["number"], "") == 1.5


def test_safe_link_blocks_private_hosts_and_other_schemes():
    assert sc.safe_link("https://shop.example/list", "/p/1", False) == ("https://shop.example/p/1", "")
    for href in ("http://127.0.0.1:8080/", "http://169.254.169.254/latest/meta-data/", "http://intranet/x", "http://box.local/"):
        assert sc.safe_link("https://shop.example/list", href, False)[0] is None
    assert sc.safe_link("http://127.0.0.1/list", "http://127.0.0.1:8080/", True)[0] == "http://127.0.0.1:8080/"
    for href in ("file:///etc/passwd", "ftp://shop.example/x", "data:text/html,x"):
        assert sc.safe_link("https://shop.example/list", href, True)[0] is None


def test_probe_finds_repeating_blocks():
    tree = sc.wx.lxml.html.fromstring(listing(1))
    rows = sc.probe(tree)
    assert rows and rows[0]["selector"] == "li.product" and rows[0]["count"] == 3 and rows[0]["parent"] == "ul#products"
    assert "h2" in rows[0]["children"] and "Widget 11" in rows[0]["sample"]
    assert sc.probe(sc.wx.lxml.html.fromstring(b"<html><body><p>one</p></body></html>")) == []
    blank = b"<ul>" + b'<li class="item"><div class=" ">some text long enough to count</div></li>' * 4 + b"</ul>"
    assert sc.probe(sc.wx.lxml.html.fromstring(blank))[0]["children"] == ["div"]


def test_output_formats():
    recs = [{"a": "x|y", "b": None, "c": [1, 2], "_page": "u"}, {"a": "z", "d": 3}]
    csv_text = sc.to_csv(recs)
    assert csv_text.splitlines()[0] == "a,b,c,_page,d" and '"[1, 2]"' in csv_text and csv_text.splitlines()[2].startswith("z,,,")
    md = sc.to_markdown(recs)
    assert md.splitlines()[0] == "| a | b | c | d |" and "x\\|y" in md and "_page" not in md


# ------------------------------------------------------------------ end to end

def test_items_fields_pagination_and_diagnostics(server):
    rc, data, err = run_json(f"{server}/list", "--item", "li.product", "--field", "name=h2 a", "--field", "url=h2 a@href",
                             "--field", "price=.price|number", "--field", "sku=xpath:./@data-sku", "--field", "img=img@src",
                             "--field", "tags=.tags li|list", "--field", "tagline=.tags li|join", "--field", "date=time|date",
                             "--field", "missing=.nope", "--field", "html=h2@html", "--next", "a.next")
    assert rc == 0 and data["count"] == 6
    r = data["records"][0]
    assert r["name"] == "Widget 11" and r["url"] == f"{server}/p/11" and r["price"] == 1119.5 and r["sku"] == "SKU-11"
    assert r["img"] == f"{server}/img/11.png" and r["tags"] == ["tag-a", "tag-1"] and r["tagline"] == "tag-a, tag-1" and r["date"] == "2024-03-02"
    assert r["missing"] is None and r["html"].startswith("<a href=") and r["_page"] == f"{server}/list"
    assert data["records"][3]["_page"] == f"{server}/list?page=2"
    assert [p["records"] for p in data["pages"]] == [3, 3] and data["stats"]["item_matches"] == 6
    assert data["stats"]["field_hits"]["missing"] == 0 and "fields never matched: missing 0/6" in err
    # default next-link detection (text "Next →") and --max-pages cap
    rc, data, _ = run_json(f"{server}/list", "--item", "li.product", "--field", "name=h2", "--max-pages", "2")
    assert rc == 0 and data["count"] == 6
    rc, data, _ = run_json(f"{server}/list", "--item", "li.product", "--field", "name=h2")
    assert data["count"] == 3  # single page by default
    rc, data, _ = run_json(f"{server}/list", "--item", "li.product", "--field", "name=h2", "--next", "a.next", "--limit", "4", "--no-meta")
    assert data["count"] == 4 and "_page" not in data["records"][0]


def test_follow_detail_pages_and_unique(server):
    rc, out, err = run(f"{server}/list", "--item", "li.product", "--field", "name=h2 a", "--field", "url=h2 a@href", "--field", "kind=.tags li:first-child",
                       "--follow", "url", "--detail-field", "desc=.desc p", "--detail-field", "weight=.weight|number", "--detail-field", "stock=.stock|int",
                       "-f", "csv")
    rows = out.splitlines()
    assert rc == 0 and rows[0] == "name,url,kind,_page,desc,weight,stock" and len(rows) == 4
    assert rows[1].endswith(",Long description for 11 with details.,1250,42") and "details 3 ok / 0 failed" in err
    rc, data, err = run_json(f"{server}/list", "--item", "li.product", "--field", "kind=.tags li:first-child", "--unique", "kind")
    assert rc == 0 and data["count"] == 1 and "2 duplicate(s) dropped" in err
    # records sharing a detail URL: one fetch, copied to each
    HITS.pop("/p/shared", None)
    rc, data, err = run_json(f"{server}/list", "--item", "li.product", "--field", "name=h2 a", "--field", f'u=xpath:concat("{server}", "/p/shared")',
                             "--follow", "u", "--detail-field", "desc=.desc p")
    assert rc == 0 and HITS["/p/shared"] == 1 and all(r["desc"] == "Long description for shared with details." for r in data["records"])
    assert data["stats"]["details_fetched"] == 3
    # a slow detail page within --timeout fails into _detail_error / details_failed
    rc, data, err = run_json(f"{server}/list", "--item", "li.product", "--field", "name=h2 a", "--field", f'u=xpath:concat("{server}", "/slow")',
                             "--follow", "u", "--detail-field", "desc=.desc p", "--timeout", "1")
    assert rc == 0 and data["stats"]["details_failed"] == 3 and data["records"][0]["_detail_error"]["message"]
    # relative --follow URLs are resolved against the listing page
    rc, data, err = run_json(f"{server}/list", "--item", "li.product", "--field", "u=xpath:string(.//h2/a/@href)", "--follow", "u",
                             "--detail-field", "desc=.desc p")
    assert rc == 0 and data["records"][0]["u"] == "/p/11" and data["records"][0]["desc"] == "Long description for 11 with details."
    assert data["stats"]["details_failed"] == 0
    # --max-time is not overshot by a retry
    t0 = time.monotonic()
    rc, _, _ = run(f"{server}/slow", "--item", "li", "--timeout", "2", "--max-time", "2")
    assert rc == 6 and time.monotonic() - t0 < 3.9
    # --item without fields: each item's text
    rc, data, _ = run_json(f"{server}/list", "--item", "li.product")
    assert rc == 0 and data["count"] == 3 and data["records"][0]["text"].startswith("Widget 11")


def test_json_ld_and_tables(server):
    rc, data, _ = run_json(f"{server}/list", "--json-ld", "Product")
    assert rc == 0 and [r["name"] for r in data["records"]] == ["LD Widget 1", "Solo Product"]  # nested @graph/itemListElement and bare object
    rc, data, _ = run_json(f"{server}/list", "--json-ld", "Product", "--field", "name=name", "--field", "price=offers.price")
    assert data["records"][0] == {"name": "LD Widget 1", "price": "9.99", "_page": f"{server}/list"}
    rc, data, err = run_json(f"{server}/list", "--json-ld", "Product", "--field", "img=image.0", "--field", "id=@id", "--field", "t=@type",
                             "--field", "price=offers.price|number", "--field", "sku=sku")
    assert rc == 0 and data["records"][1] == {"img": "/a.png", "id": "#solo", "t": "Product", "price": 1.5, "sku": None, "_page": f"{server}/list"}
    assert "fields never matched: sku 0/2" in err
    rc, data, _ = run_json(f"{server}/list", "--json-ld")
    assert {r["@type"] for r in data["records"]} >= {"BreadcrumbList", "ItemList", "Product"}
    rc, out, _ = run(f"{server}/list", "--table", "1", "-f", "markdown", "--no-meta")
    assert rc == 0 and out.splitlines()[0] == "| SKU | Qty |" and "| B | 0 |" in out
    rc, data, _ = run_json(f"{server}/list", "--table", "all")
    assert data["count"] == 4 and data["records"][2] == {"col1": "a", "col2": "b", "_table": 2, "_page": f"{server}/list"}
    rc, data, err = run_json(f"{server}/list", "--table", "7")
    assert rc == 1 and "the page has 2 data table(s)" in " ".join(data["stats"]["notes"])


def test_probe_command_and_failure_exit_codes(server):
    rc, data, _ = run_json(f"{server}/list", "--probe")
    assert rc == 0 and data["repeating_blocks"][0]["selector"] == "li.product" and data["tables"] == 2 and data["json_ld_scripts"] == 2
    rc, out, _ = run(f"{server}/list", "--probe", "-f", "markdown")
    assert rc == 0 and "3 × li.product" in out
    rc, out, _ = run(f"{server}/empty", "--probe", "-f", "markdown")
    assert rc == 1 and "no repeating blocks" in out
    rc, data, err = run_json(f"{server}/empty", "--item", ".product", "--field", "n=h2")
    assert rc == 1 and data["count"] == 0 and "matched 0 element(s)" in err and "--probe" in err
    rc, data, err = run_json(f"{server}/blocked", "--item", ".x", "--field", "n=h2")
    assert rc == 4 and data["pages"][0]["error"]["kind"] == "cloudflare_challenge"
    rc, data, err = run_json(f"{server}/nope", "--item", ".x", "--field", "n=h2")
    assert rc == 3
    rc, data, err = run_json(f"{server}/pdf", "--item", ".x", "--field", "n=h2")
    assert rc == 5
    rc, data, err = run_json("http://127.0.0.1:9/", "--item", ".x", "--field", "n=h2", "--timeout", "3")
    assert rc == 6
    rc, data, err = run_json(f"{server}/comment", "--item", ".x", "--field", "n=h2")
    assert rc == 1 and "Traceback" not in err
    rc, out, err = run(f"{server}/list", "--item", "li.product", "--field", "n=h2", "-o", "/dev/null/x.json")
    assert rc == 7 and err.strip().splitlines()[-1].startswith("error:") and "Traceback" not in err
    rc, out, err = run(f"{server}/list", "--item", "li.product", "--field", "n=xpath:.//svg:a")
    assert rc == 2 and "bad XPath" in err


def test_follow_private_rule_uses_each_records_start():
    import argparse
    args = argparse.Namespace(timeout=2.0, impersonate="none", proxy=None, render="off", render_wait=0.0, item=None, next=None,
                              follow="u", concurrency=1, action_steps=[])
    scraper = sc.Scraper(args, [], [sc.Field("d=h1")])
    pub = {"u": "http://127.0.0.1:9/x"}
    loc = {"u": "/nowhere"}
    scraper.origin = {id(pub): ("https://shop.example/", "https://shop.example/list"), id(loc): ("http://127.0.0.1:9/", "http://127.0.0.1:9/list")}
    scraper.fetch_details([pub, loc], time.monotonic() + 5)
    assert pub["_detail_error"]["kind"] == "unsafe_url"  # public start: a local detail URL is refused
    assert loc["_detail_error"]["kind"] != "unsafe_url"  # local start: resolved to http://127.0.0.1:9/nowhere and tried


def test_unsafe_next_candidate_is_skipped(server):
    rc, data, _ = run_json(f"{server}/badnext", "--item", "li.product", "--field", "name=h2", "--next", "a.nx", "--max-pages", "2")
    assert rc == 0 and data["count"] == 6 and data["pages"][1]["url"] == f"{server}/list?page=2"
    assert any("file:///etc/passwd" in n and "not followed" in n for n in data["stats"]["notes"])


def test_max_pages_one_with_next_is_honoured(server):
    rc, data, _ = run_json(f"{server}/list", "--item", "li.product", "--field", "name=h2", "--next", "a.next", "--max-pages", "1")
    assert rc == 0 and data["count"] == 3 and len(data["pages"]) == 1


def _playwright_ready() -> bool:
    try:
        return sc.wx.playwright_api()[0] is not None
    except Exception:
        return False


@pytest.mark.skipif(not _playwright_ready(), reason="Playwright/Chromium not available")
def test_render_auto_fallback_and_always(server):
    rc, out, err = run(f"{server}/dyn", "--item", "li.product", "--field", "name=h2", "-f", "json", render="off")
    assert rc == 1
    for mode in ("auto", "always"):
        rc, out, err = run(f"{server}/dyn", "--item", "li.product", "--field", "name=h2", "-f", "json", render=mode)
        data = json.loads(out)
        assert rc == 0, err
        assert [r["name"] for r in data["records"]] == ["JS Widget 1", "JS Widget 2", "JS Widget 3"] and data["stats"]["rendered"] == 1


def test_bad_arguments_exit2():
    assert run("https://example.com/")[0] == 2  # nothing says what a record is
    assert run("https://example.com/", "--item", ".a", "--table", "1")[0] == 2
    assert run("https://example.com/", "--field", "bad spec")[0] == 2
    assert run("https://example.com/", "--field", "a=.a|zap")[0] == 2
    assert run("https://example.com/", "--item", ".a", "--field", "a=.a", "--follow", "nope", "--detail-field", "b=.b")[0] == 2
    assert run("https://example.com/", "--item", ".a", "--field", "a=.a", "--follow", "a")[0] == 2
    assert run("https://example.com/", "--table", "zero")[0] == 2
    assert run("https://example.com/", "--table", "0")[0] == 2
    assert run("https://example.com/", "--item", ".a", "--actions", "[]")[0] == 2  # --actions with --render off
    assert run("https://example.com/", "--json-ld", "Product", "--field", "x=")[0] == 2
    assert run("not a url", "--table", "1")[0] == 2


def test_crash_exits_7_not_1():
    """An unexpected exception in main() exits 7 with one error line, never 1, which has its own meaning."""
    code = ("import argparse, runpy, sys\n"
            "def boom(*a, **k): raise RuntimeError('boom\\nsecond line')\n"
            "argparse.ArgumentParser.parse_args = boom\n"
            f"sys.argv = [{str(SCRIPT)!r}, 'x']\n"
            f"runpy.run_path({str(SCRIPT)!r}, run_name='__main__')\n")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    assert p.returncode == 7, p.stderr
    assert p.stderr.count("error:") == 1 and "unexpected RuntimeError: boom" in p.stderr
    assert "second line" not in p.stderr and "Traceback" not in p.stderr
