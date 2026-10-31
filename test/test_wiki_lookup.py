"""Tests for skills/wiki-lookup/scripts/wiki.py.

Run: uv run --no-project --with pytest python -I -m pytest test/test_wiki_lookup.py -q \
         --doctest-modules skills/wiki-lookup/scripts/wiki.py

Offline: Net.get is replaced with canned answers shaped like the REST API (search, summary), the Action API
(query with extracts, parse, links) and Wikidata (wbgetentities). Net itself is tested against a local HTTP server.
WIKI_LIVE=1 adds smoke tests against the real services in English, Russian and German.
"""

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
SCRIPT = ROOT / "skills" / "wiki-lookup" / "scripts" / "wiki.py"
sys.path.insert(0, str(SCRIPT.parent))
import wiki  # noqa: E402

ORIGINAL_GET = wiki.Net.get

BERLIN_TEXT = """Berlin ist die Hauptstadt Deutschlands.

== Geographie ==

=== Lage ===
Berlin liegt in Ostdeutschland.

=== Klima ===
Gemäßigt.

== Literatur ==

== Geschichte ==
Erste Erwähnung 1237."""
MERCURY_HTML = """<div class="mw-parser-output"><div role="note" class="hatnote">See also <a href="/wiki/Hg">Hg</a></div>
<p><b>Mercury</b> most commonly refers to:</p><ul>
<li><a href="/wiki/Mercury_(planet)" title="Mercury (planet)">Mercury (planet)</a>, the closest planet to the Sun</li>
<li><a href="/wiki/Mercury_(element)" title="Mercury (element)">Mercury (element)</a>, a chemical element<sup class="reference">[1]</sup></li>
</ul><h2>Companies</h2><ul><li><a href="/wiki/Mercury_Records" title="Mercury Records">Mercury Records</a>, a record label
<ul><li>"<a href="/wiki/Mercury_(song)" title="Mercury (song)">Mercury</a>" (song), 2008, by Bloc Party</li></ul></li>
<li>Red <a href="/w/index.php?title=Mercury_X&amp;action=edit" class="new" title="Mercury X">Mercury X</a></li>
<li><a href="/wiki/Wiktionary:mercury" title="Wiktionary:mercury">mercury</a> in Wiktionary</li></ul>
<div class="navbox"><ul><li><a href="/wiki/Planet" title="Planet">Planet</a></li></ul></div></div>"""
MERCURY_TEXT = """Mercury most commonly refers to:

Mercury (planet), the closest planet to the Sun
Mercury (element), a chemical element

== Companies ==
Mercury Records, a record label"""
INFOBOX_HTML = """<div class="mw-parser-output"><table class="infobox biography vcard" data-name="Infobox scientist"><tbody>
<tr><th colspan="2" class="infobox-above">Marie Curie</th></tr>
<tr><td colspan="2"><span typeof="mw:File"><img src="x.jpg"/></span><div>Curie in 1920</div></td></tr>
<tr><th scope="row">Born</th><td>Maria Skłodowska<br/>7 November 1867<sup class="reference"><a href="#c1">[1]</a></sup><br/>
Warsaw</td></tr>
<tr><th scope="row">Spouse</th><td>Pierre Curie&#8203; <span style="display:none">(hidden)</span>(m. 1895)</td></tr>
<tr><th scope="row">Awards</th><td><div class="plainlist"><ul><li>Nobel Prize in Physics (1903)</li>
<li>Nobel Prize in Chemistry (1911)</li></ul></div><style>.x{color:red}</style></td></tr>
<tr><th scope="row">Fields</th><td><table><tr><td>Physics</td></tr><tr><td>Chemistry</td></tr></table></td></tr>
</tbody></table><p>Marie Curie was a physicist.</p><table class="wikitable"><tr><th>Other</th><td>table</td></tr></table></div>"""

