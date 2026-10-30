#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "typst>=0.15,<0.16",
#   "markdown-it-py>=3,<5",
#   "mdit-py-plugins>=0.4,<0.7",
#   "pyyaml>=6,<7",
#   "pypdf>=6.1,<7",
#   "pypdfium2>=4.30,<6",
#   "pillow>=11",
# ]
# ///
"""Typeset Markdown or JSON into a professional-looking PDF with Typst, then check the result: missing glyphs,
text running into the margins, fonts, metadata, blank and nearly empty pages, and PNG previews to look at.

    uv run pdf.py render notes.md -o report.pdf --template report --png preview/   # Markdown → PDF + checks
    uv run pdf.py render invoice.json -o invoice.pdf                               # JSON → invoice (or cv)
    uv run pdf.py check some.pdf --png preview/                                    # the same checks on any PDF
    uv run pdf.py templates                                                        # templates and themes
    uv run pdf.py schema invoice                                                   # fields a JSON template takes
    uv run pdf.py fonts --sample "Привет 你好"                                     # fonts and what they cover

Exit codes:
    0 PDF written, checks pass · 1 PDF written but checks found problems · 2 bad input (missing file, invalid
    JSON or front matter, unknown template or theme, missing dependency, unwritable output) · 3 Typst compile
    error · 5 an image, font folder or file the document names was not found · 7 anything else
"""

import argparse
import contextlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import urllib.parse
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

EXIT_OK, EXIT_FINDINGS, EXIT_BAD_INPUT, EXIT_COMPILE, EXIT_NOT_FOUND, EXIT_OTHER = 0, 1, 2, 3, 5, 7

try:
    import pypdf
    import pypdfium2 as pdfium
    import typst
    import yaml
    from markdown_it import MarkdownIt
    from markdown_it.tree import SyntaxTreeNode
    from mdit_py_plugins.footnote import footnote_plugin
    from mdit_py_plugins.front_matter import front_matter_plugin
    from mdit_py_plugins.tasklists import tasklists_plugin
    from PIL import Image
except ImportError as e:
    sys.stderr.write(f"error: missing dependency {e.name}; run with `uv run pdf.py ...`, which installs them\n")
    sys.exit(EXIT_BAD_INPUT)

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
TEMPLATES = SKILL / "templates"
FONTS = SKILL / "fonts"

MARKDOWN_TEMPLATES = {
    "report": "Cover page, contents, numbered headings, running header, page X of Y.",
    "document": "Memo, article or note: title block, no cover, page numbers.",
    "letter": "Business letter: sender, recipient, date, subject, closing, signature (front matter).",
    "onepager": "One page: title band, key-figure cards, body as large as fits; a second page is a finding.",
    "slides": "16:9 slides: every level-1 or level-2 heading starts a slide; a slide that spills is a finding.",
}
JSON_TEMPLATES = {
    "invoice": "Invoice from JSON: seller, buyer, lines, VAT; totals computed exactly in the script.",
    "cv": "CV from JSON Resume (basics, work, education, skills, languages, projects).",
}
THEMES = {
    "classic": "Libertinus Serif, justified and hyphenated, dark-blue accent.",
    "modern": "Source Sans 3, ragged right, teal accent.",
    "minimal": "Libertinus Serif body with Source Sans 3 headings, near-black accent.",
}
LANG_DEFAULT = "en"
MODERN_BY_DEFAULT = {"slides", "onepager"}  # sans-serif reads better on screens and in dense layouts; JSON templates too
META_KEYS = {"title", "subtitle", "author", "org", "date", "lang", "theme", "template", "toc", "toc-depth",
             "numbered", "paper", "keywords", "from", "to", "subject", "opening", "closing", "signature",
             "highlights"}
