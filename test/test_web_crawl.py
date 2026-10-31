"""Tests for skills/web-crawl/scripts/crawl.py.

All cases run against a local HTTP server started in-process (no network): a small
site with robots.txt, a sitemap index, nested pages, an excluded area, binary links,
external links, a duplicate page, a JavaScript shell and a PDF. The script is run
as a subprocess so exit codes and output formats are tested end to end.
"""

import gzip
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
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


@pytest.fixture
def mini_server():
    """Start a throwaway server for a routes dict {path: (status, content-type, body[, delay_s])}; `{origin}` in bodies is replaced."""
    servers = []

    def start(routes):
        class H(http.server.BaseHTTPRequestHandler):
            hits: list[str] = []

            def log_message(self, *a):
                pass

            def do_GET(self):
                H.hits.append(self.path)
                status, ctype, body, *delay = routes.get(self.path.split("?", 1)[0], (404, "text/html", b"<html><body>nope</body></html>"))
                if delay:
                    time.sleep(delay[0])
                body = body.replace(b"{origin}", f"http://{self.headers['Host']}".encode())
                try:
                    self.send_response(status)
                    if ctype == "redirect":
                        self.send_header("Location", body.decode())
                        body = b""
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:  # client gave up (timeout test)
                    pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}", H.hits

    yield start
    for srv in servers:
        srv.shutdown()


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
    assert cw.page_links(b"\xff\xfe not html", "https://x.example/") == []
    assert cw.page_links(b"", "https://x.example/") == []
    assert cw.page_links(b"<a href='http://localhost:PORT/api'>x</a><a href='/ok'>y</a>", "https://x.example/") == ["https://x.example/ok"]
    assert cw.page_links(b"<link rel='alternate' hreflang='de' href='/de/'><link rel='next' href='/p2'>", "https://x.example/") == ["https://x.example/p2"]
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


def test_robots_403_and_5xx_disallow_all(mini_server):
    origin, hits = mini_server({"/robots.txt": (403, "text/plain", b"no"), "/": (200, "text/html", SITE["/about"])})
    rc, data, _ = run_json(f"{origin}/", "--no-sitemap")
    assert rc == 4 and data["failed"][0]["error"]["kind"] == "robots_disallow" and "/" not in hits
    origin, hits = mini_server({"/robots.txt": (503, "text/plain", b"down"), "/": (200, "text/html", SITE["/about"])})
    rc, data, _ = run_json(f"{origin}/", "--no-sitemap")
    assert rc == 4 and data["failed"][0]["error"]["kind"] == "robots_unreachable" and "/" not in hits
    assert any("RFC 9309" in n for n in data["stats"]["notes"])


def test_crawl_delay_is_capped(mini_server):
    origin, _ = mini_server({"/robots.txt": (200, "text/plain", b"User-agent: *\nCrawl-delay: 20\n"), "/": (200, "text/html", SITE["/about"])})
    rc, data, _ = run_json(f"{origin}/", "--no-sitemap", "--depth", "0")
    assert rc == 0 and any("Crawl-delay 20s: waiting 10s" in n and "capped" in n for n in data["stats"]["notes"])


def test_markdown_stdout_is_truncated(mini_server):
    big = page("Big", "".join(f"<p>Paragraph {i} " + "with plenty of words in it " * 12 + "</p>" for i in range(400)))
    origin, _ = mini_server({"/": (200, "text/html", big)})
    rc, out, err = run(f"{origin}/", "--no-sitemap", "--depth", "0")
    assert rc == 0 and len(out) < 61_000 and "[truncated: showed " in out and "output truncated" in err


