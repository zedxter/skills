"""Tests for skills/pdf-typeset/scripts/pdf.py.

Run: uv run --no-project --with pytest --with typst --with markdown-it-py --with mdit-py-plugins --with pyyaml \
         --with pypdf --with pypdfium2 --with pillow python -I -m pytest test/test_pdf_typeset.py -q \
         --doctest-modules skills/pdf-typeset/scripts/pdf.py

Everything is offline. test/data/pdf/ has one fixture per template and defects/ one input per check, each with a
planted problem the check must find.
"""

import io
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pypdf
import pypdfium2 as pdfium
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "pdf-typeset" / "scripts" / "pdf.py"
DATA = ROOT / "test" / "data" / "pdf"
DEFECTS = DATA / "defects"
sys.path.insert(0, str(SCRIPT.parent))
import pdf  # noqa: E402


def run(*argv: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    p = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True, encoding="utf-8",
                       cwd=cwd, env={**os.environ, **(env or {})}, timeout=180)
    return p.returncode, p.stdout, p.stderr


def render(src: Path, out: Path, *extra: str) -> tuple[int, dict, str]:
    code, stdout, stderr = run("render", str(src), "-o", str(out), "-f", "json", "-q", *extra)
    return code, (json.loads(stdout) if stdout.strip() else {}), stderr


def text_of(path: Path) -> str:
    doc = pdfium.PdfDocument(str(path))
    return "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc))).replace("\u00a0", " ")


# ------------------------------------------------------------------ every template, clean input

@pytest.mark.parametrize("name, pages", [
    ("report.md", 3), ("document.md", 1), ("letter.md", 1), ("onepager.md", 1), ("slides.md", 7),
    ("invoice.json", 1), ("cv.json", 1),
])
def test_every_template_renders_without_findings(tmp_path, name, pages):
    code, report, err = render(DATA / name, tmp_path / "out.pdf", "--png", str(tmp_path / "png"))
    assert code == 0, err
    assert report["findings"] == [] and report["ok"] and report["pages"] == pages
    pngs = report["png"]
    assert len([p for p in pngs if p.endswith(".png") and "page-" in p]) == pages
    assert all(Path(p).stat().st_size > 1000 for p in pngs)
    assert ("contact-sheet.png" in " ".join(pngs)) == (pages > 1)


def test_report_metadata_tagging_outline_and_embedded_fonts(tmp_path):
    out = tmp_path / "r.pdf"
    assert render(DATA / "report.md", out)[0] == 0
    r = pypdf.PdfReader(str(out))
    root = r.trailer["/Root"]
    assert r.metadata["/Title"] == "Квартальный отчёт команды платформы"
    assert r.metadata["/Author"] == "Команда платформы"
    assert root["/Lang"] == "ru" and "/StructTreeRoot" in root and "/MarkInfo" in root and "/Outlines" in root
    fonts, covered = pdf.font_report(r)
    assert fonts and all(f["embedded"] for f in fonts)
    assert {"К", "П", "€", "«", "»", "—"} <= covered


def test_cyrillic_is_hyphenated(tmp_path):
    src = tmp_path / "ru.md"
    src.write_text("---\ntitle: Т\nauthor: А\nlang: ru\n---\n\n" + "Высокопроизводительная распределённая "
                   "инфраструктура обрабатывает многочисленные транзакционные взаимодействия. " * 12, encoding="utf-8")
    out = tmp_path / "ru.pdf"
    assert render(src, out)[0] == 0
    assert "\ufffe" in text_of(out)  # pdfium marks a hyphenation break with U+FFFE
    code, stdout, _ = run("check", str(out), "-f", "json")  # hyphenation hyphens are not missing glyphs
    assert code == 0 and not [f for f in json.loads(stdout)["findings"] if f["rule"] == "missing-glyph"]