PAPERS = {"a4", "us-letter", "a5", "us-legal"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
MAX_INLINE_DEPTH = 40
IMAGE_DPI = 150  # natural size of a raster image on the page; anything wider than the text is scaled to fit
MAX_INPUT_BYTES = 20_000_000


class Failure(Exception):
    def __init__(self, code: int, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.hint = code, hint


# ------------------------------------------------------------------ Typst source helpers

TYPST_SPECIAL = set("\\#$*_`<>@[]~/-+=")


def typ_str(s: str) -> str:
    """A Typst string literal.

    >>> typ_str('a"b\\\\c\\n\\x01')
    '"a\\\\"b\\\\\\\\c\\\\n\\\\u{1}"'
    """
    out = []
    for ch in s:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or 0x7F <= ord(ch) < 0xA0:
            out.append(f"\\u{{{ord(ch):x}}}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def typ_text(s: str) -> str:
    """Literal text in Typst markup: every markup character escaped, control characters dropped.

    >>> typ_text("a*b #c 1. $5 // x")
    'a\\\\*b \\\\#c 1\\\\. \\\\$5 \\\\/\\\\/ x'
    """
    out = []
    prev = ""
    for ch in s:
        if ch in TYPST_SPECIAL or (ch == "." and prev.isdigit()):  # "1." starting any content would be a list
            out.append("\\" + ch)
        elif ch in "\u2028\u2029":  # Typst reads these as line breaks
            out.append(" ")
        elif ord(ch) < 0x20 or 0x7F <= ord(ch) < 0xA0:
            continue
        else:
            out.append(ch)
        prev = ch
    return "".join(out)


def typ_value(v: Any) -> str:
    """A Python value as a Typst literal (for meta and data passed as code, not markup)."""
    if v is None:
        return "none"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return repr(v) if math.isfinite(v) else "none"
    if isinstance(v, str):
        return typ_str(v)
    if isinstance(v, (list, tuple)):
        return "(" + "".join(typ_value(x) + ", " for x in v) + ")"
    if isinstance(v, dict):
        if not v:
            return "(:)"
        return "(" + ", ".join(f"{typ_str(str(k))}: {typ_value(x)}" for k, x in v.items()) + ")"
    return typ_str(str(v))


# ------------------------------------------------------------------ typography

NBSP = "\u00a0"
SHORT_WORD_LANGS = {"ru", "uk", "be", "bg", "cs", "sk", "pl", "sl", "sr", "hr"}
UNIT_AFTER_NUMBER = re.compile(r"(?<=\d) (?=(?:[%‰°€$£¥₽₴]|[A-Za-zА-Яа-яЁёІіЇїЄєҐґ]{1,4})(?![\wА-Яа-яЁё]))")
DIGIT_GROUP = re.compile(r"(?<=\d) (?=\d{3}(?!\d))")
SHORT_WORD = re.compile(r"(?:(?<=^)|(?<=[\s(«„\"]))([A-Za-zА-Яа-яЁёІіЇїЄєҐґ]{1,2}) (?=\S)")


def typo(s: str, lang: str) -> str:
    """Non-breaking spaces where a line break would look wrong: inside "41 200", between a number and its unit,
    and (in Slavic languages) after one- and two-letter words such as в, к, на, по.

    >>> typo("41 200 € за 3 ч в месяц", "ru") == "41\u00a0200\u00a0€ за\u00a03\u00a0ч\u00a0в\u00a0месяц"
    True
    >>> typo("14.2 GB in a day", "en") == "14.2\u00a0GB in a day"
    True
    """
    if not s or "://" in s:
        return s
    s = DIGIT_GROUP.sub(NBSP, s)
    s = UNIT_AFTER_NUMBER.sub(NBSP, s)
    if lang in SHORT_WORD_LANGS:
        s = SHORT_WORD.sub(lambda m: m.group(1) + NBSP, s)
    return s


def typo_all(v: Any, lang: str) -> Any:
    """typo() on every string in nested data (prepared JSON-template data, front matter)."""
    if isinstance(v, str):
        return typo(v, lang)
    if isinstance(v, dict):
        return {k: typo_all(x, lang) for k, x in v.items()}
    if isinstance(v, list):
        return [typo_all(x, lang) for x in v]
    if isinstance(v, tuple):
        return tuple(typo_all(x, lang) for x in v)
    return v


# ------------------------------------------------------------------ Markdown → Typst

URL_IN_TEXT = re.compile(
    r"https?://[^\s<>\"'`«»“”‘’„]+[^\s<>\"'`«»“”‘’„.,;:!?)\]]"  # a URL, minus closing punctuation and quotes
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?!(?:png|jpe?g|gif|svg|webp|pdf|txt)\b)[A-Za-z]{2,}")
NUMERIC_CELL = re.compile(r"^[-+−–]?[\s$€£¥₽%]*[0-9][0-9\s.,  ']*(?:[%‰]|[a-zA-Z€$£¥₽]{0,4})?\s*$")


def make_markdown() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    return md.use(front_matter_plugin).use(footnote_plugin).use(tasklists_plugin)


class Emitter:
    """Walks markdown-it's syntax tree and writes Typst markup. All text goes through typ_text or typ_str, so
    nothing in the Markdown can run Typst code; images are copied into the build folder under fixed names."""

    def __init__(self, src_dir: Path, build_dir: Path, note, image_dirs: list[Path] | None = None,
                 lang: str = LANG_DEFAULT) -> None:
        self.src_dir, self.build_dir, self.note, self.lang = src_dir, build_dir, note, lang
        self.image_dirs = [d.resolve() for d in image_dirs or []]
        self.image_names: dict[Path, tuple[str, str]] = {}
        self.footnote_ids: dict[str, int] = {}
        self.footnote_busy: set[str] = set()
        self.depth = 0
        self.in_link = False
        self.footnotes: dict[str, SyntaxTreeNode] = {}
        self.images: list[str] = []
        self.dropped_html = False
        self.headings: list[tuple[int, str]] = []
        self.plain: list[str] = []  # every text the reader will see, for the glyph check
        self.shift = 0  # heading levels to move up (when the only level-1 heading became the title)

    # -------- inline
    def inline(self, node: SyntaxTreeNode) -> str:
        self.depth += 1
        try:
            if self.depth > MAX_INLINE_DEPTH:  # absurd nesting: keep the words, drop the styling
                return self.text("".join(c.content for c in node.walk() if c.type in ("text", "code_inline")))
            return "".join(self.inline_node(c) for c in node.children)
        finally:
            self.depth -= 1

    def text(self, s: str) -> str:
        self.plain.append(s)
        s = typo(s, self.lang)
        if self.in_link:
            return typ_text(s)
        out, pos = [], 0
        for m in URL_IN_TEXT.finditer(s):
            url, end = m.group(), m.end()
            if url.startswith("http") and url.count("(") > url.count(")") and s[end:end + 1] == ")":
                url, end = url + ")", end + 1  # a Wikipedia-style "Foo_(bar)" keeps its closing parenthesis
            out.append(typ_text(s[pos:m.start()]))
            target = url if url.startswith("http") else "mailto:" + url
            out.append(f"#link({typ_str(target)})[{typ_text(url)}];")
            pos = end
        out.append(typ_text(s[pos:]))
        return "".join(out)

    def inline_node(self, n: SyntaxTreeNode) -> str:
        # Every embedded call ends with ";": without it, text that follows such as ".json" or "(x)" would continue
        # the expression (a field access or a call) instead of being printed.
        t = n.type
        if t == "text":
            return self.text(n.content)
        if t == "softbreak":
            return " "
        if t == "hardbreak":
            return "#linebreak();"
        if t == "strong":
            return f"#strong[{self.inline(n)}];"
        if t == "em":
            return f"#emph[{self.inline(n)}];"
        if t == "s":
            return f"#strike[{self.inline(n)}];"
        if t == "code_inline":
            self.plain.append(n.content)
            return f"#raw({typ_str(n.content)});"
        if t == "link":
            href = str(n.attrs.get("href", ""))
            self.in_link, was = True, self.in_link
            try:
                inner = self.inline(n)
            finally:
                self.in_link = was
            if re.match(r"(?i)(https?|mailto):", href):
                return f"#link({typ_str(href)})[{inner}];"
            return inner  # anchors and relative links have no target inside a PDF
        if t == "image":
            return self.image(n, inline=True)
        if t == "html_inline":
            html = n.content.strip().lower()
            if re.fullmatch(r"<br\s*/?>", html):
                return "#linebreak();"
            if "task-list-item-checkbox" in html:
                return f"#checkbox({'true' if 'checked' in html else 'false'}); "
            self.dropped_html = True
            return ""
        if t == "footnote_ref":
            return self.footnote_ref(str(n.meta.get("label", n.meta.get("id", ""))))
        return self.inline(n) if n.children else self.text(n.content or "")

    def footnote_ref(self, label: str) -> str:
        """The first reference prints the note with a label; later ones point at it. A note that refers to itself
        (directly or through others) would otherwise expand without end."""
        if label in self.footnote_ids:
            return f"#footnote(<pdffn{self.footnote_ids[label]}>);"
        fn = self.footnotes.get(label)
        if fn is None or label in self.footnote_busy:
            return ""
        self.footnote_busy.add(label)
        k = len(self.footnote_ids) + 1
        self.footnote_ids[label] = k
        body = self.blocks(fn).strip()
        self.footnote_busy.discard(label)
        return f"#footnote[{body}]<pdffn{k}>"

    def image(self, n: SyntaxTreeNode, inline: bool) -> str:
        src = urllib.parse.unquote(str(n.attrs.get("src", "")))  # markdown-it percent-encodes spaces and non-ASCII
        alt = "".join(c.content for c in n.walk() if c.type == "text")
        title = str(n.attrs.get("title") or "")
        is_drive = re.match(r"(?i)[a-z]:[\\/]", src)
        if not is_drive and re.match(r"(?i)[a-z][a-z0-9+.-]*:", src):
            self.note(f"image {src} is a URL; the PDF is built offline, so its alt text is shown instead "
                      "(download it and use a local path)")
            return f"#emph[{self.text(alt or src)}];"
        path = (self.src_dir / src).resolve()
        allowed = [self.src_dir.resolve(), *self.image_dirs]
        if not any(path.is_relative_to(d) for d in allowed):
            raise Failure(EXIT_BAD_INPUT, f"image {src} is outside the input's folder",
                          "images are read only from the Markdown file's folder (and below); pass "
                          "--allow-images-from DIR to allow another folder")
        if not path.is_file():
            raise Failure(EXIT_NOT_FOUND, f"image not found: {src}", f"looked for {path}")
        ext = path.suffix.lower()
        if ext not in IMAGE_TYPES:
            raise Failure(EXIT_BAD_INPUT, f"unsupported image type {ext} ({src})", "use PNG, JPEG, GIF, SVG or WebP")
        if path not in self.image_names:
            name = f"assets/img{len(self.image_names) + 1}{ext}"
            (self.build_dir / "assets").mkdir(exist_ok=True)
            shutil.copyfile(path, self.build_dir / name)
            width = "100%"
            if ext != ".svg":
                try:
                    with Image.open(path) as im:
                        natural_in = im.size[0] / IMAGE_DPI
                except (OSError, ValueError, Image.DecompressionBombError) as e:
                    raise Failure(EXIT_BAD_INPUT, f"cannot read image {src}: {e}") from None
                width = f"{natural_in * 72:.1f}pt"  # capped to the text width when placed
            self.image_names[path] = (name, width)
        name, width = self.image_names[path]
        self.images.append(name)
        if inline:
            return f"#box(height: 1.2em, image({typ_str(name)}, height: 1.2em, alt: {typ_str(alt)}));"
        caption = f", caption: [{self.text(title)}]" if title else ""
        if width == "100%":
            img = f"image({typ_str(name)}, width: 100%, alt: {typ_str(alt)})"
        else:  # natural size at 150 dpi, never wider than the column it lands in
            img = f"layout(s => image({typ_str(name)}, width: calc.min({width}, s.width), alt: {typ_str(alt)}))"
        return f"#figure({img}{caption})"

    # -------- blocks
    def blocks(self, node: SyntaxTreeNode | None) -> str:
        if node is None:
            return ""
        return "".join(self.block(c) for c in node.children)

    def block(self, n: SyntaxTreeNode) -> str:
        t = n.type
        if t in ("front_matter", "footnote_block"):
            return ""
        if t == "heading":
            level = max(1, int(n.tag[1]) - self.shift)
            body = self.inline(n.children[0]) if n.children else ""
            self.headings.append((level, "".join(c.content for c in n.walk() if c.type in ("text", "code_inline"))))
            return f"#heading(level: {level})[{body}]\n\n"
        if t == "paragraph":
            inl = n.children[0] if n.children else None
            if inl is not None and len(inl.children) == 1 and inl.children[0].type == "image":
                return self.image(inl.children[0], inline=False) + "\n\n"
            body = self.inline(inl) if inl is not None else ""
            return body + ("\n" if n.hidden else "\n\n")
        if t in ("bullet_list", "ordered_list"):
            items = []
            for li in n.children:
                content = self.blocks(li).strip()
                items.append(f"[{content}]")
            tight = "true" if n.children and all(
                c.hidden for li in n.children for c in li.children if c.type == "paragraph") else "false"
            if t == "bullet_list":
                return f"#list(tight: {tight}, {', '.join(items)})\n\n"
            start = int(n.attrs.get("start", 1) if n.attrs.get("start") is not None else 1)
            return f"#enum(tight: {tight}, start: {start}, {', '.join(items)})\n\n"
        if t in ("fence", "code_block"):
            lang = re.sub(r"[^A-Za-z0-9_+#.-]", "", (n.info or "").split(" ")[0])[:20]
            self.plain.append(n.content)
            lang_arg = f"lang: {typ_str(lang)}, " if lang else ""
            return f"#raw(block: true, {lang_arg}{typ_str(n.content.rstrip(chr(10)))})\n\n"
        if t == "blockquote":
            return f"#quote(block: true)[{self.blocks(n).strip()}]\n\n"
        if t == "hr":
            return "#hr()\n\n"
        if t == "table":
            return self.table(n)
        if t == "html_block":
            self.dropped_html = True
            return ""
        return self.blocks(n)

    def table(self, n: SyntaxTreeNode) -> str:
        rows: list[list[tuple[str, str, str]]] = []  # (typst, plain text, align)
        header_rows = 0
        for section in n.children:
            for tr in section.children:
                cells = []
                for cell in tr.children:
                    style = str(cell.attrs.get("style", ""))
                    align = (re.search(r"text-align:(\w+)", style) or [None, ""])[1]
                    inl = cell.children[0] if cell.children else None
                    plain = "".join(c.content for c in cell.walk() if c.type in ("text", "code_inline"))
                    cells.append((self.inline(inl) if inl is not None else "", plain, align))
                rows.append(cells)
                if section.type == "thead":
                    header_rows += 1
        if not rows:
            return ""
        ncols = max(len(r) for r in rows)
        for r in rows:
            r.extend([("", "", "")] * (ncols - len(r)))
        aligns, longest, numerics = [], [], []
        for c in range(ncols):
            declared = rows[0][c][2]
            body = [r[c][1].strip() for r in rows[header_rows:] if r[c][1].strip()]
            numeric = bool(body) and all(NUMERIC_CELL.match(x) for x in body)
            aligns.append(declared or ("right" if numeric else "left"))
            longest.append(max((len(r[c][1]) for r in rows), default=1))
            numerics.append(numeric)
        # Natural widths keep a small table compact; a table whose longest cells would not fit on one line shares
        # the width out, text columns by length, number columns at their natural size.
        if sum(longest) + 3 * ncols <= 85:
            widths = ["auto"] * ncols
        else:
            widths = ["auto" if numerics[c] else f"{max(1.0, math.sqrt(longest[c])):.1f}fr" for c in range(ncols)]
        cell = lambda x: f"[{x[0]}]"  # noqa: E731
        header = f"table.header({', '.join(cell(x) for x in rows[0])}), " if header_rows else ""
        body_rows = rows[header_rows:] if header_rows else rows
        body = ", ".join(cell(x) for r in body_rows for x in r)
        return (f"#table(columns: ({', '.join(widths)},), align: ({', '.join(aligns)},), "
                f"{header}{body})\n\n")

    def document(self, root: SyntaxTreeNode) -> str:
        for c in root.walk():
            if c.type == "footnote":
                self.footnotes[str(c.meta.get("label", c.meta.get("id", "")))] = c
        return self.blocks(root)


class NoAliasLoader(yaml.SafeLoader):
    """YAML without anchors and aliases: front matter never needs them, and nested aliases expand exponentially."""

    def compose_node(self, parent, index):  # noqa: ANN001 - PyYAML's signature
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("aliases (*name) are not allowed in front matter")
        return super().compose_node(parent, index)


MAX_META_TEXT = 2000


def check_meta_value(key: str, v: Any) -> Any:
    def scalar(x: Any) -> bool:
        return x is None or isinstance(x, (str, int, float, bool, date))
    if key == "highlights":
        if not isinstance(v, list) or not all(isinstance(h, dict) and all(scalar(x) for x in h.values()) for h in v):
            raise Failure(EXIT_BAD_INPUT, "highlights must be a list of {value, label, note}",
                          "e.g. - value: 41 min / label: nightly export / note: was 9 h")
        return v
    if isinstance(v, list):
        if len(v) > 50 or not all(scalar(x) for x in v):
            raise Failure(EXIT_BAD_INPUT, f"{key}: expected text or a short list of text")
    elif not scalar(v):
        raise Failure(EXIT_BAD_INPUT, f"{key}: expected text, a number, true/false or a date")
    if len(str(v)) > MAX_META_TEXT:
        raise Failure(EXIT_BAD_INPUT, f"{key} is longer than {MAX_META_TEXT} characters")
    return v


def parse_markdown(text: str, src_dir: Path, build_dir: Path, note, image_dirs: list[Path] | None = None,
                   lang: str | None = None, template: str | None = None,
                   title_given: bool = False) -> tuple[dict[str, Any], str, Emitter]:
    md = make_markdown()
    tokens = md.parse(text)
    meta: dict[str, Any] = {}
    fm = next((t for t in tokens if t.type == "front_matter"), None)
    if fm is not None:
        try:
            loaded = yaml.load(fm.content, Loader=NoAliasLoader) or {}  # noqa: S506 - a SafeLoader subclass
        except (yaml.YAMLError, ValueError) as e:  # ValueError: a date such as 2024-13-45
            raise Failure(EXIT_BAD_INPUT, f"front matter is not valid YAML: {e}") from None
        if not isinstance(loaded, dict):
            raise Failure(EXIT_BAD_INPUT, "front matter must be key: value pairs")
        meta = {str(k): check_meta_value(str(k), v) for k, v in loaded.items()}
    root = SyntaxTreeNode(tokens)
    em = Emitter(src_dir, build_dir, note, image_dirs, str(lang or meta.get("lang") or LANG_DEFAULT).lower())
    # A Markdown file that starts with "# Title" and has no title in its front matter: that heading is the title.
    first = next((c for c in root.children if c.type not in ("front_matter",)), None)
    lifted = False
    if (not meta.get("title") and not title_given and first is not None and first.type == "heading"
            and first.tag == "h1"):
        meta["title"] = "".join(c.content for c in first.walk() if c.type in ("text", "code_inline"))
        root.children = [c for c in root.children if c is not first]
        lifted = True
    # "# Title" + "## Sections" is the usual shape: once the title is lifted, the sections are the top level.
    # Slides keep their levels: there "##" is a slide and "#" a section divider.
    kind = template or meta.get("template")
    if lifted and kind != "slides" and not any(c.type == "heading" and c.tag == "h1" for c in root.children):
        em.shift = 1
    body = em.document(root)
    if em.dropped_html:
        note("raw HTML in the Markdown was left out (a PDF has no HTML); use Markdown syntax instead")
    return meta, body, em


# ------------------------------------------------------------------ build

def normalize_meta(meta: dict[str, Any], args: argparse.Namespace, note) -> dict[str, Any]:
    """Front matter plus command-line overrides, validated; dates become text."""
    out: dict[str, Any] = {}
    unknown = sorted(k for k in meta if k not in META_KEYS)
    if unknown:
        note(f"front matter keys ignored: {', '.join(unknown)} (known: {', '.join(sorted(META_KEYS))})")
    for k in META_KEYS:
        if k in meta and meta[k] is not None:
            v = meta[k]
            out[k] = v.isoformat() if isinstance(v, (date, datetime)) else v
    for k in ("title", "subtitle", "author", "date", "lang", "theme", "paper", "org"):
        v = getattr(args, k.replace("-", "_"), None)
        if v is not None:
            out[k] = v
    if getattr(args, "toc", None) is not None:
        out["toc"] = args.toc
    for k in ("title", "subtitle", "author", "org", "date", "subject", "opening", "closing", "signature"):
        if k in out:
            if isinstance(out[k], list) and k == "author":
                out[k] = ", ".join(str(x) for x in out[k])
            if not isinstance(out[k], str):
                out[k] = str(out[k])
    for k in ("from", "to"):
        if k in out:
            v = out[k]
            out[k] = [str(x) for x in v] if isinstance(v, list) else [x.strip() for x in str(v).strip().splitlines()]
    lang = str(out.get("lang", LANG_DEFAULT)).lower()
    if not re.fullmatch(r"[a-z]{2,3}", lang):
        raise Failure(EXIT_BAD_INPUT, f"bad lang {lang!r}", "use an ISO 639 code such as en, ru, de")
    out["lang"] = lang
    if isinstance(out.get("date"), str):
        out["date"] = long_date(out["date"], lang)
    for k in ("title", "subtitle", "author", "org", "date", "subject", "opening", "closing", "highlights"):
        if k in out:
            out[k] = typo_all(out[k], lang)
    theme = str(out.get("theme", "classic"))
    if theme not in THEMES:
        raise Failure(EXIT_BAD_INPUT, f"unknown theme {theme!r}", f"choose one of {', '.join(THEMES)}")
    out["theme"] = theme
    paper = str(out.get("paper", "a4"))
    if paper not in PAPERS:
        raise Failure(EXIT_BAD_INPUT, f"unknown paper {paper!r}", f"choose one of {', '.join(sorted(PAPERS))}")
    out["paper"] = paper
    for k in ("toc", "numbered"):
        if k in out and not isinstance(out[k], bool):
            raise Failure(EXIT_BAD_INPUT, f"{k} must be true or false")
    if "toc-depth" in out and (not isinstance(out["toc-depth"], int) or isinstance(out["toc-depth"], bool)
                               or not 1 <= out["toc-depth"] <= 6):
        raise Failure(EXIT_BAD_INPUT, "toc-depth must be a whole number from 1 to 6")
    if "highlights" in out:
        hs = out["highlights"]
        if not isinstance(hs, list) or not all(isinstance(h, dict) and str(h.get("value", "")).strip() for h in hs):
            raise Failure(EXIT_BAD_INPUT, "highlights must be a list of {value, label, note}",
                          "e.g. - value: 41 min / label: nightly export / note: was 9 h")
        out["highlights"] = [{k: str(h.get(k, "") or "") for k in ("value", "label", "note")} for h in hs[:6]]
    if "keywords" in out:
        kw = out["keywords"]
        out["keywords"] = [str(x) for x in kw] if isinstance(kw, list) else [x.strip() for x in str(kw).split(",") if x.strip()]
    return out


def main_typ(template: str, json_template: bool) -> str:
    """The entry file: document metadata as top-level set rules, then the template around the body."""
    lines = [
        '#import "lib.typ": *',
        f'#import "{template}.typ": template',
        '#let meta = json("meta.json")',
        '#set document(title: meta.at("title", default: none), '
        'author: if meta.at("author", default: none) != none { (meta.author,) } else { () }, '
        'keywords: meta.at("keywords", default: ()))',
    ]
    if json_template:
        lines.append('#template(meta, json("data.json"))')
    else:
        lines += ['#show: template.with(meta)', '#include "body.typ"']
    lines.append("#checker-hooks()")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ JSON templates

SCHEMAS: dict[str, dict[str, Any]] = {
    "invoice": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "pdf-typeset invoice",
        "type": "object",
        "required": ["number", "date", "currency", "seller", "buyer", "lines"],
        "properties": {
            "number": {"type": "string"}, "date": {"type": "string", "format": "date"},
            "due": {"type": "string", "format": "date"}, "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
            "title": {"type": "string", "description": "Overrides the word 'Invoice'."},
            "seller": {"$ref": "#/$defs/party"}, "buyer": {"$ref": "#/$defs/party"},
            "lines": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["description", "unit_price"],
                "properties": {"description": {"type": "string"}, "qty": {"type": "number", "default": 1},
                               "unit": {"type": "string"}, "unit_price": {"type": "number"},
                               "vat_rate": {"type": "number", "description": "Percent; overrides the invoice rate."}}}},
            "vat_rate": {"type": "number", "default": 0, "description": "Percent, e.g. 19."},
            "notes": {"type": "string"}, "payment_terms": {"type": "string"},
            "service_date": {"type": "string", "description": "Date or period of the service (e.g. 2026-10-30, or 'October 2026')."},
            "reference": {"type": "string", "description": "Payment reference; defaults to the invoice number."},
            "lang": {"type": "string"}, "theme": {"enum": list(THEMES)}, "paper": {"enum": sorted(PAPERS)},
        },
        "$defs": {"party": {"type": "object", "required": ["name"], "properties": {
            "name": {"type": "string"}, "address": {"type": ["string", "array"]}, "email": {"type": "string"},
            "phone": {"type": "string"}, "website": {"type": "string"}, "vat_id": {"type": "string"},
            "tax_id": {"type": "string"}, "iban": {"type": "string"}, "bic": {"type": "string"},
            "bank": {"type": "string"}}}},
    },
    "cv": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "pdf-typeset cv (a subset of JSON Resume, https://jsonresume.org/schema)",
        "type": "object", "required": ["basics"],
        "properties": {
            "basics": {"type": "object", "required": ["name"], "properties": {
                "name": {"type": "string"}, "label": {"type": "string"}, "email": {"type": "string"},
                "phone": {"type": "string"}, "url": {"type": "string"}, "summary": {"type": "string"},
                "location": {"type": "object", "properties": {"city": {"type": "string"},
                                                              "region": {"type": "string"},
                                                              "countryCode": {"type": "string"}}},
                "profiles": {"type": "array", "items": {"type": "object", "properties": {
                    "network": {"type": "string"}, "username": {"type": "string"}, "url": {"type": "string"}}}}}},
            "work": {"type": "array", "items": {"$ref": "#/$defs/work"}},
            "volunteer": {"type": "array", "items": {"$ref": "#/$defs/work"}},
            "education": {"type": "array", "items": {"type": "object", "properties": {
                "institution": {"type": "string"}, "area": {"type": "string"}, "studyType": {"type": "string"},
                "startDate": {"type": "string"}, "endDate": {"type": "string"}, "score": {"type": "string"},
                "courses": {"type": "array", "items": {"type": "string"}}}}},
            "skills": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "level": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}}}}},
            "languages": {"type": "array", "items": {"type": "object", "properties": {
                "language": {"type": "string"}, "fluency": {"type": "string"}}}},
            "projects": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "description": {"type": "string"}, "url": {"type": "string"},
                "startDate": {"type": "string"}, "endDate": {"type": "string"},
                "highlights": {"type": "array", "items": {"type": "string"}}}}},
            "certificates": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "issuer": {"type": "string"}, "date": {"type": "string"}}}},
            "awards": {"type": "array", "items": {"type": "object", "properties": {
                "title": {"type": "string"}, "awarder": {"type": "string"}, "date": {"type": "string"},
                "summary": {"type": "string"}}}},
            "lang": {"type": "string"}, "theme": {"enum": list(THEMES)}, "paper": {"enum": sorted(PAPERS)},
        },
        "$defs": {"work": {"type": "object", "properties": {
            "name": {"type": "string"}, "organization": {"type": "string"}, "position": {"type": "string"},
            "location": {"type": "string"}, "url": {"type": "string"}, "startDate": {"type": "string"},
            "endDate": {"type": "string"}, "summary": {"type": "string"},
            "highlights": {"type": "array", "items": {"type": "string"}}}}},
    },
}

