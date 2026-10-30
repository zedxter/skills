"""Tests for skills/anki-deck/scripts/anki_deck.py.

Run: uv run --no-project --with pytest --with genanki==0.13.1 --with "markdown-it-py>=3,<5" --with anki \
         python -I -m pytest test/test_anki_deck.py -q --doctest-modules skills/anki-deck/scripts/anki_deck.py

The import tests load the built decks into a fresh collection with Anki's own Python library (the `anki` package,
the code Anki desktop runs), so "opens in Anki" and "importing again updates instead of duplicating" are checked
against Anki itself. Without the `anki` package those tests are skipped.
"""

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "anki-deck" / "scripts" / "anki_deck.py"
# Loaded under another name so that it never shadows Anki's own `anki` package.
spec = importlib.util.spec_from_file_location("anki_deck_script", SCRIPT)
ad = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ad)

try:
    from anki.collection import Collection, ImportAnkiPackageOptions, ImportAnkiPackageRequest
    HAVE_ANKI = True
except ImportError:
    HAVE_ANKI = False
needs_anki = pytest.mark.skipif(not HAVE_ANKI, reason="the anki package is not installed")

CELLS = """# Biology::Cells

Q: What organelle makes ATP?
A: The **mitochondrion**.
Extra: Via oxidative phosphorylation.
Tags: organelles

Q: Write the formula for glucose.
A: $C_6H_{12}O_6$ and \\(x < y\\)

Q: What does this show?
A: ![cell](img/cell.png)
---

The {{c1::nucleus}} stores DNA; the {{c2::ribosome}} makes proteins.
"""


def run(*argv, capsys):
    code = ad.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def cells(tmp_path):
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "cell.png").write_bytes(b"\x89PNG fake")
    p = tmp_path / "cells.md"
    p.write_text(CELLS, encoding="utf-8")
    return p


def preview(*argv, capsys):
    code, out, err = run("build", *argv, "--dry-run", "--json", capsys=capsys)
    assert code == 0, err
    return json.loads(out)


def test_ids_are_stable_and_documented():
    assert ad.stable_id("deck", "Biology::Cells") == ad.stable_id("deck", "Biology::Cells")
    assert ad.stable_id("deck", "Biology::Cells") != ad.stable_id("deck", "Biology::Cells", "v2")
    assert ad.stable_id("model", "anki-deck Basic") != ad.stable_id("deck", "anki-deck Basic")
    for name in ("x", "Deutsch::Wörter", "日本語"):
        assert (1 << 30) <= ad.stable_id("deck", name) < (1 << 31)
    import hashlib
    h = int.from_bytes(hashlib.sha256(b"anki-deck/deck//Biology::Cells").digest()[:8], "big")
    assert ad.stable_id("deck", "Biology::Cells") == h % (1 << 30) + (1 << 30)


def test_markdown_qa_with_math_media_and_cloze(cells, capsys):
    d = preview(cells, capsys=capsys)
    assert d["notes"] == 4 and d["cards"] == 5 and d["skipped"] == 0
    assert list(d["decks"]) == ["Biology::Cells"] and d["media"] == ["cell.png"]
    p = d["preview"]
    assert p[0]["fields"] == ["What organelle makes ATP?", "The <strong>mitochondrion</strong>.",
                              "Via oxidative phosphorylation."] and p[0]["tags"] == ["organelles"]
    assert p[1]["fields"][1] == "\\(C_6H_{12}O_6\\) and \\(x &lt; y\\)"
    assert p[2]["fields"][1] == '<img src="cell.png" alt="cell" />'
    assert p[3]["type"] == "cloze" and p[3]["fields"][0].startswith("The {{c1::nucleus}} stores DNA")


