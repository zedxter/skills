"""Tests for skills/hackernews/scripts/hn.py.

Run: uv run --no-project --with pytest python -I -m pytest test/test_hackernews.py -q

Offline: Net.get is replaced with canned answers shaped like Algolia HN Search (search, search_by_date, items) and
the Firebase API (lists, items, users); Net itself is tested against a local HTTP server. HN_LIVE=1 adds the
ticket's smoke test (`item 1`) and a few more against the real services.
"""

import http.server
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "hackernews" / "scripts" / "hn.py"
sys.path.insert(0, str(SCRIPT.parent))
import hn  # noqa: E402

ORIGINAL_GET = hn.Net.get

HITS = [{"objectID": "26406989", "title": "Why asynchronous Rust doesn't work", "url": "https://theta.eu.org/a.html",
         "author": "tazjin", "points": 603, "num_comments": 482, "created_at": "2021-03-10T02:16:55Z",
         "_tags": ["story", "author_tazjin", "story_26406989"]},
        {"objectID": "45774086", "title": "Ask HN: Is async Rust worth it?", "url": None, "author": "bc",
         "points": 449, "num_comments": 245, "created_at": "2025-10-31T16:49:26Z", "story_text": "<p>Asking.",
         "_tags": ["story", "ask_hn", "author_bc"]}]
COMMENT_HIT = {"objectID": "50308504", "comment_text": "haha <i>you</i> reminded me of <a href=\"https://example.com/x\">"
               "https://example.com/x</a>", "author": "ann", "story_id": 50291116, "story_title": "Launch HN: Foo",
               "parent_id": 50303035, "created_at": "2026-10-30T18:52:49Z", "_tags": ["comment", "author_ann"]}
TREE = {"id": 8863, "type": "story", "title": "My YC app: Dropbox", "url": "http://www.getdropbox.com/",
        "author": "dhouston", "points": 104, "created_at": "2007-04-04T19:16:40.000Z", "text": None,
        "children": [
            {"id": 8865, "type": "comment", "author": "a", "created_at": "2007-04-04T19:20:00.000Z",
             "text": "<p>first by time", "children": []},
            {"id": 8884, "type": "comment", "author": "BrandonM", "created_at": "2007-04-05T00:00:00.000Z",
             "text": "I have a few qualms<p>1. ftp<pre><code>  mount curlftpfs\n  svn add</code></pre>",
             "children": [{"id": 8917, "type": "comment", "author": "dhouston", "created_at": "2007-04-05T01:00:00.000Z",
                           "text": "re: the first part", "children": [
                               {"id": 9000, "type": "comment", "author": "BrandonM", "text": "fair",
                                "created_at": "2007-04-05T02:00:00.000Z", "children": []}]}]},
            {"id": 8886, "type": "comment", "author": None, "text": None, "created_at": "2007-04-05T03:00:00.000Z",
             "children": []}]}
FIREBASE_ITEMS = {
    8863: {"by": "dhouston", "descendants": 5, "id": 8863, "kids": [8884, 8886, 8865], "score": 104, "time": 1175714200,
           "title": "My YC app: Dropbox", "type": "story", "url": "http://www.getdropbox.com/"},
    1: {"by": "pg", "id": 1, "score": 57, "time": 1160418111, "title": "Y Combinator", "type": "story",
        "url": "http://ycombinator.com", "descendants": 0},
    2: {"by": "x", "id": 2, "score": 3, "time": 1160418111, "title": "Show HN: thing", "type": "story", "descendants": 1},
    3: {"id": 3, "deleted": True},
    500: {"by": "new", "id": 500, "score": 1, "time": 1700000000, "title": "Brand new", "type": "story", "kids": [501]},
    501: {"by": "c1", "id": 501, "parent": 500, "text": "first!", "time": 1700000100, "type": "comment", "kids": [502]},
    502: {"by": "c2", "id": 502, "parent": 501, "text": "second", "time": 1700000200, "type": "comment"},
}


