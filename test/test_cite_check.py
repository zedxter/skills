"""Tests for skills/cite-check/scripts/cite_check.py.

Run: uv run --no-project --with pytest --with "bibtexparser>=2.0.0b7" --with pypdf python -I -m pytest \
         test/test_cite_check.py -q --doctest-modules skills/cite-check/scripts/cite_check.py

Offline: Net.get is replaced with canned answers shaped like doi.org (CSL-JSON), Crossref, arXiv (Atom), PubMed
esummary, Europe PMC and Unpaywall. CITE_LIVE=1 adds the smoke test the ticket asks for: three real DOIs and one
fake, against the real services.
"""

import io
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "cite-check" / "scripts" / "cite_check.py"
sys.path.insert(0, str(SCRIPT.parent))
import cite_check as cc  # noqa: E402

LECUN = {"DOI": "10.1038/nature14539", "title": "Deep learning", "container-title": "Nature", "type": "journal-article",
         "issued": {"date-parts": [[2015, 5, 27]]},
         "author": [{"family": "LeCun", "given": "Yann"}, {"family": "Bengio", "given": "Yoshua"},
                    {"family": "Hinton", "given": "Geoffrey"}]}
SILVER = {"DOI": "10.1038/nature16961", "title": ["Mastering the game of Go with deep neural networks and tree search"],
          "container-title": ["Nature"], "type": "journal-article", "issued": {"date-parts": [[2016, 1, 27]]},
          "author": [{"family": "Silver", "given": "David"}, {"family": "Huang", "given": "Aja"}]}
PNAS = {"DOI": "10.1073/pnas.0000001", "title": "Protein folding kinetics", "issued": {"date-parts": [[2010]]},
        "container-title": "Proceedings of the National Academy of Sciences of the United States of America",
        "author": [{"family": "Müller", "given": "Jörg"}]}
