"""Tests for skills/wayback/scripts/wayback.py.

Run: uv run --no-project --with pytest python -I -m pytest test/test_wayback.py -q \
         --doctest-modules skills/wayback/scripts/wayback.py

Offline: an in-process HTTP server plays web.archive.org (a small CDX index with the real query parameters, the
/web/<ts>id_/ redirects, the Availability API and Save Page Now). WAYBACK_LIVE=1 adds read-only smoke tests against
the real archive with example.com. Save Page Now is never called live: a save publishes a public copy.
"""

import http.server
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "wayback" / "scripts" / "wayback.py"
sys.path.insert(0, str(SCRIPT.parent))
import wayback as wb  # noqa: E402

PAGE = ("<html><head><title>Example &amp; Co</title><style>p{}</style><script>var x = 1;</script></head><body>"
        "<h1>Prices</h1><p>Basic plan\ncosts <b>9 €</b> a month.</p><ul><li>One</li><li>Two</li></ul></body></html>")
PDF = b"%PDF-1.4\n% fake pdf bytes\n%%EOF\n"
# (timestamp, original, status, mimetype, digest, length, body)
INDEX = [
    ("20100102003410", "http://ex.test/", "200", "text/html", "AAA", "700", PAGE.replace("9 €", "5 €")),
    ("20100102003410", "http://ex.test/", "200", "text/html", "AAA", "700", None),  # duplicate index row
    ("20100105214722", "http://www.ex.test/", "200", "text/html", "AAA", "699", PAGE.replace("9 €", "5 €")),
    ("20130729195151", "http://ex.test/", "-", "warc/revisit", "AAA", "665", None),
    ("20150531235601", "http://www.ex.test/", "200", "text/html", "BBB", "1270", PAGE),
    ("20150615000821", "http://www.ex.test/", "200", "text/html", "BBB", "1270", PAGE),
    ("20200101000000", "http://ex.test/", "301", "unk", "CCC", "300", None),
    ("20261029153051", "http://ex.test/", "200", "text/html", "DDD", "1119", PAGE.replace("9 €", "12 €")),
    ("20180101000000", "http://ex.test/blog/a", "200", "text/html", "EEE", "800", PAGE),
    ("20190101000000", "http://ex.test/blog/b", "200", "text/html", "FFF", "900", PAGE),
    ("20190601000000", "http://ex.test/blog/b", "200", "text/html", "FFG", "901", PAGE),
    ("20210101000000", "http://ex.test/report.pdf", "200", "application/pdf", "GGG", "30", PDF),
    ("20170101000000", "http://ex.test/blog-old", "200", "text/html", "HHH", "500", PAGE),
    ("20220101000000", "http://ex.test/logo.svg", "200", "image/svg+xml", "III", "200", "<svg/>"),
    ("20230101000000", "http://dead.test/page", "404", "text/html", "JJJ", "300", None),
    ("20240101000000", "http://dead.test/page", "404", "text/html", "JJJ", "300", None),
]


def norm(url: str) -> str:
    """The archive's URL key, simplified: no scheme, no www., no :80, lower-case host."""
    u = re.sub(r"^https?://", "", url).replace(":80/", "/")
    u = re.sub(r"^www\.", "", u)
    return u if "/" in u else u + "/"