class FakeNet:
    calls: list[str] = []
    algolia_items: dict = {8863: TREE}

    @staticmethod
    def get(self, url):  # noqa: ANN001, ANN205 - replaces a method
        FakeNet.calls.append(url)
        u = urllib.parse.urlsplit(url)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.hostname == "hn.algolia.com":
            if u.path.startswith("/api/v1/items/"):
                return FakeNet.algolia_items.get(int(u.path.rsplit("/", 1)[1]))
            if q.get("query") == "zzzz":
                return {"hits": [], "nbHits": 0}
            if q.get("tags", "").startswith("story_"):
                return {"hits": [{"objectID": "50291116", "title": "Launch HN: Foo", "_tags": ["story"]}], "nbHits": 1}
            hits = [COMMENT_HIT] if "comment" in q.get("tags", "") else HITS
            if q.get("tags", "").startswith("author_"):
                hits = [COMMENT_HIT, HITS[0]]
            return {"hits": hits[:int(q.get("hitsPerPage", 20))], "nbHits": 99}
        if u.hostname == "hacker-news.firebaseio.com":
            path = u.path[len("/v0/"):]
            if path in ("topstories.json", "showstories.json"):
                return [1, 3, 2, 8863]
            if path.startswith("item/"):
                return FIREBASE_ITEMS.get(int(path[5:-5]))
            if path == "user/pg.json":
                return {"id": "pg", "karma": 157316, "created": 1160418092, "about": "Bug fixer.",
                        "submitted": [1, 2, 3]}
            if path.startswith("user/"):
                return None
        raise AssertionError(f"unexpected request {url}")


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    FakeNet.calls, FakeNet.algolia_items = [], {8863: TREE}
    monkeypatch.setattr(hn.Net, "get", FakeNet.get)


def run(*argv, capsys):
    code = hn.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


def js(*argv, capsys):
    code, out, err = run(*argv, "-f", "json", capsys=capsys)
    assert code == 0, err
    return json.loads(out)


def test_search_filters_reach_algolia(capsys):
    d = js("search", "rust", "async", "--min-points", "100", "--min-comments", "10", "--since", "2024-01-01",
           "--until", "2024-12-31", "--author", "tazjin", "-n", "5", capsys=capsys)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(FakeNet.calls[-1]).query)
    assert q["query"] == ["rust async"] and q["tags"] == ["story,author_tazjin"] and q["hitsPerPage"] == ["5"]
    assert q["numericFilters"] == ["created_at_i>=1704067200,created_at_i<1735689600,points>=100,num_comments>=10"]
    assert d["results"][0]["points"] == 603 and d["results"][1]["tags"] == ["ask_hn"]
    assert d["results"][1]["text"] == "Asking." and d["total"] == 99
    js("search", "x", "--sort", "date", "--type", "show", capsys=capsys)
    u = urllib.parse.urlsplit(FakeNet.calls[-1])
    assert u.path.endswith("/search_by_date") and "tags=show_hn" in u.query


def test_comment_search(capsys):
    code, out, _ = run("search", "python", "--type", "comment", capsys=capsys)
    assert "1. comment on: Launch HN: Foo" in out and "haha *you* reminded me of https://example.com/x" in out


@pytest.mark.parametrize("since,ok", [("24h", True), ("7d", True), ("4w", True), ("6m", True), ("1y", True),
                                      ("2024-02-29", True), ("yesterday", False), ("2024-13-01", False)])
def test_since(since, ok):
    if ok:
        assert hn.since_epoch(since) > 0
    else:
        with pytest.raises(hn.Failure):
            hn.since_epoch(since)


def test_lists_keep_order_and_skip_deleted(capsys):
    d = js("top", "-n", "4", capsys=capsys)
    assert [r["id"] for r in d["results"]] == [1, 2, 8863] and [r["rank"] for r in d["results"]] == [1, 2, 3]
    assert d["results"][1]["tags"] == ["show_hn"]
    code, out, _ = run("top", "-n", "4", "-f", "urls", capsys=capsys)
    assert out.splitlines() == ["http://ycombinator.com", "https://news.ycombinator.com/item?id=2",
                                "http://www.getdropbox.com/"]


def test_item_ranks_top_level_and_renders_tree(capsys):
    d = js("item", "https://news.ycombinator.com/item?id=8863", capsys=capsys)
    assert [c["id"] for c in d["comments"]] == [8884, 8886, 8865]          # Hacker News's ranking, not Algolia's
    assert d["total"] == 5 and d["shown"] == 5 and not d["truncated"]
    first = d["comments"][0]
    assert first["text"] == "I have a few qualms\n\n1. ftp\n\n      mount curlftpfs\n      svn add"
    assert first["replies"][0]["replies"][0]["text"] == "fair" and d["comments"][1]["deleted"]
    code, out, _ = run("item", "8863", capsys=capsys)
    assert out.startswith("# My YC app: Dropbox\n104 points · dhouston · 2007-04-04 · 5 comments\n")
    assert "\n- BrandonM · " in out and "\n  - dhouston · " in out and "\n    - BrandonM · " in out