def test_markdown_headings_and_cloze_lists(tmp_path, capsys):
    (tmp_path / "h.md").write_text("# Deck\n\n## What is 2+2?\n4\n\nTags: math easy\n\n## Capital of France?\n"
                                   "Paris\n\n### detail\nstill part of the answer\n", encoding="utf-8")
    d = preview(tmp_path / "h.md", capsys=capsys)
    assert [x["front"] for x in d["preview"]] == ["What is 2+2?", "Capital of France?"]
    assert d["preview"][0]["tags"] == ["math", "easy"] and "still part of the answer" in d["preview"][1]["back"]
    assert list(d["decks"]) == ["Deck"]
    (tmp_path / "c.md").write_text("- The {{c1::heart}} pumps blood.\n- Not a card.\n"
                                   "- {{c1::Mitosis}} has {{c2::four}} phases.\nExtra: PMAT\n", encoding="utf-8")
    d = preview(tmp_path / "c.md", capsys=capsys)
    assert d["notes"] == 2 and d["cards"] == 3 and d["preview"][1]["fields"][1] == "PMAT"


@pytest.mark.parametrize("name,text,expected", [
    ("a.csv", "front,back,tags\nhola,hello,es greet\n\"multi\nline\",\"x, y\",\n",
     [("hola", "hello", ["es", "greet"]), ("multi\nline", "x, y", [])]),
    ("b.csv", "hola,hello\nadiós,goodbye\n", [("hola", "hello", []), ("adiós", "goodbye", [])]),
    ("c.tsv", "Question\tAnswer\nH2O?\twater\n", [("H2O?", "water", [])]),
    ("d.csv", "frente;dorso\nuno;one\n", [("frente", "dorso", []), ("uno", "one", [])]),
    ("e.json", '[{"question": "Q1", "answer": "A1", "tags": ["t1"]}, ["Q2", "A2"]]',
     [("Q1", "A1", ["t1"]), ("Q2", "A2", [])]),
])
def test_tables_and_json(tmp_path, name, text, expected, capsys):
    (tmp_path / name).write_text(text, encoding="utf-8")
    d = preview(tmp_path / name, capsys=capsys)
    assert [(x["front"], x["back"], x["tags"]) for x in d["preview"]] == expected


def test_json_object_with_deck_cloze_and_types(tmp_path, capsys):
    (tmp_path / "x.json").write_text(json.dumps({"deck": "Langs::Spanish", "cards": [
        {"front": "perro", "back": "dog", "type": "reversed"},
        {"text": "El {{c1::gato}} duerme.", "extra": "cat"},
        {"front": "casa", "back": "house", "deck": "Langs::Spanish::Home"},
    ]}), encoding="utf-8")
    d = preview(tmp_path / "x.json", "--tags", "spanish", capsys=capsys)
    assert [x["type"] for x in d["preview"]] == ["reversed", "cloze", "basic"]
    assert d["cards"] == 4 and set(d["decks"]) == {"Langs::Spanish", "Langs::Spanish::Home"}
    assert all("spanish" in x["tags"] for x in d["preview"])


def test_bad_and_duplicate_cards_are_skipped(tmp_path, capsys):
    (tmp_path / "x.csv").write_text("front,back,id\nQ1,A1,\nQ1,A1 again,\nQ1,A1 kept,n3\nQ2,,\n,A3,\n",
                                    encoding="utf-8")
    code, out, err = run("build", tmp_path / "x.csv", "--dry-run", "--json", capsys=capsys)
    d = json.loads(out)
    assert code == 0 and d["notes"] == 2 and d["skipped"] == 3
    assert "the same card as x.csv:2" in err and "no answer" in err and "no question" in err
    code, _, err = run("build", tmp_path / "x.csv", "--type", "cloze", "--dry-run", capsys=capsys)
    assert code == 1 and "none of the" in err


def test_html_is_escaped_unless_asked(tmp_path, capsys):
    (tmp_path / "x.csv").write_text('front,back\n"<script>alert(1)</script> a < b","<b>bold</b>"\n', encoding="utf-8")
    d = preview(tmp_path / "x.csv", capsys=capsys)
    assert d["preview"][0]["fields"][:2] == ["&lt;script&gt;alert(1)&lt;/script&gt; a &lt; b", "&lt;b&gt;bold&lt;/b&gt;"]
    d = preview(tmp_path / "x.csv", "--html", capsys=capsys)
    assert d["preview"][0]["fields"][1] == "<b>bold</b>"


