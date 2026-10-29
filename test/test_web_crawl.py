"""Tests for skills/web-crawl/scripts/crawl.py.

All cases run against a local HTTP server started in-process (no network): a small
site with robots.txt, a sitemap index, nested pages, an excluded area, binary links,
external links, a duplicate page, a JavaScript shell and a PDF. The script is run
as a subprocess so exit codes and output formats are tested end to end.
"""

import gzip
import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "web-crawl" / "scripts" / "crawl.py"
sys.path.insert(0, str(SCRIPT.parent))
import crawl as cw  # noqa: E402

PARA = "<p>" + "This paragraph carries enough words to be extracted as real page content by the extractor. " * 3 + "</p>"


def page(title, body, links=()):
    nav = "".join(f'<a href="{h}">{h}</a> ' for h in links)
    return (f"<html lang='en'><head><title>{title}</title></head><body><nav>{nav}</nav>"
            f"<article><h1>{title}</h1>{body}</article><footer>{nav}</footer></body></html>").encode()


SITE = {
    "/": page("Home", PARA + "<p>Welcome home paragraph with several more words in it for extraction.</p>",
              ["/docs/", "/about", "/private/secret", "/blog/post-1", "https://other.example/x", "/img/logo.png", "/files/a.zip",
               "/dupe", "/app", "/paper.pdf", "/docs/?utm_source=nav", "mailto:x@y", "#top", "/missing"]),
    "/docs/": page("Docs", PARA + "<p>Documentation index paragraph with words.</p>", ["/docs/install", "/docs/usage", "/docs/deep/one"]),
    "/docs/install": page("Install", PARA + "<p>Install it with uv and run it.</p>", ["/docs/"]),
    "/docs/usage": page("Usage", PARA + "<p>Usage notes paragraph.</p>", ["/docs/deep/one"]),
    "/docs/deep/one": page("Deep One", PARA + "<p>Deep page one.</p>", ["/docs/deep/two"]),
    "/docs/deep/two": page("Deep Two", PARA + "<p>Deep page two.</p>", []),
    "/about": page("About", PARA + "<p>About us paragraph.</p>", ["/"]),
    "/blog/post-1": page("Post 1", PARA + "<p>Blog post one paragraph.</p>", ["/blog/post-2"]),
    "/blog/post-2": page("Post 2", PARA + "<p>Blog post two paragraph.</p>", []),
    "/private/secret": page("Secret", PARA + "<p>Should never be fetched.</p>", []),
    "/dupe": page("Home", PARA + "<p>Welcome home paragraph with several more words in it for extraction.</p>", []),
    "/app": b"<html lang='en'><head><title>App</title></head><body><div id='root'></div><noscript>You need JavaScript.</noscript></body></html>",
    "/only-in-sitemap": page("Sitemap Only", PARA + "<p>Reached through the sitemap.</p>", []),
}
ROBOTS = b"User-agent: *\nDisallow: /private/\nCrawl-delay: 0\nSitemap: {origin}/sitemap-index.xml\n"
SITEMAP_INDEX = b"<?xml version='1.0'?><sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><sitemap><loc>{origin}/sitemap-1.xml.gz</loc></sitemap></sitemapindex>"
SITEMAP_1 = b"<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><url><loc>{origin}/only-in-sitemap</loc></url><url><loc>{origin}/about</loc></url><url><loc>https://other.example/far</loc></url></urlset>"


