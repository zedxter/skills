# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest>=8", "curl_cffi>=0.17,<1", "trafilatura>=2.3,<3", "lxml_html_clean"]
# ///
"""Smoke tests for skills/web-extract/scripts/extract.py.

Run:  uv run --python 3.12 --with pytest --with trafilatura --with curl_cffi --with lxml_html_clean pytest test/test_web_extract.py
  or: uv run test/test_web_extract.py

Offline cases import the script as a module. Network cases run it as a subprocess
and are skipped when WEB_EXTRACT_OFFLINE=1; archive.org-side failures (rate
limit, outage) are reported as skips, not failures. Jina cases run only with
WEB_EXTRACT_TEST_JINA=1 (they send URLs to a third party).
"""

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "web-extract" / "scripts" / "extract.py"
sys.path.insert(0, str(SCRIPT.parent))
import extract as wx  # noqa: E402

OFFLINE = os.environ.get("WEB_EXTRACT_OFFLINE") == "1"
network = pytest.mark.skipif(OFFLINE, reason="WEB_EXTRACT_OFFLINE=1")
jina_optin = pytest.mark.skipif(os.environ.get("WEB_EXTRACT_TEST_JINA") != "1", reason="set WEB_EXTRACT_TEST_JINA=1")

# ------------------------------------------------------------------ fixtures
CLOUDFLARE_403 = b"""<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>
<div id="cf-wrapper"><script>window._cf_chl_opt={cvId:'3'};</script>
<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>
Enable JavaScript and cookies to continue</div></body></html>"""
ANUBIS_202 = b"""<!DOCTYPE html><html><head><title>Making sure you&#39;re not a bot!</title>
<script id="anubis_challenge" type="application/json">{"challenge":"x"}</script>
<script src="/.within.website/x/cmd/anubis/static/js/main.mjs"></script></head>
<body><p>Making sure you're not a bot!</p></body></html>"""
_PARA = ("Operators of large sites increasingly deploy Anubis, a proof-of-work interstitial, or a Cloudflare "
         "managed challenge with a CAPTCHA to slow down scrapers; this article explains the trade-offs. ")
ARTICLE_WALLS = ("<html lang='en'><head><title>Bot walls explained</title></head><body><article><h1>Bot walls</h1>"
                 + "".join(f"<p>{_PARA}Paragraph {i}.</p>" for i in range(400)) + "</article></body></html>").encode()
REWE_SHAPE = ("<html><head><title>REWE Online Shop</title></head><body><main>"
              + "".join(f"<p>Produkt {i}: Bio Apfel, 1 kg, 2,99 EUR. Lieferung morgen.</p>" for i in range(4000))
              + "</main></body></html>").encode()
CP1251 = ("<html lang='ru'><head><meta charset='windows-1251'><title>Привет мир</title></head><body><article>"
          "<h1>Привет мир</h1>" + "<p>Это проверочный абзац на русском языке с кодировкой cp1251, чтобы убедиться, что байты "
          "декодируются правильно и текст читается.</p>" * 6 + "</article></body></html>").encode("cp1251")
JS_SHELL = b"""<!DOCTYPE html><html lang="en"><head><title>App</title></head><body><div id="root"></div>
<noscript>You need to enable JavaScript to run this app.</noscript><script src="/static/js/main.js"></script></body></html>"""
TINY = b"<html><body><p>Hi</p></body></html>"
LINKS_PAGE = ("<html><head><title>Links</title></head><body><article><h1>Heading One</h1>"
              "<p>Intro paragraph with <strong>bold</strong> text and a <a href='/rel/path'>relative link</a> plus more words to pass.</p>"
              "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>"
              + "<p>Filler sentence number that keeps the extractor happy and the paragraph long enough.</p>" * 5
              + "</article></body></html>").encode()


def ns(**kw):
    base = dict(format="markdown", out=None, links=False, raw=False, no_header=False, max_chars=0, comments=False,
                recall=False, precision=False, timeout=30.0, impersonate="chrome", proxy=None, wayback=False,
                no_wayback=True, jina=False, quiet=True, render="off", headful=False, profile=None, actions=None,
                action_steps=[], render_wait=0.0, screenshot=None, structured=False, track=False)
    base.update(kw)
    return wx.argparse.Namespace(**base)


def run(*args, timeout=180):
    p = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def run_json(*args):
    code, out, err = run(*args, "-f", "json")
    data = json.loads(out)
    return code, data, err


def skip_if_archive_down(result, err):
    """Archive-side trouble (rate limit, outage, refused connection) is a SKIP, not a FAIL."""
    lines = [e for e in result.get("fallback_errors", []) if e.startswith("wayback-")]
    if result.get("error") and result["error"].get("kind") == "no_snapshot" and "wayback-cdx: HTTP" in " ".join(lines):
        lines.append(result["error"]["message"])
    txt = " | ".join(lines)
    if re.search(r"HTTP (429|5\d\d)|connection failed|timed out|DNS lookup|unreachable", txt):
        pytest.skip(f"archive.org unavailable: {txt[:200]}")
    if result.get("error") and "archive.org unreachable" in result["error"].get("message", ""):
        pytest.skip(f"archive.org unavailable: {result['error']['message'][:200]}")


# ---------------------------------------------------------------- offline
def test_classify_cloudflare_header():
    c = wx.classify(403, {"cf-mitigated": "challenge", "server": "cloudflare"}, CLOUDFLARE_403)
    assert c.error.code == 4 and c.error.kind == "cloudflare_challenge" and c.error.vendor == "cloudflare"
    assert c.error.message.endswith("do not retry in a loop")


def test_classify_cloudflare_body_on_200():
    c = wx.classify(200, {}, CLOUDFLARE_403)
    assert c.error.code == 4 and c.error.kind in ("cloudflare_challenge", "bot_check")


def test_classify_anubis_202():
    c = wx.classify(202, {}, ANUBIS_202)
    assert c.error.code == 4 and c.error.kind == "anubis_challenge"


def test_classify_article_mentioning_walls_is_ok():
    assert len(ARTICLE_WALLS) >= 60_000
    c = wx.classify(200, {}, ARTICLE_WALLS)
    assert c.error is None and c.warnings == []


def test_classify_header_beats_large_body():
    assert len(REWE_SHAPE) > 200_000
    c = wx.classify(403, {"cf-mitigated": "challenge"}, REWE_SHAPE[:65536], "https://x", len(REWE_SHAPE))
    assert c.error.kind == "cloudflare_challenge"


def test_classify_plain_statuses():
    c = wx.classify(429, {"retry-after": "5"}, b"")
    assert (c.error.code, c.error.kind, c.error.retry_after) == (4, "rate_limited", 5)
    c = wx.classify(403, {}, b"<html>Members only</html>")
    assert (c.error.code, c.error.kind) == (4, "forbidden")
    assert (wx.classify(401, {}, b"").error.code, wx.classify(401, {}, b"").error.kind) == (3, "unauthorized")
    assert wx.classify(404, {}, b"").error.kind == "not_found"
    assert wx.classify(410, {}, b"").error.kind == "not_found"
    assert wx.classify(503, {}, b"<html>oops</html>").error.kind == "server_error"
    assert wx.classify(405, {}, b"").error.kind == "http_error"
    assert wx.classify(200, {}, b"<html><p>fine</p></html>").error is None


def test_classify_small_200_with_generic_marker_is_only_suspected():
    c = wx.classify(200, {}, b"<html><body><div class='g-recaptcha'></div>Are you a human?</body></html>")
    assert c.error is None and c.warnings == ["challenge_suspected"]


def test_gate():
    assert wx.gate("application/pdf", b"%PDF-1.4") == "binary"
    assert wx.gate("text/plain", b"hello world") == "text"
    assert wx.gate("text/html", b"%PDF-1.4 ...") == "binary"
    assert wx.gate("text/html", b'{"a":1}') == "text"
    assert wx.gate("", b"<html><body>x</body></html>") == "html"
    assert wx.gate("application/octet-stream", b"\x00\x01\x02") == "binary"
    assert wx.gate("image/png", b"\x89PNG") == "binary"
    assert wx.gate("application/json", b"[1,2]") == "text"


def test_extract_cp1251():
    data = wx.extract(CP1251, "https://ru.example/x", ns())
    assert isinstance(data, dict)
    assert data["title"] == "Привет мир" and "проверочный абзац" in data["content"] and data["language"] == "ru"