LABELS = {
    "en": dict(invoice="Invoice", bill_to="Bill to", number="Invoice no.", date="Date", due="Due", description="Description",
               qty="Qty", unit_price="Unit price", amount="Amount", subtotal="Subtotal", vat="VAT", total="Total due",
               payment="Payment", notes="Notes", present="present", work="Experience", volunteer="Volunteering",
               education="Education", skills="Skills", languages="Languages", projects="Projects",
               certificates="Certificates", awards="Awards", vat_id="VAT ID", tax_id="Tax ID", service="Service date", reference="Reference"),
    "de": dict(invoice="Rechnung", bill_to="Rechnungsempfänger", number="Rechnungsnr.", date="Datum", due="Fällig",
               description="Beschreibung", qty="Menge", unit_price="Einzelpreis", amount="Betrag", subtotal="Zwischensumme",
               vat="USt.", total="Gesamtbetrag", payment="Zahlung", notes="Hinweise", present="heute",
               work="Berufserfahrung", volunteer="Ehrenamt", education="Ausbildung", skills="Kenntnisse",
               languages="Sprachen", projects="Projekte", certificates="Zertifikate", awards="Auszeichnungen",
               vat_id="USt-IdNr.", tax_id="Steuernr.", service="Leistungsdatum", reference="Verwendungszweck"),
    "ru": dict(invoice="Счёт", bill_to="Плательщик", number="Номер", date="Дата", due="Оплатить до",
               description="Наименование", qty="Кол-во", unit_price="Цена", amount="Сумма", subtotal="Итого без НДС",
               vat="НДС", total="Итого к оплате", payment="Реквизиты для оплаты", notes="Примечания",
               present="по настоящее время", work="Опыт работы", volunteer="Волонтёрство", education="Образование",
               skills="Навыки", languages="Языки", projects="Проекты", certificates="Сертификаты", awards="Награды",
               vat_id="ИНН", tax_id="КПП", service="Дата оказания услуг", reference="Назначение платежа"),
    "uk": dict(invoice="Рахунок", bill_to="Платник", number="Номер", date="Дата", due="Сплатити до",
               description="Найменування", qty="К-сть", unit_price="Ціна", amount="Сума", subtotal="Разом без ПДВ",
               vat="ПДВ", total="Разом до сплати", payment="Реквізити для оплати", notes="Примітки",
               present="дотепер", work="Досвід роботи", volunteer="Волонтерство", education="Освіта",
               skills="Навички", languages="Мови", projects="Проєкти", certificates="Сертифікати", awards="Нагороди",
               vat_id="ІПН", tax_id="ЄДРПОУ", service="Дата надання послуг", reference="Призначення платежу"),
    "fr": dict(invoice="Facture", bill_to="Facturé à", number="N° de facture", date="Date", due="Échéance",
               description="Description", qty="Qté", unit_price="Prix unitaire", amount="Montant", subtotal="Sous-total",
               vat="TVA", total="Total à payer", payment="Paiement", notes="Notes", present="aujourd'hui",
               work="Expérience", volunteer="Bénévolat", education="Formation", skills="Compétences",
               languages="Langues", projects="Projets", certificates="Certifications", awards="Distinctions",
               vat_id="N° TVA", tax_id="SIRET", service="Date de prestation", reference="Référence"),
    "es": dict(invoice="Factura", bill_to="Facturar a", number="N.º de factura", date="Fecha", due="Vencimiento",
               description="Descripción", qty="Cant.", unit_price="Precio unitario", amount="Importe",
               subtotal="Subtotal", vat="IVA", total="Total a pagar", payment="Pago", notes="Notas", present="actualidad",
               work="Experiencia", volunteer="Voluntariado", education="Formación", skills="Habilidades",
               languages="Idiomas", projects="Proyectos", certificates="Certificados", awards="Premios",
               vat_id="NIF-IVA", tax_id="NIF", service="Fecha del servicio", reference="Referencia"),
}
CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "RUB": "₽", "JPY": "¥", "CHF": "CHF", "UAH": "₴", "PLN": "zł",
                    "CNY": "¥", "INR": "₹"}