ENT_Q64 = {"id": "Q64", "lastrevid": 99, "labels": {"en": {"value": "Berlin"}, "de": {"value": "Berlin"}},
           "descriptions": {"en": {"value": "capital of Germany"}}, "aliases": {"en": [{"value": "DE-BE"}]},
           "sitelinks": {"enwiki": {"title": "Berlin", "url": "https://en.wikipedia.org/wiki/Berlin"},
                         "dewiki": {"title": "Berlin", "url": "https://de.wikipedia.org/wiki/Berlin"},
                         "ruwiki": {"title": "Берлин", "url": "https://ru.wikipedia.org/wiki/Берлин"},
                         "commonswiki": {"title": "Category:Berlin"}, "enwikivoyage": {"title": "Berlin"}},
           "claims": {
               "P31": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {
                   "type": "wikibase-entityid", "value": {"id": "Q515"}}}},
                       {"rank": "deprecated", "mainsnak": {"snaktype": "value", "datavalue": {
                           "type": "wikibase-entityid", "value": {"id": "Q1"}}}}],
               "P1082": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {
                   "type": "quantity", "value": {"amount": "+3500000", "unit": "1"}}},
                   "qualifiers": {"P585": [{"snaktype": "value", "datavalue": {"type": "time", "value": {
                       "time": "+2015-00-00T00:00:00Z", "precision": 9}}}]}},
                         {"rank": "preferred", "mainsnak": {"snaktype": "value", "datavalue": {
                             "type": "quantity", "value": {"amount": "+3850000", "unit": "1"}}},
                          "qualifiers": {"P585": [{"snaktype": "value", "datavalue": {"type": "time", "value": {
                              "time": "+2023-12-31T00:00:00Z", "precision": 11}}}]}}],
               "P2046": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"type": "quantity", "value": {
                   "amount": "+891.12", "unit": "http://www.wikidata.org/entity/Q712226"}}}}],
               "P625": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {
                   "type": "globecoordinate", "value": {"latitude": 52.516666666667, "longitude": 13.383333333333}}}}],
               "P571": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"type": "time", "value": {
                   "time": "+1237-00-00T00:00:00Z", "precision": 9,
                   "calendarmodel": "http://www.wikidata.org/entity/Q1985786"}}}}],
               "P6": [{"rank": "normal", "mainsnak": {"snaktype": "somevalue"}}],
               "P18": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datatype": "commonsMedia",
                                                       "datavalue": {"type": "string", "value": "Berlin skyline.jpg"}}}],
               "P1448": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {
                   "type": "monolingualtext", "value": {"text": "Berlin", "language": "de"}}}}],
           }}
LABELS = {"Q515": "city", "P31": "instance of", "P1082": "population", "P585": "point in time", "P2046": "area",
          "Q712226": "square kilometre", "P625": "coordinate location", "P571": "inception", "P6": "head of government",
          "P18": "image", "P1448": "official name"}


