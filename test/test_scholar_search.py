"""Tests for skills/scholar-search/scripts/scholar.py.

Run: uv run --no-project --with pytest python -I -m pytest test/test_scholar_search.py -q \
         --doctest-modules skills/scholar-search/scripts/scholar.py

Offline: Net.get is replaced with canned answers shaped like Crossref, arXiv (Atom), PubMed (esearch JSON, efetch
XML), Europe PMC, Unpaywall and doi.org (CSL-JSON and BibTeX); Zotero is a real local HTTP server. SCHOLAR_LIVE=1
adds a smoke test per source against the real services.
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
SCRIPT = ROOT / "skills" / "scholar-search" / "scripts" / "scholar.py"
sys.path.insert(0, str(SCRIPT.parent))
import scholar as sc  # noqa: E402

ORIGINAL_GET = sc.Net.get

LECUN = {"DOI": "10.1038/nature14539", "title": ["Deep learning"], "container-title": ["Nature"],
         "type": "journal-article", "published": {"date-parts": [[2015, 5, 27]]}, "is-referenced-by-count": 50000,
         "author": [{"family": "LeCun", "given": "Yann"}, {"family": "Bengio", "given": "Yoshua"},
                    {"family": "Hinton", "given": "Geoffrey"}]}
DL_BOOK = {"DOI": "10.7551/mitpress/deep", "title": ["Deep learning"], "type": "book", "published": {"date-parts": [[2016]]},
           "author": [{"family": "Goodfellow", "given": "Ian"}], "is-referenced-by-count": 30000}
GNN = {"DOI": "10.1109/tnnls.2020.2978386", "title": ["A Comprehensive Survey on Graph Neural Networks"],
       "container-title": ["IEEE Transactions on Neural Networks and Learning Systems"], "type": "journal-article",
       "published": {"date-parts": [[2021, 1]]}, "is-referenced-by-count": 9000,
       "abstract": "<jats:p>Deep learning has <jats:italic>revolutionized</jats:italic> many tasks.</jats:p>",
       "author": [{"family": "Wu", "given": "Zonghan"}, {"family": "Pan", "given": "Shirui"}]}
LSTM = {"DOI": "10.1162/neco.1997.9.8.1735", "title": ["Long Short-Term Memory"], "type": "journal-article",
        "published": {"date-parts": [[1997, 11, 1]]}, "is-referenced-by-count": 90000,
        "author": [{"family": "Hochreiter", "given": "Sepp"}]}
FIGURE = {"DOI": "10.6084/m9.figshare.1", "title": ["Figure 1: graph neural networks"], "type": "component"}
CROSSREF_ITEMS = [GNN, LECUN, DL_BOOK, LSTM, FIGURE]

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/1901.00596v4</id><published>2019-01-03T03:20:55Z</published>
<title>A Comprehensive Survey on Graph
  Neural Networks</title><summary>  Deep learning has revolutionized many machine learning tasks in recent years,
  ranging from image classification to natural language understanding.</summary>
<author><name>Zonghan Wu</name></author><author><name>Shirui Pan</name></author>
<arxiv:doi>10.1109/TNNLS.2020.2978386</arxiv:doi></entry>
<entry><id>http://arxiv.org/abs/1706.03762v7</id><published>2017-06-12T17:57:34Z</published>
<title>Attention Is All You Need</title><summary>The dominant sequence transduction models.</summary>
<author><name>Ashish Vaswani</name></author></entry></feed>"""
EMPTY_ATOM = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
EFETCH = """<?xml version="1.0"?><PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>26017442</PMID>
<Article><Journal><JournalIssue><PubDate><Year>2015</Year><Month>May</Month><Day>28</Day></PubDate></JournalIssue>
<Title>Nature</Title></Journal><ArticleTitle>Deep learning.</ArticleTitle>
<Abstract><AbstractText Label="BACKGROUND">Deep learning allows <i>computational</i> models.</AbstractText>
<AbstractText>It has improved the state of the art.</AbstractText></Abstract>
<AuthorList><Author><LastName>LeCun</LastName><ForeName>Yann</ForeName></Author>
<Author><CollectiveName>The Deep Group</CollectiveName></Author></AuthorList>
<PublicationTypeList><PublicationType>Review</PublicationType></PublicationTypeList></Article></MedlineCitation>
<PubmedData><ArticleIdList><ArticleId IdType="pubmed">26017442</ArticleId><ArticleId IdType="doi">10.1038/nature14539</ArticleId>
</ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""
EPMC = [{"id": "26017442", "source": "MED", "pmid": "26017442", "pmcid": "PMC9999999", "doi": "10.1038/nature14539",
         "title": "Deep learning.", "authorString": "LeCun Y, Bengio Y, Hinton G.", "pubYear": "2015",
         "firstPublicationDate": "2015-05-28", "journalInfo": {"journal": {"title": "Nature"}}, "citedByCount": 61000,
         "isOpenAccess": "N", "abstractText": "Deep learning allows computational models that are composed of "
                                              "multiple processing layers to learn representations of data.",
         "fullTextUrlList": {"fullTextUrl": [{"availabilityCode": "S", "url": "https://doi.org/10.1038/nature14539"}]}},
        {"id": "PPR1", "source": "PPR", "doi": "10.64898/2026.01.01.1", "title": "Base editing in sickle cell disease",
         "authorList": {"author": [{"fullName": "Doe J"}]}, "pubYear": "2026", "firstPublicationDate": "2026-01-02",
         "bookOrReportDetails": {"publisher": "bioRxiv"}, "citedByCount": 0,
         "fullTextUrlList": {"fullTextUrl": [{"availabilityCode": "F", "documentStyle": "doi",
                                              "url": "https://doi.org/10.64898/2026.01.01.1"}]}}]
BIBTEX = {"10.1038/nature14539": " @article{LeCun_2015, title={Deep learning}, volume={521}, DOI={10.1038/nature14539}, "
                                 "journal={Nature}, author={LeCun, Yann and Bengio, Yoshua}, year={2015}, month=May, "
                                 "pages={436–444} }",
          "10.48550/arXiv.1706.03762": "@misc{https://doi.org/10.48550/arxiv.1706.03762,\n  doi = {10.48550/ARXIV.1706.03762},"
                                       "\n  author = {Vaswani, Ashish},\n  keywords = {cs.CL},\n  title = {Attention Is All You "
                                       "Need},\n  year = {2017},\n  copyright = {arXiv.org perpetual}\n}"}


class FakeNet:
    """Answers by URL, records calls; installed in place of Net.get."""
    calls: list[str] = []
    down: set[str] = set()
    rate_limited: set[str] = set()
    epmc: list = EPMC
    atom: str = ATOM

    @staticmethod
    def get(self, url, accept="application/json", headers=None, follow=True):  # noqa: ANN001, ANN205
        FakeNet.calls.append(url)
        u = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(u.query)
        host = u.hostname
        if host in FakeNet.down:
            raise sc.Failure(sc.EXIT_NETWORK, f"{host} is not answering (HTTP 503)", "down")
        if host in FakeNet.rate_limited:
            raise sc.Failure(sc.EXIT_RATE, f"{host} rate limit (HTTP 429)", "wait")
        if host == "api.crossref.org":
            if u.path.startswith("/works/"):
                doi = urllib.parse.unquote(u.path[len("/works/"):])
                item = next((d for d in CROSSREF_ITEMS if d["DOI"].lower() == doi.lower()), None)
                return (200, json.dumps({"message": item}).encode()) if item else (404, b"Resource not found.")
            words = set(sc.title_key(q["query"][0]).split())
            items = [d for d in CROSSREF_ITEMS if words & set(sc.title_key(d["title"][0]).split())]
            return 200, json.dumps({"message": {"items": items}}).encode()
        if host == "export.arxiv.org":
            if "id_list" in q:
                head, *entries = FakeNet.atom.split("<entry>")
                hits = [e for e in entries if f"/abs/{q['id_list'][0]}v" in e]
                return 200, ((head + "".join("<entry>" + e for e in hits)).removesuffix("</feed>") + "</feed>").encode()
            return 200, FakeNet.atom.encode()
        if host == "eutils.ncbi.nlm.nih.gov":
            if u.path.endswith("esearch.fcgi"):
                return 200, json.dumps({"esearchresult": {"idlist": ["26017442"]}}).encode()
            if q["id"][0] == "26017442":
                return 200, EFETCH.encode()
            return 200, b'<?xml version="1.0"?><PubmedArticleSet></PubmedArticleSet>'
        if host == "www.ebi.ac.uk":
            query = q["query"][0]
            res = [r for r in FakeNet.epmc if ("SRC:PPR" in query) == (r["source"] == "PPR")]
            if "DOI:" in query:
                res = [r for r in FakeNet.epmc if f'DOI:"{r["doi"]}"' in query]
            return 200, json.dumps({"hitCount": len(res), "resultList": {"result": res}}).encode()
        if host == "api.unpaywall.org":
            doi = urllib.parse.unquote(u.path[len("/v2/"):])
            if doi == "10.1109/tnnls.2020.2978386".upper() or doi.lower() == "10.1109/tnnls.2020.2978386":
                return 200, json.dumps({"best_oa_location": {"url_for_pdf": "https://repo.example/gnn.pdf"}}).encode()
            return 200, json.dumps({"best_oa_location": None}).encode()
        if host == "doi.org":
            doi = urllib.parse.unquote(u.path.lstrip("/")).removeprefix("api/handles/")
            if accept == "application/x-bibtex":
                return (200, BIBTEX[doi].encode()) if doi in BIBTEX else (404, b"")
            item = next((d for d in CROSSREF_ITEMS if d["DOI"].lower() == doi.lower()), None)
            return (200, json.dumps(item).encode()) if item else (404, b"")
        raise AssertionError(f"unexpected request {url}")


@pytest.fixture(autouse=True)
def fake_net(monkeypatch):
    FakeNet.calls, FakeNet.down, FakeNet.rate_limited = [], set(), set()
    FakeNet.epmc, FakeNet.atom = EPMC, ATOM
    monkeypatch.setattr(sc.Net, "get", FakeNet.get)
    for k in ("SCHOLAR_EMAIL", "OPENALEX_API_KEY", "S2_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def run(*argv, capsys):
    code = sc.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def results(*argv, capsys):
    code, out, err = run(*argv, "-f", "json", capsys=capsys)
    assert code == 0, err
    return json.loads(out)


def test_one_record_per_work_across_sources(capsys):
    d = results("deep learning", capsys=capsys)
    lecun = [r for r in d["results"] if r["doi"] == "10.1038/nature14539"]
    assert len(lecun) == 1
    r = lecun[0]
    assert set(r["sources"]) == {"crossref", "europepmc", "pubmed"}
    assert r["pmid"] == "26017442" and r["pmcid"] == "PMC9999999" and r["citations"] == 61000
    assert r["abstract"].startswith("Background: Deep learning allows computational") or "multiple processing" in r["abstract"]
    assert r["url"] == "https://doi.org/10.1038/nature14539"
    assert r["authors"][0] == "Yann LeCun"
    # The 2016 book "Deep learning" shares the short title but not the year: a different work.
    assert any(x["doi"] == "10.7551/mitpress/deep" for x in d["results"])


def test_preprint_and_journal_version_merge(capsys):
    d = results("graph neural networks", "--source", "arxiv,crossref", capsys=capsys)
    gnn = [r for r in d["results"] if "Graph Neural Networks" in r["title"]]
    assert len(gnn) == 1
    r = gnn[0]
    assert r["doi"].lower() == "10.1109/tnnls.2020.2978386" and r["arxiv"] == "1901.00596"
    assert r["venue"].startswith("IEEE Transactions") and r["date"] == "2021-01"  # the published version's metadata
    assert r["oa_url"] == "https://arxiv.org/pdf/1901.00596" and r["citations"] == 9000
    assert "revolutionized many machine learning tasks" in r["abstract"]  # the longer abstract
    assert all(x["title"] != "Figure 1: graph neural networks" for x in d["results"])  # Crossref components dropped


def test_merge_by_title_alone_and_alt_dois():
    a = sc.record("arxiv", 0, title="Learning to rank documents with sparse transformers", arxiv="2101.00001",
                  date="2021-01-01")
    b = sc.record("crossref", 0, title="Learning to Rank Documents with Sparse Transformers.", doi="10.1000/j.1",
                  date="2022-03-01", type="journal-article")
    c = sc.record("biorxiv", 1, title="Learning to rank documents with sparse transformers", doi="10.1101/2021.01.01",
                  type="preprint", date="2021-01-05")
    (m,) = sc.merge([a, b, c])
    assert m["doi"] == "10.1000/j.1" and m["alt_dois"] == ["10.1101/2021.01.01"] and m["arxiv"] == "2101.00001"
    assert m["year"] == 2022 and m["sources"] == ["arxiv", "crossref", "biorxiv"]


def test_sorts(capsys):
    by_cites = results("deep learning neural networks", "--sort", "citations", capsys=capsys)["results"]
    counts = [r["citations"] for r in by_cites if r["citations"] is not None]
    assert counts == sorted(counts, reverse=True)
    assert all(r["citations"] is not None for r in by_cites[:len(counts)])  # uncounted (arXiv-only) come last
    by_date = results("deep learning", "--sort", "date", capsys=capsys)["results"]
    dates = [r["date"] for r in by_date]
    assert dates == sorted(dates, reverse=True)
    assert any("sort=P_PDATE_D+desc" in c for c in FakeNet.calls)
    assert any("sortBy=submittedDate" in c for c in FakeNet.calls)


def test_crossref_every_word_filter_outside_relevance(capsys):
    code, out, err = run("short networks", "--source", "crossref", "--sort", "citations", "-f", "json", capsys=capsys)
    assert code == 1  # LSTM has "short" but not "networks"; GNN has "networks" but not "short"
    rel = results("short networks", "--source", "crossref", capsys=capsys)  # relevance keeps Crossref's own matches
    assert {r["title"] for r in rel["results"]} >= {"Long Short-Term Memory"}


def test_dates_reach_every_source(capsys):
    d = results("deep learning", "--since", "2015-05", "--until", "2016", capsys=capsys)
    calls = "\n".join(urllib.parse.unquote_plus(c) for c in FakeNet.calls)
    assert "from-pub-date:2015-05-01,until-pub-date:2016-12-31" in calls
    assert "submittedDate:[201505010000 TO 201612312359]" in calls
    assert "mindate=2015/05/01" in calls and "maxdate=2016/12/31" in calls
    assert "FIRST_PDATE:[2015-05-01 TO 2016-12-31]" in calls
    assert all("2015-05" <= (r["date"] or "9999") <= "2016-12-31" for r in d["results"])
    assert not any(r["arxiv"] == "1706.03762" for r in d["results"])  # 2017: filtered locally too


def test_europepmc_searches_titles_and_abstracts(capsys):
    results("deep learning", "--source", "europepmc", capsys=capsys)
    assert "TITLE_ABS:(deep learning)" in urllib.parse.unquote_plus(FakeNet.calls[-1])
    results("AUTH:LeCun AND deep", "--source", "europepmc", capsys=capsys)
    assert "query=(AUTH:LeCun AND deep)" in urllib.parse.unquote_plus(FakeNet.calls[-1])


def test_biorxiv_through_europepmc(capsys):
    d = results("sickle cell", "--source", "biorxiv", capsys=capsys)
    (r,) = d["results"]
    assert r["type"] == "preprint" and r["venue"] == "bioRxiv" and r["oa_url"] == "https://doi.org/10.64898/2026.01.01.1"
    assert 'SRC:PPR AND (PUBLISHER:"bioRxiv" OR PUBLISHER:"medRxiv")' in urllib.parse.unquote_plus(FakeNet.calls[-1])


def test_one_source_down_still_answers(capsys):
    FakeNet.down = {"export.arxiv.org"}
    code, out, err = run("deep learning", "-f", "json", capsys=capsys)
    d = json.loads(out)
    assert code == 0 and "arxiv failed" in err and d["sources"]["arxiv"].startswith("failed")


@pytest.mark.parametrize("down,limited,code", [
    ({"export.arxiv.org", "api.crossref.org", "eutils.ncbi.nlm.nih.gov", "www.ebi.ac.uk"}, set(), 6),
    ({"export.arxiv.org", "api.crossref.org"}, {"eutils.ncbi.nlm.nih.gov", "www.ebi.ac.uk"}, 4),
])
def test_every_source_failing(down, limited, code, capsys):
    FakeNet.down, FakeNet.rate_limited = down, limited
    c, out, err = run("deep learning", capsys=capsys)
    assert c == code and out == "" and err.startswith("error: every source failed")


def test_no_results_is_exit_1(capsys):
    FakeNet.epmc, FakeNet.atom = [], EMPTY_ATOM
    code, out, err = run("zzzz qqqq", "--source", "arxiv,crossref,europepmc", capsys=capsys)
    assert code == 1 and "no results" in err


def test_open_access(capsys):
    d = results("graph neural networks", "--source", "crossref", "--email", "me@example.org", capsys=capsys)
    gnn = next(r for r in d["results"] if "Graph" in r["title"])
    assert gnn["oa_url"] == "https://repo.example/gnn.pdf"
    assert any("mailto=me%40example.org" in c for c in FakeNet.calls)
    d = results("deep learning neural", "--oa-only", "--email", "me@example.org", capsys=capsys)
    assert d["results"] and all(r["oa_url"] for r in d["results"])
    code, out, err = run("deep learning", "--oa-only", "-f", "json", capsys=capsys)
    assert "--oa-only without --email" in err and all(r["oa_url"] for r in json.loads(out)["results"])
    assert not any("unpaywall" in c for c in FakeNet.calls[-4:])


def test_json_shape(capsys):
    d = results("deep learning", "-n", "2", capsys=capsys)
    assert set(d) == {"query", "sort", "since", "until", "sources", "count", "results"} and d["count"] == 2
    assert set(d["results"][0]) == set(sc.FIELDS)
    assert d["sources"] == {"arxiv": 2, "europepmc": 1, "pubmed": 1, "crossref": 2}


def test_table_and_markdown(capsys):
    code, out, _ = run("graph neural networks", "--source", "arxiv,crossref", "-q", capsys=capsys)
    assert code == 0 and out.startswith("# graph neural networks · ")
    assert "1. A Comprehensive Survey on Graph Neural Networks" in out and "cited 9000" in out
    code, out, _ = run("graph neural networks", "--source", "arxiv", "-f", "markdown", "-q", capsys=capsys)
    assert "[A Comprehensive Survey on Graph Neural Networks](https://doi.org/10.1109/TNNLS.2020.2978386)" in out
    assert "[open access](https://arxiv.org/pdf/1901.00596)" in out


def test_hostile_titles_stay_on_one_line():
    r = sc.record("crossref", 0, title="Real title\n2. Fake [entry](http://evil) \x1b[2J‮", doi="10.1/x")
    assert "\n" not in sc.table([r], "#", 0).split("\n", 3)[2]
    md = sc.markdown([r])
    assert md.count("\n") == 1 and "\\[entry\\]" in md


def test_lookup_by_identifier(capsys):
    code, out, err = run("doi", "https://doi.org/10.1038/nature14539", "arXiv:1706.03762v7", "PMID:26017442",
                         "-f", "json", capsys=capsys)
    d = json.loads(out)
    assert code == 0 and d["not_found"] == []
    lecun, attention, lecun2 = d["results"]
    assert lecun["pmid"] == "26017442" and lecun["citations"] == 61000 and lecun["sources"][0] == "doi.org"
    assert attention["arxiv"] == "1706.03762" and attention["title"] == "Attention Is All You Need"
    assert lecun2["doi"] == "10.1038/nature14539" and lecun2["type"] in ("journal-article", "review")


def test_lookup_survives_a_failing_source(capsys):
    FakeNet.down = {"www.ebi.ac.uk"}  # an extra source: the record comes back without its PMC data
    code, out, err = run("doi", "10.1038/nature14539", "-f", "json", capsys=capsys)
    r = json.loads(out)["results"][0]
    assert code == 0 and r["title"] == "Deep learning" and r["pmcid"] is None and "Europe PMC skipped" in err
    FakeNet.down = {"export.arxiv.org"}  # the main source for one id: the others are still printed
    code, out, err = run("doi", "10.1038/nature14539", "arXiv:1706.03762", "-f", "json", capsys=capsys)
    d = json.loads(out)
    assert code == 6 and [r["doi"] for r in d["results"]] == ["10.1038/nature14539"]
    assert list(d["failed"]) == ["arXiv:1706.03762"] and "could not be looked up" in err
    FakeNet.down = {"doi.org"}
    code, out, err = run("bibtex", "10.1038/nature14539", capsys=capsys)
    assert code == 6 and out == "" and "could not look up 10.1038/nature14539" in err
    code, out, err = run("graph neural networks", "--source", "arxiv", "-f", "bibtex", capsys=capsys)
    assert code == 0 and out.count("@") == 2 and "BibTeX built from the search record" in err


def test_lookup_names_pubmed_as_the_source(capsys):
    code, out, _ = run("doi", "PMID:26017442", "-f", "json", capsys=capsys)
    assert json.loads(out)["results"][0]["sources"][0] == "pubmed"


def test_source_all(capsys, monkeypatch):
    d = results("deep learning", "--source", "all", capsys=capsys)
    assert list(d["sources"]) == ["arxiv", "europepmc", "pubmed", "crossref", "biorxiv"]


def test_lookup_missing_and_bad(capsys):
    code, out, err = run("doi", "10.1038/nature14539", "10.9999/nope", "-f", "json", capsys=capsys)
    assert code == 1 and json.loads(out)["not_found"] == ["10.9999/nope"] and "10.9999/nope: not found" in err
    code, out, err = run("doi", "10.9999/nope", capsys=capsys)
    assert code == 1 and out == ""
    code, _, err = run("doi", "not-an-id", capsys=capsys)
    assert code == 2 and "not a DOI, arXiv id or PMID" in err


def test_bibtex(capsys):
    code, out, _ = run("bibtex", "10.1038/nature14539", "1706.03762", "10.1038/nature14539", capsys=capsys)
    assert code == 0
    entries = out.strip().split("\n\n")
    assert entries[0].startswith("@article{lecun2015deep,\n  title = {Deep learning},")
    assert "  month = May," in entries[0] and "  pages = {436–444}" in entries[0]
    assert entries[1].startswith("@misc{vaswani2017attention,") and "keywords" not in entries[1]
    assert entries[2].startswith("@article{lecun2015deepa,")
    # No BibTeX from the registry: built from the record.
    code, out, _ = run("graph neural networks", "--source", "arxiv", "-f", "bibtex", "-q", capsys=capsys)
    assert "@misc{vaswani2017attention," in out  # arXiv's DataCite DOI
    assert "@article{wu" in out or "@misc{wu" in out


@pytest.mark.parametrize("parts,expected", [
    ("transformer attention", "all:transformer AND all:attention"),
    ('"graph neural" ti:survey', 'all:"graph neural" AND ti:survey'),
    ("ti:bert OR abs:roberta", "ti:bert OR abs:roberta"),
])
def test_arxiv_query(parts, expected):
    assert sc.arxiv_query(parts) == expected


def test_helpers():
    assert sc.date_bound("2024", False) == "2024-01-01" and sc.date_bound("2024-02", True) == "2024-02-29"
    with pytest.raises(ValueError):
        sc.date_bound("2023-02-30", False)
    assert sc.parse_id("doi:10.1038/NATURE14539.") == ("doi", "10.1038/NATURE14539")
    assert sc.parse_id("https://arxiv.org/abs/hep-th/9901001v2") == ("arxiv", "hep-th/9901001")
    assert sc.parse_id("https://pubmed.ncbi.nlm.nih.gov/26017442/") == ("pmid", "26017442")
    assert sc.parse_id("deep learning") is None
    assert sc.clean_abstract("<jats:title>Abstract</jats:title><jats:p>We show <i>x</i> .</jats:p>") == "We show x."
    assert sc.title_key("Déjà vu: a “study”") == "deja vu a study"
    assert sc.norm_date("2015", "Oct", "07") == "2015-10-07" and sc.norm_date("2015", "Spring") == "2015"


def test_arxiv_spacing_holds_across_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    first = sc.machine_slot("export.arxiv.org", 3.1)
    second = sc.machine_slot("export.arxiv.org", 3.1)  # another run, a moment later
    assert second - first == pytest.approx(3.1, abs=0.01)
    assert sc.slot_path("export.arxiv.org").parent == tmp_path / "zedxter-skills"


class Zotero(http.server.BaseHTTPRequestHandler):
    status = 200
    items: list = [{"key": "A", "data": {"itemType": "journalArticle", "title": "Something else", "DOI": "",
                                         "extra": "DOI: 10.1109/TNNLS.2020.2978386"}}]

    def do_GET(self):  # noqa: N802
        body = json.dumps(Zotero.items if Zotero.status == 200 else "Local API is not enabled").encode()
        self.send_response(Zotero.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def zotero(monkeypatch):
    srv = http.server.HTTPServer(("127.0.0.1", 0), Zotero)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(sc, "ZOTERO", f"http://127.0.0.1:{srv.server_port}/api")
    yield
    srv.shutdown()


def test_zotero_marks_saved_papers(zotero, capsys):
    Zotero.status = 200
    d = results("graph neural networks", "--source", "arxiv", "--zotero", capsys=capsys)
    marks = {r["arxiv"]: r["in_zotero"] for r in d["results"]}
    assert marks == {"1901.00596": True, "1706.03762": False}
    code, out, _ = run("graph neural networks", "--source", "arxiv", "--zotero", "-q", capsys=capsys)
    assert "in your Zotero" in out


def test_zotero_off_or_closed(zotero, capsys, monkeypatch):
    Zotero.status = 403
    code, out, err = run("graph neural networks", "--source", "arxiv", "--zotero", "-f", "json", capsys=capsys)
    assert code == 0 and "local API is off" in err and {r["in_zotero"] for r in json.loads(out)["results"]} == {None}
    monkeypatch.setattr(sc, "ZOTERO", "http://127.0.0.1:9/api")
    code, out, err = run("graph neural networks", "--source", "arxiv", "--zotero", capsys=capsys)
    assert code == 0 and "Zotero is not running" in err


def test_keyed_sources(capsys, monkeypatch):
    code, _, err = run("x", "--source", "semanticscholar", capsys=capsys)
    assert code == 2 and "S2_API_KEY" in err
    monkeypatch.setenv("OPENALEX_API_KEY", "k1")
    seen = {}

    def openalex(self, url, accept="application/json", headers=None, follow=True):
        seen.update(url=url, follow=follow)
        return 200, json.dumps({"results": [{"title": "Deep learning", "doi": "https://doi.org/10.1038/nature14539",
                                             "publication_date": "2015-05-27", "cited_by_count": 70000,
                                             "abstract_inverted_index": {"Deep": [0], "learning": [1], "works": [2]},
                                             "open_access": {"oa_url": None}, "authorships": [], "ids": {}}]}).encode()
    monkeypatch.setattr(sc.Net, "get", openalex)
    d = results("deep learning", "--source", "openalex", "--sort", "citations", capsys=capsys)
    assert d["results"][0]["abstract"] == "Deep learning works" and d["results"][0]["citations"] == 70000
    assert "api_key=k1" in seen["url"] and "sort=cited_by_count%3Adesc" in seen["url"] and seen["follow"] is False


@pytest.mark.parametrize("argv", [
    [""], ["x", "-n", "0"], ["x", "-n", "101"], ["x", "--source", "scholar"], ["x", "--since", "2024-13"],
    ["x", "--since", "2025", "--until", "2024"], ["x", "--email", "not-an-email"], ["x", "--timeout", "0"],
    ["x", "--snippet", "-1"],
])
def test_bad_input(argv, capsys):
    code, out, err = run(*argv, capsys=capsys)
    assert code == 2 and out == "" and err.startswith("error: ")


def test_unwritable_output_falls_back_to_stdout(tmp_path, capsys):
    code, out, err = run("deep learning", "-o", str(tmp_path / "missing" / "x.json"), "-q", capsys=capsys)
    assert code == 2 and "cannot write" in err and "Deep learning" in out


def test_help_runs_standalone():
    out = subprocess.run([sys.executable, "-I", str(SCRIPT), "--help"], capture_output=True, text=True, check=True)
    assert "--source" in out.stdout
    out = subprocess.run([sys.executable, "-I", str(SCRIPT), "bibtex", "--help"], capture_output=True, text=True, check=True)
    assert "DOIs, arXiv ids" in out.stdout


LIVE = pytest.mark.skipif(os.environ.get("SCHOLAR_LIVE") != "1", reason="set SCHOLAR_LIVE=1 to query the real services")


@LIVE
@pytest.mark.parametrize("source,query", [
    ("arxiv", "retrieval augmented generation"), ("crossref", "retrieval augmented generation"),
    ("europepmc", "crispr base editing"), ("pubmed", "crispr base editing"), ("biorxiv", "crispr base editing"),
])
def test_live_each_source(source, query):
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), query, "--source", source, "-n", "3", "-f", "json",
                        "--since", "2023"], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    d = json.loads(p.stdout)
    assert d["count"] >= 1 and all(r["title"] and r["year"] >= 2023 for r in d["results"])


@LIVE
def test_live_lookup_and_bibtex():
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), "doi", "10.1038/nature14539", "arXiv:1706.03762",
                        "PMID:26017442", "-f", "json"], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    titles = [r["title"] for r in json.loads(p.stdout)["results"]]
    assert titles == ["Deep learning", "Attention Is All You Need", "Deep learning"]
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), "bibtex", "10.1038/nature14539", "1706.03762"],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0 and "@article{lecun2015deep," in p.stdout and "@misc{vaswani2017attention," in p.stdout


# ------------------------------------------------------------------ review findings

def test_less_than_signs_survive():
    assert sc.clean_abstract("Patients aged <65 years had higher risk (HR >1.5, p<0.01).", False) == \
        "Patients aged <65 years had higher risk (HR >1.5, p<0.01)."
    assert sc.clean("Uptake of CO<sub>2</sub> in <i>E. coli</i> at p&lt;0.05") == "Uptake of CO2 in E. coli at p<0.05"
    assert sc.clean("<jats:p>One.</jats:p><jats:p>Two.</jats:p>") == "One. Two."
    root = sc.ET.fromstring(EFETCH.replace("Deep learning.</ArticleTitle>", "Outcomes in adults &lt;65 years and "
                                           "children &gt;2 years</ArticleTitle>"))
    assert sc.pubmed_records(root, ["26017442"])[0]["title"] == "Outcomes in adults <65 years and children >2 years"


def test_same_short_title_different_works():
    a = sc.record("crossref", 0, title="Introduction", doi="10.1007/978-3-030-1_1", date="2020", type="book-chapter")
    b = sc.record("crossref", 1, title="Introduction", doi="10.1007/978-3-030-2_1", date="2020", type="book-chapter")
    c = sc.record("pubmed", 0, title="Editorial", pmid="1", date="2020")
    d = sc.record("europepmc", 0, title="Editorial", pmid="2", date="2020")
    assert len(sc.merge([a, b, c, d])) == 4


def test_colons_in_plain_queries():
    assert sc.arxiv_query("Transformers: a survey") == "all:Transformers AND all:survey"
    assert sc.query_words("Transformers: a survey of efficient attention") == ["transformers", "survey", "efficient",
                                                                                "attention"]
    for q in ("ti:bert", "AUTH:Doudna AND crispr", "crispr[ti]", "(cat:cs.CL)"):
        assert sc.query_words(q) == [], q


def test_pubmed_book_records():
    xml = """<PubmedArticleSet><PubmedBookArticle><BookDocument><PMID>20301295</PMID>
    <Book><BookTitle>GeneReviews®</BookTitle><PubDate><Year>1993</Year></PubDate></Book>
    <ArticleTitle>Cystic Fibrosis</ArticleTitle><AuthorList><Author><LastName>Ong</LastName><ForeName>Thida</ForeName>
    </Author></AuthorList><Abstract><AbstractText>Clinical characteristics.</AbstractText></Abstract></BookDocument>
    <PubmedBookData><ArticleIdList><ArticleId IdType="bookaccession">NBK1250</ArticleId></ArticleIdList></PubmedBookData>
    </PubmedBookArticle></PubmedArticleSet>"""
    (r,) = sc.pubmed_records(sc.ET.fromstring(xml), ["20301295"])
    assert (r["title"], r["venue"], r["authors"], r["year"], r["type"]) == \
        ("Cystic Fibrosis", "GeneReviews®", ["Thida Ong"], 1993, "book-chapter")


def test_plural_and_singular_match():
    r = sc.record("crossref", 0, title="Lithium battery degradation in a cohort study")
    assert sc.has_all_words(r, "lithium batteries") and sc.has_all_words(r, "cohort studies")
    assert not sc.has_all_words(r, "sodium batteries")


def test_failed_source_is_not_no_results(capsys):
    FakeNet.rate_limited, FakeNet.epmc = {"export.arxiv.org"}, []
    code, out, err = run("zzzz qqqq", "--source", "arxiv,europepmc", capsys=capsys)
    assert code == 4 and "no results, and some sources failed" in err


def test_zotero_bypasses_proxies(zotero, capsys, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    Zotero.status = 200
    d = results("graph neural networks", "--source", "arxiv", "--zotero", capsys=capsys)
    assert {r["in_zotero"] for r in d["results"]} == {True, False}


def test_truncated_answer_is_a_network_error(monkeypatch):
    import http.client
    monkeypatch.setattr(sc.Net, "get", ORIGINAL_GET)
    calls = []

    def broken(req, timeout):
        calls.append(req.full_url)
        raise http.client.IncompleteRead(b"partial")
    monkeypatch.setattr(sc.urllib.request, "urlopen", broken)
    monkeypatch.setattr(sc.time, "sleep", lambda s: None)
    net = sc.Net(5, None, lambda m: None)
    with pytest.raises(sc.Failure) as e:
        net.get("https://api.crossref.org/works?query=x")
    assert e.value.code == sc.EXIT_NETWORK and len(calls) == 2


def test_blank_europepmc_title_is_skipped(capsys):
    FakeNet.epmc = [{**EPMC[0], "title": "&nbsp;"}, EPMC[0]]
    d = results("deep learning", "--source", "europepmc", capsys=capsys)
    assert [r["title"] for r in d["results"]] == ["Deep learning"]