def test_extract_tiny_is_ok_but_short():
    data = wx.extract(TINY, "https://x.example/", ns())
    assert isinstance(data, dict) and data["words"] == 1 and "short" in data["warnings"]


def test_extract_js_shell():
    data = wx.extract(JS_SHELL, "https://x.example/", ns())
    assert isinstance(data, wx.Diag) and data.code == 1 and data.kind == "js_shell"


def test_extract_links_and_markdown_shape():
    data = wx.extract(LINKS_PAGE, "https://site.example/dir/page", ns(links=True))
    assert isinstance(data, dict)
    assert data["links"] == [{"url": "https://site.example/rel/path", "text": "relative link"}]
    assert "# " in data["content"] and "**bold**" in data["content"] and "|---" in data["content"]
    assert "[relative link](https://site.example/rel/path)" in data["content"]
    plain = wx.extract(LINKS_PAGE, "https://site.example/dir/page", ns(format="text"))
    assert "**" not in plain["content"] and "](" not in plain["content"]


def test_coverage():
    assert wx.coverage_of(4043, 4514) == (0.9, [])
    assert wx.coverage_of(60, 900) == (0.07, ["low_coverage"])
    assert wx.coverage_of(5, 0) == (None, [])


def test_parse_cdx():
    assert wx.parse_cdx("[]") == []
    assert wx.parse_cdx('[["timestamp","original"],["20261029040627","https://example.com/"]]') == [("20261029040627", "https://example.com/")]
    assert wx.parse_cdx('[["timestamp","original"]]') == []
    assert wx.parse_cdx("<html>oops</html>") is None
    assert wx.parse_cdx('{"a":1}') is None


def test_url_normalisation_and_private_hosts():
    assert wx.normalize_url("example.com") == "https://example.com"
    assert wx.normalize_url("https://example.com/a#frag") == "https://example.com/a"
    for bad in ("ftp://x", "", "notaurl", "file:///etc/passwd", "javascript:alert(1)", "http://"):
        assert wx.normalize_url(bad) is None, bad
    for h in ("localhost", "127.0.0.1", "10.0.0.5", "[::1]", "box.local", "svc.internal"):
        assert wx.is_private_host(h), h
    assert not wx.is_private_host("example.com")
    assert wx.is_archive_url("https://web.archive.org/web/2024/https://example.com/")


def test_memento_and_truncate():
    assert wx.memento_to_iso("Tue, 31 Dec 2024 23:31:04 GMT") == "2024-12-31T23:31:04Z"
    text, cut = wx.truncate("x" * 100, 10)
    assert cut and text.startswith("x" * 10) and "[truncated: showed 10 of 100 chars" in text
    assert wx.truncate("short", 10) == ("short", False)
    assert wx.strip_md_links("a [b](http://c) d") == "a b d"


def _check_shape(r):
    assert set(r) - {"saved_to", "screenshot"} == set(wx.RESULT_KEYS)
    assert isinstance(r["ok"], bool) and isinstance(r["content"], str) and isinstance(r["warnings"], list)
    assert isinstance(r["words"], int) and isinstance(r["chars"], int) and isinstance(r["fallback_errors"], list)
    assert isinstance(r["partial"], bool) and isinstance(r["truncated"], bool)
    assert r["content_format"] in ("markdown", "text", "raw", "jina_markdown")
    json.dumps(r)


def test_result_shape_ok_and_failed():
    f = wx.Fetched(200, {"content-type": "text/html; charset=utf-8"}, "https://site.example/dir/page", LINKS_PAGE)
    ok = wx.new_result("https://site.example/dir/page")
    assert wx.handle_body(f, ok, ns(links=True), "live") is None
    ok = wx.finish(ok, ns(links=True))
    _check_shape(ok)
    assert ok["ok"] and ok["source"] == "live" and ok["status"] == 200 and ok["links"] and ok["error"] is None
    bad = wx.new_result("https://x.example/")
    bad["error"] = wx.classify(404, {}, b"").error.as_dict()
    bad = wx.finish(bad, ns())
    _check_shape(bad)
    assert not bad["ok"] and bad["source"] is None and bad["error"]["code"] == 3 and bad["content"] == ""
    text = wx.new_result("https://x.example/a.txt")
    assert wx.handle_body(wx.Fetched(200, {"content-type": "text/plain"}, "https://x.example/a.txt", b"hello there friend"), text, ns(), "live") is None
    assert text["content_format"] == "text" and text["title"] == "a.txt" and text["words"] == 3


def test_binary_body_without_raw_is_exit5():
    r = wx.new_result("https://x.example/f.png")
    d = wx.handle_body(wx.Fetched(200, {"content-type": "image/png"}, "https://x.example/f.png", b"\x89PNG\r\n\x1a\n x"), r, ns(), "live")
    assert isinstance(d, wx.Diag) and d.code == 5 and d.kind == "non_html" and "image/png" in d.message
    r = wx.new_result("https://x.example/f.pdf")  # a PDF that is not a PDF: exit 5 with a save hint, never exit 7
    d = wx.handle_body(wx.Fetched(200, {"content-type": "application/pdf; qs=0.001"}, "https://x.example/f.pdf", b"%PDF-1.4 x"), r, ns(), "live")
    assert isinstance(d, wx.Diag) and d.code == 5 and d.kind == "non_html" and "could not be parsed" in d.message