class FakeNet:
    calls: list[str] = []

    @staticmethod
    def get(self, url):  # noqa: ANN001, ANN205 - replaces a method
        FakeNet.calls.append(url)
        u = urllib.parse.urlsplit(url)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        host, path = u.hostname, urllib.parse.unquote(u.path)
        lang = host.split(".")[0]
        if path.endswith("/rest.php/v1/search/page"):
            if q["q"] == "zzzz":
                return {"pages": []}
            return {"pages": [{"id": 1, "key": "Mercury_(planet)", "title": "Mercury (planet)",
                               "description": "First planet", "excerpt": '<span class="searchmatch">Mercury</span> is a planet'}]}
        if "/rest_v1/page/summary/" in path:
            title = path.rsplit("/", 1)[-1].replace("_", " ")
            if title == "Mercury":
                return {"type": "disambiguation", "title": "Mercury", "titles": {"canonical": "Mercury"}}
            if title in ("Mercury (planet)", "Planet Mercury"):
                return {"type": "standard", "title": "Mercury (planet)", "titles": {"canonical": "Mercury_(planet)"},
                        "description": "First planet", "extract": "Mercury is the first planet.\nSecond line.",
                        "revision": "123", "timestamp": "2026-10-01T00:00:00Z", "wikibase_item": "Q308",
                        "thumbnail": {"source": "https://upload.example/m.jpg"}, "coordinates": None}
            if title == "Москва":
                return {"type": "standard", "title": "Москва", "titles": {"canonical": "Москва"},
                        "extract": "Москва — столица России.", "revision": "5", "wikibase_item": "Q649"}
            return None
        if path.endswith("/w/api.php") and host == "www.wikidata.org":
            if q.get("sites"):
                return {"entities": {"Q64": {"id": "Q64"}}} if q["titles"] in ("Berlin",) else \
                    {"entities": {"-1": {"site": q["sites"], "title": q["titles"], "missing": True}}}
            ids = q["ids"].split("|")
            if q["props"] == "labels":
                return {"entities": {i: {"labels": {"en": {"value": LABELS[i]}}} for i in ids if i in LABELS}}
            if ids == ["Q64"]:
                return {"entities": {"Q64": ENT_Q64}}
            return {"errors": [{"code": "no-such-entity", "text": f"Could not find an entity with the ID \"{ids[0]}\"."}]}
        if path.endswith("/w/api.php"):
            if q["action"] == "parse":
                html = {"Marie Curie": INFOBOX_HTML, "Mercury": MERCURY_HTML}.get(q["page"], "<p>x</p>")
                return {"parse": {"title": q["page"], "text": html}}
            title = q["titles"]
            if q.get("prop") == "links":
                start = int(q.get("plcontinue", "0"))
                batch = [{"ns": 0, "title": f"Link {i}"} for i in range(start, min(start + int(q["pllimit"]), 7))]
                out = {"query": {"pages": [{"title": title, "links": batch}]}}
                if start + len(batch) < 7:
                    out["continue"] = {"plcontinue": str(start + len(batch)), "continue": "||"}
                return out
            if title in ("Nope", "Qwxyz"):
                return {"query": {"pages": [{"ns": 0, "title": title, "missing": True}]}}
            if title == "Bad|title":
                return {"query": {"pages": [{"title": title, "invalid": True, "invalidreason": "contains |"}]}}
            canonical = {"Berlin": "Berlin", "Berlin (Stadt)": "Berlin", "Mercury": "Mercury",
                         "Marie Curie": "Marie Curie", "Alan Turing": "Alan Turing"}.get(title, title)
            page = {"pageid": 7, "ns": 0, "title": canonical,
                    "revisions": [{"revid": 271721898, "timestamp": "2026-10-29T11:16:53Z"}],
                    "pageprops": {"wikibase_item": "Q64"}}
            if canonical == "Mercury":
                page["pageprops"]["disambiguation"] = ""
            if q.get("prop", "").endswith("extracts"):
                page["extract"] = MERCURY_TEXT if canonical == "Mercury" else BERLIN_TEXT
            out = {"query": {"pages": [page]}}
            if title != canonical:
                out["query"]["redirects"] = [{"from": title, "to": canonical}]
            return out
        raise AssertionError(f"unexpected request {url}")


@pytest.fixture(autouse=True)
def fake_net(monkeypatch):
    FakeNet.calls = []
    monkeypatch.setattr(wiki.Net, "get", FakeNet.get)


def run(*argv, capsys):
    code = wiki.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def js(*argv, capsys, code=0):
    c, out, err = run(*argv, "-f", "json", capsys=capsys)
    assert c == code, err
    return json.loads(out)


@pytest.mark.parametrize("target,lang,expected", [
    ("Marie Curie", None, ("en", "Marie Curie", None)),
    ("Marie_Curie", "de", ("de", "Marie Curie", None)),
    ("https://ru.wikipedia.org/wiki/%D0%9C%D0%BE%D1%81%D0%BA%D0%B2%D0%B0", "en", ("ru", "Москва", None)),
    ("https://de.m.wikipedia.org/wiki/Berlin#Klima", None, ("de", "Berlin", "Klima")),
    ("en.wikipedia.org/w/index.php?title=Alan_Turing", None, ("en", "Alan Turing", None)),
    ("Berlin#Klima", "de", ("de", "Berlin", "Klima")),
    ("fr:Paris", None, ("fr", "Paris", None)),
    ("Star Wars: Episode IV", None, ("en", "Star Wars: Episode IV", None)),
    ("https://ru.wikipedia.org/wiki/X#.D0.98.D1.81.D1.82.D0.BE.D1.80.D0.B8.D1.8F", None, ("ru", "X", "История")),
    ("https://nb.wikipedia.org/wiki/Oslo", None, ("no", "Oslo", None)),
    ("https://zh-yue.wikipedia.org/wiki/香港", None, ("zh-yue", "香港", None)),
])
def test_targets(target, lang, expected):
    assert wiki.parse_target(target, lang) == expected


@pytest.mark.parametrize("target", ["https://example.com/wiki/X", "https://en.wikipedia.org/", "www.google.com",
                                    "https://en.wikipedia.org/w/index.php?title=Paris&oldid=123", "Paris|London",
                                    "https://en.wikipedia.org/w/index.php?title=Paris&diff=5", "[[Paris]]"])
def test_bad_targets(target):
    with pytest.raises(wiki.Failure) as e:
        wiki.parse_target(target, None)
    assert e.value.code == wiki.EXIT_BAD_INPUT