def test_reproducible_with_source_date_epoch(tmp_path):
    env = {"SOURCE_DATE_EPOCH": "1790000000"}
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    for out in (a, b):
        assert run("render", str(DATA / "document.md"), "-o", str(out), "-q", env=env)[0] == 0
    assert a.read_bytes() == b.read_bytes()


# ------------------------------------------------------------------ every check, planted defect

# Text pushed past the margin by a too-wide table also lands on its neighbours: the same defect, two symptoms.
ALSO = {"wide-table.md": {"overlap"}}


@pytest.mark.parametrize("name, extra, rule, severity, exit_code", [
    ("cjk.md", ["--no-system-fonts"], "missing-glyph", "error", 1),
    ("wide-table.md", [], "overflow", "error", 1),
    ("long-code.md", [], "overflow", "error", 1),
    ("slide-overflow.md", [], "slide-overflow", "error", 1),
    ("onepager-too-long.md", [], "too-long", "error", 1),
    ("blank-page.typ", [], "blank-page", "error", 1),
    ("runt-page.typ", ["-t", "document"], "runt-page", "warning", 0),
    ("overlap.typ", [], "overlap", "error", 1),
    ("unknown-font.typ", [], "unknown-font", "error", 1),
    ("low-res.typ", [], "low-res-image", "warning", 0),
])
def test_each_check_finds_its_planted_defect(tmp_path, name, extra, rule, severity, exit_code):
    code, report, err = render(DEFECTS / name, tmp_path / "out.pdf", *extra)
    assert code == exit_code, err
    hits = [f for f in report["findings"] if f["rule"] == rule]
    assert hits and all(f["severity"] == severity for f in hits), report["findings"]
    assert {f["rule"] for f in report["findings"]} <= {rule} | ALSO.get(name, set()), "something else was flagged"


def test_missing_glyph_names_the_characters(tmp_path):
    _, report, _ = render(DEFECTS / "cjk.md", tmp_path / "o.pdf", "--no-system-fonts")
    msg = report["findings"][0]["message"]
    assert all(c in msg for c in "東京서울") and "U+" in msg


def test_slide_overflow_names_the_spilled_pages(tmp_path):
    _, report, _ = render(DEFECTS / "slide-overflow.md", tmp_path / "o.pdf")
    assert {f["page"] for f in report["findings"]} == set(range(3, report["pages"] + 1))