CURRENCY_DECIMALS = {"JPY": 0, "KRW": 0, "HUF": 0, "CLP": 0, "ISK": 0, "VND": 0,
                     "KWD": 3, "BHD": 3, "OMR": 3, "JOD": 3, "TND": 3, "IQD": 3, "LYD": 3}


def labels(lang: str) -> dict[str, str]:
    return LABELS.get(lang, LABELS["en"])


MONTHS_LONG = {
    "en": "January February March April May June July August September October November December",
    "de": "Januar Februar März April Mai Juni Juli August September Oktober November Dezember",
    "ru": "января февраля марта апреля мая июня июля августа сентября октября ноября декабря",
    "uk": "січня лютого березня квітня травня червня липня серпня вересня жовтня листопада грудня",
    "fr": "janvier février mars avril mai juin juillet août septembre octobre novembre décembre",
    "es": "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre",
}


def long_date(v: str, lang: str) -> str:
    """A full ISO date in the document's language; anything else exactly as given.

    >>> long_date("2026-10-30", "en"), long_date("2026-10-30", "ru"), long_date("2026-10-30", "de"), long_date("Q3", "en")
    ('30 October 2026', '30 октября 2026', '30. Oktober 2026', 'Q3')
    """
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v.strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        return v
    y, mo, d = int(m.group(1)), MONTHS_LONG.get(lang, MONTHS_LONG["en"]).split()[int(m.group(2)) - 1], int(m.group(3))
    if lang == "de":
        return f"{d}. {mo} {y}"
    if lang == "es":
        return f"{d} de {mo} de {y}"
    return f"{d} {mo} {y}"


def money(amount: Decimal, currency: str, lang: str, places: int | None = None) -> str:
    """Locale-shaped money text.

    >>> money(Decimal("1234.5"), "EUR", "en"), money(Decimal("1234.5"), "EUR", "de"), money(Decimal("-3"), "RUB", "ru")
    ('€1,234.50', '1.234,50\\xa0€', '−3,00\\xa0₽')
    """
    if places is None:
        places = CURRENCY_DECIMALS.get(currency, 2)
    if lang not in LABELS:
        lang = "en"  # labels fall back to English, so the number format does too
    q = amount.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    sign = "−" if q < 0 else ""
    whole, _, frac = f"{abs(q):.{places}f}".partition(".")
    groups = f"{int(whole):,}"
    sym = CURRENCY_SYMBOLS.get(currency, currency)
    if lang == "en":
        num = groups + (f".{frac}" if places else "")
        return f"{sign}{sym}{num}" if len(sym) == 1 else f"{sign}{num} {sym}"
    sep = {"de": ".", "es": "."}.get(lang, " ")
    num = groups.replace(",", sep) + (f",{frac}" if places else "")
    return f"{sign}{num} {sym}"


def number_text(v: Decimal, lang: str) -> str:
    s = f"{v.normalize():f}"
    return s if lang == "en" else s.replace(".", ",")


def dec(v: Any, where: str) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise Failure(EXIT_BAD_INPUT, f"{where}: expected a number, got {type(v).__name__}")
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise Failure(EXIT_BAD_INPUT, f"{where}: {v!r} is not a number") from None
    if not d.is_finite() or abs(d) >= Decimal("1e12"):
        raise Failure(EXIT_BAD_INPUT, f"{where}: {v!r} is not a finite number below a trillion")
    return d


def req_str(obj: dict[str, Any], key: str, where: str) -> str:
    v = obj.get(key)
    if not isinstance(v, str) or not v.strip():
        raise Failure(EXIT_BAD_INPUT, f"{where}.{key} is required and must be text", "see: pdf.py schema <template>")
    return v.strip()


def opt_str(obj: dict[str, Any], key: str) -> str:
    v = obj.get(key)
    return str(v).strip() if v is not None and not isinstance(v, (dict, list)) else ""


def address_lines(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v or "").splitlines() if x.strip()]


def check_date(v: str, where: str) -> str:
    try:
        date.fromisoformat(v)
    except ValueError:
        raise Failure(EXIT_BAD_INPUT, f"{where}: {v!r} is not a date", "use YYYY-MM-DD") from None
    return v


def strings_in(v: Any) -> list[str]:
    """Every string inside nested lists and dicts: the text a JSON template prints."""
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [s for x in v.values() for s in strings_in(x)]
    if isinstance(v, (list, tuple)):
        return [s for x in v for s in strings_in(x)]
    return []


def invoice_data(d: dict[str, Any], lang: str) -> dict[str, Any]:
    """Validate an invoice and compute every amount with Decimal (half-up to the currency's minor unit)."""
    L = labels(lang)
    number = req_str(d, "number", "invoice")
    issued = check_date(req_str(d, "date", "invoice"), "invoice.date")
    due = check_date(opt_str(d, "due"), "invoice.due") if opt_str(d, "due") else ""
    currency = req_str(d, "currency", "invoice").upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise Failure(EXIT_BAD_INPUT, f"invoice.currency {currency!r} is not an ISO 4217 code", "e.g. EUR, USD, RUB")
    parties = {}
    for role in ("seller", "buyer"):
        p = d.get(role)
        if not isinstance(p, dict):
            raise Failure(EXIT_BAD_INPUT, f"invoice.{role} is required and must be an object")
        lines = address_lines(p.get("address"))
        for key, label in (("vat_id", L["vat_id"]), ("tax_id", L["tax_id"]), ("email", ""), ("phone", ""), ("website", "")):
            if opt_str(p, key):
                lines.append(f"{label} {opt_str(p, key)}".strip())
        parties[role] = {"name": req_str(p, "name", f"invoice.{role}"), "lines": lines}
    items = d.get("lines")
    if not isinstance(items, list) or not items:
        raise Failure(EXIT_BAD_INPUT, "invoice.lines must be a non-empty list")
    default_rate = dec(d.get("vat_rate", 0), "invoice.vat_rate")
    places = CURRENCY_DECIMALS.get(currency, 2)
    unit = Decimal(1).scaleb(-places)
    rows, bases = [], {}
    subtotal = Decimal(0)
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            raise Failure(EXIT_BAD_INPUT, f"invoice.lines[{i}] must be an object")
        qty = dec(it.get("qty", 1), f"invoice.lines[{i}].qty")
        price = dec(it.get("unit_price"), f"invoice.lines[{i}].unit_price")
        rate = dec(it.get("vat_rate", default_rate), f"invoice.lines[{i}].vat_rate")
        amount = (qty * price).quantize(unit, rounding=ROUND_HALF_UP)
        subtotal += amount
        bases[rate] = bases.get(rate, Decimal(0)) + amount
        unit_name = opt_str(it, "unit")
        rows.append({"description": req_str(it, "description", f"invoice.lines[{i}]"),
                     "qty": number_text(qty, lang) + (f" {unit_name}" if unit_name else ""),
                     "price": money(price, currency, lang, max(places, min(4, -price.normalize().as_tuple().exponent))),
                     "amount": money(amount, currency, lang)})
    totals = [(L["subtotal"], money(subtotal, currency, lang))]
    tax_total = Decimal(0)
    for rate, base in sorted(bases.items()):
        if rate:
            tax = (base * rate / 100).quantize(unit, rounding=ROUND_HALF_UP)
            tax_total += tax
            totals.append((f"{L['vat']} {number_text(rate, lang)} %", money(tax, currency, lang)))
    total = subtotal + tax_total
    seller = d["seller"]
    pay = [f"{k} {opt_str(seller, key)}" for key, k in (("bank", ""), ("iban", "IBAN"), ("bic", "BIC/SWIFT"))
           if opt_str(seller, key)]
    pay = [p.strip() for p in pay]
    if pay:
        pay.append(f"{L['reference']}: {opt_str(d, 'reference') or number}")
    if opt_str(d, "payment_terms"):
        pay.insert(0, opt_str(d, "payment_terms"))
    sections = []
    if pay:
        sections.append((L["payment"], pay))
    if opt_str(d, "notes"):
        sections.append((L["notes"], address_lines(opt_str(d, "notes"))))
    service = opt_str(d, "service_date") or opt_str(d, "period")
    facts = ([(L["number"], number), (L["date"], long_date(issued, lang))] +
             ([(L["service"], human_date(long_date(service, lang), lang))] if service else []) +
             ([(L["due"], long_date(due, lang))] if due else []))
    footer = [parties["seller"]["name"]] + [x for x in parties["seller"]["lines"][:3]]
    return {"title": opt_str(d, "title") or L["invoice"], "seller": parties["seller"], "buyer": parties["buyer"],
            "facts": facts, "rows": rows, "totals": totals, "total": money(total, currency, lang),
            "sections": sections, "footer": footer,
            "labels": {"bill-to": L["bill_to"], "description": L["description"], "qty": L["qty"],
                       "unit-price": L["unit_price"], "amount": L["amount"], "total": L["total"]},
            "_numbers": {"subtotal": str(subtotal), "tax": str(tax_total), "total": str(total)}}