def test_search(capsys):
    d = js("search", "mercury", capsys=capsys)
    assert d["results"][0] == {"title": "Mercury (planet)", "description": "First planet", "excerpt": "Mercury is a planet",
                               "url": "https://en.wikipedia.org/wiki/Mercury_(planet)", "pageid": 1}
    code, _, err = run("search", "zzzz", capsys=capsys)
    assert code == 1 and "no results" in err


def test_summary_carries_what_a_citation_needs(capsys):
    d = js("summary", "Planet Mercury", capsys=capsys)
    assert d["title"] == "Mercury (planet)" and d["revision"] == 123 and d["license"] == "CC BY-SA 4.0"
    assert d["permalink"] == "https://en.wikipedia.org/w/index.php?title=Mercury_%28planet%29&oldid=123"
    assert d["redirected_from"] == "Planet Mercury" and d["wikibase_item"] == "Q308"
    code, out, err = run("summary", "Mercury (planet)", capsys=capsys)
    assert out.startswith("# Mercury (planet)\nFirst planet\n\nMercury is the first planet.\nSecond line.\n")
    assert "revision 123" in out and "permalink: " in out and "redirects" not in err
    d = js("summary", "https://ru.wikipedia.org/wiki/Москва", capsys=capsys)
    assert d["lang"] == "ru" and "ru.wikipedia.org/api/rest_v1" in FakeNet.calls[-1]


def test_disambiguation_lists_options(capsys):
    code, out, err = run("summary", "Mercury", "-f", "json", capsys=capsys)
    d = json.loads(out)
    assert code == 3 and d["disambiguation"] and "disambiguation page" in err
    assert d["options"] == [
        {"title": "Mercury (planet)", "line": "Mercury (planet), the closest planet to the Sun"},
        {"title": "Mercury (element)", "line": "Mercury (element), a chemical element"},
        {"title": "Mercury Records", "line": "Mercury Records, a record label"},
        {"title": "Mercury (song)", "line": '"Mercury" (song), 2008, by Bloc Party'},
    ]
    assert d["total"] == 4 and d["truncated"] is False
    code, out, _ = run("text", "Mercury", capsys=capsys)
    assert code == 3 and "- Mercury (planet), the closest planet to the Sun  → Mercury (planet)" in out
    code, _, _ = run("infobox", "Mercury", capsys=capsys)
    assert code == 3


def test_not_found(capsys):
    code, out, err = run("summary", "Qwxyz", capsys=capsys)
    assert code == 1 and out == "" and "search 'Qwxyz' --lang en" in err
    code, _, err = run("text", "Nope", capsys=capsys)
    assert code == 1
    code, _, err = run("text", "Bad|title", capsys=capsys)
    assert code == 2 and "contains a character" in err


def test_sections_drop_empty_headings():
    s = wiki.sections_of(BERLIN_TEXT)
    assert [(x["title"], x["level"]) for x in s] == [("(lead)", 1), ("Geographie", 2), ("Lage", 3), ("Klima", 3),
                                                     ("Geschichte", 2)]
    assert [x["total"] for x in s] == [39, 40, 31, 9, 21]
    deep = wiki.sections_of("lead\n== A ==\n=== B ===\n==== C ====\n== D ==\ntext")
    assert [x["title"] for x in deep] == ["(lead)", "D"]
    assert [x["title"] for x in wiki.find_section(s, "geographie")] == ["Geographie", "Lage", "Klima"]
    assert [x["title"] for x in wiki.find_section(s, "Kli")] == ["Klima"]
    assert wiki.find_section(s, "Wirtschaft") is None


def test_text(capsys):
    code, out, _ = run("text", "Berlin", "--lang", "de", capsys=capsys)
    assert out.startswith("# Berlin\n\nBerlin ist die Hauptstadt Deutschlands.\n\n## Geographie\n\n### Lage\n\n")
    assert "Literatur" not in out and "permalink: https://de.wikipedia.org/w/index.php?title=Berlin&oldid=271721898" in out
    d = js("text", "Berlin", "--lang", "de", "--sections", capsys=capsys)
    assert d["text"] is None and [s["title"] for s in d["sections"]][:3] == ["(lead)", "Geographie", "Lage"]
    d = js("text", "https://de.wikipedia.org/wiki/Berlin#Klima", capsys=capsys)
    assert d["section"] == "Klima" and d["text"] == "### Klima\n\nGemäßigt."
    code, out, _ = run("text", "Berlin", "--lang", "de", "--section", "Geschichte", "-f", "text", capsys=capsys)
    assert out.startswith("Berlin — Geschichte\n\nGeschichte\n\nErste Erwähnung 1237.")
    code, _, err = run("text", "Berlin", "--lang", "de", "--section", "Wirtschaft", capsys=capsys)
    assert code == 1 and "sections: Geographie, Lage, Klima, Geschichte" in err
    d = js("text", "Berlin (Stadt)", "--lang", "de", capsys=capsys)
    assert d["redirected_from"] == "Berlin (Stadt)" and d["title"] == "Berlin"