def test_item_limits(capsys):
    d = js("item", "8863", "--max-comments", "2", capsys=capsys)
    assert d["shown"] == 2 and d["truncated"] and [c["id"] for c in d["comments"]] == [8884]
    d = js("item", "8863", "--depth", "1", capsys=capsys)
    assert all(c["replies"] == [] for c in d["comments"]) and d["comments"][0]["reply_count"] == 2
    code, out, err = run("item", "8863", "--depth", "1", capsys=capsys)
    assert "2 more replies not shown" in out and "comments shown" in err


def test_item_falls_back_to_firebase(capsys):
    d = js("item", "500", capsys=capsys)
    assert d["item"]["title"] == "Brand new" and d["comments"][0]["replies"][0]["text"] == "second"


def test_comment_item_names_its_story(capsys):
    FakeNet.algolia_items[50308504] = {"id": 50308504, "type": "comment", "author": "ann", "text": "hi",
                                       "story_id": 50291116, "parent_id": 1, "children": []}
    code, out, _ = run("item", "50308504", capsys=capsys)
    assert out.startswith("# Comment by ann on: Launch HN: Foo")


def test_item_urls_include_links_in_comments(capsys):
    FakeNet.algolia_items[8863] = {**TREE, "children": TREE["children"] + [
        {"id": 9999, "type": "comment", "author": "z", "text": '<a href="https://lwn.net/x">lwn</a>.', "children": []}]}
    code, out, _ = run("item", "8863", "-f", "urls", capsys=capsys)
    assert out.splitlines() == ["http://www.getdropbox.com/", "https://lwn.net/x"]


def test_user(capsys):
    d = js("user", "pg", "-n", "2", capsys=capsys)
    assert d["user"]["karma"] == 157316 and d["user"]["submissions"] == 3 and d["user"]["created"].startswith("2006")
    assert [r["type"] for r in d["recent"]] == ["comment", "story"]
    code, _, err = run("user", "nobody_here", capsys=capsys)
    assert code == 1 and "no user" in err


def test_markdown_escapes_titles(capsys, monkeypatch):
    def evil(self, url):
        if "algolia" in url:
            return {"hits": [{"objectID": "7", "title": "A [link](http://evil) | pipe\n# head", "url": "http://x.com/a b)",
                              "author": "e\x1b[2J", "points": 1, "_tags": ["story"]}], "nbHits": 1}
        return FakeNet.get(self, url)
    monkeypatch.setattr(hn.Net, "get", evil)
    code, out, _ = run("search", "x", "-f", "markdown", capsys=capsys)
    line = out.splitlines()[2]
    assert line.startswith("1. [A \\[link\\](http://evil) \\| pipe # head](http://x.com/a%20b%29)")
    assert "\x1b" not in out


def test_no_results_and_missing_items(capsys):
    code, _, err = run("search", "zzzz", capsys=capsys)
    assert code == 1 and "no results" in err
    code, _, err = run("item", "424242", capsys=capsys)
    assert code == 1 and "no item 424242" in err


@pytest.mark.parametrize("argv", [["search", ""], ["search", "x", "-n", "0"], ["search", "x", "-n", "201"],
                                  ["search", "x", "--since", "soon"], ["search", "x", "--author", "a b"],
                                  ["item", "abc"], ["item", "https://example.com/item?id=1"],
                                  ["item", "1", "--max-comments", "-1"], ["user", "bad name"], ["top", "-n", "0"],
                                  ["search", "x", "--timeout", "0"]])
def test_bad_input(argv, capsys):
    code, out, err = run(*argv, capsys=capsys)
    assert code == 2 and out == "" and err.startswith("error: ")


@pytest.mark.parametrize("html,text", [
    ("a<p>b", "a\n\nb"),
    ('see <a href="https://x.org/very/long/path" rel="nofollow">https://x.org/very/lo...</a>', "see https://x.org/very/long/path"),
    ('<a href="https://x.org">the docs</a>', "the docs (https://x.org)"),
    ("&gt; quoted &amp; <i>it</i>", "> quoted & *it*"),
    (None, ""),
])
def test_text_of(html, text):
    assert hn.text_of(html) == text


# ------------------------------------------------------------------ the network layer, against a local server

class Server(http.server.BaseHTTPRequestHandler):
    script: list = []
    seen: list = []

    def do_GET(self):  # noqa: N802
        Server.seen.append(dict(self.headers))
        status, body, headers = Server.script.pop(0) if Server.script else (200, b"{}", {})
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(hn.Net, "get", ORIGINAL_GET)
    monkeypatch.setattr(hn.time, "sleep", lambda s: None)
    srv = http.server.HTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Server.script, Server.seen = [], []
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


