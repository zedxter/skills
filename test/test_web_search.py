"""Tests for skills/web-search/scripts/search.py.

Offline cases inject a fake engine into main(); network cases call ddgs for real and
are skipped with WEB_EXTRACT_OFFLINE=1 or when the engine is rate-limited.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "web-search" / "scripts" / "search.py"
sys.path.insert(0, str(SCRIPT.parent))
import search as ws  # noqa: E402

OFFLINE = os.environ.get("WEB_EXTRACT_OFFLINE") == "1"
network = pytest.mark.skipif(OFFLINE, reason="WEB_EXTRACT_OFFLINE=1")

FAKE = [
    {"title": " Alpha  page ", "href": "https://www.alpha.example/a?utm_source=x&id=1#top", "body": "First  snippet\nhere"},
    {"title": "Alpha dup", "href": "https://www.alpha.example/a?id=1", "body": "dup"},
    {"title": "Beta", "href": "https://beta.example/b", "body": "Second snippet"},
    {"title": "Bad", "href": "ftp://nope/x", "body": ""},
    {"title": "Sub", "href": "https://docs.beta.example/c", "body": ""},
]


def fake_engine(records):
    calls = []

    def engine(query, args):
        calls.append((query, dict(vars(args))))
        return records
    engine.calls = calls
    return engine


def run_main(*argv, engine=None, capsys=None):
    rc = ws.main(list(argv), engine=engine)
    out, err = capsys.readouterr()
    return rc, out, err


def test_helpers():
    assert ws.clean_url("https://a.example/p?utm_source=x&id=2&gclid=9#f") == "https://a.example/p?id=2"
    assert ws.host_of("https://WWW.Example.com/x") == "example.com"
    recs = ws.normalise(FAKE, news=False)
    assert [r["url"] for r in recs] == ["https://www.alpha.example/a?id=1", "https://beta.example/b", "https://docs.beta.example/c"]
    assert recs[0]["title"] == "Alpha page" and recs[0]["snippet"] == "First snippet here" and recs[0]["host"] == "alpha.example"
    news = ws.normalise([{"title": "N", "url": "https://n.example/1", "body": "b", "date": "2026-10-29T10:00:00+00:00", "source": "Wire"}], news=True)
    assert news[0]["date"] == "2026-10-29" and news[0]["source"] == "Wire"


def test_query_building_and_filters(capsys):
    eng = fake_engine(FAKE)
    rc, out, err = run_main("uv", "inline", "--site", "beta.example", "--exclude-site", "spam.example", "-f", "json", engine=eng, capsys=capsys)
    assert rc == 0
    assert eng.calls[0][0] == "uv inline site:beta.example -site:spam.example"
    data = json.loads(out)
    assert [r["host"] for r in data["results"]] == ["beta.example", "docs.beta.example"]  # site filter enforced locally, subdomains kept
    assert data["engine"] == "ddgs/auto" and "2 result(s)" in err
    rc, out, _ = run_main("x", "--exclude-site", "alpha.example", "-n", "1", "-f", "jsonl", engine=fake_engine(FAKE), capsys=capsys)
    rows = [json.loads(ln) for ln in out.splitlines()]
    assert rc == 0 and len(rows) == 1 and rows[0]["host"] == "beta.example"
    eng = fake_engine(FAKE)
    rc, out, _ = run_main("x", "--time", "week", "--region", "de-de", "--news", "--backend", "bing", "-f", "json", engine=eng, capsys=capsys)
    a = eng.calls[0][1]
    assert a["time"] == "week" and a["region"] == "de-de" and a["news"] and a["backend"] == "bing"


def test_markdown_rendering_and_empty(capsys):
    rc, out, _ = run_main("alpha", "beta", engine=fake_engine(FAKE), capsys=capsys)
    assert rc == 0 and out.startswith("# search: alpha beta\n# engine: ddgs/auto · 3 results\n")
    assert "1. **Alpha page**  \n   https://www.alpha.example/a?id=1\n   First snippet here" in out
    rc, out, err = run_main("nothing", engine=fake_engine([]), capsys=capsys)
    assert rc == 1 and "no results" in err and "0 results" in out
    rc, out, err = run_main("nothing", "-f", "json", engine=fake_engine([]), capsys=capsys)
    assert rc == 1 and json.loads(out)["count"] == 0


def test_engine_errors_map_to_exit_codes(capsys):
    def blocked(q, a):
        raise ws.EngineBlocked("rate-limited")

    def netfail(q, a):
        raise ws.EngineNetwork("timeout")

    def boom(q, a):
        raise ValueError("weird")
    assert run_main("x", engine=blocked, capsys=capsys)[0] == 4
    assert run_main("x", engine=netfail, capsys=capsys)[0] == 6
    rc, _, err = run_main("x", engine=boom, capsys=capsys)
    assert rc == 7 and "ValueError" in err


def test_bad_arguments(capsys):
    assert run_main("x", "-n", "0", engine=fake_engine(FAKE), capsys=capsys)[0] == 2
    assert run_main("x", "--searx", "not-a-url", engine=fake_engine(FAKE), capsys=capsys)[0] == 2
    assert run_main("x", "--read-chars", "5", engine=fake_engine(FAKE), capsys=capsys)[0] == 2
    assert run_main("   ", engine=fake_engine(FAKE), capsys=capsys)[0] == 2


def test_read_attaches_content_via_web_extract(capsys, monkeypatch):
    class FakeWX:
        QUIET = False

        @staticmethod
        def make_session(ns):
            return object()

        @staticmethod
        def process(session, url, ns):
            if "beta.example/b" in url:
                return {"ok": True, "content": "Body text " * 100, "source": "live", "words": 200, "title": "Beta", "date": "2026-01-01", "warnings": []}
            return {"ok": False, "error": {"code": 3, "kind": "not_found", "message": "HTTP 404"}}
    monkeypatch.setattr(ws, "load_web_extract", lambda: FakeWX)
    rc, out, err = run_main("x", "--read", "2", "--read-chars", "120", "-f", "json", engine=fake_engine(FAKE), capsys=capsys)
    data = json.loads(out)
    r0, r1, r2 = data["results"]
    assert rc == 0 and r0["read"]["ok"] is False and r0["content"] is None and r0["read"]["error"]["kind"] == "not_found"
    assert r1["read"]["ok"] and r1["read"]["truncated"] and r1["content"].endswith(" […]") and len(r1["content"]) <= 130
    assert "read" not in r2 and "read 1/2" in err
    rc, out, _ = run_main("x", "--read", "2", engine=fake_engine(FAKE), capsys=capsys)
    assert "> read failed: not_found" in out and "> read: live, 200 words" in out and "   Body text" in out
    monkeypatch.setattr(ws, "load_web_extract", lambda: None)
    assert run_main("x", "--read", "1", engine=fake_engine(FAKE), capsys=capsys)[0] == 2


@network
def test_n1_live_search_and_cli():
    p = subprocess.run([sys.executable, str(SCRIPT), "trafilatura", "python", "library", "-n", "5", "-f", "json"],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if p.returncode in (4, 6):
        pytest.skip(f"search engine unavailable: {p.stderr.strip()[:200]}")
    data = json.loads(p.stdout)
    assert p.returncode == 0 and 1 <= data["count"] <= 5 and all(r["url"].startswith("http") for r in data["results"])
    assert any("trafilatura" in (r["title"] or "").lower() or "trafilatura" in r["url"].lower() for r in data["results"])