def test_media(cells, tmp_path, capsys):
    (tmp_path / "img" / "cell.png").unlink()
    code, _, err = run("build", cells, "-o", tmp_path / "d.apkg", capsys=capsys)
    assert code == 3 and "media file 'img/cell.png' not found (cells.md:" in err and not (tmp_path / "d.apkg").exists()
    code, out, _ = run("build", cells, "--dry-run", capsys=capsys)
    assert code == 3 and "missing media: img/cell.png" in out
    other = tmp_path / "pics"
    other.mkdir()
    (other / "cell.png").write_bytes(b"png")
    code, out, err = run("build", cells, "-o", tmp_path / "d.apkg", "--media", other, capsys=capsys)
    assert code == 0 and "1 media file" in out
    (tmp_path / "two.md").write_text("Q: a\nA: ![](x/p.png)\n\nQ: b\nA: ![](y/p.png)\n", encoding="utf-8")
    for d in ("x", "y"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "p.png").write_bytes(d.encode())
    code, _, err = run("build", tmp_path / "two.md", "-o", tmp_path / "t.apkg", capsys=capsys)
    assert code == 2 and "both named 'p.png'" in err


def test_build_twice_gives_identical_ids(cells, tmp_path, capsys):
    for name in ("a.apkg", "b.apkg"):
        assert run("build", cells, "-o", tmp_path / name, capsys=capsys)[0] == 0
    a, b = (ad.inspect_package(tmp_path / n) for n in ("a.apkg", "b.apkg"))
    assert a["decks"] == b["decks"] and a["note_types"] == b["note_types"]
    assert [s["guid"] for s in a["sample"]] == [s["guid"] for s in b["sample"]]
    assert a["decks"][0]["id"] == ad.stable_id("deck", "Biology::Cells") and a["media"] == ["cell.png"]
    assert {m["name"]: m["kind"] for m in a["note_types"]} == {"anki-deck Basic": "standard", "anki-deck Cloze": "cloze"}


def test_inspect_cli(cells, tmp_path, capsys):
    run("build", cells, "-o", tmp_path / "d.apkg", capsys=capsys)
    code, out, _ = run("inspect", tmp_path / "d.apkg", capsys=capsys)
    assert code == 0 and "4 notes, 5 cards" in out and "deck Biology::Cells" in out
    (tmp_path / "junk.apkg").write_text("not a zip")
    assert run("inspect", tmp_path / "junk.apkg", capsys=capsys)[0] == 2


@pytest.mark.parametrize("argv,code", [
    (["build", "missing.md", "-o", "x.apkg"], 2),
    (["build", "{cards}", "-o", "x.txt"], 2),
    (["build", "{cards}"], 2),
    (["build", "{cards}", "-o", "x.apkg", "--deck", "A::::B"], 2),
    (["build", "{empty}", "-o", "x.apkg"], 1),
    (["build", "{badjson}", "-o", "x.apkg"], 2),
    (["build", "{cards}", "-o", "x.apkg", "--format", "csv"], 1),
    (["build", "{cards}", "-o", "{afile}/x.apkg"], 7),
])
def test_exit_codes(tmp_path, argv, code, capsys):
    files = {"cards": "Q: a\nA: b\n", "empty": "nothing here\n", "badjson": "{oops"}
    paths = {k: tmp_path / (k + (".json" if k == "badjson" else ".md")) for k in files}
    for k, v in files.items():
        paths[k].write_text(v, encoding="utf-8")
    (tmp_path / "afile").write_text("x")
    argv = [a.format(**{k: str(v) for k, v in paths.items()}, afile=str(tmp_path / "afile")) for a in argv]
    argv = [str(tmp_path / a) if a.endswith((".apkg", ".txt")) and "/" not in a else a for a in argv]
    c, out, err = run(*argv, capsys=capsys)
    assert c == code, err
    assert err.splitlines()[-1].startswith("error: ")