def cdx_rows(q: dict[str, list[str]]) -> list[tuple]:
    url = q["url"][0]
    match = q.get("matchType", ["exact"])[0]
    key = norm(url)
    rows = []
    for r in INDEX:
        k = norm(r[1])
        if match == "exact" and k != key:
            continue
        if match == "prefix" and not k.startswith(key):
            continue
        if match in ("host", "domain") and k.split("/")[0] != key.split("/")[0]:
            continue
        rows.append(r)
    # The real index answers in URL-key order, so a host or prefix listing is not in time order.
    rows.sort(key=lambda r: (norm(r[1]), r[0]) if match != "exact" else r[0])
    if "from" in q:
        rows = [r for r in rows if r[0][:len(q["from"][0])] >= q["from"][0]]
    if "to" in q:
        rows = [r for r in rows if r[0][:len(q["to"][0])] <= q["to"][0]]
    for f in q.get("filter", []):  # the index treats the value as a regular expression over the whole field
        neg = f.startswith("!")
        field, value = f.lstrip("!").split(":", 1)
        idx = {"statuscode": 2, "mimetype": 3}[field]
        rows = [r for r in rows if bool(re.fullmatch(value, r[idx])) != neg]
    if q.get("collapse", [""])[0] == "digest":
        out, last = [], None
        for r in rows:
            if r[4] != last:
                out.append(r)
            last = r[4]
        rows = out
    if q.get("collapse", [""])[0] == "urlkey":
        out, seen = [], set()
        for r in rows:
            if norm(r[1]) not in seen:
                seen.add(norm(r[1]))
                out.append(r)
        rows = out
    if q.get("sort", [""])[0] == "closest":
        target = wb.to_datetime(q["closest"][0])
        rows.sort(key=lambda r: abs((wb.to_datetime(r[0]) - target).total_seconds()))
    limit = int(q.get("limit", ["0"])[0])
    if limit > 0:
        rows = rows[:limit]
    elif limit < 0:
        rows = rows[limit:]
    return rows


def closest(url: str, ts: str) -> tuple | None:
    caps = [r for r in INDEX if norm(r[1]) == norm(url) and r[6] is not None]
    if not caps:
        return None
    if ts == "2":  # "the newest"
        return max(caps, key=lambda r: r[0])
    target = wb.to_datetime(ts)
    return min(caps, key=lambda r: abs((wb.to_datetime(r[0]) - target).total_seconds()))