def test_missing_web_extract_and_override(tmp_path):
    script = tmp_path / "a" / "b" / "scripts" / "crawl.py"
    script.parent.mkdir(parents=True)
    shutil.copy(SCRIPT, script)
    (tmp_path / "home").mkdir()
    env = {k: v for k, v in os.environ.items() if k != "WEB_EXTRACT_SCRIPT"} | {"HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home")}
    p = subprocess.run([sys.executable, str(script), "--help"], check=False, capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    assert p.returncode == 2 and "web-extract skill was not found" in p.stderr
    env["WEB_EXTRACT_SCRIPT"] = str(ROOT / "skills" / "web-extract" / "scripts" / "extract.py")
    p = subprocess.run([sys.executable, str(script), "--help"], check=False, capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    assert p.returncode == 0 and "--max-pages" in p.stdout


def test_max_time_bounds_wall_clock(mini_server):
    routes = {"/": (200, "text/html", page("Home", PARA, [f"/slow/{i}" for i in range(8)]))}
    routes |= {f"/slow/{i}": (200, "text/html", page(f"Slow {i}", PARA), 6) for i in range(8)}
    origin, _ = mini_server(routes)
    t0 = time.monotonic()
    rc, data, _ = run_json(f"{origin}/", "--no-sitemap", "--no-robots", "--max-time", "2", "--timeout", "3", "--concurrency", "2")
    wall = time.monotonic() - t0
    assert rc == 0 and data["count"] == 1
    assert data["stats"]["elapsed_s"] <= 2 + 3 and wall < 2 + 3 + 5  # budget + one request timeout (+ interpreter start-up)
    assert any(n.startswith("stopped: max_time") for n in data["stats"]["notes"])


def test_sitemap_comment_offsite_and_ports(mini_server):
    other, other_hits = mini_server({"/internal.xml": (200, "application/xml", b"<urlset><url><loc>http://10.0.0.1/x</loc></url></urlset>")})
    sm = b"<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><!-- generated --><?pi x?><url><loc>{origin}/from-sitemap</loc></url></urlset>"
    home = page("Home", PARA, ["http://localhost:PORT/api", "/next", f"{other}/elsewhere"])
    origin, _ = mini_server({
        "/robots.txt": (200, "text/plain", f"User-agent: *\nSitemap: {{origin}}/sm.xml\nSitemap: {other}/internal.xml\n".encode()),
        "/sm.xml": (200, "application/xml", sm), "/": (200, "text/html", home),
        "/next": (200, "text/html", page("Next", PARA + "<p>Next page.</p>")),
        "/from-sitemap": (200, "text/html", page("From sitemap", PARA + "<p>Sitemap page.</p>"))})
    rc, data, _ = run_json(f"{origin}/")
    urls = {p["url"] for p in data["pages"]}
    assert rc == 0 and urls == {f"{origin}/", f"{origin}/next", f"{origin}/from-sitemap"}
    assert other_hits == [] and any("internal.xml: off-site, skipped" in n for n in data["stats"]["notes"])
    assert data["stats"]["skipped"]["other_port"] == 1  # same host 127.0.0.1, other server's port
    rc, out, _ = run(f"{origin}/", "--map", "-f", "jsonl")
    rows = [json.loads(ln) for ln in out.splitlines()]
    assert rc == 0 and {"url": f"{origin}/next", "title": "Next"} in rows and "stats" in rows[-1] and len(rows) == 4


def test_max_requests_budget(mini_server):
    origin, hits = mini_server({"/": (200, "text/html", page("Home", PARA, [f"/dead/{i}" for i in range(20)]))})
    rc, data, _ = run_json(f"{origin}/", "--no-sitemap", "--max-requests", "4", "--concurrency", "1")
    assert rc == 0 and data["stats"]["requests"] == 4 and len(data["failed"]) == 3
    assert sum(h.startswith("/dead/") for h in hits) == 3 and any(n.startswith("stopped: max_requests") for n in data["stats"]["notes"])


def test_robots_disallowed_links_do_not_use_request_budget(mini_server):
    home = page("Home", PARA, [f"/search/{i}" for i in range(20)] + [f"/doc/{i}" for i in range(3)])
    routes = {"/robots.txt": (200, "text/plain", b"User-agent: *\nDisallow: /search/\n"), "/": (200, "text/html", home)}
    routes |= {f"/doc/{i}": (200, "text/html", page(f"Doc {i}", PARA + f"<p>Document number {i} text.</p>")) for i in range(3)}
    origin, hits = mini_server(routes)
    rc, data, _ = run_json(f"{origin}/", "--no-sitemap", "--max-pages", "5")
    assert rc == 0 and data["count"] == 4 and data["stats"]["requests"] == 4
    assert not any(h.startswith("/search/") for h in hits) and sum(f["error"]["kind"] == "robots_disallow" for f in data["failed"]) == 20


def test_slow_robots_respects_max_time_and_reports_cause(mini_server):
    origin, hits = mini_server({"/robots.txt": (200, "text/plain", b"User-agent: *\n", 8), "/": (200, "text/html", SITE["/about"])})
    rc, data, err = run_json(f"{origin}/", "--no-sitemap", "--max-time", "2")
    assert rc == 6 and data["stats"]["elapsed_s"] <= 3.5 and "/" not in hits
    assert data["failed"][0]["error"]["kind"] == "robots_unreachable" and "error: start URL failed (robots_unreachable)" in err


def test_slow_sitemap_spends_budget_before_start(mini_server):
    origin, hits = mini_server({"/robots.txt": (200, "text/plain", b"User-agent: *\nSitemap: {origin}/sm.xml\n"),
                                "/sm.xml": (200, "application/xml", b"<urlset></urlset>", 8), "/": (200, "text/html", SITE["/about"])})
    rc, data, err = run_json(f"{origin}/", "--max-time", "2")
    assert rc == 6 and data["failed"][0]["error"]["kind"] == "max_time" and "before the start URL was fetched" in err and "/" not in hits


def test_start_redirect_to_other_port_keeps_links(mini_server):
    target, _ = mini_server({"/": (200, "text/html", page("Home", PARA, ["/next"])),
                             "/next": (200, "text/html", page("Next", PARA + "<p>Next page.</p>"))})
    origin, _ = mini_server({"/": (302, "redirect", f"{target}/".encode())})
    rc, data, _ = run_json(f"{origin}/", "--no-sitemap", "--no-robots")
    assert rc == 0 and {p["title"] for p in data["pages"]} == {"Home", "Next"}


def test_output_probe_creates_nothing(server, tmp_path):
    ns = lambda out=None, out_dir=None: cw.argparse.Namespace(out=out, out_dir=out_dir)  # noqa: E731
    assert cw.check_writable(ns(out=str(tmp_path / "new" / "deeper" / "site.md"))) is None
    assert cw.check_writable(ns(out_dir=str(tmp_path / "pages" / "sub"))) is None
    assert sorted(tmp_path.iterdir()) == []  # the probe created no directories
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    assert cw.check_writable(ns(out=str(blocker / "x.md"))) is not None
    assert cw.check_writable(ns(out_dir=str(blocker))) is not None
    rc, _, err = run(f"{server}/", "--no-sitemap", "-o", str(blocker / "x.md"))
    assert rc == 2 and "error: cannot write" in err


def test_bad_arguments_exit2():
    assert run("not a url")[0] == 2
    assert run("http://example.com:abc/")[0] == 2
    assert run("https://example.com/", "--map", "--out-dir", "d")[0] == 2
    assert run("https://example.com/", "--max-requests", "0")[0] == 2
    assert run("https://example.com/", "--depth", "-1")[0] == 2
    assert run("https://example.com/", "--include", "(")[0] == 2
    assert run("https://example.com/", "-o", "a.md", "--out-dir", "d")[0] == 2
    assert run("https://example.com/", "--max-pages", "0")[0] == 2


def _cached_web_extract(root: Path, market: str, version: str, marker: str) -> Path:
    scripts = root / "plugins" / "cache" / market / "web-extract" / version / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "extract.py").write_text(f"FOUND = {marker!r}\n", encoding="utf-8")
    return scripts


def _found(script: Path, tmp_path: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("WEB_EXTRACT_SCRIPT", "CLAUDE_CONFIG_DIR")}
    env.update({"HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home"), **env_extra})
    code = f"import runpy; m = runpy.run_path({str(script)!r}); print(m['load_web_extract']().FOUND)"
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, cwd=tmp_path, timeout=60)