DOIS = {"10.1038/nature14539": LECUN, "10.1038/nature16961": SILVER, "10.1073/pnas.0000001": PNAS}
ARXIV_ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/1706.03762v7</id><published>2017-06-12T17:57:34Z</published>
<title>Attention Is All You
  Need</title><author><name>Ashish Vaswani</name></author><author><name>Noam Shazeer</name></author></entry></feed>"""
HE_ATOM = ARXIV_ATOM.replace("1706.03762v7", "1512.03385v1").replace("2017-06-12", "2015-12-10").replace(
    "Attention Is All You\n  Need", "Deep Residual Learning for Image Recognition").replace(
    "<name>Ashish Vaswani</name></author><author><name>Noam Shazeer</name>", "<name>Kaiming He</name></author><author><name>Xiangyu Zhang</name>")
EMPTY_ATOM = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
PUBMED = {"result": {"uids": ["31452104"], "31452104": {
    "title": "Molegro Virtual Docker for Docking.", "pubdate": "2019", "fulljournalname": "Methods in molecular biology",
    "authors": [{"name": "Bitencourt-Ferreira G"}, {"name": "de Azevedo WF Jr"}],
    "articleids": [{"idtype": "doi", "value": "10.1007/978-1-4939-9752-7_10"}]}}}


class FakeNet:
    """Answers by URL, records calls; installed in place of Net.get."""
    calls: list[str] = []
    rate_limit_after: int | None = None
    down: str | None = None

    @staticmethod
    def get(self, url, accept="application/json", headers=None):  # noqa: ANN001, ANN205 - replaces a method
        FakeNet.calls.append(url)
        if FakeNet.rate_limit_after is not None and len(FakeNet.calls) > FakeNet.rate_limit_after:
            raise cc.Failure(cc.EXIT_RATE, "api.crossref.org rate limit (HTTP 429)", "wait")
        u = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(u.query)
        if u.hostname == "doi.org":
            doi = urllib.parse.unquote(u.path.lstrip("/")).removeprefix("api/handles/")
            if doi in DOIS:
                return 200, json.dumps(DOIS[doi]).encode()
            return 404, b'{"responseCode": 100}'
        if u.hostname == "api.crossref.org":
            if FakeNet.down == "crossref":
                raise cc.Failure(cc.EXIT_NETWORK, "api.crossref.org is not answering (HTTP 503)", "down")
            # Like the real search: every record that shares a word comes back, near misses included.
            text = set(cc.norm(q["query.bibliographic"][0]).split())
            items = [d for d in (LECUN, SILVER, PNAS) if text & set(cc.content_words(
                d["title"][0] if isinstance(d["title"], list) else d["title"]))]
            items = [{**d, "title": d["title"] if isinstance(d["title"], list) else [d["title"]],
                      "container-title": d["container-title"] if isinstance(d["container-title"], list)
                      else [d["container-title"]]} for d in items]
            return 200, json.dumps({"message": {"items": items}}).encode()
        if u.hostname == "export.arxiv.org":
            if "1706.03762" in q.get("id_list", [""])[0] or "attention" in q.get("search_query", [""])[0]:
                return 200, ARXIV_ATOM.encode()
            if "1512.03385" in q.get("id_list", [""])[0]:
                return 200, HE_ATOM.encode()
            return 200, EMPTY_ATOM.encode()
        if u.hostname == "eutils.ncbi.nlm.nih.gov":
            pmid = q["id"][0]
            if pmid == "31452104":
                return 200, json.dumps(PUBMED).encode()
            return 200, json.dumps({"result": {"uids": [pmid], pmid: {"error": "cannot get document summary"}}}).encode()
        if u.hostname == "www.ebi.ac.uk":
            return 200, json.dumps({"resultList": {"result": []}}).encode()
        if u.hostname == "api.unpaywall.org":
            return 200, json.dumps({"best_oa_location": {"url_for_pdf": "https://oa.example.org/paper.pdf"}}).encode()
        return 404, b""


@pytest.fixture(autouse=True)
def fake_net(monkeypatch):
    FakeNet.calls = []
    FakeNet.rate_limit_after = None
    FakeNet.down = None
    monkeypatch.setattr(cc.Net, "get", FakeNet.get)
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)


def run(*argv, stdin: str | None = None, capsys=None):
    saved = sys.stdin
    if stdin is not None:
        sys.stdin = io.TextIOWrapper(io.BytesIO(stdin.encode()))
    try:
        code = cc.main(list(argv))
    finally:
        sys.stdin = saved
    out = capsys.readouterr() if capsys else None
    return code, (out.out if out else ""), (out.err if out else "")


BIB = r"""@string{nat = "Nature"}
@article{lecun2015, author = {LeCun, Yann and Bengio, Yoshua and Hinton, Geoffrey}, title = {Deep {L}earning},
  journal = nat, year = 2015, doi = {https://doi.org/10.1038/nature14539}}
@article{wrongyear, author = {LeCun, Yann and Bengio, Yoshua}, title = {Deep learning}, journal = {Nature}, year = {2019}}
@article{offbyone, author = {LeCun, Yann}, title = {Deep learning}, journal = {Nature}, year = {2016}}
@article{fake, author = {Smith, John}, title = {Quantum gradient boosting for protein folding at scale},
  journal = {Nature Machine Intelligence}, year = {2023}, doi = {10.1038/s42256-023-99999-9}}
@article{wrongdoi, author = {Silver, David}, title = {Mastering the game of Go with deep neural networks and tree search},
  journal = {Nature}, year = {2016}, doi = {10.1038/nature14539}}
@misc{vaswani, author = {Vaswani, Ashish and others}, title = {Attention Is All You Need}, year = {2017},
  eprint = {1706.03762}, archivePrefix = {arXiv}}
@article{pnas, author = {M{\"u}ller, J{\"o}rg}, title = {Protein folding kinetics},
  journal = {Proc. Natl. Acad. Sci. U.S.A.}, year = {2010}, doi = {10.1073/pnas.0000001}}
@article{wrongvenue, author = {LeCun, Yann}, title = {Deep learning}, journal = {Science}, year = {2015},
  doi = {10.1038/nature14539}}
@misc{nothing, note = {see the web}}
@article{broken, title = {unclosed
"""


@pytest.fixture
def bib(tmp_path):
    p = tmp_path / "refs.bib"
    p.write_text(BIB, encoding="utf-8")
    return p


def by_key(out: str) -> dict:
    return {r["key"]: r for r in json.loads(out)["results"]}


# ------------------------------------------------------------------ verdicts on BibTeX

def test_bibtex_verdicts(bib, capsys):
    code, out, err = run(str(bib), "-f", "json", "-q", capsys=capsys)
    r = by_key(out)
    assert code == 1
    assert r["lecun2015"]["verdict"] == "verified" and r["lecun2015"]["match"]["doi"] == "10.1038/nature14539"
    assert r["wrongyear"]["verdict"] == "mismatch" and r["wrongyear"]["mismatches"] == [
        {"field": "year", "given": "2019", "found": "2015"}]
    assert r["offbyone"]["verdict"] == "verified" and "one year apart" in r["offbyone"]["notes"][0]
    assert r["fake"]["verdict"] == "not_found" and "is not registered" in r["fake"]["notes"][0]
    assert r["vaswani"]["verdict"] == "verified" and r["vaswani"]["oa_url"] == "https://arxiv.org/pdf/1706.03762"
    assert r["pnas"]["verdict"] == "verified"  # abbreviated journal name, LaTeX-encoded author
    assert r["wrongvenue"]["verdict"] == "mismatch" and [d["field"] for d in r["wrongvenue"]["mismatches"]] == ["venue"]
    assert r["nothing"]["verdict"] == "unverifiable"
    assert any(k.startswith("line-") and v["verdict"] == "unparsed" for k, v in r.items())


def test_doi_pointing_to_another_paper(bib, capsys):
    _, out, _ = run(str(bib), "-f", "json", "-q", capsys=capsys)
    r = by_key(out)["wrongdoi"]
    assert r["verdict"] == "mismatch" and {d["field"] for d in r["mismatches"]} >= {"doi", "title", "authors"}
    assert r["mismatches"][0] == {"field": "doi", "given": "10.1038/nature14539", "found": "10.1038/nature14539",
                                  "reason": "resolves to a different work"}
    assert "resolves to a different work" in " ".join(r["notes"])
    assert r["suggested"]["doi"] == "10.1038/nature16961"


def test_summary_and_issues_only(bib, capsys):
    _, out, _ = run(str(bib), "-f", "json", "-q", "--only", "issues", capsys=capsys)
    d = json.loads(out)
    assert d["summary"]["verified"] == 4
    _, out, _ = run(str(bib), "-q", "--only", "issues", capsys=capsys)
    assert "4 verified" in out.splitlines()[-1] and "VERIFIED" not in out  # the table summary counts every entry
    assert all(r["verdict"] != "verified" for r in d["results"])
    assert sum(d["summary"].values()) == d["checked"] == d["total"]


def test_table_output(bib, capsys):
    code, out, _ = run(str(bib), "-q", capsys=capsys)
    assert "VERIFIED     lecun2015\n  cited:   Deep Learning\n  found:   Deep learning\n" in out
    assert "differs: year: cited 2019 · found 2015" in out
    assert out.rstrip().splitlines()[-1].startswith("summary: ")


def test_annotated_bibtex(bib, tmp_path, capsys):
    out = tmp_path / "checked.bib"
    code, _, _ = run(str(bib), "-f", "bibtex", "-o", str(out), "-q", capsys=capsys)
    text = out.read_text(encoding="utf-8")
    assert "@article{lecun2015,\n  cite_check = {verified},\n  cite_check_doi = {10.1038/nature14539}," in text
    assert "cite_check_differs = {year: found 2015}" in text
    assert "journal = nat" in text  # nothing else in the file changes
    stripped = re.sub(r"\n  cite_check\w* = \{[^{}\n]*\},", "", text)
    assert " ".join(stripped.split()) == " ".join(BIB.split())
    reparsed = cc.bibtexparser.parse_string(text)  # the result is still valid BibTeX
    assert len(reparsed.entries) == len(cc.bibtexparser.parse_string(BIB).entries)
    code, _, _ = run(str(out), "-f", "bibtex", "-o", str(out), "-q", capsys=capsys)  # annotating again replaces
    assert out.read_text(encoding="utf-8").count("cite_check = ") == text.count("cite_check = ")


def test_annotation_keeps_latin1_and_crlf(tmp_path, capsys):
    p = tmp_path / "l1.bib"
    p.write_bytes("@article{a,\r\n  author = {M\xfcller, J\xf6rg},\r\n  title = {Protein folding kinetics},\r\n"
                  "  journal = {Proc. Natl. Acad. Sci. U.S.A.},\r\n  year = {2010},\r\n  doi = {10.1073/pnas.0000001}\r\n}\r\n"
                  .encode("latin-1"))
    out = tmp_path / "o.bib"
    run(str(p), "-f", "bibtex", "-o", str(out), "-q", capsys=capsys)
    data = out.read_bytes().decode("utf-8")
    assert "M\xfcller" in data and "\r\n  cite_check = {verified},\r\n" in data


# ------------------------------------------------------------------ text, PDF and one-off input

TEXT = """# References

1. LeCun Y, Bengio Y, Hinton G. Deep learning. Nature. 2015;521:436-444.
2. Johnson A, Patel R. Neural scaling of synthetic mitochondria in zebrafish cognition. Cell. 2021;184:455-470.
3. Vaswani A, et al. Attention is all you need. arXiv:1706.03762 (2017).
4. Bitencourt-Ferreira G, de Azevedo WF. Molegro Virtual Docker for Docking. PMID: 31452104. 2019.
5. Some made up paper. PMID: 99999999. 2020.
"""


def test_text_references(tmp_path, capsys):
    p = tmp_path / "refs.md"
    p.write_text(TEXT, encoding="utf-8")
    code, out, _ = run(str(p), "-f", "json", "-q", capsys=capsys)
    r = by_key(out)
    assert [r[f"ref{i}"]["verdict"] for i in range(1, 6)] == ["verified", "not_found", "verified", "verified", "not_found"]
    assert r["ref5"]["mismatches"] == [{"field": "pmid", "given": "99999999", "found": None, "reason": "does not exist"}]
    assert r["ref2"]["input"]["raw"].startswith("Johnson A, Patel R.")
    assert r["ref4"]["match"]["source"] == "pubmed" and "PMID 99999999 does not exist" in r["ref5"]["notes"]


def test_numbered_continuation_lines_stay_whole():
    text = ("1. LeCun Y, Bengio Y. Deep learning. Nature\n521. 436-444 (2015).\n"
            "2. He K. Deep residual learning.\n2016. pp 770-778.\n3. Watson JD. Molecular structure. Nature 1953.")
    refs = cc.split_references(text)
    assert len(refs) == 3 and refs[0].endswith("521. 436-444 (2015).")


@pytest.mark.parametrize("text, n", [
    ("[1] A. Smith. A title here. 2020.\n[2] B. Jones. Another title. 2021.", 2),
    ("- Smith A. A title here. 2020.\n- Jones B. Another title, 2021.", 2),
    ("Smith A. A title here. 2020.\n\nJones B. Another title.\nSecond line, 2021.", 2),
    ("Not a reference line\nSmith A. A title here. 2020.", 1),
])
def test_splitting(text, n):
    assert len(cc.split_references(text)) == n


def test_pdf_bibliography(tmp_path, capsys):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DecodedStreamObject, DictionaryObject
    w = PdfWriter()
    page = w.add_blank_page(612, 792)
    lines = ["Some paper text.", "References", "[1] LeCun Y, Bengio Y, Hinton G. Deep learning. Nature 2015.",
             "[2] Johnson A. Neural scaling of synthetic mitochondria. Cell 2021."]
    ops = "BT /F1 10 Tf 50 750 Td 14 TL " + " ".join(f"({l}) Tj T*" for l in lines) + " ET"
    stream = DecodedStreamObject()
    stream.set_data(ops.encode())
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    page[NameObject("/Contents")] = w._add_object(stream)
    p = tmp_path / "paper.pdf"
    with open(p, "wb") as fh:
        w.write(fh)
    code, out, err = run(str(p), "-f", "json", "-q", capsys=capsys)
    r = by_key(out)
    assert code == 1 and r["ref1"]["verdict"] == "verified" and r["ref2"]["verdict"] == "not_found", err


def test_pdf_without_bibliography(tmp_path, capsys):
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(100, 100)
    p = tmp_path / "x.pdf"
    with open(p, "wb") as fh:
        w.write(fh)
    code, _, err = run(str(p), capsys=capsys)
    assert code == 7 and "no References" in err


def test_single_reference_from_flags(capsys):
    code, out, _ = run("--doi", "10.1038/nature14539", "--title", "Deep learning", "--year", "2015", "-f", "json",
                       "-q", capsys=capsys)
    assert code == 0 and json.loads(out)["results"][0]["verdict"] == "verified"
    code, out, _ = run("--title", "Totally invented study of nothing", "-f", "json", "-q", capsys=capsys)
    assert code == 1 and json.loads(out)["results"][0]["verdict"] == "not_found"


def test_stdin(capsys):
    code, out, _ = run("-", "-f", "json", "-q", stdin="LeCun Y, Bengio Y, Hinton G. Deep learning. Nature 2015.",
                       capsys=capsys)
    assert code == 0 and json.loads(out)["results"][0]["verdict"] == "verified"


# ------------------------------------------------------------------ email, open access, resume, rate limits

def test_email_enables_unpaywall_and_polite_pool(bib, capsys):
    code, out, err = run(str(bib), "-f", "json", "-q", "--email", "me@uni.edu", capsys=capsys)
    assert by_key(out)["lecun2015"]["oa_url"] == "https://oa.example.org/paper.pdf"
    assert any("api.unpaywall.org" in c and "email=me%40uni.edu" in c for c in FakeNet.calls)
    assert any("api.crossref.org" in c and "mailto=me%40uni.edu" in c for c in FakeNet.calls)


def test_no_email_means_no_unpaywall(bib, capsys):
    run(str(bib), "-q", capsys=capsys)
    assert not any("unpaywall" in c for c in FakeNet.calls)


def test_resume_skips_done_entries(bib, tmp_path, capsys):
    run(str(bib), "-q", capsys=capsys)
    full_run_calls = len(FakeNet.calls)
    state = tmp_path / "progress.json"
    FakeNet.calls = []
    FakeNet.rate_limit_after = 4
    code, out, err = run(str(bib), "-q", "--resume", str(state), capsys=capsys)
    assert code == 4 and "unchecked" in err and state.is_file()
    done = json.loads(state.read_text())
    assert 0 < len(done) < 11 and "lecun2015" in {v["key"] for v in done.values()}
    FakeNet.rate_limit_after = None
    FakeNet.calls = []
    code, out, err = run(str(bib), "-f", "json", "-q", "--resume", str(state), capsys=capsys)
    assert json.loads(out)["checked"] == json.loads(out)["total"]
    assert 0 < len(FakeNet.calls) < full_run_calls
    assert not any("nature14539" in c for c in FakeNet.calls[:1])  # lecun2015 was not looked up again


def test_corrupt_resume_file_is_refused(bib, tmp_path, capsys):
    state = tmp_path / "p.json"
    state.write_bytes(b"\xff\xfe not json")
    code, _, err = run(str(bib), "--resume", str(state), capsys=capsys)
    assert code == 2 and "progress file" in err and state.read_bytes().startswith(b"\xff")


def test_outage_is_exit_6_and_not_cached(bib, tmp_path, capsys):
    state = tmp_path / "p.json"
    FakeNet.down = "crossref"
    code, out, err = run(str(bib), "-q", "--resume", str(state), capsys=capsys)
    assert code == 6 and "not answering" in err
    assert all(v["verdict"] != "not_found" or "is not registered" in " ".join(v["notes"])
               for v in json.loads(state.read_text()).values())


# ------------------------------------------------------------------ helpers and input errors

def test_title_and_venue_helpers():
    assert cc.same_title("Deep learning", "Deep Learning.") and not cc.same_title("Deep learning", "Deep learning: a review")
    assert cc.similarity("Attention is all you need", "Deep residual learning") < cc.TITLE_CLOSE
    assert cc.name_keys("LeCun, Yann") == cc.name_keys("Yann LeCun") == {"lecun"} and cc.name_keys("Hinton GE") == {"hinton"}
    assert cc.venue_match("J. Mach. Learn. Res.", "Journal of Machine Learning Research")
    assert not cc.venue_match("Nature", "Science")
    assert cc.clean_doi("https://doi.org/10.1038/NATURE14539.") == "10.1038/NATURE14539"
    assert cc.ids_from_text("see arXiv:2301.00001v2 and doi:10.1234/x.") == {"doi": "10.1234/x", "arxiv": "2301.00001"}


@pytest.mark.parametrize("argv, code", [
    ([], 2), (["missing.bib"], 2), (["x.md", "-f", "bibtex"], 2), (["--title", "x", "--year", "15"], 2),
    (["--title", "x", "--email", "not-an-email"], 2), (["--title", "x", "--timeout", "0"], 2),
])
def test_bad_input(argv, code, capsys):
    assert run(*argv, capsys=capsys)[0] == code


def test_empty_inputs_are_parse_errors(tmp_path, capsys):
    p = tmp_path / "empty.md"
    p.write_text("just some words without any reference\n", encoding="utf-8")
    assert run(str(p), capsys=capsys)[0] == 7
    p = tmp_path / "empty.bib"
    p.write_text("% only a comment\n", encoding="utf-8")
    assert run(str(p), capsys=capsys)[0] == 7


def test_help_runs_standalone():
    p = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=120)
    assert p.returncode in (0, 2) and ("Exit codes" in p.stdout or "missing dependency" in p.stderr)


# ------------------------------------------------------------------ live smoke test

@pytest.mark.skipif(os.environ.get("CITE_LIVE") != "1", reason="live network test; set CITE_LIVE=1")
def test_live_three_dois_and_a_fake(tmp_path):
    bib = tmp_path / "live.bib"
    bib.write_text("""
@article{a, title={Deep learning}, author={LeCun, Yann}, journal={Nature}, year={2015}, doi={10.1038/nature14539}}
@article{b, title={Molecular Structure of Nucleic Acids: A Structure for Deoxyribose Nucleic Acid},
  author={Watson, J. D.}, journal={Nature}, year={1953}, doi={10.1038/171737a0}}
@inproceedings{c, title={Deep Residual Learning for Image Recognition}, author={He, Kaiming}, year={2016},
  booktitle={CVPR}, doi={10.1109/CVPR.2016.90}}
@article{d, title={Quantum gradient boosting for protein folding at scale}, author={Smith, John}, year={2023},
  journal={Nature Machine Intelligence}, doi={10.1038/s42256-023-99999-9}}
""", encoding="utf-8")
    p = subprocess.run([sys.executable, "-c", "import sys, runpy; sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')",
                        str(SCRIPT), str(bib), "-f", "json", "-q"], capture_output=True, text=True, encoding="utf-8",
                       timeout=300)
    r = {x["key"]: x["verdict"] for x in json.loads(p.stdout)["results"]}
    assert p.returncode == 1 and r == {"a": "verified", "b": "verified", "c": "verified", "d": "not_found"}, p.stderr


# ------------------------------------------------------------------ fabricated references must not verify

ATTACKS = r"""
@article{realdoi_faketitle, author={LeCun, Yann}, title={Deep learning for protein structure prediction at scale},
  journal={Nature}, year={2015}, doi={10.1038/nature14539}}
@article{fake_coauthors, author={LeCun, Yann and Fabricated, Zed and Imaginary, Quinn}, title={Deep learning},
  journal={Nature}, year={2015}, doi={10.1038/nature14539}}
@article{wrong_journal, author={LeCun, Yann and Bengio, Yoshua}, title={Deep learning}, journal={Nature Communications},
  year={2015}}
@misc{fake_doi_real_arxiv, author={Vaswani, Ashish}, title={Attention Is All You Need}, year={2017},
  doi={10.1000/fabricated.2017.0042}, eprint={1706.03762}, archivePrefix={arXiv}}
@article{extended_title, author={LeCun, Yann and Hinton, Geoffrey}, title={Deep learning for protein structure prediction},
  journal={Nature}, year={2015}}
@misc{he_arxiv, author={He, Kaiming and Zhang, Xiangyu}, title={Deep Residual Learning for Image Recognition},
  year={2015}, eprint={1512.03385}, archivePrefix={arXiv}}
"""


def test_fabrications_are_caught(tmp_path, capsys):
    p = tmp_path / "attacks.bib"
    p.write_text(ATTACKS, encoding="utf-8")
    _, out, _ = run(str(p), "-f", "json", "-q", capsys=capsys)
    r = by_key(out)
    assert {k: v["verdict"] for k, v in r.items()} == {
        "realdoi_faketitle": "mismatch", "fake_coauthors": "mismatch", "wrong_journal": "mismatch",
        "fake_doi_real_arxiv": "mismatch", "extended_title": "not_found", "he_arxiv": "verified"}
    assert r["fake_coauthors"]["mismatches"][0]["not_in_record"] == ["Fabricated, Zed", "Imaginary, Quinn"]
    assert r["wrong_journal"]["mismatches"] == [{"field": "venue", "given": "Nature Communications", "found": "Nature"}]
    assert r["fake_doi_real_arxiv"]["mismatches"][0]["reason"] == "does not exist"


def test_free_text_fabrications(tmp_path, capsys):
    p = tmp_path / "t.md"
    p.write_text("1. LeCun Y, Bengio Y, Hinton G. Deep learning for quantum gravity. Nature. 2015.\n"
                 "2. Smith J, LeCun Y. Deep learning approaches for quantum chemistry. Nature. 2015;521:1-10.\n"
                 "3. Smith J. \"Deep learning.\" Nature 2015.\n", encoding="utf-8")
    _, out, _ = run(str(p), "-f", "json", "-q", capsys=capsys)
    r = by_key(out)
    assert all(v["verdict"] != "verified" for v in r.values()), {k: v["verdict"] for k, v in r.items()}
    assert [d["field"] for d in r["ref3"]["mismatches"]] == ["authors"]


def test_doi_alone_and_doi_without_metadata(capsys, monkeypatch):
    code, out, _ = run("--doi", "10.1038/nature14539", "-f", "json", "-q", capsys=capsys)
    r = json.loads(out)["results"][0]
    assert code == 0 and r["verdict"] == "verified" and "only the identifier was checked" in r["notes"][-1]
    original = FakeNet.get

    def nometa(self, url, accept="application/json", headers=None, follow=True):
        if "doi.org/10.5555" in url:
            return 406, b"<html>landing page</html>"
        if "doi.org/api/handles/10.5555" in url:
            return 200, b"{}"
        return original(self, url, accept, headers)
    monkeypatch.setattr(cc.Net, "get", nometa)
    code, out, _ = run("--doi", "10.5555/registered", "--title", "Quantum gradient boosting for protein folding",
                       "-f", "json", "-q", capsys=capsys)
    assert json.loads(out)["results"][0]["verdict"] in ("unverifiable", "not_found")


def test_names():
    assert cc.same_person("He, Kaiming", "Kaiming He", True) and cc.same_person("Ng, Andrew", "Andrew Ng", True)
    assert cc.same_person("de Azevedo, Walter F.", "de Azevedo WF Jr")
    assert cc.same_person("Bitencourt-Ferreira, G.", "Bitencourt-Ferreira G")
    assert not cc.same_person("Smith, John", "LeCun, Yann")


def test_output_is_one_line_per_value():
    assert cc.safe("Deep learning\nVERIFIED     spoofed\x1b[2J") == "Deep learning VERIFIED spoofed [2J"
    assert cc.bibtex_value("ends with \\") == "{ends with}"