def test_infobox(capsys):
    d = js("infobox", "Marie Curie", capsys=capsys)
    assert d["template"] == "Infobox scientist"
    assert d["fields"] == [
        {"label": "Born", "value": "Maria Skłodowska; 7 November 1867; Warsaw"},
        {"label": "Spouse", "value": "Pierre Curie (m. 1895)"},
        {"label": "Awards", "value": "Nobel Prize in Physics (1903); Nobel Prize in Chemistry (1911)"},
        {"label": "Fields", "value": "Physics; Chemistry"},
    ]
    code, _, err = run("infobox", "Alan Turing", capsys=capsys)
    assert code == 1 and "entity 'Alan Turing'" in err


def test_links_follow_continuation(capsys):
    d = js("links", "Alan Turing", "-n", "5", capsys=capsys)
    assert [x["title"] for x in d["links"]] == [f"Link {i}" for i in range(5)] and d["complete"] is False
    d = js("links", "Alan Turing", "-n", "100", capsys=capsys)
    assert len(d["links"]) == 7 and d["complete"] is True


def test_entity(capsys):
    d = js("entity", "Q64", "--props", "P31,P1082,P2046,P625,P571,P6,P18,P1448", "--langs", "ru,de", capsys=capsys)
    facts = {f["property"]: f["values"] for f in d["facts"]}
    assert facts["P31"] == ["city"]                                   # the deprecated statement is left out
    assert facts["P1082"] == ["3,850,000 (as of 2023-12-31)"]         # the preferred statement wins
    assert facts["P2046"] == ["891.12 square kilometre"]
    assert facts["P625"] == ["52.5167, 13.3833"]
    assert facts["P571"] == ["1237 (Julian)"]
    assert facts["P6"] == ["unknown value"]
    assert facts["P18"] == ["https://commons.wikimedia.org/wiki/File:Berlin_skyline.jpg"]
    assert facts["P1448"] == ["Berlin (de)"]
    assert [a["lang"] for a in d["articles"]] == ["ru", "de"] and d["wikipedia_count"] == 3
    assert d["revision"] == 99 and d["license"] == "CC0 1.0" and d["aliases"] == ["DE-BE"]
    d = js("entity", "https://de.wikipedia.org/wiki/Berlin", "--langs", "all", capsys=capsys)
    assert {a["lang"] for a in d["articles"]} == {"en", "de", "ru"}
    assert any("sites=dewiki" in c for c in FakeNet.calls)
    code, _, err = run("entity", "Nowhere Town", capsys=capsys)
    assert code == 1 and "no Wikidata item" in err
    code, _, err = run("entity", "Q999999999", capsys=capsys)
    assert code == 1 and "check the Q-id" in err


@pytest.mark.parametrize("value,expected", [
    ({"time": "+1867-11-07T00:00:00Z", "precision": 11}, "1867-11-07"),
    ({"time": "+1867-11-00T00:00:00Z", "precision": 10}, "1867-11"),
    ({"time": "+1960-00-00T00:00:00Z", "precision": 8}, "1960s"),
    ({"time": "+1801-00-00T00:00:00Z", "precision": 7}, "19th century"),
    ({"time": "-0044-03-15T00:00:00Z", "precision": 11, "calendarmodel": "x/Q1985786"}, "0044-03-15 (Julian) BCE"),
])
def test_times(value, expected):
    assert wiki.fmt_time(value) == expected


def test_hostile_text_stays_in_place(capsys, monkeypatch):
    def evil(self, url):
        return {"pages": [{"title": "X\n# Injected\x1b[2J", "description": "d‮", "excerpt": "e\nf"}]}
    monkeypatch.setattr(wiki.Net, "get", evil)
    code, out, _ = run("search", "x", capsys=capsys)
    assert out.count("\n") == 4 and "\x1b" not in out and "‮" not in out


