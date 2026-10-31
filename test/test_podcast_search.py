"""Tests for skills/podcast-search/scripts/podcast.py.

Run: uv run --no-project --with pytest --with feedparser python -I -m pytest test/test_podcast_search.py -q \
         --doctest-modules skills/podcast-search/scripts/podcast.py

Offline: test/data/podcast_feed.xml and a fake iTunes Search API (JSON shaped like Apple's) are served by an
in-process HTTP server. PODCAST_LIVE=1 adds a smoke test against Apple and a long-lived public feed.
"""

import functools
import gzip
import http.server
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "podcast-search" / "scripts" / "podcast.py"
DATA = ROOT / "test" / "data"
sys.path.insert(0, str(SCRIPT.parent))
import podcast as pc  # noqa: E402

SHOW = {"wrapperType": "track", "kind": "podcast", "collectionId": 111222333, "collectionName": "Test Talks",
        "artistName": "Test Host", "trackCount": 3, "primaryGenreName": "Science", "releaseDate": "2024-03-02T08:00:00Z",
        "collectionViewUrl": "https://podcasts.apple.com/us/podcast/test-talks/id111222333?uo=4", "collectionExplicitness": "notExplicit"}
EPISODE = {"wrapperType": "podcastEpisode", "kind": "podcast-episode", "collectionName": "Test Talks",
           "trackName": "Episode 3: Black Holes", "releaseDate": "2024-03-02T08:00:00Z", "trackTimeMillis": 6300000,
           "episodeUrl": "https://cdn.example.org/talks/3.mp3", "episodeContentType": "audio",
           "trackViewUrl": "https://podcasts.apple.com/us/podcast/x/id111222333?i=1000&uo=4", "episodeGuid": "talks-3",
           "description": "Event horizons."}


CP1251_FEED = ("<?xml version='1.0'?><rss version='2.0'><channel><title>Русский подкаст</title>"
               "<item><title>Выпуск один</title><pubDate>Tue, 05 Mar 2024 18:00:00 -0800</pubDate>"
               "<enclosure url='https://x/1.mp3' type='audio/mpeg' length='1'/></item>"
               "<item><title>Утренний выпуск</title><pubDate>Tue, 05 Mar 2024 08:00:00 -0800</pubDate>"
               "<enclosure url='https://x/0.mp3' type='audio/mpeg' length='1'/></item></channel></rss>").encode("cp1251")
SPECIAL = {  # path → (status, headers, body)
    "/cp1251.xml": (200, {"Content-Type": "application/rss+xml; charset=windows-1251"}, CP1251_FEED),
    "/gzip.xml": (200, {"Content-Type": "application/rss+xml", "Content-Encoding": "gzip"},
                  gzip.compress((DATA / "podcast_feed.xml").read_bytes())),
    "/cut.xml": (200, {"Content-Type": "application/rss+xml", "Content-Length": "100000"}, b"<?xml version='1.0'?><rss"),
}