MONTHS = {
    "en": "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec",
    "de": "Jan. Feb. März Apr. Mai Juni Juli Aug. Sept. Okt. Nov. Dez.",
    "ru": "янв. февр. март апр. май июнь июль авг. сент. окт. нояб. дек.",
    "uk": "січ. лют. берез. квіт. трав. черв. лип. серп. верес. жовт. листоп. груд.",
    "fr": "janv. févr. mars avr. mai juin juil. août sept. oct. nov. déc.",
    "es": "ene. feb. mar. abr. may. jun. jul. ago. sept. oct. nov. dic.",
}


def human_date(v: str, lang: str) -> str:
    """ISO dates as people write them on a CV; anything else exactly as given.

    >>> human_date("2021-03", "en"), human_date("2021-03-15", "ru"), human_date("2016", "en"), human_date("Mar 2021", "en")
    ('Mar 2021', 'март 2021', '2016', 'Mar 2021')
    """
    m = re.fullmatch(r"(\d{4})(?:-(\d{2})(?:-\d{2})?)?", v.strip())
    if not m:
        return v.strip()
    if not m.group(2) or not 1 <= int(m.group(2)) <= 12:
        return m.group(1)
    return f"{MONTHS.get(lang, MONTHS['en']).split()[int(m.group(2)) - 1]} {m.group(1)}"


def cv_dates(e: dict[str, Any], L: dict[str, str], lang: str = "en") -> str:
    start, end = (human_date(opt_str(e, k), lang) for k in ("startDate", "endDate"))
    if start and not end:
        return f"{start} – {L['present']}"
    return " – ".join(x for x in (start, end) if x)


def cv_data(d: dict[str, Any], lang: str) -> dict[str, Any]:
    L = labels(lang)
    b = d.get("basics")
    if not isinstance(b, dict):
        raise Failure(EXIT_BAD_INPUT, "cv.basics is required and must be an object", "see: pdf.py schema cv")
    contacts = []
    for key in ("email", "phone", "url"):
        v = opt_str(b, key)
        if v:
            contacts.append((v, f"mailto:{v}" if key == "email" else v if key == "url" and re.match(r"https?://", v) else ""))
    loc = b.get("location") if isinstance(b.get("location"), dict) else {}
    place = ", ".join(x for x in (opt_str(loc, "city"), opt_str(loc, "region"), opt_str(loc, "countryCode")) if x)
    if place:
        contacts.append((place, ""))
    for p in b.get("profiles") or []:
        if isinstance(p, dict) and (opt_str(p, "url") or opt_str(p, "username")):
            url = opt_str(p, "url")
            text = f"{opt_str(p, 'network')}: {opt_str(p, 'username')}".strip(": ") if opt_str(p, "username") else url
            contacts.append((text, url if re.match(r"https?://", url) else ""))

    def items(key: str) -> list[dict[str, Any]]:
        v = d.get(key) or []
        if not isinstance(v, list):
            raise Failure(EXIT_BAD_INPUT, f"cv.{key} must be a list")
        return [x for x in v if isinstance(x, dict)]

    def highlights(e: dict[str, Any]) -> list[str]:
        h = e.get("highlights") or e.get("courses") or []
        return [str(x) for x in h] if isinstance(h, list) else []

    sections = []
    for key in ("work", "volunteer"):
        es = [{"title": opt_str(e, "position") or opt_str(e, "name") or opt_str(e, "organization"),
               "subtitle": (opt_str(e, "name") or opt_str(e, "organization")) if opt_str(e, "position") else "",
               "dates": cv_dates(e, L, lang), "place": opt_str(e, "location"), "summary": opt_str(e, "summary"),
               "highlights": highlights(e)} for e in items(key)]
        if es:
            sections.append({"kind": "entries", "title": L[key], "items": es})
    es = [{"title": " ".join(x for x in (opt_str(e, "studyType"), opt_str(e, "area")) if x) or opt_str(e, "institution"),
           "subtitle": opt_str(e, "institution") if (opt_str(e, "studyType") or opt_str(e, "area")) else "",
           "dates": cv_dates(e, L, lang), "place": opt_str(e, "score"), "summary": "", "highlights": highlights(e)}
          for e in items("education")]
    if es:
        sections.append({"kind": "entries", "title": L["education"], "items": es})
    es = [{"title": opt_str(e, "name"), "subtitle": opt_str(e, "url"), "dates": cv_dates(e, L, lang), "place": "",
           "summary": opt_str(e, "description"), "highlights": highlights(e)} for e in items("projects")]
    if es:
        sections.append({"kind": "entries", "title": L["projects"], "items": es})
    def keywords(s: dict[str, Any]) -> str:
        kw = s.get("keywords") or []
        return str(kw) if isinstance(kw, str) else ", ".join(str(k) for k in kw if k)

    pairs = [(opt_str(s, "name"), keywords(s) or opt_str(s, "level"))
             for s in items("skills") if opt_str(s, "name")]
    if pairs:
        sections.append({"kind": "pairs", "title": L["skills"], "items": pairs})
    pairs = [(opt_str(x, "language"), opt_str(x, "fluency")) for x in items("languages") if opt_str(x, "language")]
    if pairs:
        sections.append({"kind": "pairs", "title": L["languages"], "items": pairs})
    pairs = [(opt_str(x, "name"), " · ".join(y for y in (opt_str(x, "issuer"), human_date(opt_str(x, "date"), lang)) if y))
             for x in items("certificates") if opt_str(x, "name")]
    if pairs:
        sections.append({"kind": "pairs", "title": L["certificates"], "items": pairs})
    es = [{"title": opt_str(x, "title"), "subtitle": opt_str(x, "awarder"), "dates": human_date(opt_str(x, "date"), lang),
           "place": "", "summary": opt_str(x, "summary"), "highlights": []} for x in items("awards") if opt_str(x, "title")]
    if es:
        sections.append({"kind": "entries", "title": L["awards"], "items": es})
    return {"name": req_str(b, "name", "cv.basics"), "label": opt_str(b, "label"), "contacts": contacts,
            "summary": opt_str(b, "summary"), "sections": sections}


# ------------------------------------------------------------------ checks

MM = 72 / 25.4
# Horizontal text area per template: (left, right) margin in mm. Mirrors the page settings in templates/*.typ.
TEMPLATE_MARGINS = {"report": (35, 35), "document": (35, 35), "letter": (25, 20), "onepager": (16, 16),
                    "slides": (18, 18), "invoice": (20, 20), "cv": (18, 18)}
TEMPLATE_BOTTOM = {"report": 28, "document": 28, "letter": 25, "onepager": 16, "slides": 16, "invoice": 22, "cv": 18}
FLOWING = {"report", "document", "letter"}
# Never "missing": invisible marks, and the hyphens Typst draws at hyphenation breaks (text extraction reports
# them, but they carry no ToUnicode entry because they are not in the source text).
IGNORABLE = set("\u00ad\u200b\u200c\u200d\u2060\ufeff\ufffe\uffff\ufffd-\u2010")


def finding(rule: str, severity: str, message: str, page: int | None = None, hint: str = "") -> dict[str, Any]:
    return {"rule": rule, "severity": severity, "page": page, "message": message, "hint": hint}


def tounicode_chars(data: str) -> set[str]:
    """Characters a font's ToUnicode CMap maps glyphs to. A character Typst could not find a glyph for is drawn
    with the font's .notdef glyph, which gets no ToUnicode entry."""
    out: set[str] = set()

    def chars(h: str) -> str:
        try:
            return bytes.fromhex(h).decode("utf-16-be", errors="replace")
        except ValueError:
            return ""

    for blk in re.findall(r"beginbfchar(.*?)endbfchar", data, re.S):
        for _, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", blk):
            out.update(chars(dst))
    for blk in re.findall(r"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)", blk):
            if dst.startswith("["):
                for h in re.findall(r"<([0-9A-Fa-f]*)>", dst):
                    out.update(chars(h))
                continue
            s = chars(dst[1:-1])
            if not s:
                continue
            n = min(int(hi, 16) - int(lo, 16), 0xFF)  # a range may only vary in its last byte (PDF 9.10.3)
            for i in range(n + 1):
                with contextlib.suppress(ValueError):
                    out.update(s[:-1] + chr(ord(s[-1]) + i))
    return out


def font_report(reader: pypdf.PdfReader) -> tuple[list[dict[str, Any]], set[str]]:
    """Every font on every page: name, whether embedded, and the characters its glyphs stand for."""
    fonts: dict[str, dict[str, Any]] = {}
    covered: set[str] = set()
    for page in reader.pages:
        res = page.get("/Resources")
        res = res.get_object() if res is not None else {}
        font_dict = res.get("/Font") if hasattr(res, "get") else None
        if font_dict is None:
            continue
        for _, ref in font_dict.get_object().items():
            f = ref.get_object()
            name = str(f.get("/BaseFont", "?")).lstrip("/")
            key = name
            if key in fonts:
                continue
            subtype = str(f.get("/Subtype", ""))
            desc_holder = f["/DescendantFonts"][0].get_object() if "/DescendantFonts" in f else f
            fd = desc_holder.get("/FontDescriptor")
            fd = fd.get_object() if fd is not None else {}
            embedded = subtype == "/Type3" or any(k in fd for k in ("/FontFile", "/FontFile2", "/FontFile3"))
            fonts[key] = {"name": re.sub(r"^[A-Z]{6}\+", "", name), "embedded": embedded, "subtype": subtype.lstrip("/")}
            if "/ToUnicode" in f and len(fonts) <= 500:
                with contextlib.suppress(Exception):
                    data = f["/ToUnicode"].get_object().get_data()
                    if len(data) <= 2_000_000:
                        covered |= tounicode_chars(data.decode("latin-1"))
    return list(fonts.values()), covered