@pytest.mark.parametrize("argv", [
    ["search", ""], ["search", "x", "-n", "0"], ["summary", "x", "--lang", "EN!"], ["summary", " "],
    ["links", "x", "-n", "5001"], ["summary", "x", "--timeout", "0"], ["summary", "https://example.com/wiki/X"],
])
def test_bad_input(argv, capsys):
    code, out, err = run(*argv, capsys=capsys)
    assert code == 2 and out == "" and err.startswith("error: ")


def test_unwritable_output_goes_to_stdout(tmp_path, capsys):
    code, out, err = run("summary", "Mercury (planet)", "-o", str(tmp_path / "no" / "x.md"), capsys=capsys)
    assert code == 2 and "cannot write" in err and "Mercury is the first planet" in out


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
    monkeypatch.setattr(wiki.Net, "get", ORIGINAL_GET)
    monkeypatch.setattr(wiki.time, "sleep", lambda s: None)
    srv = http.server.HTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Server.script, Server.seen = [], []
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


def test_user_agent_follows_policy(server):
    Server.script = [(200, b'{"ok": 1}', {})]
    assert wiki.Net(5, lambda m: None).get(server) == {"ok": 1}
    ua = Server.seen[0]["User-Agent"]
    assert ua == wiki.UA and "https://github.com/zedxter/utility-belt" in ua and "Mozilla" not in ua


@pytest.mark.parametrize("script,code", [
    ([(403, b"Please set a user-agent and respect our robot policy", {})], wiki.EXIT_RATE),
    ([(429, b"", {"Retry-After": "1"}), (429, b"", {"Retry-After": "1"})], wiki.EXIT_RATE),
    ([(503, b"", {}), (503, b"", {})], wiki.EXIT_NETWORK),
    ([(200, b"<html>not json</html>", {})], wiki.EXIT_OTHER),
])
def test_http_failures(server, script, code):
    Server.script = list(script)
    with pytest.raises(wiki.Failure) as e:
        wiki.Net(5, lambda m: None).get(server)
    assert e.value.code == code
    if script[0][0] == 403:
        assert "User-Agent policy" in str(e.value)


def test_retry_then_success_and_404(server):
    Server.script = [(429, b"", {"Retry-After": "1"}), (200, b'{"ok": 2}', {}), (404, b"", {})]
    net = wiki.Net(5, lambda m: None)
    assert net.get(server) == {"ok": 2} and net.get(server) is None


def test_unknown_language_is_bad_input(monkeypatch):
    monkeypatch.setattr(wiki.Net, "get", ORIGINAL_GET)
    monkeypatch.setattr(wiki, "resolves", lambda host: True)

    def no_dns(req, timeout):
        raise wiki.urllib.error.URLError(wiki.socket.gaierror(-2, "Name or service not known"))
    monkeypatch.setattr(wiki.urllib.request, "urlopen", no_dns)
    with pytest.raises(wiki.Failure) as e:
        wiki.Net(5, lambda m: None).get("https://xx.wikipedia.org/w/api.php")
    assert e.value.code == wiki.EXIT_BAD_INPUT and "no Wikipedia in language 'xx'" in str(e.value)
    monkeypatch.setattr(wiki, "resolves", lambda host: False)  # offline: a network error, not bad input
    monkeypatch.setattr(wiki.time, "sleep", lambda s: None)
    with pytest.raises(wiki.Failure) as e:
        wiki.Net(5, lambda m: None).get("https://xx.wikipedia.org/w/api.php")
    assert e.value.code == wiki.EXIT_NETWORK


def test_help_runs_standalone():
    out = subprocess.run([sys.executable, "-I", str(SCRIPT), "entity", "--help"], capture_output=True, text=True,
                         check=True)
    assert "--props" in out.stdout


LIVE = pytest.mark.skipif(os.environ.get("WIKI_LIVE") != "1", reason="set WIKI_LIVE=1 to query the real services")


def live(*argv):
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), *argv, "-f", "json"], capture_output=True, text=True,
                       timeout=120)
    return p.returncode, json.loads(p.stdout) if p.stdout.strip() else None, p.stderr


