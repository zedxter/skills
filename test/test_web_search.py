"""Tests for skills/web-search/scripts/search.py.

Offline cases inject a fake engine into main(); network cases call ddgs for real and
are skipped with WEB_EXTRACT_OFFLINE=1, without ddgs, or when the engine is rate-limited.

Run: uv run --with pytest --with 'ddgs>=9,<10' --with curl_cffi --with trafilatura --with lxml_html_clean --with pypdf python -I -m pytest test/test_web_search.py -q
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
    {"title": "Broken", "href": "http://[::1/x", "body": ""},
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
    assert ws.clean_url("http://[::1/x") == "" and ws.host_of("http://[::1/x") == ""
    esc = ws.normalise([{"title": "Admin \x1b[31mRED\x1b[0m console", "href": "https://e.example/", "body": "snip\x1b[2Jpet\x07",
                         "source": "W\x9bire", "date": "2026\x1b-01"}], news=True)[0]
    assert esc["title"] == "Admin [31mRED[0m console" and esc["snippet"] == "snip[2Jpet" and esc["source"] == "Wire"
    assert "\x1b" not in json.dumps(esc, ensure_ascii=False)


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


def test_ddgs_kwargs_default_region(monkeypatch):
    seen = {}

    class FakeDDGS:
        def __init__(self, **kw):
            pass

        def text(self, q, **kw):
            seen.update(kw)
            return []
    import types
    fake_mod = types.ModuleType("ddgs")
    fake_mod.DDGS = FakeDDGS
    fake_mod.exceptions = types.SimpleNamespace(RatelimitException=type("R", (Exception,), {}), TimeoutException=type("T", (Exception,), {}),
                                                DDGSException=type("D", (Exception,), {}))
    monkeypatch.setitem(sys.modules, "ddgs", fake_mod)
    args = ws.argparse.Namespace(region=None, safesearch="moderate", max_results=5, time=None, backend="auto", proxy=None, timeout=5, news=False)
    ws.ddgs_engine("q", args)
    assert seen["region"] == "us-en"


def test_searx_engine_mapping(monkeypatch):
    import types

    class Resp:
        def __init__(self, status, payload):
            self.status_code, self.payload = status, payload

        def json(self):
            if self.payload is None:
                raise ValueError("no json")
            return self.payload
    state = {}

    def get(url, **kw):
        state["url"] = url
        return state["resp"]
    fake = types.ModuleType("curl_cffi")
    fake.requests = types.SimpleNamespace(get=get)
    monkeypatch.setitem(sys.modules, "curl_cffi", fake)
    base = dict(safesearch="moderate", time=None, news=True, region=None, searx="https://searx.example", timeout=5, proxy=None, max_results=5)
    state["resp"] = Resp(200, {"results": [{"title": "T", "url": "https://www.pub.example/a", "content": "c", "engine": "google news"}]})
    rows = ws.searx_engine("q", ws.argparse.Namespace(**base))
    assert rows[0]["source"] == "pub.example" and "language=" not in state["url"]
    ws.searx_engine("q", ws.argparse.Namespace(**{**base, "region": "de-de"}))
    assert "language=de" in state["url"]
    state["resp"] = Resp(200, None)
    with pytest.raises(ws.SearxConfigError):
        ws.searx_engine("q", ws.argparse.Namespace(**base))
    state["resp"] = Resp(403, None)
    with pytest.raises(ws.EngineBlocked):
        ws.searx_engine("q", ws.argparse.Namespace(**base))


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

    def noconf(q, a):
        raise ws.SearxConfigError("SearXNG returned no JSON")

    def nodep(q, a):
        raise RuntimeError("missing dependency curl_cffi (x)")
    assert run_main("x", "--searx", "https://s.example", engine=noconf, capsys=capsys)[0] == 2
    assert run_main("x", engine=nodep, capsys=capsys)[0] == 2


def test_bad_arguments(capsys):
    assert run_main("x", "-n", "0", engine=fake_engine(FAKE), capsys=capsys)[0] == 2
    assert run_main("x", "--searx", "not-a-url", engine=fake_engine(FAKE), capsys=capsys)[0] == 2
    assert run_main("x", "--read-chars", "5", engine=fake_engine(FAKE), capsys=capsys)[0] == 2
    assert run_main("   ", engine=fake_engine(FAKE), capsys=capsys)[0] == 2
    for bad in ("en", "english", "us_en", "us-eng"):
        assert run_main("x", "--region", bad, engine=fake_engine(FAKE), capsys=capsys)[0] == 2
    assert run_main("x", "--region", "WT-WT", engine=fake_engine(FAKE), capsys=capsys)[0] == 0
    rc, _, err = run_main("x", "--news", "--backend", "brave", engine=fake_engine(FAKE), capsys=capsys)
    assert rc == 2 and "--news supports" in err
    assert run_main("x", "--news", "--backend", "brave", "--searx", "https://s.example", engine=fake_engine(FAKE), capsys=capsys)[0] == 0


def test_read_attaches_content_via_web_extract(capsys, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("wx_real_for_test", ROOT / "skills" / "web-extract" / "scripts" / "extract.py")
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    fetched = []

    class FakeWX:
        QUIET = False
        is_private_host = staticmethod(real.is_private_host)

        @staticmethod
        def make_session(ns):
            return object()

        @staticmethod
        def process(session, url, ns):
            fetched.append(url)
            if "beta.example/b" not in url:
                sys.stderr.write("error: HTTP 404\n")  # web-extract's finish() does this for failed pages
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
    assert "error: HTTP 404" in run_main("x", "--read", "1", engine=fake_engine(FAKE), capsys=capsys)[2]
    rc, _, err = run_main("x", "--read", "2", "-q", engine=fake_engine(FAKE), capsys=capsys)
    assert rc == 0 and err == ""
    fetched.clear()
    local = [{"title": "L", "href": "http://127.0.0.1:8080/secret"}, {"title": "I", "href": "http://intranet/x"}, FAKE[2]]
    rc, out, _ = run_main("x", "--read", "3", "-f", "json", engine=fake_engine(local), capsys=capsys)
    rows = json.loads(out)["results"]
    assert [r["read"]["ok"] for r in rows] == [False, False, True] and rows[0]["read"]["error"]["kind"] == "private_host"
    assert fetched == ["https://beta.example/b"]
    monkeypatch.setattr(ws, "load_web_extract", lambda: None)
    assert run_main("x", "--read", "1", engine=fake_engine(FAKE), capsys=capsys)[0] == 2


@network
def test_n1_live_search_and_cli():
    pytest.importorskip("ddgs")
    p = subprocess.run([sys.executable, str(SCRIPT), "trafilatura", "python", "library", "-n", "5", "-f", "json"],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if p.returncode in (1, 2, 4, 6):  # no results / missing dependency / rate-limited / offline
        pytest.skip(f"search engine unavailable (exit {p.returncode}): {p.stderr.strip()[:200]}")
    data = json.loads(p.stdout)
    assert p.returncode == 0 and 1 <= data["count"] <= 5 and all(r["url"].startswith("http") for r in data["results"])
    assert any("trafilatura" in (r["title"] or "").lower() or "trafilatura" in r["url"].lower() for r in data["results"])


def test_auto_falls_back_to_general_backends_when_empty(monkeypatch, capsys):
    """ddgs 'auto' can stop at an empty backend; the skill then tries duckduckgo, bing, brave in turn."""
    import types
    calls = []

    class FakeDDGS:
        def __init__(self, **kw):
            pass

        def text(self, query, **kw):
            calls.append(kw.get("backend", "auto"))
            if kw.get("backend") == "bing":
                return [{"title": "Hit", "href": "https://hit.example/", "body": "found"}]
            return []

        news = text

    class DDGSException(Exception):
        pass

    exc = types.SimpleNamespace(DDGSException=DDGSException, RatelimitException=type("R", (DDGSException,), {}),
                                TimeoutException=type("T", (DDGSException,), {}))
    fake = types.ModuleType("ddgs")
    fake.DDGS, fake.exceptions = FakeDDGS, exc
    monkeypatch.setitem(sys.modules, "ddgs", fake)
    rc, out, err = run_main("q", "-f", "json", capsys=capsys)
    data = json.loads(out)
    assert rc == 0 and calls == ["auto", "bing"] and data["engine"] == "ddgs/auto→bing" and data["count"] == 1
    calls.clear()
    rc, out, _ = run_main("q", "--backend", "brave", "-f", "json", capsys=capsys)
    assert rc == 1 and calls == ["brave"]  # an explicit backend is never second-guessed
    calls.clear()
    rc, _, _ = run_main("q", "--news", "-f", "json", capsys=capsys)
    assert rc == 1 and calls == ["auto"]  # no fallback for news

    def limited(self, query, **kw):
        calls.append(kw.get("backend", "auto"))
        raise exc.RatelimitException("429 Ratelimit")
    monkeypatch.setattr(FakeDDGS, "text", limited)
    calls.clear()
    rc, _, err = run_main("q", capsys=capsys)
    assert rc == 4 and calls == ["auto"]  # a rate limit is reported, not hammered with fallbacks