def page_lines(tp: Any, height: float, skip_top: float, skip_bottom: float) -> int:
    """Lines of body text on a page: distinct baselines (char origins) outside the header and footer bands.
    Glyph-box bottoms would split one line in two wherever a descender or a raised dash sits on it."""
    import ctypes
    x, y = ctypes.c_double(), ctypes.c_double()
    ys = []
    for i in range(tp.count_chars()):
        ch = tp.get_text_range(i, 1)
        if not ch.strip() or not pdfium.raw.FPDFText_GetCharOrigin(tp.raw, i, ctypes.byref(x), ctypes.byref(y)):
            continue
        if y.value < skip_bottom or y.value > height - skip_top:
            continue
        ys.append(y.value)
    lines, last = 0, None
    for v in sorted(ys, reverse=True):
        if last is None or last - v > 5:
            lines += 1
            last = v
    return lines


MAX_CHECK_PAGES = 300
MAX_PNG_PIXELS = 40_000_000


def check_pdf(data: bytes, template: str | None = None, source_text: str | None = None,
              layout: dict[str, Any] | None = None, png_dir: Path | None = None, dpi: int = 110,
              note=lambda m: None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        return _check_pdf(data, template, source_text, layout, png_dir, dpi, note)
    except Failure:
        raise
    except Exception as e:  # noqa: BLE001 - a malformed PDF can break either parser anywhere
        raise Failure(EXIT_BAD_INPUT, f"cannot analyse the PDF ({type(e).__name__}: {e})",
                      "the file is damaged or not a PDF") from None


def _check_pdf(data: bytes, template: str | None, source_text: str | None, layout: dict[str, Any] | None,
               png_dir: Path | None, dpi: int, note) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    import io
    import logging
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    findings: list[dict[str, Any]] = []
    reader = pypdf.PdfReader(io.BytesIO(data))
    doc = pdfium.PdfDocument(data)
    if len(doc) > MAX_CHECK_PAGES:
        note(f"the PDF has {len(doc)} pages; checking the first {MAX_CHECK_PAGES}")
    info: dict[str, Any] = {"pages": len(doc), "png": []}
    root = reader.trailer["/Root"]
    meta = reader.metadata or {}

    # Fonts: embedded, and every visible character backed by a real glyph.
    fonts, covered = font_report(reader)
    info["fonts"] = sorted({f["name"] for f in fonts})
    for f in fonts:
        if not f["embedded"]:
            findings.append(finding("font-not-embedded", "error", f"font {f['name']} is not embedded",
                                    hint="the PDF will look different (or wrong) where that font is missing"))
    npages = min(len(doc), MAX_CHECK_PAGES)
    texts = []
    for i in range(npages):
        tp = doc[i].get_textpage()
        texts.append(tp.get_text_range())
    # Straight quotes in the source come out as typographic ones, so they are not expected in the PDF.
    seen = "".join(texts) + (source_text or "").replace('"', "").replace("'", "")
    missing = sorted({c for c in seen if not c.isspace() and c not in IGNORABLE and ord(c) >= 0x20 and c not in covered})
    if missing and covered:
        sample = "".join(missing[:40])
        cps = ", ".join(f"U+{ord(c):04X}" for c in missing[:8])
        findings.append(finding("missing-glyph", "error", f"{len(missing)} character(s) have no glyph in any font used: "
                                f"{sample} ({cps}{', …' if len(missing) > 8 else ''})",
                                hint="they print as empty boxes; install a font that covers them (e.g. Noto Sans CJK for "
                                     "Chinese/Japanese/Korean, Noto Sans Arabic) or pass --font-dir"))

    # Metadata and structure.
    title = str(meta.get("/Title") or "").strip()
    if not title:
        findings.append(finding("no-title", "warning", "the PDF has no title in its metadata",
                                hint="set title: in the front matter or --title; viewers show it instead of the file name"))
    if not str(meta.get("/Author") or "").strip():
        findings.append(finding("no-author", "warning", "the PDF has no author in its metadata",
                                hint="set author: in the front matter or --author"))
    if "/Lang" not in root:
        findings.append(finding("no-lang", "warning", "the PDF does not declare its language",
                                hint="screen readers and hyphenation need it; set lang:"))
    if "/StructTreeRoot" not in root or "/MarkInfo" not in root:
        findings.append(finding("untagged", "warning", "the PDF is not tagged (no structure tree)",
                                hint="untagged PDFs are hard to read with assistive technology"))
    headings = (layout or {}).get("headings", [])
    if len(doc) >= 3 and len(headings) >= 3 and "/Outlines" not in root:
        findings.append(finding("no-outline", "warning", "a long document with headings has no bookmarks"))

    # Per page: blank pages, text past the margins, glyphs on top of other glyphs, low-resolution images.
    margins = TEMPLATE_MARGINS.get(template or "")
    for i in range(npages):
        page = doc[i]
        w, h = page.get_size()
        tp = page.get_textpage()
        n = tp.count_chars()
        boxes = []
        for j in range(n):
            ch = tp.get_text_range(j, 1)
            if not ch.strip() or ch in IGNORABLE:
                continue
            boxes.append((j, ch, tp.get_charbox(j)))
        images = [o for o in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE])]
        if not boxes and not images and not list(page.get_objects(max_depth=1)):
            findings.append(finding("blank-page", "error", "the page is empty", i + 1,
                                    "remove the forced page break or the empty content that caused it"))
        left_edge, right_edge = (margins[0] * MM, w - margins[1] * MM) if margins else (0, w)
        slack = 1.5 * MM
        out = [ch for _, ch, (l, b, r, t) in boxes if l < left_edge - slack or r > right_edge + slack]
        if out:
            where = "past the page edge" if not margins else "into the margin"
            findings.append(finding("overflow", "error", f"{len(out)} character(s) run {where}: "
                                    f"{''.join(out[:30])!r}", i + 1,
                                    "a word, URL, code line or table is too wide; shorten it, allow it to break, or "
                                    "give the table fewer or narrower columns"))
        # Overlap: boxes of characters that are far apart in reading order but cover each other.
        grid: dict[tuple[int, int], list[tuple[int, tuple[float, float, float, float]]]] = {}
        overlaps = 0
        for j, ch, box in boxes[:6000]:
            l, b, r, t = box
            if r - l < 0.5 or t - b < 0.5:
                continue
            cell = (int((l + r) / 2 // 20), int((b + t) / 2 // 20))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for k, (l2, b2, r2, t2) in grid.get((cell[0] + dx, cell[1] + dy), []):
                        if abs(k - j) <= 3:
                            continue
                        ix = min(r, r2) - max(l, l2)
                        iy = min(t, t2) - max(b, b2)
                        if ix > 0 and iy > 0 and ix * iy > 0.4 * min((r - l) * (t - b), (r2 - l2) * (t2 - b2)):
                            overlaps += 1
            grid.setdefault(cell, []).append((j, box))
        if overlaps >= 3:
            findings.append(finding("overlap", "error", f"text overlaps other text in {overlaps} place(s)", i + 1,
                                    "two elements are placed on top of each other; check the PNG"))
        for img in images:
            with contextlib.suppress(Exception):
                md = img.get_metadata()
                dpi_x = float(md.horizontal_dpi)
                if 0 < dpi_x < 120:
                    findings.append(finding("low-res-image", "warning", f"an image prints at {dpi_x:.0f} dpi", i + 1,
                                            "use a larger image or show it smaller; 150 dpi or more looks sharp"))

    # Layout of flowing documents: a nearly empty last page, a heading stranded at the bottom of a page.
    if template in FLOWING and len(doc) >= 2:
        last = doc[len(doc) - 1]
        w, h = last.get_size()
        bottom = TEMPLATE_BOTTOM.get(template or "", 25)
        lines = page_lines(last.get_textpage(), h, 25 * MM, bottom * MM)
        if 0 < lines < 3:
            findings.append(finding("runt-page", "warning", f"the last page holds only {lines} line(s)", len(doc),
                                    "tighten the text or the spacing so it fits on the previous page"))
    if layout and template in FLOWING | {"onepager"}:
        bottom = TEMPLATE_BOTTOM.get(template or "", 24) * MM
        ph = float(layout.get("page-height", 0) or 0)
        for hd in headings:
            if int(hd["page"]) < len(doc) and ph and float(hd["y"]) > ph - bottom - 52:
                findings.append(finding("stranded-heading", "warning", "a heading sits at the very bottom of the page",
                                        int(hd["page"]), "move it to the next page or shorten the text before it"))
    if template == "onepager" and len(doc) > 1:
        findings.append(finding("too-long", "error", f"a one-pager runs to {len(doc)} pages", 2,
                                "cut the text, or use the document template"))
    if template == "slides" and layout:
        starts = {int(hd["page"]) for hd in headings if int(hd["level"]) <= 2}
        for p in range(2, len(doc) + 1):
            if p not in starts:
                findings.append(finding("slide-overflow", "error", "this page continues the previous slide", p,
                                        "the slide before it has too much content; split it under a new ## heading"))

    if png_dir is not None:
        try:
            info["png"] = write_pngs(doc, png_dir, dpi, note)
        except OSError as e:
            raise Failure(EXIT_BAD_INPUT, f"cannot write PNGs to {png_dir}: {e.strerror or e}") from None
    return info, findings


def write_pngs(doc: Any, out: Path, dpi: int, note=lambda m: None) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    count = min(len(doc), MAX_CHECK_PAGES)
    stale = [p.name for p in out.glob("page-*.png")
             if re.fullmatch(r"page-(\d+)\.png", p.name) and int(p.name[5:-4]) > count]
    if stale:  # never delete files in the user's folder; just say they are not from this render
        note(f"{out} also holds {', '.join(sorted(stale)[:5])}{' …' if len(stale) > 5 else ''} from an earlier "
             "render; they are not part of this PDF")
    paths = []
    thumbs = []
    for i in range(count):
        w, h = doc[i].get_size()
        scale = min(dpi / 72, math.sqrt(MAX_PNG_PIXELS / max(w * h, 1)))
        img = doc[i].render(scale=scale).to_pil()
        p = out / f"page-{i + 1:02d}.png"
        img.save(p)
        paths.append(str(p))
        if len(doc) > 1 and i < 24:
            th = img.copy()
            th.thumbnail((360, 360))
            thumbs.append(th)
    if thumbs:
        cols = min(4, len(thumbs))
        rows = math.ceil(len(thumbs) / cols)
        cw = max(t.width for t in thumbs) + 16
        ch = max(t.height for t in thumbs) + 16
        sheet = Image.new("RGB", (cols * cw + 16, rows * ch + 16), (150, 150, 150))
        for k, th in enumerate(thumbs):
            sheet.paste(th, (16 + (k % cols) * cw, 16 + (k // cols) * ch))
        p = out / "contact-sheet.png"
        sheet.save(p)
        paths.append(str(p))
    return paths


# ------------------------------------------------------------------ render

def font_paths(args: argparse.Namespace) -> list[str]:
    paths = [str(FONTS)]
    for d in getattr(args, "font_dir", None) or []:
        p = Path(d).expanduser()
        if not p.is_dir():
            raise Failure(EXIT_NOT_FOUND, f"font directory not found: {d}")
        paths.append(str(p.resolve()))
    return paths


def compile_typst(main: Path, root: Path, args: argparse.Namespace,
                  raw: bool = False) -> tuple[bytes, list[dict[str, Any]]]:
    kwargs: dict[str, Any] = {"root": str(root), "font_paths": font_paths(args),
                              "ignore_system_fonts": bool(getattr(args, "no_system_fonts", False))}
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "")
    if epoch.isdigit():
        kwargs["timestamp"] = int(epoch)
    if getattr(args, "pdf_standard", None):
        kwargs["pdf_standards"] = args.pdf_standard
    if not getattr(args, "allow_packages", False):
        # Typst downloads `@preview/…` packages on import. Point it at a dead proxy and an empty cache, so a .typ
        # (or anything else) can never fetch and run third-party code. Verified to block the download.
        for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            os.environ[var] = "http://127.0.0.1:9"
        for var in ("NO_PROXY", "no_proxy"):
            os.environ.pop(var, None)
        kwargs["package_cache_path"] = str(root / ".no-packages")
    try:
        pdf_bytes, warns = typst.compile_with_warnings(str(main), **kwargs)
    except typst.TypstError as e:
        msg = str(getattr(e, "message", "") or e).strip()
        hints = [str(h) for h in (getattr(e, "hints", None) or [])]
        if re.search(r"missing alt text", msg, re.I):
            raise Failure(EXIT_BAD_INPUT, f"PDF/UA needs alt text: {msg}",
                          "describe every image: ![what the image shows](file.png)") from None
        if re.search(r"escape the project root", msg, re.I):
            raise Failure(EXIT_BAD_INPUT, f"Typst refused a path: {msg}",
                          "a .typ file may only use files below its root; pass --root DIR") from None
        if re.search(r"failed to download package|package not found", msg, re.I):
            raise Failure(EXIT_COMPILE, f"Typst tried to fetch a package: {msg}",
                          "rendering is offline; pass --allow-packages to let a .typ file you trust import packages") from None
        if re.search(r"file not found|failed to load file|cannot read file", msg, re.I):
            raise Failure(EXIT_NOT_FOUND, f"Typst could not load a file: {msg}", "; ".join(hints)) from None
        loc = re.search(r"┌─ (\S+:\d+:\d+)", str(getattr(e, "diagnostic", "") or ""))
        where = f" at {Path(loc.group(1)).name}" if loc else ""
        raise Failure(EXIT_COMPILE, f"Typst compile error{where}: {msg}", "; ".join(hints) or (
            "fix the .typ source" if raw else
            "with Markdown or JSON input this is a bug in the converter: report it with the input")) from None
    findings = []
    for w in warns:
        msg = str(getattr(w, "message", w)).strip()
        if re.search(r"unknown font family", msg, re.I):
            findings.append(finding("unknown-font", "error", msg, hint="check the name with: pdf.py fonts; "
                                    "or add the font with --font-dir"))
        else:
            findings.append(finding("typst-warning", "warning", msg))
    return pdf_bytes, findings


def query_layout(main: Path, root: Path, args: argparse.Namespace) -> dict[str, Any] | None:
    with contextlib.suppress(Exception):
        raw = typst.query(str(main), "<pdf-typeset-layout>", field="value", one=True, root=str(root),
                          font_paths=font_paths(args), ignore_system_fonts=bool(getattr(args, "no_system_fonts", False)),
                          package_cache_path=str(root / ".no-packages"))
        return json.loads(raw)
    return None


def read_input(path: str) -> tuple[str, Path, str]:
    """(text, directory relative paths resolve against, kind) for a file or '-' (stdin)."""
    if path == "-":
        data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        src_dir, suffix = Path.cwd(), ""
    else:
        p = Path(path).expanduser()
        if not p.is_file():
            raise Failure(EXIT_BAD_INPUT, f"input not found: {path}")
        if p.stat().st_size > MAX_INPUT_BYTES:
            raise Failure(EXIT_BAD_INPUT, f"{path} is larger than {MAX_INPUT_BYTES // 1_000_000} MB")
        data, src_dir, suffix = p.read_bytes(), p.resolve().parent, p.suffix.lower()
    if len(data) > MAX_INPUT_BYTES:
        raise Failure(EXIT_BAD_INPUT, f"input larger than {MAX_INPUT_BYTES // 1_000_000} MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Failure(EXIT_BAD_INPUT, f"{path} is not UTF-8 text") from None
    kind = "json" if suffix == ".json" else "typst" if suffix == ".typ" else "markdown"
    if path == "-" and text.lstrip()[:1] in ("{", "["):
        kind = "json"
    return text, src_dir, kind


def cmd_render(args: argparse.Namespace, note) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    text, src_dir, kind = read_input(args.input)
    out = Path(args.output).expanduser()
    if out.suffix.lower() != ".pdf":
        raise Failure(EXIT_BAD_INPUT, f"output {args.output} should end in .pdf")
    keep = Path(args.keep_typ).expanduser() if getattr(args, "keep_typ", None) else None
    if keep is not None and (keep.is_file() or (keep.is_dir() and any(keep.iterdir()))):
        raise Failure(EXIT_BAD_INPUT, f"--keep-typ {args.keep_typ} must be a new or empty folder",
                      "it would overwrite files there")
    with tempfile.TemporaryDirectory(prefix="pdf-typeset-", ignore_cleanup_errors=True) as tmp:
        build = Path(tmp)
        for f in TEMPLATES.glob("*.typ"):
            shutil.copyfile(f, build / f.name)
        layout = None
        source_text = None
        totals = None
        if kind == "typst":
            template = args.template
            main = Path(args.input).expanduser().resolve()
            root = Path(args.root).expanduser().resolve() if args.root else main.parent
            if not main.is_relative_to(root):
                raise Failure(EXIT_BAD_INPUT, f"--root {args.root} does not contain {args.input}")
            pdf_bytes, findings = compile_typst(main, root, args, raw=True)
        elif kind == "json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                raise Failure(EXIT_BAD_INPUT, f"invalid JSON: {e}") from None
            if not isinstance(data, dict):
                raise Failure(EXIT_BAD_INPUT, "the JSON must be an object")
            template = args.template or ("invoice" if "lines" in data else "cv" if "basics" in data else None)
            if template not in JSON_TEMPLATES:
                raise Failure(EXIT_BAD_INPUT, f"JSON input needs a JSON template ({', '.join(JSON_TEMPLATES)})",
                              "pass --template; see: pdf.py schema <template>")
            meta = normalize_meta({k: data[k] for k in ("lang", "theme", "paper") if k in data}, args, note)
            if not args.theme and "theme" not in data:
                meta["theme"] = "modern"
            prepared = invoice_data(data, meta["lang"]) if template == "invoice" else cv_data(data, meta["lang"])
            prepared = {k: (v if k.startswith("_") else typo_all(v, meta["lang"])) for k, v in prepared.items()}
            if template == "invoice":
                prepared["labels"]["bill-to"] = prepared["labels"]["bill-to"].upper()
                prepared["sections"] = [(t.upper(), lines) for t, lines in prepared["sections"]]
            else:
                for sec in prepared["sections"]:
                    sec["title"] = sec["title"].upper()
            meta.setdefault("title", f"{prepared['title']} {data.get('number', '')}".strip()
                            if template == "invoice" else prepared["name"])
            meta.setdefault("author", data["seller"]["name"] if template == "invoice" else prepared["name"])
            printed = {k: v for k, v in prepared.items() if not k.startswith("_")}
            if template == "cv":
                printed["contacts"] = [c[0] for c in prepared["contacts"]]
                printed["sections"] = [{k: v for k, v in sec.items() if k != "kind"} for sec in prepared["sections"]]
            source_text = "\n".join(strings_in(printed))
            (build / "data.json").write_text(json.dumps(prepared, ensure_ascii=False), encoding="utf-8")
            (build / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            main = build / "main.typ"
            main.write_text(main_typ(template, True), encoding="utf-8")
            pdf_bytes, findings = compile_typst(main, build, args)
            layout = query_layout(main, build, args)
            if template == "invoice":
                totals = {**prepared["_numbers"], "currency": data["currency"].upper(), "total_text": prepared["total"]}
        else:
            image_dirs = [Path(d).expanduser() for d in (args.allow_images_from or [])]
            meta_raw, body, em = parse_markdown(text, src_dir, build, note, image_dirs, args.lang, args.template,
                                                title_given=bool(args.title))
            meta = normalize_meta(meta_raw, args, note)
            template = args.template or meta.get("template") or "document"
            if template in MODERN_BY_DEFAULT and not args.theme and "theme" not in meta_raw:
                meta["theme"] = "modern"
            if template not in MARKDOWN_TEMPLATES:
                raise Failure(EXIT_BAD_INPUT, f"unknown template {template!r} for Markdown",
                              f"choose one of {', '.join(MARKDOWN_TEMPLATES)}")
            if template == "letter":  # a letter's title is its subject; its author whoever signs it
                if not meta.get("title") and meta.get("subject"):
                    meta["title"] = meta["subject"]
                if not meta.get("author"):
                    meta["author"] = meta.get("signature") or (meta.get("from") or [None])[0]
            if "toc" not in meta and template == "report":
                meta["toc"] = sum(1 for lvl, _ in em.headings if lvl <= 2) >= 4
            shown = (("from", "to", "date", "subject", "opening", "closing", "signature") if template == "letter" else
                     ("title", "subtitle", "author", "org", "date", "highlights"))
            source_text = "\n".join(em.plain + strings_in({k: meta[k] for k in shown if k in meta}))
            (build / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            (build / "body.typ").write_text('#import "lib.typ": *\n' + body, encoding="utf-8")
            main = build / "main.typ"
            main.write_text(main_typ(template, False), encoding="utf-8")
            pdf_bytes, findings = compile_typst(main, build, args)
            layout = query_layout(main, build, args)
        if keep is not None and kind != "typst":
            try:
                keep.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise Failure(EXIT_BAD_INPUT, f"cannot create --keep-typ {args.keep_typ}: {e.strerror or e}") from None
            for f in build.rglob("*"):
                if f.is_file():
                    dest = keep / f.relative_to(build)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(f, dest)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(pdf_bytes)
    except OSError as e:
        raise Failure(EXIT_BAD_INPUT, f"cannot write {args.output}: {e.strerror or e}") from None
    info, more = check_pdf(pdf_bytes, template, source_text, layout,
                           Path(args.png).expanduser() if args.png else None, args.dpi, note)
    info.update({"pdf": str(out), "template": template, "bytes": len(pdf_bytes)})
    if totals:
        info["totals"] = totals
    return info, findings + more


def cmd_check(args: argparse.Namespace, note) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    p = Path(args.pdf).expanduser()
    if not p.is_file():
        raise Failure(EXIT_BAD_INPUT, f"PDF not found: {args.pdf}")
    if p.stat().st_size > 5 * MAX_INPUT_BYTES:
        raise Failure(EXIT_BAD_INPUT, f"{args.pdf} is larger than {5 * MAX_INPUT_BYTES // 1_000_000} MB")
    source = None
    if args.source:
        source, _, _ = read_input(args.source)
    info, findings = check_pdf(p.read_bytes(), args.template, source, None,
                               Path(args.png).expanduser() if args.png else None, args.dpi, note)
    info["pdf"] = str(p)
    return info, findings


def cmd_fonts(args: argparse.Namespace) -> tuple[str, bool]:
    fonts = typst.Fonts(include_system_fonts=not args.no_system_fonts, include_embedded_fonts=True,
                        font_paths=font_paths(args)) if _fonts_accepts_kwargs() else typst.Fonts()
    fams = sorted(set(fonts.families()))
    lines = [f"{len(fams)} font families available to Typst (bundled: Source Sans 3; built in: Libertinus Serif, "
             "New Computer Modern, DejaVu Sans Mono):"]
    lines += [f"  {f}" for f in fams]
    missing: list[str] = []
    if args.sample:
        # Set the sample with the templates' font stack (and fallback), then read back which characters got glyphs.
        src = ('#set page(width: 20cm, height: auto, margin: 1cm)\n'
               '#set text(font: ("Libertinus Serif", "Source Sans 3", "DejaVu Sans Mono"))\n'
               f'#{typ_str(args.sample)}\n').encode("utf-8")
        kwargs: dict[str, Any] = {"font_paths": font_paths(args), "ignore_system_fonts": bool(args.no_system_fonts)}
        try:
            data = typst.compile(src, **kwargs)
        except typst.TypstError as e:
            raise Failure(EXIT_OTHER, f"could not set the sample: {e}") from None
        _, covered = font_report(pypdf.PdfReader(__import__("io").BytesIO(data)))
        missing = sorted({c for c in args.sample if not c.isspace() and c not in IGNORABLE and c not in covered})
        lines.append("")
        if missing:
            lines.append(f"sample: {len(missing)} character(s) have no glyph in any available font: "
                         f"{''.join(missing)} ({', '.join(f'U+{ord(c):04X}' for c in missing[:12])})")
            lines.append("install a font that covers them (e.g. Noto Sans CJK, Noto Sans Arabic) or pass --font-dir")
        else:
            lines.append("sample: every character has a glyph")
    return "\n".join(lines) + "\n", bool(missing)


def _fonts_accepts_kwargs() -> bool:
    try:
        typst.Fonts(include_system_fonts=False)
        return True
    except TypeError:
        return False


def format_report(info: dict[str, Any], findings: list[dict[str, Any]]) -> str:
    lines = []
    if info.get("pdf"):
        what = f"{info['pages']} page{'s' if info['pages'] != 1 else ''}"
        tpl = f", template {info['template']}" if info.get("template") else ""
        lines.append(f"{info['pdf']}: {what}{tpl}")
    if info.get("totals"):
        t = info["totals"]
        lines.append(f"totals ({t['currency']}, exact): subtotal {t['subtotal']}, tax {t['tax']}, total {t['total']}")
    if info.get("fonts"):
        lines.append("fonts: " + ", ".join(info["fonts"]))
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    if not findings:
        lines.append("checks: all passed")
    else:
        lines.append(f"checks: {len(errors)} error(s), {len(warnings)} warning(s)")
        for f in errors + warnings:
            where = f" p.{f['page']}" if f.get("page") else ""
            lines.append(f"  {f['severity']}{where} [{f['rule']}] {f['message']}" + (f" — {f['hint']}" if f["hint"] else ""))
    if info.get("png"):
        lines.append("png: " + " ".join(info["png"]))
        lines.append("look at the PNG pages before handing the PDF over")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ CLI

def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    out_opts = argparse.ArgumentParser(add_help=False)
    out_opts.add_argument("--png", metavar="DIR", help="write every page as PNG (plus contact-sheet.png) to DIR")
    out_opts.add_argument("--dpi", type=int, default=110, help="PNG resolution (default 110, 30–300)")
    out_opts.add_argument("-f", "--format", choices=["text", "json"], default="text", help="report format")
    out_opts.add_argument("-q", "--quiet", action="store_true", help="no note: lines")
    fonts_opts = argparse.ArgumentParser(add_help=False)
    fonts_opts.add_argument("--font-dir", action="append", metavar="DIR", help="extra font folder (repeatable)")
    fonts_opts.add_argument("--no-system-fonts", action="store_true", help="use only bundled and built-in fonts")

    r = sub.add_parser("render", parents=[out_opts, fonts_opts], help="Markdown or JSON → PDF, then check it")
    r.add_argument("input", help="a .md file, a .json file for a JSON template, a .typ file, or - for stdin")
    r.add_argument("-o", "--output", required=True, help="the PDF to write")
    r.add_argument("-t", "--template", help="report, document, letter, onepager, slides; invoice, cv for JSON")
    r.add_argument("--theme", help="classic (default), modern, minimal")
    r.add_argument("--lang", help="document language, e.g. en, ru, de (hyphenation, quotes, labels)")
    r.add_argument("--paper", help="a4 (default), us-letter, a5, us-legal")
    for k in ("title", "subtitle", "author", "org", "date"):
        r.add_argument(f"--{k}", help=f"document {k} (overrides the front matter)")
    r.add_argument("--toc", action=argparse.BooleanOptionalAction, default=None, help="table of contents")
    r.add_argument("--pdf-standard", action="append", choices=["a-1b", "a-2b", "a-2u", "a-3b", "a-3u", "ua-1"],
                   help="also enforce a PDF/A or PDF/UA standard (repeatable)")
    r.add_argument("--keep-typ", metavar="DIR", help="keep the generated Typst sources in DIR, a new or empty folder")
    r.add_argument("--allow-images-from", action="append", metavar="DIR",
                   help="also read Markdown images from DIR (by default only the input's folder and below)")
    r.add_argument("--root", metavar="DIR", help="for .typ input: the folder it may read files from "
                   "(default: its own folder)")
    r.add_argument("--allow-packages", action="store_true",
                   help="let a .typ input import Typst packages (downloads them; off by default)")
    c = sub.add_parser("check", parents=[out_opts], help="run the checks on any PDF")
    c.add_argument("pdf")
    c.add_argument("--source", metavar="FILE", help="the text the PDF was made from (finds characters that vanished)")
    c.add_argument("-t", "--template", help="the template it was made with (enables margin and layout checks)")
    sub.add_parser("templates", help="list templates and themes")
    s = sub.add_parser("schema", help="the JSON Schema a JSON template accepts")
    s.add_argument("name", choices=list(JSON_TEMPLATES))
    f = sub.add_parser("fonts", parents=[fonts_opts], help="list the fonts Typst can use")
    f.add_argument("--sample", help="text to check: names every character no available font covers (exit 1)")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = make_parser().parse_args(argv)
    note = (lambda m: None) if getattr(args, "quiet", False) else (lambda m: sys.stderr.write(f"note: {m}\n"))

    def fail(code: int, message: str, hint: str = "") -> int:
        sys.stderr.write(f"error: {message}" + (f" — {hint}" if hint else "") + "\n")
        return code

    try:
        if args.command == "templates":
            rows = [f"  {k:<10} {v}" for k, v in MARKDOWN_TEMPLATES.items()]
            rows += [f"  {k:<10} {v}" for k, v in JSON_TEMPLATES.items()]
            sys.stdout.write("Markdown templates:\n" + "\n".join(rows[:len(MARKDOWN_TEMPLATES)]) +
                             "\nJSON templates:\n" + "\n".join(rows[len(MARKDOWN_TEMPLATES):]) +
                             "\nThemes:\n" + "\n".join(f"  {k:<10} {v}" for k, v in THEMES.items()) + "\n")
            return EXIT_OK
        if args.command == "schema":
            sys.stdout.write(json.dumps(SCHEMAS[args.name], indent=2, ensure_ascii=False) + "\n")
            return EXIT_OK
        if args.command == "fonts":
            text, missing = cmd_fonts(args)
            sys.stdout.write(text)
            return EXIT_FINDINGS if missing else EXIT_OK
        if not 30 <= args.dpi <= 300:
            return fail(EXIT_BAD_INPUT, "--dpi must be between 30 and 300")
        info, findings = cmd_render(args, note) if args.command == "render" else cmd_check(args, note)
    except Failure as e:
        return fail(e.code, str(e), e.hint)
    except KeyboardInterrupt:
        return fail(130, "interrupted")
    except RecursionError:
        return fail(EXIT_BAD_INPUT, "the input is nested too deeply", "flatten the nesting")
    except Exception as e:  # noqa: BLE001 - every exit code has a meaning; a crash must not pass as one of them
        return fail(EXIT_OTHER, f"unexpected {type(e).__name__}: {e}", "a bug; report it with the command line")
    if args.format == "json":
        sys.stdout.write(json.dumps({**info, "findings": findings,
                                     "ok": not any(f["severity"] == "error" for f in findings)},
                                    ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(format_report(info, findings))
    return EXIT_FINDINGS if any(f["severity"] == "error" for f in findings) else EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("error: interrupted\n")
        sys.exit(130)
    except BrokenPipeError as e:
        try:
            sys.stdout.flush()
        except BrokenPipeError:  # the reader closed stdout (`| head`): stop quietly, as a pipe expects
            try:
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            except OSError:
                pass
            sys.exit(0)
        sys.stderr.write(f"error: unexpected BrokenPipeError: {e} — a bug; report it with the command line\n")
        sys.exit(EXIT_OTHER)
    except Exception as e:  # noqa: BLE001 - a crash exits 7, never 1 or another code that has a meaning
        sys.stderr.write(f"error: unexpected {type(e).__name__}: {(str(e).splitlines() or [''])[0][:300]} "
                         "— a bug; report it with the command line\n")
        sys.exit(EXIT_OTHER)