def test_help_runs_standalone():
    out = subprocess.run([sys.executable, "-I", str(SCRIPT), "build", "--help"], capture_output=True, text=True)
    assert out.returncode in (0, 2)  # 2 when genanki is missing in this interpreter: a clean error, not a traceback
    assert "--deck" in out.stdout or "missing dependency" in out.stderr


# ------------------------------------------------------------------ Anki itself

def import_into(col, path):
    opts = ImportAnkiPackageOptions(with_scheduling=False)
    return col.import_anki_package(ImportAnkiPackageRequest(package_path=str(path), options=opts)).log


@needs_anki
def test_anki_imports_the_deck_and_updates_instead_of_duplicating(cells, tmp_path, capsys):
    col = Collection(str(tmp_path / "collection.anki2"))
    try:
        assert run("build", cells, "-o", tmp_path / "v1.apkg", "--tags", "bio", capsys=capsys)[0] == 0
        log = import_into(col, tmp_path / "v1.apkg")
        assert len(log.new) == 4 and col.note_count() == 4 and col.card_count() == 5
        assert "Biology::Cells" in [d.name for d in col.decks.all_names_and_ids()]
        assert col.media.have("cell.png")
        nids = col.find_notes('"note:anki-deck Basic"')
        assert len(nids) == 3 and "bio" in col.get_note(nids[0]).tags
        log = import_into(col, tmp_path / "v1.apkg")   # the same file again: nothing new
        assert len(log.new) == 0 and col.note_count() == 4

        time.sleep(1.1)  # Anki updates a note when the incoming one is newer (second resolution)
        cells.write_text(CELLS.replace("The **mitochondrion**.", "Mitochondria."), encoding="utf-8")
        assert run("build", cells, "-o", tmp_path / "v2.apkg", "--tags", "bio", capsys=capsys)[0] == 0
        log = import_into(col, tmp_path / "v2.apkg")
        assert len(log.new) == 0 and len(log.updated) >= 1 and col.note_count() == 4
        backs = [col.get_note(n)["Back"] for n in col.find_notes('"note:anki-deck Basic"')]
        assert "Mitochondria." in backs and "The <strong>mitochondrion</strong>." not in backs
    finally:
        col.close()


@needs_anki
def test_anki_card_types(tmp_path, capsys):
    (tmp_path / "x.json").write_text(json.dumps({"deck": "Langs::Spanish", "cards": [
        {"front": "perro", "back": "dog", "type": "reversed"},
        {"text": "El {{c1::gato}} y el {{c2::perro}}.", "extra": "cat, dog"}]}), encoding="utf-8")
    assert run("build", tmp_path / "x.json", "-o", tmp_path / "x.apkg", capsys=capsys)[0] == 0
    col = Collection(str(tmp_path / "c.anki2"))
    try:
        import_into(col, tmp_path / "x.apkg")
        assert col.note_count() == 2 and col.card_count() == 4
        cloze = col.get_note(col.find_notes('"note:anki-deck Cloze"')[0])
        assert cloze["Extra"] == "cat, dog"
        card = cloze.cards()[0]
        assert "[...]" in card.question() and "gato" in card.answer()
        rev = col.get_note(col.find_notes('"note:anki-deck Basic and reversed"')[0])
        assert sorted(c.ord for c in rev.cards()) == [0, 1]
    finally:
        col.close()


# ------------------------------------------------------------------ review findings

def test_media_never_leaves_the_input_folder(tmp_path, capsys):
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"not for the deck")
    deck = tmp_path / "deck"
    deck.mkdir()
    for ref, why in (("/etc/hostname.png", "outside"), ("../secret.png", "outside"), (str(secret), "outside"),
                     ("notes.txt", "not an image")):
        (deck / "c.md").write_text(f"Q: x\nA: ![]({ref})\n", encoding="utf-8")
        code, _, err = run("build", deck / "c.md", "-o", deck / "c.apkg", capsys=capsys)
        assert code == 2 and why in err, (ref, err)
    (deck / "c.md").write_text("Q: x\nA: [sound:/etc/passwd]\n", encoding="utf-8")
    assert run("build", deck / "c.md", "-o", deck / "c.apkg", capsys=capsys)[0] == 2
    (deck / "a&b.png").write_bytes(b"png")
    (deck / "c.md").write_text("Q: x\nA: ![](a&b.png)\n", encoding="utf-8")
    code, out, _ = run("build", deck / "c.md", "-o", deck / "c.apkg", capsys=capsys)
    assert code == 0 and ad.inspect_package(deck / "c.apkg")["media"] == ["a&b.png"]