class Handler(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        origin = f"http://{self.headers['Host']}".encode()
        Handler.hits.append(self.path)
        path = self.path
        if path == "/robots.txt":
            return self._send(200, "text/plain", ROBOTS.replace(b"{origin}", origin))
        if path == "/sitemap-index.xml":
            return self._send(200, "application/xml", SITEMAP_INDEX.replace(b"{origin}", origin))
        if path == "/sitemap-1.xml.gz":
            return self._send(200, "application/gzip", gzip.compress(SITEMAP_1.replace(b"{origin}", origin)))
        if path == "/img/logo.png":
            return self._send(200, "image/png", b"\x89PNG\r\n\x1a\n" + b"x" * 50)
        if path == "/files/a.zip":
            return self._send(200, "application/zip", b"PK\x03\x04" + b"x" * 50)
        if path == "/paper.pdf":
            return self._send(200, "application/pdf", b"%PDF-1.4 broken")
        if path == "/blocked":
            return self._send(403, "text/html", b"<html><title>Just a moment...</title><body>_cf_chl_opt</body></html>")
        base = path.split("?", 1)[0]
        if base in SITE:
            return self._send(200, "text/html; charset=utf-8", SITE[base])
        return self._send(404, "text/html", b"<html><body><p>nope</p></body></html>")

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def run(*args, timeout=120):
    p = subprocess.run([sys.executable, str(SCRIPT), *args, "--impersonate", "none"], capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def run_json(*args):
    rc, out, err = run(*args, "-f", "json")
    return rc, json.loads(out), err


# ---------------------------------------------------------------- pure helpers

def test_canonical_and_site_key():
    assert cw.canonical("HTTP://Example.com:80/a/index.html?utm_campaign=x&z=1&a=2#frag") == "http://example.com/a/?a=2&z=1"
    assert cw.canonical("https://example.com") == "https://example.com/"
    assert cw.canonical("https://example.com:8443/p") == "https://example.com:8443/p"
    assert cw.site_key("www.docs.example.co.uk", True) == "example.co.uk" and cw.site_key("www.docs.example.com", False) == "docs.example.com"
    assert cw.site_key("a.b.example.com", True) == "example.com" and cw.site_key("example.com", True) == "example.com"


def test_page_links_resolves_filters_and_dedupes():
    body = (b"<html><head><base href='/sub/'><link rel='next' href='p2'><link rel='stylesheet' href='x.css'></head><body>"
            b"<a href='a'>a</a><a href='a#frag'>a again</a><a href='../up'>up</a><a href='mailto:x'>m</a><a href='javascript:0'>j</a>"
            b"<a href='https://other.example/z?utm_source=t'>o</a><area href='/area'></body></html>")
    links = cw.page_links(body, "https://site.example/dir/page")
    assert links == ["https://site.example/sub/p2", "https://site.example/sub/a", "https://site.example/up", "https://other.example/z", "https://site.example/area"]
    assert cw.page_links(b"\xff\xfe not html", "https://x.example/") == [] or isinstance(cw.page_links(b"", "https://x.example/"), list)
    assert cw.slug_for("https://x.example/docs/a b/c?x=1") == "docs-a-b-c-x-1.md" and cw.slug_for("https://x.example/") == "index.md"


# ------------------------------------------------------------------ end to end

def test_crawl_collects_pages_respects_robots_and_limits(server):
    Handler.hits.clear()
    rc, data, err = run_json(f"{server}/", "--depth", "2", "--max-pages", "20", "--concurrency", "2")
    assert rc == 0 and data["count"] >= 8
    urls = {p["url"] for p in data["pages"]}
    assert f"{server}/" in urls and f"{server}/docs/" in urls and f"{server}/docs/deep/one" in urls and f"{server}/about" in urls
    assert f"{server}/only-in-sitemap" in urls  # reached via robots.txt → sitemap index → gzipped sitemap
    assert f"{server}/docs/deep/two" not in urls  # depth 3
    assert not any("/private/" in h for h in Handler.hits)  # robots.txt Disallow honoured, never requested
    assert not any(h.startswith(("/img/", "/files/")) for h in Handler.hits)  # binary extensions skipped without a request
    assert not any("other.example" in u for u in urls)
    skipped = data["stats"]["skipped"]
    assert skipped["external"] >= 1 and skipped["binary_extension"] >= 2 and skipped["too_deep"] >= 1 and skipped.get("duplicate_content") == 1
    assert f"{server}/dupe" not in urls
    failed = {f["url"]: f["error"]["kind"] for f in data["failed"]}
    assert failed[f"{server}/private/secret"] == "robots_disallow" and failed[f"{server}/missing"] == "not_found"
    assert failed[f"{server}/app"] == "js_shell" and failed[f"{server}/paper.pdf"] == "non_html"
    home = next(p for p in data["pages"] if p["url"] == f"{server}/")
    assert home["depth"] == 0 and home["parent"] is None and home["title"] == "Home" and home["words"] > 30 and "links" not in home
    docs = next(p for p in data["pages"] if p["url"] == f"{server}/docs/")
    assert docs["parent"] == f"{server}/" and docs["depth"] == 1
    assert Handler.hits.count("/docs/") == 1  # ?utm_source variant canonicalised away
    assert any("sitemap: 3 URLs listed" in n for n in data["stats"]["notes"])
    assert "[1/20]" in err or "d0" in err  # progress notes


def test_include_exclude_depth_and_max_pages(server):
    rc, data, _ = run_json(f"{server}/", "--include", r"/docs/", "--depth", "3", "--max-pages", "50", "--no-sitemap")
    urls = {p["url"] for p in data["pages"]}
    assert rc == 0 and urls == {f"{server}/", f"{server}/docs/", f"{server}/docs/install", f"{server}/docs/usage", f"{server}/docs/deep/one", f"{server}/docs/deep/two"}
    assert data["stats"]["skipped"]["not_included"] >= 3
    rc, data, _ = run_json(f"{server}/", "--exclude", r"/docs/|/blog/", "--no-sitemap")
    urls = {p["url"] for p in data["pages"]}
    assert rc == 0 and not any("/docs/" in u or "/blog/" in u for u in urls) and f"{server}/about" in urls
    rc, data, err = run_json(f"{server}/", "--max-pages", "2", "--no-sitemap")
    assert rc == 0 and data["count"] == 2 and any(n.startswith("stopped: max_pages") for n in data["stats"]["notes"])
    rc, data, _ = run_json(f"{server}/", "--depth", "0", "--no-sitemap")
    assert rc == 0 and data["count"] == 1 and data["stats"]["skipped"]["too_deep"] >= 5


def test_map_mode_lists_urls_without_extraction(server):
    rc, out, err = run(f"{server}/", "--map", "--depth", "1")
    lines = out.strip().splitlines()
    assert rc == 0 and f"{server}/" in lines and f"{server}/docs/" in lines and f"{server}/about" in lines and f"{server}/only-in-sitemap" in lines
    assert all(ln.startswith("http") for ln in lines)
    rc, data, _ = run_json(f"{server}/", "--map", "--depth", "1")
    assert rc == 0 and data["count"] == len(data["urls"]) and data["titles"][f"{server}/docs/"] == "Docs"
    # the JS shell is listed in map mode (it is a page), but still not in a content crawl
    assert f"{server}/app" in data["urls"]


def test_markdown_file_and_out_dir(server, tmp_path):
    out = tmp_path / "site.md"
    rc, stdout, _ = run(f"{server}/", "--depth", "1", "--no-sitemap", "-o", str(out))
    assert rc == 0 and stdout.startswith("saved:") and out.exists()
    text = out.read_text("utf-8")
    assert text.startswith("# Crawl of ") and "## Pages" in text and "## Failed" in text and "## Home" in text and "## Docs" in text
    assert text.index("## Pages") < text.index("\n---\n") < text.index("## Home")
    assert "<!-- url: " in text and "robots_disallow" in text
    d = tmp_path / "pages"
    rc, stdout, _ = run(f"{server}/", "--depth", "1", "--no-sitemap", "--out-dir", str(d))
    assert rc == 0 and "saved:" in stdout and (d / "index.json").exists() and (d / "index.md").exists() and (d / "docs.md").exists()
    idx = json.loads((d / "index.json").read_text("utf-8"))
    assert {p["file"] for p in idx["pages"]} >= {"index.md", "docs.md", "about.md"} and idx["stats"]["pages"] == len(idx["pages"])
    page_text = (d / "docs.md").read_text("utf-8")
    assert page_text.startswith("# title: Docs\n# url: ") and "Documentation index paragraph" in page_text
    rc, out_jsonl, _ = run(f"{server}/", "--depth", "0", "--no-sitemap", "-f", "jsonl")
    rows = [json.loads(ln) for ln in out_jsonl.splitlines()]
    assert rc == 0 and rows[0]["url"] == f"{server}/" and "stats" in rows[-1]


def test_start_url_failures_set_exit_code(server):
    rc, data, err = run_json(f"{server}/missing", "--no-sitemap")
    assert rc == 3 and data["count"] == 0 and data["failed"][0]["error"]["kind"] == "not_found" and "error: start URL failed (not_found)" in err
    rc, data, err = run_json(f"{server}/blocked", "--no-sitemap")
    assert rc == 4 and data["failed"][0]["error"]["kind"] == "cloudflare_challenge"
    rc, data, err = run_json(f"{server}/private/secret", "--no-sitemap")
    assert rc == 4 and data["failed"][0]["error"]["kind"] == "robots_disallow"
    rc, data, err = run_json(f"{server}/private/secret", "--no-sitemap", "--no-robots")
    assert rc == 0 and data["count"] == 1
    rc, data, err = run_json("http://127.0.0.1:9/", "--no-sitemap", "--timeout", "3")
    assert rc == 6 and data["failed"][0]["error"]["code"] == 6


def test_bad_arguments_exit2():
    assert run("not a url")[0] == 2
    assert run("https://example.com/", "--depth", "-1")[0] == 2
    assert run("https://example.com/", "--include", "(")[0] == 2
    assert run("https://example.com/", "-o", "a.md", "--out-dir", "d")[0] == 2
    assert run("https://example.com/", "--max-pages", "0")[0] == 2