@LIVE
def test_live_english():
    code, d, err = live("summary", "Marie Curie")
    assert code == 0 and d["wikibase_item"] == "Q7186" and d["revision"] > 0, err
    code, d, _ = live("search", "Alan Turing", "-n", "3")
    assert code == 0 and d["results"][0]["title"] == "Alan Turing"
    code, d, _ = live("summary", "Mercury")
    assert code == 3 and any(o["title"] == "Mercury (planet)" for o in d["options"])
    code, d, _ = live("entity", "Q7186", "--props", "P569")
    assert code == 0 and d["facts"][0]["values"] == ["1867-11-07"]


@LIVE
def test_live_russian_and_german():
    code, d, err = live("summary", "https://ru.wikipedia.org/wiki/Москва")
    assert code == 0 and d["lang"] == "ru" and d["wikibase_item"] == "Q649", err
    code, d, _ = live("text", "Berlin", "--lang", "de", "--section", "Klima")
    assert code == 0 and d["section"] == "Klima" and "Klima" in d["text"]
    code, d, _ = live("infobox", "Москва", "--lang", "ru")
    assert code == 0 and any(f["label"] == "Страна" for f in d["fields"])
    code, d, _ = live("entity", "Москва", "--lang", "ru", "--langs", "en,de")
    assert code == 0 and d["id"] == "Q649" and [a["lang"] for a in d["articles"]] == ["en", "de"]


# ------------------------------------------------------------------ review findings

def test_text_is_cut_at_a_section_boundary(capsys):
    d = js("text", "Berlin", "--lang", "de", "--max-chars", "100", capsys=capsys)
    assert d["truncated"] and d["chars"] > 100 and len(d["text"]) <= 100 and d["text"].endswith("Ostdeutschland.")
    d = js("text", "Berlin", "--lang", "de", "--max-chars", "80", capsys=capsys)
    assert d["text"] == "Berlin ist die Hauptstadt Deutschlands."  # never ends on a heading without its text
    code, out, err = run("text", "Berlin", "--lang", "de", "--max-chars", "80", capsys=capsys)
    assert "[cut at" in out and "text cut at" in err
    assert js("text", "Berlin", "--lang", "de", "--max-chars", "0", capsys=capsys)["truncated"] is False


def test_redirect_to_a_section(capsys, monkeypatch):
    original = FakeNet.get

    def with_fragment(self, url):
        d = original(self, url)
        if "titles=Klima+von+Berlin" in url:
            d["query"]["redirects"] = [{"from": "Klima von Berlin", "to": "Berlin", "tofragment": "Klima"}]
        return d
    monkeypatch.setattr(wiki.Net, "get", with_fragment)
    d = js("text", "Klima von Berlin", "--lang", "de", capsys=capsys)
    assert d["section"] == "Klima" and d["redirected_from"] == "Klima von Berlin"


def test_case_only_redirect_is_reported(capsys, monkeypatch):
    original = FakeNet.get
    monkeypatch.setattr(wiki.Net, "get", lambda self, url: original(
        self, url.replace("mercury_%28", "Mercury_%28").replace("PLANET", "planet")))
    assert js("summary", "mercury (PLANET)", capsys=capsys)["redirected_from"] == "mercury (PLANET)"
    assert js("summary", "mercury (planet)", capsys=capsys)["redirected_from"] is None  # first letter is not a redirect


def test_interwiki_title_is_bad_input(capsys, monkeypatch):
    monkeypatch.setattr(wiki.Net, "get", lambda self, url: {"query": {"interwiki": [{"title": "wikt:x", "iw": "wikt"}]}})
    code, _, err = run("text", "wikt:x", capsys=capsys)
    assert code == 2 and "another wiki (wikt)" in err


def test_french_infobox_v3_and_void_tags():
    html = """<div class="infobox_v3 infobox"><div class="entete">Albert Einstein</div>
    <table><tr><th scope="row">Naissance</th><td>14 mars 1879<br/>Ulm</td></tr>
    <tr><th>Nationalité</th><td>allemande<sup class="reference">[1]<img src="x"/>[2]</sup></td></tr></table>
    <table><tr><th>Domaines</th><td>Physique</td></tr></table></div>
    <table class="wikitable"><tr><th>Autre</th><td>non</td></tr></table>"""
    p = wiki.InfoboxParser()
    p.feed(html)
    assert p.fields() == [{"label": "Naissance", "value": "14 mars 1879; Ulm"},
                          {"label": "Nationalité", "value": "allemande"}, {"label": "Domaines", "value": "Physique"}]
    p = wiki.InfoboxParser()
    p.feed('<table class="infobox"><tr><th>A</th><td>x<span style="display:none">HIDDEN<br/>LEAK</span></td></tr></table>')
    assert p.fields() == [{"label": "A", "value": "x"}]