@pytest.mark.parametrize("script,code", [
    ([(429, b"", {"Retry-After": "1"})] * 2, hn.EXIT_RATE),
    ([(503, b"", {})] * 2, hn.EXIT_NETWORK),
    ([(400, b"{}", {})], hn.EXIT_BAD_INPUT),
    ([(200, b"<html>", {})], hn.EXIT_OTHER),
])
def test_http_failures(server, script, code):
    Server.script = list(script)
    with pytest.raises(hn.Failure) as e:
        hn.Net(5, lambda m: None).get(server)
    assert e.value.code == code


def test_user_agent_retry_and_404(server):
    Server.script = [(429, b"", {"Retry-After": "1"}), (200, b"[1]", {}), (404, b"", {})]
    net = hn.Net(5, lambda m: None)
    assert net.get(server) == [1] and net.get(server) is None
    assert Server.seen[0]["User-Agent"] == hn.UA and "github.com/zedxter/skills" in hn.UA


def test_help_runs_standalone():
    out = subprocess.run([sys.executable, "-I", str(SCRIPT), "item", "--help"], capture_output=True, text=True,
                         check=True)
    assert "--max-comments" in out.stdout


LIVE = pytest.mark.skipif(os.environ.get("HN_LIVE") != "1", reason="set HN_LIVE=1 to query the real services")


def live(*argv):
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), *argv, "-f", "json"], capture_output=True, text=True,
                       timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@LIVE
def test_live_item_1_and_friends():
    d = live("item", "1")
    assert d["item"]["title"] == "Y Combinator" and d["item"]["author"] == "pg" and d["total"] >= 3
    assert d["comments"][0]["author"] == "sama"
    assert live("search", "dropbox", "--min-points", "50", "-n", "3")["results"]
    assert len(live("top", "-n", "5")["results"]) == 5
    assert live("user", "pg", "-n", "1")["user"]["karma"] > 100000


# ------------------------------------------------------------------ review findings

@pytest.mark.parametrize("html_in,text", [
    ('<a href="https:&#x2F;&#x2F;example.com&#x2F;s?q=1&amp;region=us&amp;para=2&amp;not=3">x</a>',
     "x (https://example.com/s?q=1&region=us&para=2&not=3)"),
    ("<pre><code>  x = 1\n  y = 2</code></pre><p>after", "      x = 1\n      y = 2\n\nafter"),
    ("&lt;img src=x onerror=alert(1)&gt;", "<img src=x onerror=alert(1)>"),
])
def test_text_of_decodes_once(html_in, text):
    assert hn.text_of(html_in) == text


def test_links_are_raw_hrefs_without_glued_punctuation():
    h = ('see <a href="https://ja.wikipedia.org/wiki/日本">x</a><p>and <a href="https://en.wikipedia.org/wiki/Foo_(bar)">'
         'y</a> or <a href="http://www.paulgraham.com/investors.html),">z</a>')
    assert hn.links_of(h) == ["https://ja.wikipedia.org/wiki/日本", "https://en.wikipedia.org/wiki/Foo_(bar)",
                              "http://www.paulgraham.com/investors.html"]


def test_item_urls_have_no_json_escapes(capsys):
    FakeNet.algolia_items[8863] = {**TREE, "text": '<a href="https://ask.example/q">q</a>', "children": [
        {"id": 9998, "type": "comment", "author": "z", "text": 'see <a href="https://example.com/a">x</a><p>next',
         "children": []}]}
    code, out, _ = run("item", "8863", "-f", "urls", capsys=capsys)
    assert out.splitlines() == ["http://www.getdropbox.com/", "https://ask.example/q", "https://example.com/a"]


def test_markdown_escapes_bodies(capsys):
    FakeNet.algolia_items[8863] = {**TREE, "title": "<b>T</b> [x](javascript:1)", "children": [
        {"id": 9997, "type": "comment", "author": "m", "text": "&lt;img src=x onerror=alert(1)&gt; [click](javascript:alert(1))",
         "children": []}]}
    code, out, _ = run("item", "8863", "-f", "markdown", capsys=capsys)
    assert "<img" not in out and "<b>" not in out and "&lt;img src=x" in out and "\\[click\\](javascript" in out