class Handler(http.server.SimpleHTTPRequestHandler):
    calls: list[str] = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        if url.path in SPECIAL:
            status, headers, body = SPECIAL[url.path]
            self.send_response(status)
            for k, v in {"Content-Length": str(len(body)), **headers}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path in ("/search", "/lookup"):
            Handler.calls.append(self.path)
            if q.get("term") in ("blocked", "throttled"):
                self.send_error(403 if q["term"] == "blocked" else 429)
                return
            if q.get("term") == "html":
                body = b"<html>Service unavailable</html>"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if q.get("term") == "nofeed":
                results = [{k: v for k, v in SHOW.items() if k != "feedUrl"}]
                body = json.dumps({"resultCount": 1, "results": results}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if url.path == "/lookup":
                results = [SHOW] if q.get("id") == "111222333" else []
            elif q.get("entity") == "podcastEpisode":
                results = [EPISODE]
            else:
                results = [] if q.get("term") == "nothing" else [{**SHOW, "collectionName": "Test Talks Daily", "collectionId": 9}, SHOW]
            body = json.dumps({"resultCount": len(results), "results": results}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


@pytest.fixture(scope="module")
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(DATA)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    SHOW["feedUrl"] = EPISODE["feedUrl"] = f"{base}/podcast_feed.xml"
    yield base
    srv.shutdown()


@pytest.fixture
def run(server, tmp_path):
    """Run the CLI in a subprocess against the fake directory, with a private throttle file and no proxy."""
    def go(*argv: str):
        code = subprocess.run(
            [sys.executable, "-c", "import sys, podcast as pc; pc.ITUNES = sys.argv[1]; sys.exit(pc.main(sys.argv[2:]))", server, *argv],
            capture_output=True, text=True, encoding="utf-8", cwd=SCRIPT.parent, timeout=60,
            env={**{k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")},
                 "XDG_CACHE_HOME": str(tmp_path), "LOCALAPPDATA": str(tmp_path), "NO_PROXY": "127.0.0.1,localhost"})
        return code.returncode, code.stdout, code.stderr
    return go


# ------------------------------------------------------------------ unit level

def test_feed_parsing_normalises_episodes():
    show, eps = pc.parse_feed((DATA / "podcast_feed.xml").read_bytes(), "https://example.org/feed")
    assert show["title"] == "Test Talks" and show["author"] == "Test Host" and show["language"] == "en"
    assert show["description"] == "A show used by the podcast-search tests."
    assert [e["guid"] for e in eps] == ["talks-1", "talks-2", "talks-3"]  # the item without audio is dropped
    e1, e2, e3 = eps
    assert e1["published"] == "2022-01-03" and e1["duration"] == 3725 and e1["audio_bytes"] == 44700000
    assert e1["description"] == "Newton & Galileo." and e1["episode"] == "1" and e1["show"] == "Test Talks"
    assert e2["title"] == "Episode 2: Quantum Weirdness # fake header line"  # one safe line
    assert e2["published"] == "2023-06-15" and e2["duration"] == 750 and e2["audio_bytes"] is None
    assert e2["description"] == "Entanglement explained." and e2["transcript_url"] == "https://example.org/talks/2.vtt"
    assert e3["duration"] == 6300 and e3["audio_url"] == "https://cdn.example.org/talks/3.mp3?src=rss" and e3["url"] is None


def parse(*argv):
    a = pc.make_parser().parse_args(list(argv))
    a.after = pc.parse_date(a.after) if a.after else None
    a.before = pc.parse_date(a.before) if a.before else None
    return a


def test_filters_and_sorting():
    _, eps = pc.parse_feed((DATA / "podcast_feed.xml").read_bytes(), "x")
    args = parse("episodes", "x")
    assert [e["guid"] for e in pc.filter_episodes(eps, args)] == ["talks-3", "talks-2", "talks-1"]
    args = parse("episodes", "x", "--oldest", "--after", "2023-01-01", "--min-minutes", "10")
    assert [e["guid"] for e in pc.filter_episodes(eps, args)] == ["talks-2", "talks-3"]
    args = parse("episodes", "x", "--grep", "hawking|newton", "--max-minutes", "70", "--before", "2024-12-31")
    assert [e["guid"] for e in pc.filter_episodes(eps, args)] == ["talks-1"]
    assert [e["guid"] for e in pc.filter_episodes(eps, parse("episodes", "x"), by_date=False)] == ["talks-1", "talks-2", "talks-3"]


def test_media_selection_dates_and_charset():
    item = ("<rss version='2.0' xmlns:media='http://search.yahoo.com/mrss/'><channel><title>S</title>"
            "<item><title>Cover only</title><enclosure url='http://x/cover.jpg' type='image/jpeg' length='1'/></item>"
            "<item><title>Media RSS</title><media:content url='http://x/m.mp3' type='audio/mpeg' fileSize='9'/></item>"
            "</channel></rss>").encode()
    _, eps = pc.parse_feed(item, "u")
    assert [(e["title"], e["audio_url"], e["audio_bytes"]) for e in eps] == [("Media RSS", "http://x/m.mp3", 9)]
    show, eps = pc.parse_feed(CP1251_FEED, "u", {"content-type": "application/rss+xml; charset=windows-1251"})
    assert show["title"] == "Русский подкаст" and eps[0]["title"] == "Выпуск один"
    assert [e["published"] for e in eps] == ["2024-03-05", "2024-03-05"]  # the show's own day, not the UTC one
    newest = pc.filter_episodes(eps, parse("episodes", "x"))
    assert [e["audio_url"] for e in newest] == ["https://x/1.mp3", "https://x/0.mp3"]  # 18:00 before 08:00
    assert [e["audio_url"] for e in pc.filter_episodes(eps, parse("episodes", "x", "--oldest"))] == ["https://x/0.mp3", "https://x/1.mp3"]


def test_bad_feeds_are_classified():
    with pytest.raises(pc.Failure) as e:
        pc.parse_feed(b"<html><body>Not a feed</body></html>", "u")
    assert e.value.code == pc.EXIT_FEED
    blog = b"<rss version='2.0'><channel><title>Blog</title><item><title>Post</title><link>https://x/p</link></item></channel></rss>"
    with pytest.raises(pc.Failure) as e:
        pc.parse_feed(blog, "u")
    assert e.value.code == pc.EXIT_FEED and "no audio" in str(e.value)


def test_throttle_holds_across_concurrent_processes(tmp_path):
    # 24 processes call throttle() at once with a budget of 18/min: 18 go now, 6 get slots about a minute later
    code = ("import json, sys, time; import podcast as pc; pc.time.sleep = lambda s: print(round(s)); pc.throttle(lambda m: None)")
    procs = [subprocess.Popen([sys.executable, "-c", code], cwd=SCRIPT.parent, stdout=subprocess.PIPE, text=True,
                              env={**os.environ, "XDG_CACHE_HOME": str(tmp_path), "LOCALAPPDATA": str(tmp_path)})
             for _ in range(24)]
    waits = sorted(int(p.communicate(timeout=60)[0].strip() or 0) for p in procs)
    assert waits.count(0) == pc.ITUNES_PER_MINUTE and all(55 <= w <= 61 for w in waits[pc.ITUNES_PER_MINUTE:])
    saved = json.loads((tmp_path / "utility-belt" / "itunes-calls.json").read_text())
    assert len(saved) == 24  # no lost updates


def test_throttle_waits_once_the_minute_budget_is_used(monkeypatch, tmp_path):
    path = tmp_path / "calls.json"
    monkeypatch.setattr(pc, "throttle_file", lambda: path)
    slept = []
    monkeypatch.setattr(pc.time, "sleep", slept.append)
    now = pc.time.time()
    monkeypatch.setattr(pc, "locked", lambda p: __import__("contextlib").nullcontext())
    path.write_text(json.dumps([now - 30] * pc.ITUNES_PER_MINUTE + [now - 120]))
    notes = []
    pc.throttle(notes.append)
    assert slept and 29 < slept[0] < 32 and "pausing" in notes[0]
    path.write_text("not json")
    slept.clear()
    pc.throttle(notes.append)
    assert not slept and len(json.loads(path.read_text())) == 1


def test_cli_argument_errors_exit_2(run):
    for argv in (["search", "ai", "-n", "0"], ["search", "ai", "--country", "usa"], ["episodes", "x", "--grep", "("],
                 ["episodes", "x", "--after", "yesterday"], ["search", "ai", "--after", "2024-01-01"], ["search", "ai", "--oldest"]):
        rc, out, err = run(*argv)
        assert rc == 2 and out == "" and err.startswith("error: ") and err.count("\n") == 1, argv  # one line, no usage dump


# ------------------------------------------------------------------ CLI against the fake directory and feed

def test_search_shows_table_json_urls(run, server):
    rc, out, _ = run("search", "test", "talks")
    assert rc == 0 and out.startswith("# 2 result(s) for: test talks") and " 2. Test Talks\n" in out
    assert f"feed: {server}/podcast_feed.xml" in out and "Test Host · 3 episodes · Science · latest 2024-03-02" in out
    rc, out, _ = run("search", "test", "-f", "json", "-n", "1")
    data = json.loads(out)
    assert rc == 0 and data["query"] == "test" and data["country"] == "us" and len(data["results"]) == 1
    assert data["results"][0]["apple_url"].endswith("/id111222333")
    rc, out, _ = run("search", "test", "-f", "urls")
    assert rc == 0 and out.splitlines() == [f"{server}/podcast_feed.xml"] * 2


def test_search_episodes_across_shows(run):
    rc, out, _ = run("search", "black", "holes", "--episodes", "-f", "json")
    [ep] = json.loads(out)["results"]
    assert rc == 0 and ep["kind"] == "episode" and ep["show"] == "Test Talks" and ep["duration"] == 6300
    assert ep["audio_url"] == "https://cdn.example.org/talks/3.mp3" and ep["url"] == "https://podcasts.apple.com/us/podcast/x/id111222333?i=1000"
    rc, out, _ = run("search", "black", "holes", "--episodes", "-f", "urls")
    assert out == "https://cdn.example.org/talks/3.mp3\n"


def test_episodes_by_feed_apple_url_and_name(run, server):
    rc, out, err = run("episodes", f"{server}/podcast_feed.xml", "-n", "2")
    assert rc == 0 and out.startswith("# Test Talks — Test Host (3 episodes in feed)\n# feed: ")
    assert out.index("Episode 3") < out.index("Episode 2") and "Episode 1" not in out and "transcript in feed" in out
    rc, out, _ = run("episodes", "https://podcasts.apple.com/us/podcast/test-talks/id111222333", "-f", "json")
    data = json.loads(out)
    assert rc == 0 and data["show"]["apple_id"] == 111222333 and data["total_episodes"] == 3 and len(data["results"]) == 3
    rc, out, err = run("episodes", "Test", "Talks", "-n", "1", "-f", "urls")
    assert rc == 0 and out == "https://cdn.example.org/talks/3.mp3?src=rss\n" and err == ""  # exact name: no note
    rc, out, err = run("episodes", "test talk", "-n", "1", "-f", "urls")
    assert rc == 0 and "best match 'Test Talks Daily'" in err


def test_cli_failures(run, server):
    assert run("search", "nothing")[0] == 1
    rc, out, err = run("episodes", "x", "--grep", "nomatch", "-f", "json")
    assert rc == 1 and json.loads(out)["results"] == [] and "no episodes match" in err
    rc, _, err = run("search", "blocked")
    assert rc == 4 and "HTTP 403" in err and "do not loop" in err
    rc, _, err = run("episodes", f"{server}/missing.xml")
    assert rc == 5 and "HTTP 404" in err
    rc, _, _ = run("episodes", "id123456789")
    assert rc == 5
    rc, _, err = run("episodes", f"{server}/podcast_feed.xml".replace("podcast_feed.xml", ""))
    assert rc == 7  # a directory listing, not a feed
    rc, _, err = run("search", "throttled")
    assert rc == 4 and "HTTP 429" in err
    rc, _, err = run("search", "html")
    assert rc == 4 and "non-JSON" in err
    rc, _, err = run("episodes", "nofeed")
    assert rc == 5 and "no public RSS feed" in err
    rc, _, err = run("episodes", f"{server}/cut.xml")
    assert rc == 6 and "connection closed after" in err


def test_feed_encodings_and_json_keys(run, server):
    rc, out, err = run("episodes", f"{server}/cp1251.xml", "-q")
    assert rc == 0 and err == "" and out.startswith("# Русский подкаст (2 episodes in feed)") and "Выпуск один" in out
    rc, out, _ = run("episodes", f"{server}/gzip.xml", "-f", "json")
    data = json.loads(out)
    assert rc == 0 and data["total_episodes"] == 3
    assert not any(k.startswith("_") for r in data["results"] for k in r)  # internal sort key stays internal


@pytest.mark.skipif(os.environ.get("PODCAST_LIVE") != "1", reason="live network test; set PODCAST_LIVE=1")
def test_live_apple_and_public_feed():
    p = subprocess.run([sys.executable, str(SCRIPT), "episodes", "This American Life", "-n", "2", "-f", "json", "-q"],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    data = json.loads(p.stdout)
    assert p.returncode == 0 and data["show"]["title"] == "This American Life" and len(data["results"]) == 2
    assert all(r["audio_url"].startswith("http") and r["published"] for r in data["results"])


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