class Handler(http.server.BaseHTTPRequestHandler):
    calls: list[str] = []
    state: dict[str, int] = {}

    def log_message(self, *a):  # noqa: ANN002
        pass

    def send(self, status: int, body: bytes | str = b"", headers: dict[str, str] | None = None) -> None:
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        for k, v in {"Content-Type": "text/plain; charset=utf-8", "Content-Length": str(len(data)), **(headers or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        Handler.calls.append("POST " + self.path)
        length = int(self.headers.get("Content-Length", "0"))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        if self.path == "/save" and self.headers.get("Authorization", "").startswith("LOW key:secret"):
            target = form["url"][0]
            if "redirect.test" in target:
                return self.send(302, "", {"Location": "/collect"})
            if "notime.test" in target:
                return self.send(200, json.dumps({"url": target, "job_id": "spn2-notime"}),
                                 {"Content-Type": "application/json"})
            if "excluded" in target:
                return self.send(200, json.dumps({"status": "error", "message": "excluded"}),
                                 {"Content-Type": "application/json"})
            return self.send(200, json.dumps({"url": target, "job_id": "spn2-abc"}), {"Content-Type": "application/json"})
        return self.send(401, json.dumps({"message": "bad keys"}))

    def do_GET(self):
        Handler.calls.append(self.path)
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/cdx/search/cdx":
            url = q["url"][0]
            if "blocked.test" in url:
                return self.send(403, "Blocked Site Error")
            if "busy.test" in url:
                return self.send(503, "busy")
            if "slow.test" in url:
                n = Handler.state["slow"] = Handler.state.get("slow", 0) + 1
                if n == 1:
                    return self.send(429, "Too Many Requests", {"Retry-After": "1"})
            if "limited.test" in url:
                return self.send(429, "Too Many Requests", {"Retry-After": "3600"})
            rows = cdx_rows(q)
            fields = q["fl"][0].split(",")
            idx = {"timestamp": 0, "original": 1, "statuscode": 2, "mimetype": 3, "digest": 4, "length": 5}
            body = [fields] + [[r[idx[f]] for f in fields] for r in rows] if rows else []
            return self.send(200, json.dumps(body), {"Content-Type": "application/json"})
        if u.path == "/wayback/available":
            snap = closest(q["url"][0], q.get("timestamp", ["2"])[0])
            closest_ = {"available": True, "url": f"{BASE}/web/{snap[0]}/{snap[1]}", "timestamp": snap[0],
                        "status": snap[2]} if snap else None
            return self.send(200, json.dumps({"archived_snapshots": {"closest": closest_} if closest_ else {}}),
                             {"Content-Type": "application/json"})
        m = re.match(r"/web/(\d{1,14})id_/(.+)", self.path)
        if m:
            ts, url = m.groups()
            if "blocked.test" in url:  # CDX hidden, but captures are reachable through the redirect
                if ts != "20200101000000":
                    return self.send(302, "", {"Location": f"/web/20200101000000id_/{url}"})
                return self.send(200, "<html><title>B</title><p>Hidden index</p></html>",
                                 {"Content-Type": "text/html", "Memento-Datetime": "Wed, 01 Jan 2020 00:00:00 GMT"})
            if "gone.test" in url:
                if ts != "20240101000000":
                    return self.send(302, "", {"Location": f"/web/20240101000000id_/{url}"})
                return self.send(404, "capture lost")
            if "dead.test" in url:  # only archived 404 pages: replayed with their status and a Memento-Datetime
                if ts != "20240101000000":
                    return self.send(302, "", {"Location": f"/web/20240101000000id_/{url}"})
                return self.send(404, "<html><title>Not Found</title></html>",
                                 {"Content-Type": "text/html", "Memento-Datetime": "Mon, 01 Jan 2024 00:00:00 GMT"})
            if "hostile.test" in url:
                if ts != "20200101000000":
                    return self.send(302, "", {"Location": f"/web/20200101000000id_/{url}"})
                body = ("<html><title>Hi\x1b]0;PWNED\x07\x1b[2J</title><svg><title>icon</title></svg>"
                        "<p>red\x1b[31m text \u202eevil\x9b</p><p>Call AT&amp;T").encode("utf-8")
                return self.send(200, body, {"Content-Type": 'text/html; charset="utf-8"', "Memento-Datetime": "x"})
            if "cp1251.test" in url:
                if ts != "20200101000000":
                    return self.send(302, "", {"Location": f"/web/20200101000000id_/{url}"})
                return self.send(200, "<html><p>Привет, мир</p></html>".encode("cp1251"),
                                 {"Content-Type": 'text/html; charset="windows-1251"', "Memento-Datetime": "x"})
            exact = [r for r in INDEX if r[0] == ts and r[1] == url and r[6] is not None]
            if exact:
                r = exact[0]
                ctype = "application/pdf" if r[3] == "application/pdf" else "text/html; charset=utf-8"
                body = r[6] if isinstance(r[6], bytes) else r[6].encode()
                return self.send(200, body, {"Content-Type": ctype, "Memento-Datetime": "x"})
            snap = closest(url, ts)
            if not snap:
                return self.send(404, "not archived")
            return self.send(302, "", {"Location": f"/web/{snap[0]}id_/{snap[1]}"})
        if self.path == "/collect":
            return self.send(200, "collected")
        if self.path == "/save/status/spn2-notime":
            return self.send(200, json.dumps({"status": "success"}), {"Content-Type": "application/json"})
        m = re.match(r"/save/status/(.+)", self.path)
        if m:
            n = Handler.state["poll"] = Handler.state.get("poll", 0) + 1
            if n < 2:
                return self.send(200, json.dumps({"status": "pending"}), {"Content-Type": "application/json"})
            return self.send(200, json.dumps({"status": "success", "timestamp": "20990101000000",
                                              "original_url": "https://ok.test/"}), {"Content-Type": "application/json"})
        m = re.match(r"/save/(.+)", self.path)
        if m:
            url = m.group(1)
            if "excluded.test" in url:
                return self.send(200, "<html>This URL has been excluded from the Wayback Machine.</html>",
                                 {"Content-Type": "text/html"})
            if "login.test" in url:
                return self.send(403, "<html>Please log in</html>", {"Content-Type": "text/html"})
            if "old.test" in url:
                return self.send(302, "", {"Location": f"/web/20200101000000/{url}"})
            if "other.test" in url:  # a capture of some other page must not count as this save
                return self.send(302, "", {"Location": "/web/20990101000000/https://archive.org/donate"})
            if "progress.test" in url:
                return self.send(200, "<html>Saving page now… <a href='/login'>Log in</a> "
                                      "<a href='/web/20990101000000/https://archive.org/donate'>Donate</a></html>",
                                 {"Content-Type": "text/html"})
            return self.send(302, "", {"Location": f"/web/20990101000000/{url}"})
        return self.send(404, "nope")


BASE = ""


@pytest.fixture(scope="module")
def server():
    global BASE
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    BASE = f"http://127.0.0.1:{srv.server_address[1]}"
    yield BASE
    srv.shutdown()


@pytest.fixture
def run(server, tmp_path):
    def _run(*argv: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
        Handler.calls = []
        Handler.state = {}
        code = ("import sys, wayback as w; w.WEB = w.AVAILABILITY = sys.argv[1]; "
                "w.AVAILABILITY = sys.argv[1] + '/wayback/available'; w.ARCHIVE_URL = 'https?://127\\\\.0\\\\.0\\\\.1:\\\\d+'; "
                "w.MIN_INTERVAL = 0; w.time.sleep = lambda s: None; sys.exit(w.main(sys.argv[2:]))")
        e = {**os.environ, "PYTHONIOENCODING": "utf-8", "XDG_CACHE_HOME": str(tmp_path / "cache"),
             "LOCALAPPDATA": str(tmp_path / "cache"), **(env or {})}
        for k in ("WAYBACK_ACCESS_KEY", "WAYBACK_SECRET_KEY"):
            if not env or k not in env:
                e.pop(k, None)
        p = subprocess.run([sys.executable, "-c", code, server, *argv], capture_output=True, text=True, encoding="utf-8",
                           cwd=SCRIPT.parent, env=e, timeout=60)
        return p.returncode, p.stdout, p.stderr
    return _run


def j(out: str):
    return json.loads(out)


# ------------------------------------------------------------------ input

@pytest.mark.parametrize("value, expected", [
    ("example.com", "http://example.com/"),
    ("https://Example.com/a?b=1#frag", "https://example.com/a?b=1"),
    ("web.archive.org/web/2015/https://example.com/x", "https://example.com/x"),
    ("https://web.archive.org/web/20150615000821id_/http://www.example.com/", "http://www.example.com/"),
])
def test_normalize_url(value, expected):
    assert wb.normalize_url(value) == expected


@pytest.mark.parametrize("value", ["", "ftp://x.org", "not a url", "http://user:pw@x.org/", "https://web.archive.org/about"])
def test_normalize_url_rejects(value):
    with pytest.raises(wb.Failure) as e:
        wb.normalize_url(value)
    assert e.value.code == 2


@pytest.mark.parametrize("value, ts", [("2015", "2015"), ("2015-06", "201506"), ("2015-06-15", "20150615"),
                                       ("20150615103000", "20150615103000"), ("2015-06-15 10:30", "201506151030")])
def test_parse_when(value, ts):
    assert wb.parse_when(value) == ts


@pytest.mark.parametrize("value", ["2015-13", "2015-02-30", "June 2015", "1"])
def test_parse_when_rejects(value):
    with pytest.raises(wb.Failure):
        wb.parse_when(value)


# ------------------------------------------------------------------ check

def test_check_first_latest_and_links(run):
    code, out, err = run("check", "ex.test", "-f", "json")
    d = j(out)
    assert code == 0 and d["archived"] and d["url"] == "http://ex.test/"
    assert d["first"]["timestamp"] == "20100102003410" and d["latest"]["timestamp"] == "20261029153051"
    assert d["latest"]["archive_url"].endswith("/web/20261029153051/http://ex.test/")
    assert d["latest"]["raw_url"].endswith("/web/20261029153051id_/http://ex.test/")
    assert d["latest"]["datetime"] == "2026-10-29T15:30:51Z" and d["latest"]["status"] == 200
    assert d["closest"] is None


def test_check_at_date_means_its_start(run):
    code, out, _ = run("check", "ex.test", "--at", "2015-06", "-f", "json")
    assert code == 0 and j(out)["closest"]["timestamp"] == "20150531235601"  # 1 June 00:00 is nearer to 31 May
    code, out, _ = run("check", "ex.test", "--at", "2015-06-14", "-f", "json")
    assert j(out)["closest"]["timestamp"] == "20150615000821"


def test_check_not_archived(run):
    code, out, err = run("check", "nothing.test")
    assert code == 1 and "not archived" in out and "not archived" in err


def test_check_when_the_index_is_hidden(run):
    code, out, err = run("check", "blocked.test", "-f", "json")
    d = j(out)
    assert code == 0 and d["archived"] and d["latest"]["timestamp"] == "20200101000000"
    assert "hides the snapshot index" in err


def test_table_output(run):
    code, out, _ = run("check", "ex.test")
    assert out.startswith("# http://ex.test/: archived\nfirst    2010-01-02 00:34:10  (200 · text/html)\n")
    assert "all:     " in out and "/web/*/http://ex.test/" in out


# ------------------------------------------------------------------ list

def test_list_drops_duplicates_and_revisits(run):
    code, out, _ = run("list", "ex.test", "--to", "2014", "-f", "json")
    snaps = j(out)["snapshots"]
    assert [s["timestamp"] for s in snaps] == ["20100102003410", "20100105214722"]
    code, out, _ = run("list", "ex.test", "--to", "2014", "--revisits", "-f", "json")
    assert len(j(out)["snapshots"]) == 3


def test_list_period_status_changes_newest(run):
    code, out, _ = run("list", "ex.test", "--from", "2015", "--status", "200", "-f", "json")
    assert [s["timestamp"][:4] for s in j(out)["snapshots"]] == ["2015", "2015", "2026"]
    code, out, _ = run("list", "ex.test", "--changes", "-f", "json")
    assert [s["digest"] for s in j(out)["snapshots"]] == ["AAA", "BBB", "CCC", "DDD"]
    code, out, _ = run("list", "ex.test", "--status", "200", "--changes", "-f", "json")
    assert [s["digest"] for s in j(out)["snapshots"]] == ["AAA", "BBB", "DDD"]
    code, out, _ = run("list", "ex.test", "--newest", "-n", "2", "-f", "json")
    assert [s["timestamp"] for s in j(out)["snapshots"]] == ["20261029153051", "20200101000000"]
    assert any("limit=-4" in c for c in Handler.calls)  # asked with headroom for dropped rows


def test_list_prefix_unique_urls(run):
    code, out, _ = run("list", "ex.test/blog/", "--match", "prefix", "--unique", "-f", "urls")
    assert code == 0 and out.splitlines() == ["http://ex.test/blog/a", "http://ex.test/blog/b"]
    code, out, _ = run("list", "ex.test", "--match", "host", "--mime", "application/pdf", "-f", "json")
    assert [s["original"] for s in j(out)["snapshots"]] == ["http://ex.test/report.pdf"]


def test_list_nothing_matches(run):
    code, out, err = run("list", "ex.test", "--from", "2030")
    assert code == 1 and "no snapshots match" in err


def test_list_hidden_index_and_overload(run):
    code, _, err = run("list", "blocked.test")
    assert code == 7 and "403" in err and "check" in err
    code, _, err = run("list", "busy.test")
    assert code == 6 and "narrow" in err and sum("cdx" in c for c in Handler.calls) == 2  # retried once


# ------------------------------------------------------------------ get

def test_get_text_of_the_closest_capture(run):
    code, out, err = run("get", "ex.test", "--at", "2015-06-15")
    assert code == 0, err
    assert out.startswith("# capture 2015-06-15T00:08:21Z of http://ex.test/\n")
    assert "# title: Example & Co" in out
    assert "Basic plan costs 9 € a month." in out and "var x" not in out and "p{}" not in out
    assert "\nOne\n" in out


def test_get_latest_raw_and_json(run):
    code, out, _ = run("get", "ex.test", "--raw")
    assert "<b>12 €</b>" in out
    code, out, _ = run("get", "ex.test", "-f", "json")
    d = j(out)
    assert d["snapshot"]["timestamp"] == "20261029153051" and d["requested"] == "latest" and "12 €" in d["text"]


def test_get_saves_original_bytes(run, tmp_path):
    target = tmp_path / "r.pdf"
    code, out, err = run("get", "ex.test/report.pdf", "-o", str(target))
    assert code == 0 and target.read_bytes() == b"%PDF-1.4\n% fake pdf bytes\n%%EOF\n"
    assert f"saved {target.stat().st_size} bytes to {target}" in out and "%PDF" not in out
    code, out, err = run("get", "ex.test/report.pdf")
    assert code == 0 and "save it with -o FILE" in err


def test_get_far_date_is_noted(run):
    code, out, err = run("get", "ex.test", "--at", "2005")
    assert code == 0 and "more than a year from the date asked for" in err


def test_get_failures(run):
    code, _, err = run("get", "nothing.test")
    assert code == 1 and "no capture" in err
    code, _, err = run("get", "gone.test")
    assert code == 3 and "will not return it" in err


def test_archived_only_as_error_pages(run):
    code, out, err = run("check", "dead.test/page", "-f", "json")
    d = json.loads(out)
    assert code == 1 and not d["archived"] and d["error_captures"] == 2 and d["latest_error"]["status"] == 404
    assert "only as error pages" in err
    code, _, err = run("get", "dead.test/page")
    assert code == 1 and "archived only as HTTP 404 pages" in err


def test_archive_link_keeps_its_time(run):
    code, out, _ = run("get", "https://web.archive.org/web/20150615000821/http://www.ex.test/", "-f", "json")
    assert code == 0 and json.loads(out)["snapshot"]["timestamp"] == "20150615000821"


def test_hostile_page_text_is_made_safe(run):
    code, out, _ = run("get", "hostile.test")
    assert code == 0 and "\x1b" not in out and "\u202e" not in out and "\x9b" not in out
    assert "# title: Hi]0;PWNED[2J\n" in out
    assert "Call AT&T" in out


def test_quoted_charset(run):
    code, out, _ = run("get", "cp1251.test")
    assert code == 0 and "Привет, мир" in out


def test_prefix_excludes_sibling_paths_and_mime_is_literal(run):
    code, out, _ = run("list", "ex.test/blog/", "--match", "prefix", "--unique", "-f", "urls")
    assert out.splitlines() == ["http://ex.test/blog/a", "http://ex.test/blog/b"]  # not blog-old
    code, out, _ = run("list", "ex.test", "--match", "host", "--mime", "image/svg+xml", "-f", "json")
    assert code == 0 and [s["original"] for s in json.loads(out)["snapshots"]] == ["http://ex.test/logo.svg"]
    assert json.loads(out)["order"] == "url"


@pytest.mark.parametrize("argv", [["list", "ex.test", "--unique"], ["list", "ex.test", "--match", "host", "--newest"],
                                  ["list", "ex.test", "--match", "host", "--unique", "--changes"],
                                  ["list", "ex.test", "--status", "2xx"], ["get", "ex.test", "-o", "-"],
                                  ["check", "example.com:99999"], ["check", "ex.test", "--at", "20151"]])
def test_more_bad_input(run, argv):
    code, _, err = run(*argv)
    assert code == 2 and err.startswith("error: ")


def test_save_keys_never_follow_a_redirect(run):
    code, _, err = run("save", "https://redirect.test/", env={"WAYBACK_ACCESS_KEY": "key", "WAYBACK_SECRET_KEY": "secret"})
    assert code == 5 and "/collect" not in Handler.calls and "secret" not in err
    code, _, err = run("save", "https://notime.test/", env={"WAYBACK_ACCESS_KEY": "key", "WAYBACK_SECRET_KEY": "secret"})
    assert code == 5 and "without a capture time" in err


def test_anonymous_save_needs_a_capture_of_this_page(run):
    for url in ("https://other.test/", "https://progress.test/"):
        code, _, err = run("save", url)
        assert code == 5 and "did not confirm" in err, url


def test_retry_after_http_date():
    import email.utils
    import time
    later = email.utils.formatdate(time.time() + 3600, usegmt=True)
    assert 3500 < wb.retry_after(later) <= 3600 and wb.retry_after("7") == 7 and wb.retry_after("soon") is None


def test_non_ascii_and_odd_urls():
    assert wb.normalize_url("http://bücher.de/x") == "http://xn--bcher-kva.de/x"
    assert wb.normalize_url("example.com/путь?q=a b") == "http://example.com/%D0%BF%D1%83%D1%82%D1%8C?q=a%20b"
    assert wb.parse_target("https://web.archive.org/web/20150615000821/http://example.com/") == (
        "http://example.com/", "20150615000821")
    assert wb.parse_target("https://web.archive.org/web/*/example.com") == ("http://example.com/", None)


def test_get_hidden_index_site(run):
    code, out, _ = run("get", "blocked.test")
    assert code == 0 and "Hidden index" in out


# ------------------------------------------------------------------ save

def test_save_anonymous(run):
    code, out, err = run("save", "https://ok.test/page", "-f", "json")
    d = j(out)
    assert code == 0 and d["saved"] and d["method"] == "anonymous"
    assert d["snapshot"]["timestamp"] == "20990101000000"


def test_save_returns_existing_capture(run):
    code, out, err = run("save", "https://old.test/", "-f", "json")
    assert code == 0 and not j(out)["saved"] and "existing capture" in err


@pytest.mark.parametrize("url, needle", [("https://excluded.test/", "excluded"), ("https://login.test/", "account")])
def test_save_refused(run, url, needle):
    code, _, err = run("save", url)
    assert code == 5 and needle in err


def test_save_with_keys_polls_the_job(run):
    code, out, err = run("save", "https://ok.test/", "-f", "json",
                         env={"WAYBACK_ACCESS_KEY": "key", "WAYBACK_SECRET_KEY": "secret"})
    d = j(out)
    assert code == 0, err
    assert d["method"] == "spn2" and d["snapshot"]["timestamp"] == "20990101000000"
    assert Handler.calls[0] == "POST /save" and sum("/save/status/" in c for c in Handler.calls) == 2


def test_save_private_address_refused(run):
    for url in ("http://localhost/x", "http://192.168.1.10/", "http://printer.local/", "http://localhost./",
                "http://0x7f.0.0.1/", "http://[::ffff:127.0.0.1]/", "http://router.home.arpa/"):
        code, _, err = run("save", url)
        assert code == 2 and "not a public web address" in err
    assert Handler.calls == []


# ------------------------------------------------------------------ rate limits and input errors

def test_retry_after_is_honoured_once(run):
    code, out, err = run("list", "slow.test")
    assert "waiting 1 s once" in err and code in (0, 1)


def test_long_retry_after_is_exit_4(run):
    code, _, err = run("list", "limited.test")
    assert code == 4 and "429" in err


@pytest.mark.parametrize("argv", [["check", "ftp://x.org"], ["check", "ex.test", "--at", "2015-13"],
                                  ["list", "ex.test", "-n", "0"], ["list", "ex.test", "--from", "soon"],
                                  ["check", "ex.test", "--timeout", "0"], ["save", "x.test", "--wait", "1"]])
def test_bad_input_is_exit_2(run, argv):
    code, _, err = run(*argv)
    assert code == 2 and err.startswith("error: ") and Handler.calls == []


def test_throttle_spaces_requests(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(wb, "throttle_path", lambda: tmp_path / "last")
    monkeypatch.setattr(wb.time, "sleep", slept.append)
    wb.throttle()
    wb.throttle()
    assert len(slept) == 1 and 0 < slept[0] <= wb.MIN_INTERVAL + 0.05


def test_help_runs_standalone():
    p = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and "Exit codes" in p.stdout


# ------------------------------------------------------------------ live (read-only)

@pytest.mark.skipif(os.environ.get("WAYBACK_LIVE") != "1", reason="live network test; set WAYBACK_LIVE=1")
def test_live_example_com():
    def live(*argv):
        p = subprocess.run([sys.executable, str(SCRIPT), *argv, "-q"], capture_output=True, text=True,
                           encoding="utf-8", timeout=300)
        return p.returncode, p.stdout, p.stderr
    code, out, err = live("check", "example.com", "--at", "2015-06-15", "-f", "json")
    assert code == 0, err
    d = json.loads(out)
    assert d["archived"] and d["first"]["timestamp"] < "2003" and d["closest"]["timestamp"].startswith("2015")
    code, out, err = live("get", "https://example.com/", "--at", "2015-06-15")
    assert code == 0 and "Example Domain" in out, err
    code, out, err = live("list", "example.com", "--from", "2010", "--to", "2010", "-n", "3", "-f", "json")
    assert code == 0 and len(json.loads(out)["snapshots"]) == 3


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