def test_deep_threads_do_not_crash(capsys):
    node = {"id": 100000, "type": "comment", "author": "d", "text": "deep", "children": []}
    root = node
    for i in range(1, 1500):
        child = {"id": 100000 + i, "type": "comment", "author": "d", "text": "deep", "children": []}
        node["children"].append(child)
        node = child
    FakeNet.algolia_items[8863] = {**TREE, "children": [root]}
    d = js("item", "8863", "--max-comments", "0", capsys=capsys)
    assert d["total"] == 1500 and d["shown"] == 1500
    assert run("item", "8863", "--max-comments", "0", capsys=capsys)[0] == 0


def test_firebase_fallback_counts_and_dead(capsys, monkeypatch):
    original = FakeNet.get
    big = {"by": "big", "id": 600, "title": "Big", "type": "story", "descendants": 200, "kids": list(range(601, 611))}

    def fake(self, url):
        m = re.search(r"/item/(\d+)\.json", url)
        if "firebaseio" in url and m:
            i = int(m[1])
            if i == 600:
                return big
            if 601 <= i <= 610:
                return {"by": "c", "id": i, "type": "comment", "text": "t", "dead": i == 605, "parent": 600}
        return original(self, url)
    monkeypatch.setattr(hn.Net, "get", fake)
    d = js("item", "600", "--max-comments", "5", capsys=capsys)
    assert d["total"] == 200 and d["shown"] == 4 and d["truncated"]  # 5 read, the dead one left out
    d = js("item", "600", "--max-comments", "0", capsys=capsys)
    assert 605 not in [c["id"] for c in d["comments"]]


def test_missing_recent_comments_are_fetched(capsys, monkeypatch):
    original = FakeNet.get

    def fake(self, url):
        if url.endswith("/item/8863.json"):
            return {**FIREBASE_ITEMS[8863], "kids": [9100, 8884, 8886, 8865], "descendants": 6}
        if url.endswith("/item/9100.json"):
            return {"by": "late", "id": 9100, "type": "comment", "text": "just posted", "parent": 8863}
        return original(self, url)
    monkeypatch.setattr(hn.Net, "get", fake)
    code, out, err = run("item", "8863", "-f", "json", capsys=capsys)
    d = json.loads(out)
    assert d["comments"][0]["author"] == "late" and d["total"] == 6 and "read from Hacker News directly" in err


def test_poll_options(capsys, monkeypatch):
    original = FakeNet.get
    FakeNet.algolia_items[700] = {"id": 700, "type": "poll", "title": "Poll: x?", "author": "pg", "points": 5,
                                  "children": [{"id": 703, "type": "pollopt", "text": "A", "children": []}]}

    def fake(self, url):
        if url.endswith("/item/700.json"):
            return {"id": 700, "type": "poll", "parts": [701, 702], "kids": [], "descendants": 0}
        if url.endswith("/item/701.json"):
            return {"id": 701, "type": "pollopt", "text": "Yes", "score": 3}
        if url.endswith("/item/702.json"):
            return {"id": 702, "type": "pollopt", "text": "No", "score": 1}
        return original(self, url)
    monkeypatch.setattr(hn.Net, "get", fake)
    code, out, _ = run("item", "700", capsys=capsys)
    assert "- Yes: 3 points" in out and "- No: 1 point" in out and d_has_no_pollopt_comments(out)


def d_has_no_pollopt_comments(out):
    return "\n- A" not in out


def test_search_by_author_without_words_and_until(capsys):
    d = js("search", "--author", "pg", "--type", "comment", capsys=capsys)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(FakeNet.calls[-1]).query, keep_blank_values=True)
    assert q["query"] == [""] and q["tags"] == ["comment,author_pg"] and d["results"]
    code, _, err = run("search", "x", "--until", "7d", capsys=capsys)
    assert code == 2 and "--until '7d' is not a date" in err
    code, _, err = run("search", "x", "--since", "99999y", capsys=capsys)
    assert code == 2 and "too far back" in err
    code, _, err = run("search", "x", "--type", "comment", "--min-points", "5", capsys=capsys)
    assert code == 2 and "no points" in err


def test_singular_counts(capsys, monkeypatch):
    def one(self, url):
        return {"hits": [{"objectID": "9", "title": "T", "url": "http://t", "author": "a", "points": 1,
                          "num_comments": 1, "_tags": ["story"]}], "nbHits": 1}
    monkeypatch.setattr(hn.Net, "get", one)
    code, out, _ = run("search", "t", capsys=capsys)
    assert "1 point · a" in out and "1 comment" in out and "1 points" not in out