def _alone(tmp_path: Path, where: Path) -> Path:
    where.mkdir(parents=True)
    (where / "crawl.py").write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    return where / "crawl.py"


@pytest.mark.parametrize("via_env", [False, True])
def test_finds_web_extract_in_plugin_cache(tmp_path, via_env):
    """Installed as a plugin, the skill has no sibling web-extract; it is found in the plugin cache instead."""
    config = tmp_path / "config" if via_env else tmp_path / "home" / ".claude"
    _cached_web_extract(config, "market", "abc123", "plugin cache")
    script = _alone(tmp_path, tmp_path / "web-crawl" / "scripts")
    p = _found(script, tmp_path, {"CLAUDE_CONFIG_DIR": str(config)} if via_env else {})
    assert p.returncode == 0 and p.stdout.strip() == "plugin cache", p.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_plugin_cache_prefers_own_marketplace_and_skips_bad_entries(tmp_path):
    config = tmp_path / "home" / ".claude"
    _cached_web_extract(config, "zedxter-skills", "v1", "own marketplace")
    other = _cached_web_extract(config, "another", "v9", "other marketplace")  # newer, but another marketplace
    os.utime(other / "extract.py", (time.time() + 100, time.time() + 100))
    orphan = _cached_web_extract(config, "zedxter-skills", "v0", "orphaned version")
    (orphan.parent / ".orphaned_at").write_text("1", encoding="utf-8")
    broken = config / "plugins" / "cache" / "zedxter-skills" / "web-extract" / "v2" / "scripts"
    broken.mkdir(parents=True)
    (broken / "extract.py").symlink_to(tmp_path / "nowhere.py")
    plugin = config / "plugins" / "cache" / "zedxter-skills" / "web-crawl" / "v1" / "scripts"
    p = _found(_alone(tmp_path, plugin), tmp_path, {})
    assert p.returncode == 0 and p.stdout.strip() == "own marketplace", p.stderr
    # Run from elsewhere, the newest usable copy in any marketplace wins and the broken one does not crash.
    p = _found(_alone(tmp_path, tmp_path / "elsewhere" / "scripts"), tmp_path, {})
    assert p.returncode == 0 and p.stdout.strip() == "other marketplace", p.stderr


def test_web_extract_lookup_is_the_same_in_every_web_skill():
    """web-crawl, web-scrape, web-browse and web-search carry the same plugin-cache lookup."""
    import re
    blocks = set()
    for name, script in [("web-crawl", "crawl.py"), ("web-scrape", "scrape.py"), ("web-browse", "browse.py"),
                         ("web-search", "search.py")]:
        src = (ROOT / "skills" / name / "scripts" / script).read_text(encoding="utf-8")
        blocks.add(re.search(r"^def plugin_versions\(.*?^    return \[Path\(p\).*?$", src, re.S | re.M)[0])
    assert len(blocks) == 1

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