def test_media_resolve_per_input_file(tmp_path, capsys):
    for d, data in (("a", b"A"), ("b", b"B")):
        (tmp_path / d / "img").mkdir(parents=True)
        (tmp_path / d / "img" / "cell.png").write_bytes(data)
    (tmp_path / "a" / "one.md").write_text("Q: one\nA: ![](img/cell.png)\n", encoding="utf-8")
    (tmp_path / "b" / "two.md").write_text("Q: two\nA: plain\n", encoding="utf-8")
    (tmp_path / "b" / "img" / "cell.png").unlink()
    code, _, err = run("build", tmp_path / "a" / "one.md", tmp_path / "b" / "two.md", "-o", tmp_path / "x.apkg",
                       capsys=capsys)
    assert code == 0, err
    import zipfile
    with zipfile.ZipFile(tmp_path / "x.apkg") as z:
        assert z.read("0") == b"A"


def test_code_fences_headings_and_bold_keys(tmp_path, capsys):
    (tmp_path / "c.md").write_text(
        "## Chapter 1\n\n**Q:** What does this YAML set?\n**A:** The kind:\n```yaml\nkind: Pod\n  id: 5\n  tags: web\n"
        "---\n# a comment\n```\nand more.\n\n## Chapter 2\n\nQ: Who designed C#?\nA: Anders Hejlsberg\n",
        encoding="utf-8")
    d = preview(tmp_path / "c.md", capsys=capsys)
    one, two = d["preview"]
    assert one["front"] == "What does this YAML set?" and one["tags"] == [] and "and more." in one["back"]
    assert "id: 5" in one["fields"][1] and "Chapter 2" not in one["fields"][1]
    assert two["front"] == "Who designed C#?" and list(d["decks"]) == ["c"]
    (tmp_path / "h.md").write_text("## Who designed C#\nAnders\n\n## Learn F# ##\nyes\n", encoding="utf-8")
    assert [x["front"] for x in preview(tmp_path / "h.md", capsys=capsys)["preview"]] == ["Who designed C#", "Learn F#"]


def test_math_and_dollars(tmp_path, capsys):
    (tmp_path / "m.md").write_text("Q: Price?\nA: $5 and $10, or `$x^2$` in code, but $x^2$ is math.\n", encoding="utf-8")
    back = preview(tmp_path / "m.md", capsys=capsys)["preview"][0]["fields"][1]
    assert "<code>$x^2$</code>" in back and "\\(x^2\\)" in back and "$5 and $10" in back


def test_cloze_ids_and_answer_edits(tmp_path, capsys):
    (tmp_path / "c.md").write_text("The {{c1::heart}} pumps blod.\nId: heart-1\nTags: cardio\n\n{{c0::zero}} is not a cloze.\n",
                                   encoding="utf-8")
    d = preview(tmp_path / "c.md", capsys=capsys)
    assert d["notes"] == 1 and d["preview"][0]["tags"] == ["cardio"]
    guid = d["preview"][0]["guid"]
    (tmp_path / "c.md").write_text("The {{c1::heart}} pumps blood.\nId: heart-1\n", encoding="utf-8")
    assert preview(tmp_path / "c.md", capsys=capsys)["preview"][0]["guid"] == guid      # the id keeps it
    (tmp_path / "d.md").write_text("The {{c1::hart}} pumps blood.\n", encoding="utf-8")
    g1 = preview(tmp_path / "d.md", capsys=capsys)["preview"][0]["guid"]
    (tmp_path / "d.md").write_text("The {{c1::heart}} pumps blood.\n", encoding="utf-8")
    assert preview(tmp_path / "d.md", capsys=capsys)["preview"][0]["guid"] == g1        # fixing the answer updates