def test_check_command_on_a_pdf_with_an_unembedded_font(tmp_path):
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    stream = b"BT /F1 24 Tf 72 720 Td (Hello) Tj ET"
    objs.append(b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream")
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    p = tmp_path / "helv.pdf"
    p.write_bytes(out.getvalue())
    code, stdout, _ = run("check", str(p), "-f", "json")
    report = json.loads(stdout)
    rules = {f["rule"] for f in report["findings"]}
    assert code == 1 and "font-not-embedded" in rules and {"no-title", "no-author", "no-lang", "untagged"} <= rules


def test_check_with_source_finds_vanished_characters(tmp_path):
    out = tmp_path / "o.pdf"
    render(DEFECTS / "cjk.md", out, "--no-system-fonts")
    src = tmp_path / "src.md"
    src.write_text("Ω and 東京", encoding="utf-8")
    code, stdout, _ = run("check", str(out), "--source", str(src), "-f", "json")
    assert code == 1 and "東" in json.loads(stdout)["findings"][0]["message"]


# ------------------------------------------------------------------ Markdown → Typst

INJECTION = ['#import "x.typ": *', '#read("/etc/passwd")', "$x^2$", "// not a comment", "/* nor this */", "@ref",
             "= not a heading", "a ~ b", "back\\slash", "#set page(width: 1cm)", "#{ panic() }",
             "under_score", "3 * 4 * 5", "`tick", "[brackets]", "{braces}", "50% & co"]


def test_markdown_text_is_never_typst_code(tmp_path):
    src = tmp_path / "inj.md"
    src.write_text("---\ntitle: Injection\nauthor: T\n---\n\n" + "\n\n".join(INJECTION) + "\n", encoding="utf-8")
    out = tmp_path / "inj.pdf"
    code, report, err = render(src, out)
    assert code == 0, err
    text = " ".join(text_of(out).replace("“", '"').replace("”", '"').split())
    for s in INJECTION:
        expect = " ".join(s.replace("\\slash", "slash").split()) if s.startswith("back") else s
        assert expect.replace("\\", "") in text.replace("\\", ""), s
    assert pdfium.PdfDocument(str(out))[0].get_size()[0] > 500  # the page width was not changed


def test_every_ascii_character_survives(tmp_path):
    chars = "".join(chr(c) for c in range(33, 127) if chr(c) not in "\"'")  # quotes become typographic quotes
    src = tmp_path / "ascii.md"
    src.write_text(f"---\ntitle: ASCII\nauthor: T\n---\n\n`{chars.replace('`', '')}`\n\n{chars}\n", encoding="utf-8")
    out = tmp_path / "ascii.pdf"
    assert render(src, out)[0] == 0
    flat = text_of(out).replace("\r", "").replace("\n", "")
    assert chars.replace("`", "") in flat  # the code span, verbatim
    for c in chars:
        if c not in "\\`*_<>[]":  # Markdown's own syntax characters are consumed by Markdown first
            assert c in flat


def emit(md: str, src_dir: Path, build_dir: Path | None = None) -> str:
    build = build_dir or src_dir
    build.mkdir(parents=True, exist_ok=True)
    _, body, _ = pdf.parse_markdown(md, src_dir, build, lambda m: None)
    return body


def test_emitter_structures(tmp_path):
    body = emit("# T\n\nText[^1]\n\n[^1]: Note.\n\n- a\n- b\n\n3. x\n4. y\n\n- [x] done\n- [ ] todo\n\n"
                "| n | v |\n|---|---|\n| a | 1,5 |\n\n> q\n\n---\n\nline<br>break <span>x</span>", tmp_path)
    assert "#footnote[Note.]" in body
    assert "#list(tight: true, [a], [b])" in body and "#enum(tight: true, start: 3, [x], [y])" in body
    assert "#checkbox(true)" in body and "#checkbox(false)" in body
    assert "table.header([n], [v])" in body and "align: (left, right,)" in body
    assert "#quote(block: true)[q]" in body and "#hr()" in body and "#linebreak()" in body and "span" not in body
    assert "#heading" not in body  # the leading "# T" became the title


def test_raw_html_is_left_out_with_a_note(tmp_path):
    notes = []
    _, body, _ = pdf.parse_markdown("a <label>b</label> c\n\n<div>block</div>\n", tmp_path, tmp_path, notes.append)
    assert "label" not in body and "div" not in body and any("raw HTML" in n for n in notes)


def test_title_from_first_heading_and_front_matter(tmp_path):
    meta, body, _ = pdf.parse_markdown("# Real title\n\nText", tmp_path, tmp_path, lambda m: None)
    assert meta["title"] == "Real title" and "Real title" not in body
    meta, body, _ = pdf.parse_markdown("---\ntitle: FM\n---\n# Section\n", tmp_path, tmp_path, lambda m: None)
    assert meta["title"] == "FM" and "#heading(level: 1)[Section]" in body


def test_images_are_copied_sized_and_checked(tmp_path):
    from PIL import Image
    Image.new("RGB", (1500, 600), (200, 30, 30)).save(tmp_path / "wide.png")
    Image.new("RGB", (300, 200), (30, 30, 200)).save(tmp_path / "small.png")
    src = tmp_path / "img.md"
    src.write_text("---\ntitle: Images\nauthor: T\n---\n\n![Wide chart](wide.png \"Revenue by month\")\n\n"
                   "![small](small.png)\n\n![remote](https://example.com/x.png)\n", encoding="utf-8")
    code, stdout, err = run("render", str(src), "-o", str(tmp_path / "img.pdf"), "-f", "json")
    assert code == 0 and "is a URL" in err and json.loads(stdout)["findings"] == []
    body = emit(src.read_text(), tmp_path, tmp_path / "build")
    assert sorted(p.name for p in (tmp_path / "build" / "assets").iterdir()) == ["img1.png", "img2.png"]
    assert "calc.min(720.0pt, s.width)" in body and "calc.min(144.0pt, s.width)" in body
    assert "caption: [Revenue by month]" in body
    assert "#emph[remote]" in body
    (tmp_path / "bad.md").write_text("![x](nope.png)\n", encoding="utf-8")
    code, _, err = render(tmp_path / "bad.md", tmp_path / "bad.pdf")
    assert code == 5 and "image not found" in err


# ------------------------------------------------------------------ JSON templates

def invoice(**over):
    d = json.loads((DATA / "invoice.json").read_text(encoding="utf-8"))
    d.update(over)
    return d


def test_invoice_totals_are_exact_and_rounded_half_up():
    d = invoice(lines=[{"description": "a", "qty": 3, "unit_price": 0.335},           # 1.005 → 1.01
                       {"description": "b", "qty": 1, "unit_price": 10, "vat_rate": 7},
                       {"description": "c", "qty": 2.5, "unit_price": 19.99}], vat_rate=19)   # 49.975 → 49.98
    out = pdf.invoice_data(d, "en")
    n = out["_numbers"]
    assert Decimal(n["subtotal"]) == Decimal("60.99")
    assert Decimal(n["tax"]) == Decimal("0.70") + Decimal("9.69")   # 7 % of 10; 19 % of 50.99 = 9.6881
    assert Decimal(n["total"]) == Decimal("71.38")
    assert [t[0] for t in out["totals"]] == ["Subtotal", "VAT 7 %", "VAT 19 %"]
    assert out["total"] == "€71.38" and out["rows"][0]["amount"] == "€1.01"


def test_invoice_locales_and_zero_decimal_currency():
    assert pdf.invoice_data(invoice(), "de")["total"] == "8.374,63 €"
    assert pdf.invoice_data(invoice(), "ru")["total"] == "8 374,63 €"
    assert pdf.invoice_data(invoice(currency="JPY", lines=[{"description": "x", "unit_price": 1234.5}], vat_rate=0),
                            "en")["total"] == "¥1,235"


@pytest.mark.parametrize("bad, needle", [
    ({"seller": None}, "seller"), ({"currency": "euro"}, "ISO 4217"), ({"date": "30.10.2026"}, "not a date"),
    ({"lines": []}, "non-empty"), ({"lines": [{"description": "x", "unit_price": "abc"}]}, "not a number"),
    ({"lines": [{"description": "x", "unit_price": True}]}, "expected a number"),
    ({"lines": [{"unit_price": 1}]}, "description"), ({"vat_rate": float("nan")}, "finite"),
])
def test_invoice_validation(bad, needle):
    with pytest.raises(pdf.Failure) as e:
        pdf.invoice_data(invoice(**bad), "en")
    assert e.value.code == 2 and needle in str(e.value)


def test_cv_sections_and_dates():
    d = pdf.cv_data(json.loads((DATA / "cv.json").read_text(encoding="utf-8")), "en")
    titles = [s["title"] for s in d["sections"]]
    assert titles == ["Experience", "Education", "Skills", "Languages", "Certificates"]
    first = d["sections"][0]["items"][0]
    assert first["title"] == "Staff Engineer" and first["subtitle"] == "Acme Payments" and first["dates"] == "Mar 2021 – present"
    assert d["sections"][1]["items"][0]["dates"] == "2012 – 2016"
    assert ("jane@example.com", "mailto:jane@example.com") in d["contacts"]


def test_cv_keeps_dates_it_cannot_parse():
    d = pdf.cv_data({"basics": {"name": "X"}, "work": [{"name": "A", "startDate": "Mar 2021", "endDate": "2023-11"}]}, "ru")
    assert d["sections"][0]["items"][0]["dates"] == "Mar 2021 – нояб. 2023"  # the bug a judge found: "Mar 202"


def test_highlights_cards(tmp_path):
    src = tmp_path / "h.md"
    src.write_text("---\ntitle: K\nauthor: T\ntemplate: onepager\nhighlights:\n  - value: 41 min\n    label: Runtime\n"
                   "    note: was 9 h\n---\n\nBody.\n", encoding="utf-8")
    out = tmp_path / "h.pdf"
    code, report, err = render(src, out)
    assert code == 0, err
    assert all(s in text_of(out) for s in ("41 min", "Runtime", "was 9 h"))
    src.write_text("---\nhighlights: [41]\n---\nx\n", encoding="utf-8")
    assert render(src, out)[0] == 2


def test_json_template_needs_a_known_shape(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"something": 1}', encoding="utf-8")
    code, _, err = render(p, tmp_path / "x.pdf")
    assert code == 2 and "JSON template" in err


# ------------------------------------------------------------------ CLI

def test_schema_templates_and_fonts_commands():
    code, out, _ = run("schema", "invoice")
    assert code == 0 and json.loads(out)["required"] == ["number", "date", "currency", "seller", "buyer", "lines"]
    code, out, _ = run("templates")
    assert code == 0 and all(t in out for t in ("report", "letter", "slides", "invoice", "cv", "modern"))
    code, out, _ = run("fonts", "--no-system-fonts")
    assert code == 0 and "Source Sans 3" in out and "Libertinus Serif" in out


def test_text_report_tells_the_agent_to_look(tmp_path):
    code, out, _ = run("render", str(DATA / "document.md"), "-o", str(tmp_path / "d.pdf"), "--png", str(tmp_path / "p"))
    assert code == 0 and "checks: all passed" in out and "look at the PNG pages" in out


@pytest.mark.parametrize("argv, needle", [
    (["render", "missing.md", "-o", "x.pdf"], "input not found"),
    (["render", str(DATA / "document.md"), "-o", "x.png"], "should end in .pdf"),
    (["render", str(DATA / "document.md"), "-o", "x.pdf", "--theme", "neon"], "unknown theme"),
    (["render", str(DATA / "document.md"), "-o", "x.pdf", "--lang", "english"], "bad lang"),
    (["render", str(DATA / "document.md"), "-o", "x.pdf", "-t", "invoice"], "unknown template"),
    (["render", str(DATA / "document.md"), "-o", "x.pdf", "--dpi", "5"], "--dpi"),
    (["render", str(DATA / "document.md"), "-o", "x.pdf", "--font-dir", "/no/such/dir"], "font directory"),
])
def test_bad_input_exit_codes(tmp_path, argv, needle):
    code, _, err = run(*argv, cwd=tmp_path)
    assert code in (2, 5) and needle in err and err.startswith("error: ")


def test_bad_front_matter_and_compile_error(tmp_path):
    p = tmp_path / "fm.md"
    p.write_text("---\ntitle: [unclosed\n---\nx\n", encoding="utf-8")
    code, _, err = render(p, tmp_path / "o.pdf")
    assert code == 2 and "YAML" in err
    code, _, err = render(DEFECTS / "syntax-error.typ", tmp_path / "o.pdf")
    assert code == 3 and "syntax-error.typ:1:13" in err


def test_stdin_input(tmp_path):
    p = subprocess.run([sys.executable, str(SCRIPT), "render", "-", "-o", str(tmp_path / "s.pdf"), "-q"],
                       input="# Hello\n\nFrom stdin.", capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert p.returncode == 0 and "From stdin." in text_of(tmp_path / "s.pdf")


# ------------------------------------------------------------------ hostile input (from the security review)

CONTINUATIONS = ["Edit the `package`.json file.", "Visit **Acme**.io today.", "Call `print`(x) now.",
                 "[docs](https://e.com)(PDF)", "a<br>(b)", "Before `x`.func()(read(\"meta.json\")) after",
                 "x *y*.at(\"body\") z"]


def test_text_after_an_inline_element_never_continues_the_call(tmp_path):
    src = tmp_path / "c.md"
    src.write_text("---\ntitle: C\nauthor: T\n---\n\n" + "\n\n".join(CONTINUATIONS) + "\n", encoding="utf-8")
    out = tmp_path / "c.pdf"
    code, _, err = render(src, out)
    assert code == 0, err
    text = " ".join(text_of(out).replace("“", '"').replace("”", '"').split())
    for needle in ("package.json", "Acme.io", "print(x)", "docs(PDF)", ".func()(read(", ".at(\"body\")"):
        assert needle in text, needle
    assert '"title"' not in text  # meta.json was not read into the page


def test_footnotes_render_once_and_cycles_stop(tmp_path):
    body = emit("A[^a] and again[^a].\n\n[^a]: Note with self[^a] and [^b].\n\n[^b]: Back to [^a].\n", tmp_path)
    assert body.count("#footnote[") == 2 and "#footnote(<pdffn1>);" in body
    chain = "".join(f"[^f{i}]: a[^f{i + 1}] b[^f{i + 1}]\n\n" for i in range(25))
    body = emit("x[^f0]\n\n" + chain, tmp_path)
    assert len(body) < 20_000  # each note printed once, not 2^25 times


def test_yaml_aliases_and_nested_values_are_refused(tmp_path):
    for fm in ("a: &x [1, 2]\ntitle: *x", "title: {nested: map}", "date: 2024-13-45", "toc: true\ntoc-depth: abc"):
        p = tmp_path / "y.md"
        p.write_text(f"---\n{fm}\n---\nx\n", encoding="utf-8")
        code, _, err = render(p, tmp_path / "y.pdf")
        assert code == 2, (fm, err)


def test_images_stay_inside_the_input_folder(tmp_path):
    from PIL import Image
    (tmp_path / "secret").mkdir()
    (tmp_path / "doc").mkdir()
    Image.new("RGB", (40, 40)).save(tmp_path / "secret" / "id.png")
    Image.new("RGB", (40, 40)).save(tmp_path / "doc" / "фото 1.png")
    (tmp_path / "doc" / "link.png").symlink_to(tmp_path / "secret" / "id.png")
    for ref in ("../secret/id.png", str(tmp_path / "secret" / "id.png"), "link.png"):
        (tmp_path / "doc" / "a.md").write_text(f"---\ntitle: I\nauthor: T\n---\n![x]({ref})\n", encoding="utf-8")
        code, _, err = render(tmp_path / "doc" / "a.md", tmp_path / "a.pdf")
        assert code == 2 and "outside the input's folder" in err, ref
    code, _, err = render(tmp_path / "doc" / "a.md", tmp_path / "a.pdf", "--allow-images-from", str(tmp_path / "secret"))
    assert code == 0, err
    (tmp_path / "doc" / "b.md").write_text("---\ntitle: I\nauthor: T\n---\n![x](<фото 1.png>)\n\n![y](фото%201.png)\n",
                                           encoding="utf-8")
    code, _, err = render(tmp_path / "doc" / "b.md", tmp_path / "b.pdf")
    assert code == 0, err


def test_typ_input_cannot_fetch_packages(tmp_path):
    p = tmp_path / "pkg.typ"
    p.write_text('#import "@preview/example:0.1.0": add\n#add(1, 2)\n', encoding="utf-8")
    code, _, err = run("render", str(p), "-o", str(tmp_path / "p.pdf"), env={"XDG_CACHE_HOME": str(tmp_path / "cache")})
    assert code == 3 and "--allow-packages" in err
    assert not (tmp_path / "cache" / "typst").exists()


def test_keep_typ_never_overwrites(tmp_path):
    (tmp_path / "k").mkdir()
    (tmp_path / "k" / "mine.txt").write_text("keep me", encoding="utf-8")
    code, _, err = render(DATA / "document.md", tmp_path / "d.pdf", "--keep-typ", str(tmp_path / "k"))
    assert code == 2 and "new or empty folder" in err
    assert render(DATA / "document.md", tmp_path / "d.pdf", "--keep-typ", str(tmp_path / "fresh"))[0] == 0
    assert (tmp_path / "fresh" / "main.typ").is_file() and (tmp_path / "fresh" / "body.typ").is_file()


def test_tounicode_ranges_are_bounded():
    import time
    cmap = "beginbfrange\n" + "<0000> <FFFF> <0041>\n" * 2000 + "endbfrange\n"
    start = time.monotonic()
    chars = pdf.tounicode_chars(cmap)
    assert time.monotonic() - start < 2 and len(chars) <= 256


def test_damaged_pdf_is_bad_input(tmp_path):
    good = tmp_path / "g.pdf"
    render(DATA / "document.md", good)
    data = good.read_bytes()
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(data[: len(data) // 3])
    code, _, err = run("check", str(bad))
    assert code in (0, 1, 2) and "Traceback" not in err and "a bug" not in err


def test_huge_numbers_and_deep_nesting_are_bad_input(tmp_path):
    with pytest.raises(pdf.Failure) as e:
        pdf.invoice_data(invoice(lines=[{"description": "x", "unit_price": 1e30}]), "en")
    assert e.value.code == 2
    p = tmp_path / "deep.md"
    p.write_text("---\ntitle: D\nauthor: T\n---\n" + "*" * 1000 + "x" + "*" * 1000 + "\n", encoding="utf-8")
    code, _, err = render(p, tmp_path / "d.pdf")
    assert code in (0, 1, 2) and "Traceback" not in err



# ------------------------------------------------------------------ correctness review regressions

def test_slides_keep_their_heading_levels(tmp_path):
    body = emit("---\ntemplate: slides\n---\n# Deck\n\n## Revenue\n\n- a\n\n### Detail\n\n## Costs\n", tmp_path)
    assert "#heading(level: 2)[Revenue]" in body and "#heading(level: 3)[Detail]" in body
    body = emit("# Deck\n\n## Revenue\n", tmp_path)  # a document: the sections move up once the title is lifted
    assert "#heading(level: 1)[Revenue]" in body


def test_title_option_keeps_the_first_heading(tmp_path):
    src = tmp_path / "t.md"
    src.write_text("# Chapter One\n\nText.\n", encoding="utf-8")
    out = tmp_path / "t.pdf"
    assert render(src, out, "--title", "Book", "--author", "A")[0] == 0
    assert "Chapter One" in text_of(out)


@pytest.mark.parametrize("lang", ["ru", "de", "uk"])
def test_json_templates_in_other_languages_are_clean(tmp_path, lang):
    d = json.loads((DATA / "invoice.json").read_text(encoding="utf-8"))
    d["lang"] = lang
    p = tmp_path / "i.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    code, report, err = render(p, tmp_path / "i.pdf")
    assert code == 0 and report["findings"] == [], (err, report.get("findings"))
    c = json.loads((DATA / "cv.json").read_text(encoding="utf-8"))
    c["lang"] = lang
    c["basics"]["profiles"] = [{"network": "Telegram", "username": "ivan", "url": "https://t.me/ivan_petrov?x=1"}]
    p.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    code, report, err = render(p, tmp_path / "c.pdf")
    assert code == 0 and report["findings"] == [], (err, report.get("findings"))


def test_pdf_ua_report_and_missing_alt_text(tmp_path):
    code, report, err = render(DATA / "report.md", tmp_path / "ua.pdf", "--pdf-standard", "ua-1")
    assert code == 0, err
    from PIL import Image
    Image.new("RGB", (60, 60)).save(tmp_path / "x.png")
    (tmp_path / "noalt.md").write_text("---\ntitle: A\nauthor: T\n---\n![](x.png)\n", encoding="utf-8")
    code, _, err = render(tmp_path / "noalt.md", tmp_path / "n.pdf", "--pdf-standard", "ua-1")
    assert code == 2 and "alt text" in err


def test_unit_prices_keep_their_precision():
    out = pdf.invoice_data(invoice(lines=[{"description": "calls", "qty": 1000, "unit_price": 0.0125}], vat_rate=0), "en")
    assert out["rows"][0]["price"] == "€0.0125" and out["rows"][0]["amount"] == "€12.50"
    out = pdf.invoice_data(invoice(currency="KWD", lines=[{"description": "x", "unit_price": 1.234}], vat_rate=0), "en")
    assert out["total"] == "1.234 KWD"
    assert pdf.money(Decimal("0.5"), "EUR", "it") == "€0.50"  # no Italian labels, so English format too


def test_wide_image_fits_a_small_page(tmp_path):
    from PIL import Image
    Image.new("RGB", (700, 300), (10, 120, 60)).save(tmp_path / "w.png")
    (tmp_path / "a5.md").write_text("---\ntitle: A5\nauthor: T\npaper: a5\n---\n![chart](w.png)\n", encoding="utf-8")
    code, report, err = render(tmp_path / "a5.md", tmp_path / "a5.pdf")
    assert code == 0 and report["findings"] == [], err
    page = pdfium.PdfDocument(str(tmp_path / "a5.pdf"))[0]
    w, _ = page.get_size()
    for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]):
        l, b, r, t = obj.get_bounds()
        assert l >= 24 * pdf.MM - 1 and r <= w - 22 * pdf.MM + 1


def test_runt_page_counts_baselines(tmp_path):
    for lines, flagged in ((1, True), (2, True), (5, False)):
        tail = "\n\n".join(f"Line {i} — «quoted» with descenders gjpqy." for i in range(lines))
        p = tmp_path / f"r{lines}.typ"
        p.write_text('#set document(title: "R", author: ("T",))\n#set text(lang: "en")\n'
                     '#set page(paper: "a4", margin: (top: 25mm, bottom: 28mm, left: 35mm, right: 35mm))\n'
                     f'#block(height: 100%, width: 100%)[x]\n\n{tail}\n', encoding="utf-8")
        _, report, _ = render(p, tmp_path / f"r{lines}.pdf", "-t", "document")
        runts = [f for f in report["findings"] if f["rule"] == "runt-page"]
        assert bool(runts) == flagged, (lines, report["findings"])
        if runts:
            assert f"only {lines} line" in runts[0]["message"]


def test_links_and_bare_urls(tmp_path):
    body = emit("[https://a.com](https://b.com) and <https://c.com>", tmp_path)
    assert body.count("#link(") == 2 and '#link("https://b.com")' in body
    body = emit("См. «https://example.ru/doc» и https://en.wikipedia.org/wiki/Foo_(bar). Файл icon@2x.png.", tmp_path)
    assert '#link("https://example.ru/doc")' in body and '#link("https://en.wikipedia.org/wiki/Foo_(bar)")' in body
    assert "mailto:icon" not in body


def test_small_markdown_details(tmp_path):
    assert "start: 0" in emit("0. zero\n1. one\n", tmp_path)
    d = pdf.cv_data({"basics": {"name": "X"}, "skills": [{"name": "Langs", "keywords": "Go, Rust"}]}, "en")
    assert d["sections"][0]["items"][0][1] == "Go, Rust"


def test_typ_root_option(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "sub").mkdir()
    (tmp_path / "shared" / "lib.typ").write_text("#let hello = [Hello from lib]", encoding="utf-8")
    main = tmp_path / "sub" / "main.typ"
    main.write_text('#set document(title: "R", author: ("T",))\n#import "../shared/lib.typ": hello\n#hello\n',
                    encoding="utf-8")
    code, _, err = render(main, tmp_path / "o.pdf")
    assert code == 2 and "--root" in err
    code, _, err = render(main, tmp_path / "o.pdf", "--root", str(tmp_path))
    assert code == 0, err