def test_entity_site_ids_and_aliases(capsys, monkeypatch):
    assert wiki.site_id("be-tarask") == "be_x_oldwiki" and wiki.site_lang("be_x_oldwiki") == "be-tarask"
    assert wiki.site_id("zh-classical") == "zh_classicalwiki" and wiki.site_lang("zh_min_nanwiki") == "zh-min-nan"
    ENT_Q64["sitelinks"]["be_x_oldwiki"] = {"title": "Бэрлін"}
    try:
        d = js("entity", "Q64", "--langs", "be-tarask,nb", capsys=capsys)
    finally:
        del ENT_Q64["sitelinks"]["be_x_oldwiki"]
    assert d["articles"] == [{"lang": "be-tarask", "title": "Бэрлін", "url": "https://be-tarask.wikipedia.org/wiki/"
                              "%D0%91%D1%8D%D1%80%D0%BB%D1%96%D0%BD"}]
    assert wiki.entity_id(None, "https://www.wikidata.org/wiki/Property:P31", None) == "P31"
    assert wiki.entity_id(None, "https://www.wikidata.org/wiki/Special:EntityPage/Q42", None) == "Q42"


def test_entity_on_a_disambiguation_item(capsys, monkeypatch):
    original = FakeNet.get
    dis = {"id": "Q48397", "labels": {"en": {"value": "Mercury"}}, "sitelinks": {"enwiki": {"title": "Mercury"}},
           "claims": {"P31": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {
               "type": "wikibase-entityid", "value": {"id": "Q4167410"}}}}]}}

    def fake(self, url):
        if "ids=Q48397" in url and "props=labels%7Cdescriptions" in url:
            return {"entities": {"Q48397": dis}}
        if "ids=" in url and "wikidata" in url and "props=labels&" in url:
            return {"entities": {}}
        return original(self, url)
    monkeypatch.setattr(wiki.Net, "get", fake)
    code, out, err = run("entity", "Q48397", "-f", "json", capsys=capsys)
    assert code == 3 and json.loads(out)["options"][0]["title"] == "Mercury (planet)"


def test_values_beyond_the_limit_are_counted(capsys):
    stmts = [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"type": "string", "value": str(i)}}}
             for i in range(15)]
    values, more = wiki.statement_values(stmts, {})
    assert len(values) == 12 and more == 3
    award = [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"type": "wikibase-entityid",
                                                                                "value": {"id": "Q38104"}}},
              "qualifiers": {"P585": [{"snaktype": "value", "datavalue": {"type": "time", "value": {
                  "time": "+1911-00-00T00:00:00Z", "precision": 9}}}]}}]
    assert wiki.statement_values(award, {"Q38104": "Nobel Prize in Chemistry"})[0] == ["Nobel Prize in Chemistry (1911)"]


@pytest.mark.parametrize("value,expected", [
    ({"time": "+2001-00-00T00:00:00Z", "precision": 7}, "21st century"),
    ({"time": "-0044-00-00T00:00:00Z", "precision": 7}, "1st century BCE"),
    ({"time": "+1999-00-00T00:00:00Z", "precision": 6}, "2nd millennium"),
    ({"time": "-13798000000-00-00T00:00:00Z", "precision": 0}, "13,798,000,000 years ago"),
])
def test_more_times(value, expected):
    assert wiki.fmt_time(value) == expected


@pytest.mark.parametrize("amount,expected", [("-0.5", "-0.5"), ("+1234567.25", "1,234,567.25"), ("-12", "-12"),
                                             ("+3", "3"), ("+1e5", "1e5")])
def test_amounts(amount, expected):
    assert wiki.fmt_amount(amount) == expected


def test_long_retry_after_and_cut_off_answers(server):
    Server.script = [(503, b"", {"Retry-After": "120"})]
    with pytest.raises(wiki.Failure) as e:
        wiki.Net(5, lambda m: None).get(server)
    assert e.value.code == wiki.EXIT_NETWORK and "120 s" in e.value.hint and len(Server.seen) == 1
    Server.script = [(200, b'{"partial', {"Content-Length": "1000"}), (200, b'{"ok": 3}', {})]
    assert wiki.Net(5, lambda m: None).get(server) == {"ok": 3}