@pytest.mark.parametrize("text,expected", [
    ("Term,Meaning\nperro,dog\n", [("perro", "dog")]),
    ("front,back\nPrimary colors?,red; green; blue\nRGB?,r; g; b\n",
     [("Primary colors?", "red; green; blue"), ("RGB?", "r; g; b")]),
    ("﻿front,back\nuno,one\n", [("uno", "one")]),
])
def test_csv_headers_and_delimiters(tmp_path, text, expected, capsys):
    (tmp_path / "x.csv").write_text(text, encoding="utf-8")
    assert [(x["front"], x["back"]) for x in preview(tmp_path / "x.csv", capsys=capsys)["preview"]] == expected


def test_unnamed_csv_header_is_noted(tmp_path, capsys):
    (tmp_path / "x.csv").write_text("Spanish,English\nperro,dog\n", encoding="utf-8")
    code, out, err = run("build", tmp_path / "x.csv", "--dry-run", capsys=capsys)
    assert code == 0 and "first row (Spanish, English) is read as a card" in err


def test_json_values_must_be_text(tmp_path, capsys):
    (tmp_path / "x.json").write_text('[{"front": "a", "back": ["x"]}]', encoding="utf-8")
    code, _, err = run("build", tmp_path / "x.json", "--dry-run", capsys=capsys)
    assert code == 2 and "'back' must be text" in err


def test_dry_run_json_reports_missing_media(tmp_path, capsys):
    (tmp_path / "c.md").write_text("Q: x\nA: ![](gone.png)\n", encoding="utf-8")
    code, out, _ = run("build", tmp_path / "c.md", "--dry-run", "--json", capsys=capsys)
    assert code == 3 and json.loads(out)["missing_media"][0]["file"] == "gone.png"


def test_output_permissions_and_no_temp_leftovers(cells, tmp_path, capsys, monkeypatch):
    import os
    import tempfile
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    assert run("build", cells, "-o", tmp_path / "d.apkg", capsys=capsys)[0] == 0
    assert list(scratch.iterdir()) == [] and tempfile.tempdir == str(scratch)
    umask = os.umask(0)
    os.umask(umask)
    if os.name != "nt":
        assert (tmp_path / "d.apkg").stat().st_mode & 0o777 == 0o666 & ~umask


def test_inspect_refuses_newer_and_broken_packages(tmp_path, capsys):
    import zipfile
    with zipfile.ZipFile(tmp_path / "new.apkg", "w") as z:
        z.writestr("collection.anki21b", b"zstd")
        z.writestr("collection.anki2", b"placeholder")
        z.writestr("media", b"\x0a\x03protobuf")
    code, _, err = run("inspect", tmp_path / "new.apkg", capsys=capsys)
    assert code == 2 and "newer Anki exports" in err
    with zipfile.ZipFile(tmp_path / "bad.apkg", "w") as z:
        z.writestr("collection.anki2", b"not sqlite at all")
    code, _, err = run("inspect", tmp_path / "bad.apkg", capsys=capsys)
    assert code == 2 and "cannot be read" in err


@needs_anki
def test_anki_cloze_answer_fix_updates(tmp_path, capsys):
    col = Collection(str(tmp_path / "c.anki2"))
    try:
        (tmp_path / "c.md").write_text("The {{c1::hart}} pumps blood.\n", encoding="utf-8")
        run("build", tmp_path / "c.md", "-o", tmp_path / "v1.apkg", capsys=capsys)
        import_into(col, tmp_path / "v1.apkg")
        time.sleep(1.1)
        (tmp_path / "c.md").write_text("The {{c1::heart}} pumps blood.\n", encoding="utf-8")
        run("build", tmp_path / "c.md", "-o", tmp_path / "v2.apkg", capsys=capsys)
        log = import_into(col, tmp_path / "v2.apkg")
        assert col.note_count() == 1 and len(log.updated) == 1
        assert "heart" in col.get_note(col.find_notes("")[0])["Text"]
    finally:
        col.close()