def _pdf(text: str, title: str | None = "Report Q3") -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    w = PdfWriter()
    page = w.add_blank_page(300, 300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = w._add_object(stream)
    if title:
        w.add_metadata({"/Title": title, "/Author": "Ada Lovelace", "/CreationDate": "D:20240315120000Z"})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_pdf_text_layer_is_read_with_metadata():
    body = _pdf("Quarterly revenue grew twelve percent on strong demand.")
    assert body.startswith(b"%PDF-")
    r = wx.new_result("https://x.example/q3.pdf")
    f = wx.Fetched(200, {"content-type": "application/octet-stream"}, "https://x.example/q3.pdf", body)  # sniffed by magic, not by type
    assert wx.handle_body(f, r, ns(), "live") is None
    assert r["ok"] and r["content_format"] == "text" and r["pages"] == 1 and r["words"] == 8
    assert r["title"] == "Report Q3" and r["author"] == "Ada Lovelace" and r["date"] == "2024-03-15"
    assert "Quarterly revenue" in r["content"] and "--- page" not in r["content"]  # single page: no markers
    r = wx.finish(r, ns())
    _check_shape(r)
    hdr = "\n".join(wx.header_lines(r))
    assert "application/pdf" not in hdr and "· 1 page" in hdr and "# warning: short" in hdr
    assert wx.pdf_date("D:20240315120000Z") == "2024-03-15" and wx.pdf_date("garbage") is None


def test_pdf_without_text_layer_is_exit1_not_5():
    r = wx.new_result("https://x.example/scan.pdf")
    d = wx.handle_body(wx.Fetched(200, {"content-type": "application/pdf"}, "https://x.example/scan.pdf", _pdf("", title=None)), r, ns(), "live")
    assert isinstance(d, wx.Diag) and d.code == 1 and d.kind == "nothing_extracted" and "no text layer" in d.message
    r = wx.new_result("https://x.example/scan.pdf")  # --raw still passes the bytes through untouched
    d = wx.handle_body(wx.Fetched(200, {"content-type": "application/pdf"}, "https://x.example/scan.pdf", _pdf("x", title=None)), r, ns(raw=True), "live")
    assert isinstance(d, wx.Diag) and d.code == 5


STRUCTURED_PAGE = b"""<html lang="en"><head><title>Widget 3000 - Shop</title>
<meta property="og:title" content="Widget 3000"><meta property="og:type" content="product"><meta name="twitter:card" content="summary">
<meta name="description" content="A fine widget."><meta name="robots" content="index"><link rel="canonical" href="/p/widget-3000">
<link rel="alternate" type="application/rss+xml" href="/feed.xml">
<script type="application/ld+json"><!--
{"@context":"https://schema.org","@type":"Product","name":"Widget 3000","offers":{"@type":"Offer","price":"19.99"}}
--></script>
<script type="application/ld+json">[{"@type":"BreadcrumbList"},{"@type":"WebSite"}]</script>
<script type="application/ld+json">not json at all</script>
</head><body><article><h1>Widget 3000</h1>""" + b"<p>A fine widget with many useful properties and a long description paragraph.</p>" * 6 + b"""
<table><caption>Specs</caption><tr><th>Property</th><th>Value</th></tr><tr><td>Weight</td><td>1.2 kg</td></tr><tr><td>Colour</td><td>Teal</td></tr></table>
<table><tr><td>layout only</td></tr></table>
<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>
</article></body></html>"""


def test_structured_data_collects_json_ld_opengraph_meta_and_tables():
    st = wx.structured_data(STRUCTURED_PAGE, "https://shop.example/p/widget-3000?ref=1")
    assert [x["@type"] for x in st["json_ld"]] == ["Product", "BreadcrumbList", "WebSite"]  # HTML-comment wrapper and list form handled, bad JSON skipped
    assert st["opengraph"] == {"og:title": "Widget 3000", "og:type": "product", "twitter:card": "summary"}
    assert st["meta"]["canonical"] == "https://shop.example/p/widget-3000" and st["meta"]["feeds"] == ["https://shop.example/feed.xml"]
    assert st["meta"]["description"] == "A fine widget." and st["meta"]["robots"] == "index"
    assert len(st["tables"]) == 2 and st["tables"][0] == {"caption": "Specs", "headers": ["Property", "Value"],
                                                          "rows": [["Weight", "1.2 kg"], ["Colour", "Teal"]], "row_count": 2, "truncated": False}
    assert st["tables"][1]["headers"] is None and st["tables"][1]["row_count"] == 2 and not st["truncated"]
    assert wx.structured_data(b"", "https://x.example/")["json_ld"] == []
    # flows through handle_body into the result and the markdown appendix (tables stay out: already pipe tables)
    r = wx.new_result("https://shop.example/p/widget-3000")
    assert wx.handle_body(wx.Fetched(200, {"content-type": "text/html"}, "https://shop.example/p/widget-3000", STRUCTURED_PAGE), r, ns(structured=True), "live") is None
    r = wx.finish(r, ns(structured=True))
    _check_shape(r)
    out = wx.render(r, ns(structured=True))
    assert "## Structured data" in out and '"@type": "Product"' in out and '"rows"' not in out
    assert "## Structured data" not in wx.render(r, ns(structured=True, format="html"))
    # a JavaScript shell still yields its JSON-LD / OpenGraph
    shell = STRUCTURED_PAGE.replace(b"<article>", b"<div id='root'></div><article style='display:none'>").split(b"<p>")[0] + b"</article></body></html>"
    r = wx.new_result("https://shop.example/p/widget-3000")
    d = wx.handle_body(wx.Fetched(200, {"content-type": "text/html"}, "https://shop.example/p/widget-3000", shell), r, ns(structured=True), "live")
    assert isinstance(d, wx.Diag) and d.code == 1 and r["structured"]["opengraph"]["og:title"] == "Widget 3000"


def test_track_changes_new_same_changed(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_EXTRACT_CACHE", str(tmp_path))
    def result(text):
        r = wx.new_result("https://x.example/page")
        r.update(ok=True, source="live", status=200, final_url="https://x.example/page", content=text, words=len(text.split()), chars=len(text))
        return r
    r1 = wx.finish(result("line one\nline two\nline three"), ns(track=True))
    assert r1["change"]["status"] == "new" and r1["change"]["previous"] is None and r1["change"]["diff"] is None
    assert (tmp_path / "track").is_dir() and "# change: new (first tracked fetch)" in wx.header_lines(r1)
    r2 = wx.finish(result("line one\nline two\nline three"), ns(track=True))
    assert r2["change"]["status"] == "same" and r2["change"]["previous"] == json.loads(next((tmp_path / "track").iterdir()).read_text("utf-8"))["fetched_at"]
    r3 = wx.finish(result("line one\nline 2\nline three\nline four"), ns(track=True))
    ch = r3["change"]
    assert ch["status"] == "changed" and ch["added"] == 2 and ch["removed"] == 1 and "-line two" in ch["diff"] and "+line four" in ch["diff"]
    out = wx.render(r3, ns(track=True))
    assert "# change: changed since" in out and "## Changes since" in out and "```diff" in out
    _check_shape(r3)
    # the stored copy is the full content, even when stdout is truncated afterwards
    r4 = wx.finish(result("x " * 500), ns(track=True, max_chars=100))
    assert r4["truncated"] and json.loads(next((tmp_path / "track").iterdir()).read_text("utf-8"))["content"] == "x " * 500
    # a different URL has its own store; failed results are never stored
    bad = wx.new_result("https://x.example/other")
    bad["error"] = wx.classify(404, {}, b"").error.as_dict()
    assert wx.finish(bad, ns(track=True))["change"] is None and len(list((tmp_path / "track").iterdir())) == 1


def test_browser_error_mapping_and_action_validation():
    be = wx.browser_error
    assert be(Exception("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://nope.example/"), "https://nope.example/", 30).kind == "dns"
    assert be(Exception("Page.goto: net::ERR_CERT_AUTHORITY_INVALID"), "https://x.example/", 30).kind == "tls"
    assert be(Exception("Page.goto: net::ERR_CONNECTION_REFUSED"), "https://x.example/", 30).kind == "connection"
    d = be(Exception("Page.goto: Timeout 30000ms exceeded."), "https://x.example/", 30)
    assert d.code == 6 and d.kind == "timeout" and "30s" in d.message
    d = be(Exception("BrowserType.launch: Executable doesn't exist at /x/chrome\n╔═══╗ Looks like Playwright was just installed"), "https://x.example/", 30)
    assert d.code == 2 and "chromium-headless-shell" in d.message
    assert be(Exception("Page.goto: net::ERR_ABORTED"), "https://x.example/a.pdf", 30).code == 5
    assert be(RuntimeError("weird"), "https://x.example/", 30).code == 7
    assert wx.parse_actions('[{"wait": 2}, {"scroll": 1}]') == [{"wait": 2}, {"scroll": 1}]
    assert wx.parse_actions('[{"type": "input", "text": "hi"}]') == [{"type": "input", "text": "hi"}]
    assert "between 0 and 120" in wx.parse_actions('[{"wait": 999}]') and "non-empty object" in wx.parse_actions("[{}]")
    assert "must be a JSON list" in wx.parse_actions("5") and "selector/key string" in wx.parse_actions('[{"click": ""}]')


def test_signals_dynamic_and_header_browser_source():
    sig = wx.page_signals(b"<html><head><script>" + b"x" * 5000 + b"</script></head><body><p>Login</p> <p>Next</p></body></html>")
    assert sig.body_words == 2 and sig.script_ratio > 0.5 and sig.dynamic and not sig.shell
    assert not wx.page_signals(b"<html><body>" + b"<p>Plain words here.</p>" * 30 + b"</body></html>").dynamic
    r = wx.new_result("https://x.example/")
    r.update(ok=True, source="browser", status=200, words=190, title="T", final_url="https://x.example/",
             live_error={"code": 1, "kind": "js_shell", "status": 200, "vendor": None, "message": "m"}, screenshot="/tmp/s.png")
    hdr = wx.header_lines(r)
    assert hdr[2] == "# source: browser · HTTP 200 (live: 200 js_shell) · 190 words" and "# screenshot: /tmp/s.png" in hdr
    f = wx.Fetched(200, {"content-type": "text/html"}, "https://x.example/", LINKS_PAGE, action_errors=["{\"click\": \"#x\"}: not found"], screenshot="/tmp/s.png")
    r = wx.new_result("https://x.example/")
    assert wx.handle_body(f, r, ns(), "browser") is None
    assert "action_failed" in r["warnings"] and r["screenshot"] == "/tmp/s.png" and r["source"] == "browser"


def test_main_browser_flags_are_validated():
    assert wx.main(["https://example.com/", "--render", "off", "--screenshot", "x.png"]) == 2
    assert wx.main(["https://example.com/", "--render", "always", "--wayback"]) == 2
    assert wx.main(["https://example.com/", "--actions", "[{\"zap\": 1}]"]) == 2
    assert wx.main(["https://example.com/", "--render-wait", "-1"]) == 2


def test_header_lines_wayback_variant():
    r = wx.new_result("https://x.example/")
    r.update(ok=True, source="wayback", snapshot="2024-12-31T23:31:04Z", words=1210, title="T", final_url="https://x.example/",
             live_error={"code": 4, "kind": "cloudflare_challenge", "status": 403, "vendor": "cloudflare", "message": "m"})
    lines = wx.header_lines(r)
    assert lines[2] == "# source: wayback snapshot 2024-12-31 (live: 403 cloudflare_challenge) · 1210 words"


# ------------------------------------------------- offline: review regressions
_CONTENT_50 = b"".join(f"<p>Ordinary paragraph number {i} with enough words to look like a real page body.</p>".encode() for i in range(50))
JSD_BEACON_PAGE = (b"<html><head><title>Cloudflare Community</title><script>(function(){var u='/cdn-cgi/challenge-platform/scripts/jsd/main.js?ray=a5230502ea45630b'})();</script></head><body>"
                   + _CONTENT_50 + b"</body></html>")
DATADOME_PAGE = (b"<html><head><title>Shop</title><script src='https://js.datadome.co/tags.js' async></script></head><body>"
                 + _CONTENT_50 + b"</body></html>")
CF_INTERSTITIAL = b"<!DOCTYPE html><html><head><title>Just a moment...</title></head><body><script>window._cf_chl_opt={cvId:'3'};</script></body></html>"
CODE_PAGE = ("<html><head><title>Code post</title></head><body><article><h1>Code post</h1>"
             "<p>Intro paragraph with enough words to be considered content by the extractor here.</p>"
             "<pre><span class='pl-c'># /// script</span>\n<span class='pl-c'># requires-python = \"&gt;=3.12\"</span>\n    indented()</pre>"
             "<p>Another paragraph with enough words to be considered content by the extractor here too.</p></article></body></html>").encode()
MEDIAWIKI_PAGE = ("<html lang='en'><head><title>Topic - Wiki</title></head><body><div id='content'><h1>Topic</h1>"
                  "<h2><span class='mw-headline'>History</span><span class='mw-editsection'>[<a href='/edit'>edit</a>]</span></h2>"
                  "<p>Websites are scraped for data.<sup class='reference'><a href='#cite1'>[1]</a></sup> This practice is "
                  "widespread<sup class='Inline-Template'>[<i>citation needed</i>]</sup> and the paragraph continues with words.</p>"
                  + "<p>Filler paragraph with enough ordinary words to keep the extractor interested in the article body.</p>" * 6
                  + "</div></body></html>").encode()
ATOM_FEED = b'<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom"><title>Simon&#39;s Weblog</title><entry><title>Post one</title><content>Body text of the first entry.</content></entry></feed>'
INTERSTITIAL_SHOP = (b"<html><head><title>Amazon.com</title></head><body><form action='/errors/validateCaptcha'>"
                     b"<p>Click the button below to continue shopping</p><button>Continue shopping</button></form>"
                     b"<p>Conditions of Use Privacy Policy</p></body></html>")
INTERSTITIAL_ROBOT = b"<html><head><title>Robot Check</title></head><body><p>Sorry, we just need to make sure you're not a robot. For best results, please make sure your browser is accepting cookies.</p></body></html>"
ARTICLE_200_WORDS = ("<html><head><title>Real article</title></head><body><article><h1>Real article</h1>"
                     + "".join(f"<p>Paragraph {i} of the archived article text, with plenty of distinct words in it to count.</p>" for i in range(20))
                     + "</article></body></html>").encode()


class FakeResponse:
    def __init__(self, status, headers, url, body):
        self.status_code, self.headers, self.url, self._body = status, headers, url, body

    def iter_content(self):
        yield self._body

    def close(self):
        pass


class FakeSession:
    """Maps requested URL -> FakeResponse (or an exception to raise)."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kw):
        self.calls.append(url)
        r = self.routes[url]
        if isinstance(r, Exception):
            raise r
        return r


def test_classify_cloudflare_jsd_beacon_and_unverified_vendor_on_200_are_not_blocked():
    assert len(JSD_BEACON_PAGE) < wx.MARKER_SCAN_BYTES
    c = wx.classify(200, {"content-type": "text/html"}, JSD_BEACON_PAGE)
    assert c.error is None and c.warnings == []
    c = wx.classify(200, {"content-type": "text/html"}, DATADOME_PAGE)
    assert c.error is None  # small body: at most the weak `challenge_suspected` warning, extraction proceeds
    assert wx.classify(200, {}, DATADOME_PAGE + b"<p>x</p>" * 2000).warnings == []  # body >= 8 KB: no warning either
    c = wx.classify(403, {}, DATADOME_PAGE)  # a real wall still names the vendor
    assert (c.error.code, c.error.kind, c.error.vendor) == (4, "bot_check", "datadome")


@pytest.mark.parametrize("status", [403, 503, 200])
def test_classify_genuine_cloudflare_interstitial_at_any_status(status):
    c = wx.classify(status, {}, CF_INTERSTITIAL)
    assert c.error.code == 4 and c.error.kind == "cloudflare_challenge"


def test_gate_feed_comment_prefix_and_wider_window():
    assert wx.gate("application/atom+xml", ATOM_FEED) == "text"
    assert wx.gate("application/xml", ATOM_FEED) == "text"
    assert wx.gate("", ATOM_FEED) == "text"
    assert wx.gate("text/html", b"<!--" + b"x" * 900 + b"--><!DOCTYPE html><html>") == "html"
    assert wx.gate("text/html", b"\n" * 600 + b"<!DOCTYPE html><html>") == "html"
    assert wx.gate("text/html", b"plain prose, no markup at all") == "text"


def test_passthrough_feed_title_and_warning():
    r = wx.new_result("https://x.example/atom/everything/")
    f = wx.Fetched(200, {"content-type": "application/xml"}, "https://x.example/atom/everything/", ATOM_FEED)
    assert wx.handle_body(f, r, ns(), "live") is None
    assert r["content_format"] == "text" and r["title"] == "Simon's Weblog" and "feed" in r["warnings"]
    assert r["content"].startswith("<?xml")


def test_extract_code_block_keeps_fence_and_lines():
    data = wx.extract(CODE_PAGE, "https://x.example/post", ns())
    assert isinstance(data, dict)
    assert "```\n# /// script\n# requires-python = \">=3.12\"\n    indented()\n```" in data["content"]
    assert "\\#" not in data["content"]


def test_extract_prunes_mediawiki_boilerplate():
    data = wx.extract(MEDIAWIKI_PAGE, "https://wiki.example/Topic", ns())
    assert isinstance(data, dict)
    assert "<sup>" not in data["content"] and "[edit]" not in data["content"] and "citation needed" not in data["content"]
    assert "Websites are scraped for data." in data["content"]


def test_tidy_markdown_and_clean_author():
    assert wx.tidy_markdown("x.<sup>[1]</sup> y\n\n\\[edit\\]\n\nz") == "x.^[1] y\n\nz"
    assert wx.clean_author("Jane Doe") == "Jane Doe"
    assert wx.clean_author("Authority control databases") is None
    assert wx.clean_author("Author search results export search results as first") is None
    assert wx.clean_author("") is None


def test_extract_text_format_is_plain_not_passthrough_in_header():
    data = wx.extract(LINKS_PAGE, "https://site.example/dir/page", ns(format="text"))
    assert data["content_format"] == "plain"
    r = wx.new_result("https://site.example/dir/page")
    wx.fill(r, wx.Fetched(200, {"content-type": "text/html"}, "https://site.example/dir/page", LINKS_PAGE), data, "live", [])
    assert "text/html" not in wx.header_lines(r)[2]
    pt = wx.new_result("https://x.example/a.json")
    wx.handle_body(wx.Fetched(200, {"content-type": "application/json"}, "https://x.example/a.json", b'{"a": 1}'), pt, ns(), "live")
    assert "· application/json ·" in wx.header_lines(pt)[2]


def test_coverage_is_capped_and_url_helpers():
    assert wx.coverage_of(26, 25) == (1.0, [])
    assert wx.normalize_url("example.com:8080/path") == "https://example.com:8080/path"
    assert wx.normalize_url("192.168.1.1:8080/") == "https://192.168.1.1:8080/"
    assert wx.normalize_url("mailto:a@b.com") is None and wx.normalize_url("example.com:abc/x") is None
    assert wx.normalize_url("https://user:pw@example.com/x") is None
    assert wx.has_userinfo("https://user:pw@example.com/x") and not wx.has_userinfo("example.com/a@b")
    assert wx.redact_url("https://alice:s3cret@host.example/p?q=1") == "https://***@host.example/p?q=1"
    assert wx.redact_url("https://host.example/p") == "https://host.example/p"


def test_decode_text_uses_in_document_charset():
    body = "<html><head><meta charset='windows-1251'><title>Привет</title></head><body><p>Привет мир</p></body></html>".encode("cp1251")
    assert "Привет мир" in wx.decode_text(body, {"content-type": "text/html"})
    assert wx.decode_text("héllo wörld".encode("utf-8"), {}) == "héllo wörld"
    long_cp1251 = ("Это длинный текст на русском языке без объявления кодировки. " * 8).encode("cp1251")
    assert "длинный текст" in wx.decode_text(long_cp1251, {"content-type": "text/plain"})


def test_fetch_refuses_redirect_into_private_host_and_flags_downgrade():
    secret = b"<html><body><article>" + b"<p>SECRET paragraph with enough words to extract.</p>" * 20 + b"</article></body></html>"
    s = FakeSession({"https://public.example/go": FakeResponse(200, {"content-type": "text/html"}, "http://127.0.0.1:8000/secret.html", secret)})
    d = wx.fetch(s, "https://public.example/go", timeout=5)
    assert isinstance(d, wx.Diag) and d.code == 4 and d.kind == "redirect_to_private" and "body discarded" in d.message
    # a private host the user named explicitly may redirect within private space
    s = FakeSession({"http://127.0.0.1:8000/a": FakeResponse(200, {"content-type": "text/html"}, "http://127.0.0.1:8000/b", secret)})
    f = wx.fetch(s, "http://127.0.0.1:8000/a", timeout=5)
    assert isinstance(f, wx.Fetched) and f.status == 200
    s = FakeSession({"https://public.example/x": FakeResponse(200, {"content-type": "text/html"}, "http://public.example/x", secret)})
    f = wx.fetch(s, "https://public.example/x", timeout=5)
    assert isinstance(f, wx.Fetched) and f.downgraded and not f.redirected
    r = wx.new_result("https://public.example/x")
    assert wx.handle_body(f, r, ns(), "live") is None and "downgraded" in r["warnings"]


def _cdx_body(*rows):
    return json.dumps([["timestamp", "original"], *rows]).encode()


def _wayback_routes(cdx_status, cdx_body, snapshots):
    url = "https://shop.example/dp/X"
    params = [("url", url), ("output", "json"), ("limit", "-3"), ("filter", "statuscode:200"), ("filter", "mimetype:text/html"), ("fl", "timestamp,original")]
    routes = {f"{wx.CDX_URL}?{wx.urlencode(params)}": FakeResponse(cdx_status, {"content-type": "application/json"}, "cdx", cdx_body)}
    for ts, body in snapshots.items():
        routes[f"https://web.archive.org/web/{ts}id_/{url}"] = FakeResponse(
            200, {"content-type": "text/html", "memento-datetime": "Sat, 15 Nov 2025 06:19:31 GMT"}, f"https://web.archive.org/web/{ts}id_/{url}", body)
    return url, routes


def test_wayback_skips_interstitial_snapshots():
    url, routes = _wayback_routes(200, _cdx_body(["20251115061931", "https://shop.example/dp/X"], ["20240604105805", "https://shop.example/dp/X"]),
                                  {"20251115061931": INTERSTITIAL_SHOP, "20240604105805": INTERSTITIAL_ROBOT})
    s = FakeSession(routes)
    result = wx.new_result(url)
    result["live_error"] = {"code": 3, "kind": "not_found", "status": 404, "vendor": None, "message": "m"}
    assert wx.try_wayback(s, url, result, ns(no_wayback=False)) == (False, False)
    assert len(result["fallback_errors"]) == 2 and all("challenge_suspected" in e for e in result["fallback_errors"])
    assert result["ok"] is False and result["content"] == ""


def test_wayback_prefers_older_real_article_over_short_first_snapshot():
    short_but_clean = b"<html><body><p>Page moved. Please use the new address of this product listing instead.</p></body></html>"
    url, routes = _wayback_routes(200, _cdx_body(["20251115061931", "https://shop.example/dp/X"], ["20240604105805", "https://shop.example/dp/X"]),
                                  {"20251115061931": short_but_clean, "20240604105805": ARTICLE_200_WORDS})
    result = wx.new_result(url)
    got, down = wx.try_wayback(FakeSession(routes), url, result, ns(no_wayback=False))
    assert got and not down and result["ok"] and result["source"] == "wayback"
    assert result["snapshot_url"] == "https://web.archive.org/web/20240604105805/https://shop.example/dp/X"
    assert result["snapshot"] == "2025-11-15T06:19:31Z" and result["words"] >= 100
    assert any(e.startswith("wayback-snapshot 20251115061931: short") for e in result["fallback_errors"])


def test_wayback_cdx_403_falls_back_to_direct_probe_or_explains():
    url, routes = _wayback_routes(403, b"<p>This type of CDX query requires authorization.</p>", {})
    routes[f"https://web.archive.org/web/2id_/{url}"] = FakeResponse(
        200, {"content-type": "text/html", "memento-datetime": "Sat, 15 Nov 2025 06:19:31 GMT"},
        f"https://web.archive.org/web/20251115061931id_/{url}", ARTICLE_200_WORDS)
    result = wx.new_result(url)
    got, down = wx.try_wayback(FakeSession(routes), url, result, ns(no_wayback=False))
    assert got and result["source"] == "wayback" and result["snapshot_url"].startswith("https://web.archive.org/web/20251115061931/")
    routes[f"https://web.archive.org/web/2id_/{url}"] = FakeResponse(404, {}, f"https://web.archive.org/web/2id_/{url}", b"")
    result = wx.new_result(url)
    assert wx.try_wayback(FakeSession(routes), url, result, ns(no_wayback=False)) == (False, False)
    assert result["fallback_errors"] == ["wayback-cdx: HTTP 403 (archive.org restricts CDX lookups for this domain; snapshot index unavailable)"]


def test_render_skips_header_for_raw_and_html():
    r = wx.new_result("https://x.example/")
    r.update(ok=True, source="live", status=200, content="<html>x</html>", content_format="raw", content_type="text/html")
    assert wx.render(r, ns(raw=True)) == "<html>x</html>"
    r["content_format"] = "markdown"
    assert wx.render(r, ns(format="html")) == "<html>x</html>"
    assert wx.render(r, ns()).startswith("# title: —\n")


def test_main_rejects_raw_out_with_several_urls_and_userinfo(capsys):
    with tempfile.TemporaryDirectory() as d:
        assert wx.main(["https://a.example/", "https://b.example/", "--raw", "-o", str(Path(d) / "x.bin"), "--no-wayback"]) == 2
        assert "--raw -o FILE takes a single URL" in capsys.readouterr().err
        assert wx.main(["https://alice:pw@a.example/", "--no-wayback"]) == 2
        assert "credentials in URL" in capsys.readouterr().err
        assert wx.main(["https://a.example/", "-o", d, "--no-wayback"]) == 2
        assert "is a directory" in capsys.readouterr().err


def test_main_out_file_untouched_when_every_url_fails(monkeypatch, capsys):
    monkeypatch.setattr(wx, "fetch", lambda *a, **k: wx.Diag(wx.EXIT_NETWORK, "dns", "DNS lookup failed for host a.example"))
    with tempfile.TemporaryDirectory() as d:
        existing = Path(d) / "notes.md"
        existing.write_text("previous content", encoding="utf-8")
        assert wx.main(["https://a.example/", "--no-wayback", "-o", str(existing)]) == 6
        assert existing.read_text(encoding="utf-8") == "previous content"
        fresh = Path(d) / "sub" / "new.md"
        assert wx.main(["https://a.example/", "--no-wayback", "-o", str(fresh)]) == 6
        assert not fresh.exists()
        out = capsys.readouterr()
        assert out.err.count("error: DNS lookup failed") == 2 and "note: nothing written to" in out.err
        js = Path(d) / "r.json"  # json still carries the failure object
        assert wx.main(["https://a.example/", "--no-wayback", "-o", str(js), "-f", "json"]) == 6
        assert json.loads(js.read_text(encoding="utf-8"))["results"][0]["error"]["kind"] == "dns"


def test_process_guard_turns_unexpected_exception_into_exit7(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(wx, "fetch", boom)
    r = wx.process(object(), "https://a.example/", ns())
    _check_shape(r)
    assert r["ok"] is False and r["error"]["code"] == 7 and r["error"]["kind"] == "other" and "kaboom" in r["error"]["message"]
    assert capsys.readouterr().err.count("error:") == 1


def test_help_prints_under_legacy_console_encoding():
    env = dict(os.environ, PYTHONIOENCODING="cp932", PYTHONUTF8="0")
    p = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, env=env)
    assert p.returncode == 0 and b"Exit codes" in p.stdout


# ------------------------------------------------- offline: second review round
_P1 = " ".join(f"first part word{i}" for i in range(30))
_P2 = " ".join(f"second part word{i}" for i in range(30))
SPLIT_BODY = f"""<html><head><title>Split</title></head><body><main>
<div class="wrap-a"><div class="post-content"><p>{_P1}.</p><p>{_P1} again.</p></div></div>
<div class="ars-interlude"><p>Advertisement slot here with some words.</p></div>
<div class="wrap-b"><div class="post-content"><figure><img src="x.jpg"></figure><p>{_P2}.</p><p>{_P2} again.</p></div></div>
</main></body></html>""".encode()
_NAV = "".join(f"<li><a href='/p/{i}'>Post {i}</a></li>" for i in range(30))
INDEX_PAGE = f"""<html><head><title>Idx</title></head><body><nav><ul>{_NAV}</ul></nav><article><h1>Blog</h1>
<p>Intro paragraph with <a href='#sec2'>section two</a> and <a href='#cite_ref-1'>↑</a> and <a href='/wiki/Other'>other page</a> with enough words to count as content.</p>
<p>{_P1}.</p></article></body></html>""".encode()
CONSENT_PAGE = ("<html lang='de'><head><title>Ihre Datenschutzeinstellungen</title></head><body><article><h1>Ihre Datenschutzeinstellungen</h1>"
                "<p>Wir und unsere Partner verwenden Cookies, um Informationen auf einem Gerät zu speichern und abzurufen, damit wir Ihnen passende Inhalte zeigen.</p>"
                "<p>Klicken Sie auf Alle akzeptieren, um zuzustimmen, oder auf Alle ablehnen. Sie können Ihre Auswahl unter Datenschutzeinstellungen verwalten ändern.</p>"
                + "<p>Weitere Informationen zu den Verarbeitungszwecken finden Sie in unserer Cookie-Richtlinie und Datenschutzerklärung mit weiteren Worten.</p>" * 3
                + "</article></body></html>").encode()
ELEMENT_SHELL = (b'<html lang="en"><head><title>Element</title><meta property="og:site_name" content="Element"></head>'
                 b'<body><div id="root"></div><noscript>Sorry, Element requires JavaScript</noscript></body></html>')


def test_merge_split_body_recovers_text_after_an_ad_slot():
    data = wx.extract(SPLIT_BODY, "https://site.example/a", ns())
    assert isinstance(data, dict)
    assert "first part word0" in data["content"] and "second part word29 again" in data["content"]
    assert "low_coverage" not in data["warnings"] and data["para_words"] >= 300
    tree = wx.load_html(SPLIT_BODY)
    assert wx.merge_split_body(tree) == 1 and wx.merge_split_body(wx.load_html(LINKS_PAGE)) == 0


def test_coverage_second_signal_from_paragraph_words():
    assert wx.coverage_of(306, 1000, 549) == (0.31, ["low_coverage"])
    assert wx.coverage_of(655, 919, 352) == (0.71, [])
    assert wx.coverage_of(40, 100, 60) == (0.4, [])  # para_words under 300: rule does not apply
    assert "para_words" in wx.RESULT_KEYS


def test_links_pruned_warning_and_fragment_links_dropped():
    data = wx.extract(INDEX_PAGE, "https://w.example/wiki/A", ns(links=True))
    assert data["links"] == [{"url": "https://w.example/wiki/Other", "text": "other page"}]
    assert "links_pruned" in data["warnings"] and "links_pruned" not in wx.PARTIAL_WARNINGS
    assert "](https://w.example#" not in data["content"] and "section two and ↑ and [other page](" in data["content"]
    article = wx.extract(LINKS_PAGE, "https://site.example/dir/page", ns(links=True))
    assert "links_pruned" not in article["warnings"]
    plain = wx.extract(INDEX_PAGE, "https://w.example/wiki/A", ns(links=True, format="text"))
    assert "](" not in plain["content"] and "other page" in plain["content"]  # -f text stays plain even with --links
    assert wx.unwrap_fragment_links("[↑](https://h.example#c1) [x](https://h.example/p#s) [y](https://h.example/q)", "https://h.example/p") == "↑ x [y](https://h.example/q)"


def test_empty_2xx_bodies_are_exit1():
    for status, ctype, body in ((200, "text/html; charset=utf-8", b""), (200, "text/html", b"  \n\n "), (204, "", b""), (200, "text/plain", b"")):
        r = wx.new_result("https://x.example/")
        d = wx.handle_body(wx.Fetched(status, {"content-type": ctype} if ctype else {}, "https://x.example/", body), r, ns(), "live")
        assert isinstance(d, wx.Diag) and d.code == 1 and d.kind == "nothing_extracted", (status, ctype)
        assert d.status == status and ("empty body" in d.message or "only whitespace" in d.message)
    r = wx.new_result("https://x.example/r.txt")
    assert wx.handle_body(wx.Fetched(200, {"content-type": "text/plain"}, "https://x.example/r.txt", b"User-agent: * Disallow:"), r, ns(), "live") is None
    assert "short" in r["warnings"]


def test_exit1_result_keeps_title_language_and_body_words():
    r = wx.new_result("https://app.example/")
    d = wx.handle_body(wx.Fetched(200, {"content-type": "text/html"}, "https://app.example/", ELEMENT_SHELL), r, ns(), "live")
    assert isinstance(d, wx.Diag) and d.kind == "js_shell" and d.status == 200
    assert r["title"] == "Element" and r["language"] == "en" and r["body_words"] == 1 and r["sitename"] == "Element"
    assert "meta" not in d.as_dict()
    assert wx.html_title(b"<html><head><title>  Telegram\n Web </title></head></html>") == "Telegram Web"


def test_binary_hint_depends_on_media_type():
    def diag(ctype, head):
        return wx.handle_body(wx.Fetched(200, {"content-type": ctype}, "https://x.example/f", head + b"x" * 20), wx.new_result("u"), ns(), "live")
    assert "an image has no text" in diag("image/png", b"\x89PNG").message
    # a PDF body is read by pypdf now, whatever the declared type; a corrupt one is exit 5 with a save hint
    assert "could not be parsed" in diag("application/pdf", b"%PDF-1.4").message
    assert "could not be parsed" in diag("text/html", b"%PDF-1.4").message
    assert "document tool" in wx.binary_hint("application/pdf", b"%PDF-1.4") and "document tool" in wx.binary_hint("application/msword", b"")
    assert "if the bytes are needed" in diag("application/zip", b"PK\x03\x04").message
    assert all(diag(c, h).code == 5 for c, h in (("image/png", b"\x89PNG"), ("application/zip", b"PK\x03\x04")))


def test_consent_wall_warning():
    d = wx.extract(CONSENT_PAGE, "https://consent.yahoo.com/v2/collectConsent?sessionId=1", ns())
    assert "consent_wall" in d["warnings"] and "consent_wall" in wx.PARTIAL_WARNINGS
    d = wx.extract(CONSENT_PAGE, "https://news.example/story", ns())  # two distinct consent phrases are enough
    assert "consent_wall" in d["warnings"]
    assert "consent_wall" not in wx.extract(LINKS_PAGE, "https://site.example/dir/page", ns())["warnings"]
    assert "consent_wall" not in wx.extract(ARTICLE_WALLS, "https://privacy.example/long", ns())["warnings"]  # 300+ words: never


def test_clean_author_drops_dates_sitename_and_abbreviations():
    assert wx.clean_author("Oct") is None and wx.clean_author("Bo") is None
    assert wx.clean_author("Oct. 26, 2026 · Jakub Beránek") == "Jakub Beránek"
    assert wx.clean_author("Heise Online; Dafina Maloku", "Heise Online") == "Dafina Maloku"
    assert wx.clean_author("May Smith") == "May Smith" and wx.clean_author("Li Wu") == "Li Wu"
    assert wx.clean_author("Jane Doe | Example News", "Example News") == "Jane Doe"


def test_tidy_markdown_dedupes_blocks_drops_empty_headings_keeps_brackets():
    assert wx.tidy_markdown("## Reviews\n\n## Specs\n\n### Weight\n\n8 oz\n\n## Related\n") == "## Specs\n\n### Weight\n\n8 oz"
    dup = "Same paragraph repeated here for the mobile layout."
    assert wx.tidy_markdown(f"{dup}\n\n{dup}") == dup
    assert wx.tidy_markdown("- Yes\n\n- Yes\n\n## A\n\n### B\n\ntext") == "- Yes\n\n## A\n\n### B\n\ntext".replace("- Yes", "- Yes\n\n- Yes", 1)
    assert wx.tidy_markdown("a\n\n\\[Applause\\]\n\n[Update: this was fixed]\n\n[Reserved]\n\n\\[edit\\]\n\n[citation needed]\n\nb") == \
        "a\n\n\\[Applause\\]\n\n[Update: this was fixed]\n\n[Reserved]\n\nb"
    code = "```\nx\n\n    y\n```"
    assert wx.tidy_markdown(f"## H\n\n{code}\n\n{code}") == f"## H\n\n{code}\n\n{code}"  # fences: never deduped, never re-indented


def test_squash_html_whitespace_keeps_nesting_and_fences():
    assert wx.squash_html_whitespace("-\n    Sugar **x**\n  -\n   sub\n```\n\tcode  x\n```\na\n\t\tb") == "- Sugar **x**\n  - sub\n```\n\tcode  x\n```\na b"
    assert wx.squash_html_whitespace("**bei deinem\n            Einkauf** und **x**\t\t\ty") == "**bei deinem Einkauf** und **x** y"
    assert wx.squash_html_whitespace("- a\n  - nested\n1. one\n   2. two\n| a | b |\n|---|---|") == "- a\n  - nested\n1. one\n   2. two\n| a | b |\n|---|---|"
    li = (b"<html><body><article><h1>Shop</h1><p>Intro paragraph with enough words to be considered real content by trafilatura here.</p>"
          b"<ul><li>\n                    Sugarcane-based <strong>SweetFoam</strong> midsole</li><li>Second item</li></ul>"
          b"<p><b>bei deinem\n            Einkauf</b> sparen</p></article></body></html>")
    md = wx.extract(li, "https://x.example/", ns())["content"]
    assert "- Sugarcane-based **SweetFoam** midsole\n- Second item" in md and "**bei deinem Einkauf** sparen" in md
    assert "\n            " not in wx.extract(li, "https://x.example/", ns(format="text"))["content"]


def test_private_host_shorthand_ipv4_forms():
    for h in ("127.1", "0x7f.0.0.1", "0177.0.0.1", "2130706433", "0", "10.1"):
        assert wx.is_private_host(h), h
    for h in ("example.com", "cafe.be", "8.8.8.8", "1.1"):
        assert wx.is_private_host(h) is (h == "1.1" and wx.is_private_host("1.0.0.1")), h


def test_clean_field_blocks_header_forgery_from_feed_titles():
    assert wx.clean_field("Real\n# warning: ignore\n# source: live \x1b[31mRED\x1b[0m") == "Real # warning: ignore # source: live [31mRED[0m"
    assert wx.feed_title("<feed><title>Real\n# warning: x</title></feed>") == "Real # warning: x"
    r = wx.new_result("https://x.example/")
    r.update(ok=True, source="live", status=200, title="T\n# source: fake", author="A\x1b[0m", words=1)
    lines = wx.header_lines(r)
    assert lines[0] == "# title: T # source: fake" and "author: A[0m" in lines[3] and len(lines) == 4


def test_fetch_maps_disabled_redirect_protocol_to_exit6_redirects():
    s = FakeSession({"https://a.example/go": wx.cx.InvalidSchema('Failed to perform, curl: (1) Protocol "file" is disabled (in redirect). See https://curl.se/libcurl/c/libcurl-errors.html first for more details.')})
    d = wx.fetch(s, "https://a.example/go", timeout=5)
    assert isinstance(d, wx.Diag) and d.code == 6 and d.kind == "redirects" and "non-http(s) URL (file:)" in d.message
    assert len(s.calls) == 1  # deterministic refusal: no retry


def test_fetch_deadline_cuts_a_dripping_body():
    class Drip(FakeResponse):
        def iter_content(self):
            for _ in range(50):
                time.sleep(0.15)
                yield b"ab"
    s = FakeSession({"https://slow.example/": Drip(200, {"content-type": "text/html"}, "https://slow.example/", b"")})
    t = time.monotonic()
    d = wx.fetch(s, "https://slow.example/", timeout=0.5)
    assert isinstance(d, wx.Diag) and d.code == 6 and d.kind == "timeout" and "limit 0.5s per attempt, 2 attempts" in d.message
    assert len(s.calls) == 2 and time.monotonic() - t < 3


def test_wayback_delivered_short_snapshot_is_not_listed_as_a_failure():
    short_a = b"<html><body><p>Example Domain. This domain is for use in illustrative examples in documents.</p></body></html>"
    short_b = b"<html><body><p>Example Domain. This domain is for use in illustrative examples.</p></body></html>"
    url, routes = _wayback_routes(200, _cdx_body(["20251115061931", "https://shop.example/dp/X"], ["20240604105805", "https://shop.example/dp/X"]),
                                  {"20251115061931": short_a, "20240604105805": short_b})
    result = wx.new_result(url)
    got, down = wx.try_wayback(FakeSession(routes), url, result, ns(no_wayback=False))
    assert got and result["ok"] and result["snapshot_url"].startswith("https://web.archive.org/web/20251115061931/")
    assert result["fallback_errors"] == ["wayback-snapshot 20240604105805: short (10 words)"]
    assert "short" in wx.finish(result, ns(no_wayback=False))["warnings"]


def test_wayback_on_private_host_is_bad_input_not_no_snapshot(capsys):
    r = wx._process(FakeSession({}), "http://127.0.0.1:9/x", ns(wayback=True, no_wayback=False))
    assert r["ok"] is False and r["error"]["code"] == 2 and r["error"]["kind"] == "bad_input" and "archive lookup skipped" in r["error"]["message"]
    assert "no archived snapshot" not in capsys.readouterr().err
    r = wx._process(FakeSession({}), "https://web.archive.org/web/2020/https://example.com/", ns(wayback=True, no_wayback=False))
    assert r["error"]["code"] == 2


def test_main_raw_out_json_does_not_overwrite_saved_binary(monkeypatch, capsys):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    monkeypatch.setattr(wx, "fetch", lambda *a, **k: wx.Fetched(200, {"content-type": "image/png"}, "https://a.example/x.png", png))
    monkeypatch.setattr(wx, "make_session", lambda args: object())
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "x.png"
        assert wx.main(["https://a.example/x.png", "--no-wayback", "--raw", "-o", str(target), "-f", "json"]) == 0
        assert target.read_bytes() == png
        out = json.loads(capsys.readouterr().out)
        assert out["results"][0]["saved_to"] == str(target) and out["results"][0]["content_format"] == "raw"
        assert wx.main(["https://a.example/x.png", "--no-wayback", "--raw", "-o", str(target)]) == 0
        assert target.read_bytes() == png and capsys.readouterr().out.startswith("saved: ")


def test_finish_adds_short_for_every_source():
    r = wx.new_result("https://x.example/")
    r.update(ok=True, source="jina", content="sixteen words " * 8, words=16, chars=100, content_format="jina_markdown", warnings=["third_party"])
    assert "short" in wx.finish(r, ns())["warnings"]
    r = wx.new_result("https://x.example/")
    r.update(ok=True, source="live", content="", words=0, content_format="raw", warnings=["binary_saved"])
    assert "short" not in wx.finish(r, ns())["warnings"]


# ---------------------------------------------------------------- network
@network
def test_n1_example_json():
    code, data, _ = run_json("https://example.com", "--no-wayback")
    r = data["results"][0]
    _check_shape(r)
    assert code == 0 and r["source"] == "live" and r["title"] == "Example Domain" and r["status"] == 200
    assert "documentation examples" in r["content"] and r["words"] > 10


@network
def test_n2_scheme_prefixed():
    code, out, _ = run("example.com", "--no-wayback")
    assert code == 0 and "# url: https://example.com" in out


@network
def test_n3_html_markdown():
    code, out, _ = run("https://httpbingo.org/html", "--no-wayback")
    assert code == 0 and "Herman Melville" in out


@network
def test_n4_json_passthrough():
    code, data, _ = run_json("https://httpbingo.org/json", "--no-wayback")
    r = data["results"][0]
    assert code == 0 and r["content_format"] == "text" and r["content_type"] == "application/json"


@network
@pytest.mark.parametrize("status,code,kind", [(404, 3, "not_found"), (403, 4, "forbidden"), (429, 4, "rate_limited"), (401, 3, "unauthorized")])
def test_n5_n7_statuses(status, code, kind):
    rc, data, err = run_json(f"https://httpbingo.org/status/{status}", "--no-wayback")
    r = data["results"][0]
    assert rc == code and r["ok"] is False and r["error"]["code"] == code and r["error"]["kind"] == kind
    assert err.count("error:") == 1
    if status == 429:
        assert r["retry_after"] is None


@network
def test_n8_503_retries_once():
    rc, out, err = run("https://httpbingo.org/status/503", "--no-wayback")
    assert rc == 3 and err.count("note: retrying") == 1 and "server error" in err


@network
def test_n9_png_exit5_and_raw_save():
    rc, _, err = run("https://httpbingo.org/image/png", "--no-wayback")
    assert rc == 5 and "image/png" in err
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "sub" / "x.png"
        rc, out, _ = run("https://httpbingo.org/image/png", "--no-wayback", "--raw", "-o", str(target))
        assert rc == 0 and out.startswith("saved: ") and target.read_bytes().startswith(b"\x89PNG")


@network
def test_n9b_png_raw_save_with_json_keeps_binary():
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "x.png"
        rc, out, _ = run("https://httpbingo.org/image/png", "--no-wayback", "--raw", "-o", str(target), "-f", "json")
        assert rc == 0 and target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        obj = json.loads(out)  # the report goes to stdout, never over the saved body
        assert obj["results"][0]["saved_to"] == str(target) and obj["results"][0]["ok"]


@network
def test_n10_pdf_is_read():
    rc, data, _ = run_json("https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf", "--no-wayback")
    r = data["results"][0]
    assert rc == 0 and r["ok"] and r["content_type"] == "application/pdf" and r["pages"] == 1 and "Dummy PDF" in r["content"]
    assert r["content_format"] == "text" and r["date"] == "2007-02-23" and "short" in r["warnings"]


def _browser_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True
    except Exception:
        return False


browser = pytest.mark.skipif(OFFLINE or not _browser_ready(), reason="needs network and an installed Playwright Chromium")


@browser
def test_n24_auto_render_fills_a_javascript_page():
    rc, data, err = run_json("https://quotes.toscrape.com/js/")
    r = data["results"][0]
    assert rc == 0 and r["source"] == "browser" and r["words"] > 100 and "rendering the page in a headless browser" in err
    rc, data, err = run_json("https://quotes.toscrape.com/js/", "--render", "off")
    assert rc == 0 and data["results"][0]["source"] == "live" and data["results"][0]["words"] < 20 and "browser" not in err


@browser
def test_n25_render_always_with_actions_and_screenshot(tmp_path):
    shot = tmp_path / "page.png"
    rc, data, err = run_json("https://quotes.toscrape.com/scroll", "--render", "always", "--actions", '[{"scroll": 2}, {"click": "#nope-missing"}]',
                             "--screenshot", str(shot), "--timeout", "20")
    r = data["results"][0]
    assert rc == 0 and r["source"] == "browser" and r["words"] > 300 and r["screenshot"] == str(shot) and shot.stat().st_size > 1000
    assert "action_failed" in r["warnings"] and "browser action failed" in err


@browser
def test_n26_render_always_maps_network_errors():
    rc, data, _ = run_json("https://nxdomain-web-extract-test.invalid/", "--render", "always", "--no-wayback")
    assert rc == 6 and data["results"][0]["error"]["kind"] == "dns"


@network
def test_n11_timeout():
    # --timeout is per attempt (two attempts, ~1 s streaming overhead each): worst case 2 * (2 + 1) s + startup
    t = time.monotonic()
    rc, _, err = run("https://httpbingo.org/delay/10", "--timeout", "2", "--no-wayback")
    assert rc == 6 and "timed out (limit 2s per attempt, 2 attempts)" in err and time.monotonic() - t < 8


@network
def test_n11b_dripping_body_is_cut_by_the_deadline():
    t = time.monotonic()
    rc, _, err = run("https://httpbingo.org/drip?duration=10&numbytes=10&delay=0", "--timeout", "2", "--no-wayback", "--raw")
    assert rc == 6 and "timed out (limit 2s" in err and time.monotonic() - t < 9


@network
def test_n12_dns():
    rc, data, err = run_json("https://nx-domain-zzz-123456.example/", "--no-wayback")
    assert rc == 6 and data["results"][0]["error"]["kind"] == "dns" and "DNS lookup failed" in err


@network
def test_n13_dns_then_archive_miss():
    rc, data, err = run_json("https://nx-domain-zzz-123456.example/")
    r = data["results"][0]
    skip_if_archive_down(r, err)
    assert rc == 6 and "wayback-cdx: no snapshot" in r["fallback_errors"]


@network
def test_n14_redirects():
    rc, data, _ = run_json("https://httpbingo.org/redirect/3", "--no-wayback")
    r = data["results"][0]
    assert rc == 0 and r["final_url"].endswith("/get")


@network
def test_n15_octet_stream_exit5():
    rc, _, _ = run("https://httpbingo.org/bytes/300000", "--no-wayback")
    assert rc == 5


@network
def test_n16_wayback_mode():
    rc, data, err = run_json("https://example.com", "--wayback")
    r = data["results"][0]
    skip_if_archive_down(r, err)
    assert rc == 0 and r["source"] == "wayback" and re.match(r"^\d{4}-\d{2}-\d{2}T", r["snapshot"])
    assert r["snapshot_url"].startswith("https://web.archive.org/web/") and "web.archive.org" not in r["final_url"]
    assert "archived_copy" in r["warnings"] and r["live_error"] is None


@network
def test_n17_archive_url_fetched_live():
    rc, data, err = run_json("https://web.archive.org/web/20240101000832id_/https://www.example.com/", "--no-wayback")
    r = data["results"][0]
    skip_if_archive_down(r, err)
    if not r["ok"]:
        pytest.skip(f"archive.org answered {r['error']}")
    assert rc == 0 and r["source"] == "live"


@network
def test_n18_cdx_with_query_string_never_7():
    rc, data, err = run_json("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "--wayback")
    r = data["results"][0]
    skip_if_archive_down(r, err)
    assert rc in (0, 3, 1) and rc != 7


@network
def test_n19_wikipedia_article():
    rc, data, _ = run_json("https://en.wikipedia.org/wiki/Web_scraping", "--no-wayback")
    r = data["results"][0]
    assert rc == 0 and r["title"] and "Web scraping" in r["title"] and r["words"] > 2000
    assert r["coverage"] > 0.5 and r["partial"] is False and "## " in r["content"] and r["language"] == "en"


@network
def test_n20_max_chars():
    rc, out, err = run("https://en.wikipedia.org/wiki/Web_scraping", "--max-chars", "5000", "--no-wayback")
    assert rc == 0 and "[truncated: showed 5000 of" in out and "note: output truncated" in err
    rc, data, _ = run_json("https://en.wikipedia.org/wiki/Web_scraping", "--max-chars", "5000", "--no-wayback")
    assert data["results"][0]["truncated"] is True and data["results"][0]["partial"] is True


@network
def test_n21_multi_url_exit_is_first_failure():
    rc, data, _ = run_json("https://example.com", "https://httpbingo.org/status/404", "--no-wayback")
    assert rc == 3 and data["count"] == 2 and data["results"][0]["ok"] and not data["results"][1]["ok"]
    assert data["results"][1]["error"]["code"] == 3


def test_n22_bad_arguments_exit2():
    assert run("--impersonate", "bogus", "https://example.com")[0] == 2
    assert run("--recall", "--precision", "https://example.com")[0] == 2
    assert run("--wayback", "--no-wayback", "https://example.com")[0] == 2
    rc, _, err = run("ftp://x")
    assert rc == 2 and "not an http(s) URL" in err


@network
@jina_optin
def test_n23_jina_unresolvable_host():
    rc, data, _ = run_json("https://nx-domain-zzz-123456.example/", "--jina", "--no-wayback")
    r = data["results"][0]
    assert rc == 6 and any(e.startswith("jina: HTTP 422") for e in r["fallback_errors"])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
