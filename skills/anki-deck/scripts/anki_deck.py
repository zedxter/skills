#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "genanki==0.13.1",
#   "markdown-it-py>=3,<5",
# ]
# ///
"""Build an Anki .apkg deck from Markdown, CSV/TSV or JSON cards, offline, with deterministic deck, note type
and note ids: importing a rebuilt deck updates the cards already in Anki instead of adding duplicates.

    uv run anki_deck.py build cards.md -o biology.apkg --deck "Biology::Cells"
    uv run anki_deck.py build words.csv -o spanish.apkg --deck Spanish --type reversed --tags vocab
    uv run anki_deck.py build notes.md -o deck.apkg --type cloze --media images/
    uv run anki_deck.py build cards.json --dry-run          # show the parsed cards, write nothing
    uv run anki_deck.py inspect biology.apkg                 # what a deck file contains

Exit codes:
    0 done · 1 no cards found in the input · 2 bad input or a missing dependency · 3 a media file is missing ·
    7 the deck could not be written, or an unexpected failure · 130 interrupted
"""

import argparse
import contextlib
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

EXIT_OK, EXIT_NO_CARDS, EXIT_BAD_INPUT, EXIT_MEDIA, EXIT_WRITE = 0, 1, 2, 3, 7

try:
    import genanki
    from markdown_it import MarkdownIt
except ImportError as e:
    sys.stderr.write(f"error: missing dependency {e.name} — run with `uv run anki_deck.py ...`, or "
                     "`pip install genanki==0.13.1 markdown-it-py`\n")
    sys.exit(EXIT_BAD_INPUT)

CLOZE_RE = re.compile(r"\{\{c[1-9]\d*::.+?\}\}", re.S)
MEDIA_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".mp3", ".ogg", ".wav", ".m4a", ".flac",
             ".opus", ".mp4", ".webm"}
FIELD_NAMES = {"front": "front", "question": "front", "q": "front", "term": "front", "word": "front",
               "back": "back", "answer": "back", "a": "back", "definition": "back", "translation": "back",
               "text": "text", "cloze": "text", "extra": "extra", "notes": "extra", "back extra": "extra",
               "tags": "tags", "tag": "tags", "deck": "deck", "id": "id", "guid": "id", "type": "type"}
TYPES = ("basic", "reversed", "cloze")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
KEY_LINE = re.compile(r"^\s*(?:\*\*)?(Q|Question|A|Answer|Extra|Tags|Deck|Id)\s*(?:\*\*)?\s*:\s*(?:\*\*)?\s?(.*)$", re.I)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$")

CSS = """.card { font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 20px; line-height: 1.45; color: #1d1d1f; background: #fff; text-align: left;
  max-width: 40em; margin: 0 auto; padding: 0 0.5em; }
.nightMode.card, .night_mode .card { color: #e8e8ea; background: #1e1e20; }
hr#answer { border: 0; border-top: 1px solid #c8c8cc; margin: 1em 0; }
.extra { margin-top: 1em; font-size: 0.85em; color: #6e6e73; }
.nightMode .extra { color: #a1a1a6; }
code, pre { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 0.85em; }
pre { background: #f2f2f5; padding: 0.6em 0.8em; border-radius: 6px; overflow-x: auto; }
.nightMode pre { background: #2c2c2e; }
img { max-width: 100%; height: auto; }
table { border-collapse: collapse; } td, th { border: 1px solid #c8c8cc; padding: 0.2em 0.5em; }
.cloze { font-weight: bold; color: #0a66c2; } .nightMode .cloze { color: #64a8f0; }
"""
EXTRA = "{{#Extra}}<div class=extra>{{Extra}}</div>{{/Extra}}"


class Failure(Exception):
    def __init__(self, code: int, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.hint = code, hint


def safe(s: Any) -> str:
    """One printable line: no control, C1 or bidi-override characters, whitespace collapsed."""
    return " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", " ", str(s if s is not None else "")).split())


# ------------------------------------------------------------------ ids

def stable_id(kind: str, name: str, seed: str = "") -> int:
    """A note type or deck id from its name: the first 8 bytes of SHA-256("anki-deck/<kind>/<seed>/<name>") as a
    big-endian integer, mod 2**30, plus 2**30. Same name and seed, same id, on every machine and every run.

    >>> stable_id("deck", "Biology::Cells")
    1931295993
    """
    digest = hashlib.sha256(f"anki-deck/{kind}/{seed}/{name}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % (1 << 30) + (1 << 30)


def note_guid(key: str, kind: str, seed: str = "") -> str:
    """A note's guid from its identity (an id column, or its front or cloze text) and its kind."""
    return genanki.guid_for("anki-deck", seed, kind, key)


def model_for(kind: str, seed: str = "") -> "genanki.Model":
    name = {"basic": "anki-deck Basic", "reversed": "anki-deck Basic and reversed",
            "cloze": "anki-deck Cloze"}[kind]
    mid = stable_id("model", name, seed)
    if kind == "cloze":
        return genanki.Model(mid, name, fields=[{"name": "Text"}, {"name": "Extra"}], css=CSS,
                             model_type=genanki.Model.CLOZE,
                             templates=[{"name": "Cloze", "qfmt": "{{cloze:Text}}",
                                         "afmt": "{{cloze:Text}}" + EXTRA}])
    templates = [{"name": "Card 1", "qfmt": "{{Front}}", "afmt": "{{FrontSide}}<hr id=answer>{{Back}}" + EXTRA}]
    if kind == "reversed":
        templates.append({"name": "Card 2", "qfmt": "{{Back}}", "afmt": "{{FrontSide}}<hr id=answer>{{Front}}" + EXTRA})
    return genanki.Model(mid, name, fields=[{"name": "Front"}, {"name": "Back"}, {"name": "Extra"}],
                         templates=templates, css=CSS)


# ------------------------------------------------------------------ reading cards

def card(front: str = "", back: str = "", extra: str = "", tags: Any = None, deck: str | None = None,
         nid: str | None = None, kind: str | None = None, where: str = "") -> dict[str, Any]:
    if isinstance(tags, str):
        tags = re.split(r"[,\s]+", tags)
    return {"front": (front or "").strip(), "back": (back or "").strip(), "extra": (extra or "").strip(),
            "tags": [t for t in (tags or []) if str(t).strip()], "deck": (deck or "").strip() or None,
            "id": str(nid).strip() if nid not in (None, "") else None, "type": (kind or "").strip().lower() or None,
            "where": where}


def fenced(lines: list[str]) -> list[bool]:
    """For each line, whether it belongs to a fenced code block (fence lines included)."""
    out, fence = [], None
    for ln in lines:
        m = FENCE.match(ln)
        if fence is None and m:
            fence = m[1][0] * 3
            out.append(True)
        elif fence is not None:
            out.append(True)
            if m and m[1].startswith(fence):
                fence = None
        else:
            out.append(False)
    return out


def parse_markdown(text: str, src: str) -> tuple[list[dict[str, Any]], str | None]:
    """Cards from Markdown, and the H1 title if there is one. Three layouts, tried in this order:
    Q:/A: blocks; headings (## question, body = answer); cloze paragraphs and list items. Nothing inside a
    fenced code block is read as a key, heading or separator."""
    lines = text.lstrip("\ufeff").replace("\r\n", "\n").split("\n")
    code = fenced(lines)
    title = next((ln[2:].strip() for ln, c in zip(lines, code) if not c and re.match(r"^# \S", ln)), None)
    cards: list[dict[str, Any]] = []

    # Q:/A: blocks. A block runs until the next Q:, a --- line, a heading or the end; Extra:/Tags:/Deck:/Id: lines
    # attach to it. Text outside the blocks can hold cloze notes.
    if any(not c and (m := KEY_LINE.match(ln)) and m[1].lower() in ("q", "question") for ln, c in zip(lines, code)):
        cur: dict[str, Any] | None = None
        part = None
        outside = [""] * len(lines)
        for i, (ln, in_code) in enumerate(zip(lines, code), 1):
            m = None if in_code else KEY_LINE.match(ln)
            key = m[1].lower() if m else None
            if key in ("q", "question"):
                cur = {"front": [m[2]], "back": [], "extra": [], "tags": "", "deck": None, "id": None, "line": i}
                cards.append(cur)
                part = "front"
            elif cur is not None and key in ("a", "answer"):
                cur["back"].append(m[2])
                part = "back"
            elif cur is not None and key == "extra":
                cur["extra"].append(m[2])
                part = "extra"
            elif cur is not None and key in ("tags", "deck", "id"):
                cur[key] = m[2].strip()
            elif not in_code and (re.match(r"^\s*(---+|\*\*\*+)\s*$", ln) or HEADING.match(ln)):
                cur, part = None, None
            elif cur is not None and part:
                cur[part].append(ln)
            elif cur is None:
                outside[i - 1] = ln
        cloze = cloze_notes(outside, src, code)
        return [card("\n".join(c["front"]), "\n".join(c["back"]), "\n".join(c["extra"]), c["tags"], c["deck"],
                     c["id"], None, f"{src}:{c['line']}") for c in cards] + cloze, title

    # Headings: the shallowest level below H1 that is used is the card level.
    heads = [(i, len(m[1]), m[2]) for i, (ln, c) in enumerate(zip(lines, code))
             if not c and (m := HEADING.match(ln)) and len(m[1]) >= 2]
    if heads:
        level = min(h[1] for h in heads)
        starts = [h for h in heads if h[1] == level]
        for n, (i, _, q) in enumerate(starts):
            stop = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
            meta: dict[str, str] = {}
            keep = []
            for ln, c in zip(lines[i + 1:stop], code[i + 1:stop]):
                if not c and (m := re.match(r"^\s*(Tags|Deck|Id|Extra)\s*:\s*(.*)$", ln, re.I)):
                    meta[m[1].lower()] = m[2].strip()
                else:
                    keep.append(ln)
            cards.append(card(q, "\n".join(keep), meta.get("extra", ""), meta.get("tags"), meta.get("deck"),
                              meta.get("id"), None, f"{src}:{i + 1}"))
        return cards, title

    return cloze_notes(lines, src, code), title


def cloze_notes(lines: list[str], src: str, code: list[bool]) -> list[dict[str, Any]]:
    """Each paragraph or list item with {{c1::...}} is a cloze note. Extra:, Tags: and Id: lines right after it
    belong to it."""
    cards: list[dict[str, Any]] = []
    block: list[str] = []
    start = 0

    def flush() -> None:
        nonlocal block
        txt = "\n".join(block).strip()
        if txt and CLOZE_RE.search(txt):
            cards.append(card(txt, where=f"{src}:{start}", kind="cloze"))
        block = []

    for i, (ln, in_code) in enumerate(zip(lines, code), 1):
        if in_code:
            if not block:
                start = i
            block.append(ln)
            continue
        if m := re.match(r"^\s*(Extra|Tags|Id)\s*:\s*(.*)$", ln, re.I):
            flush()
            if cards:
                key, val = m[1].lower(), m[2].strip()
                if key == "extra":
                    cards[-1]["extra"] = (cards[-1]["extra"] + "\n" + val).strip()
                elif key == "tags":
                    cards[-1]["tags"] += [t for t in re.split(r"[,\s]+", val) if t]
                else:
                    cards[-1]["id"] = val or None
            continue
        if not ln.strip() or re.match(r"^\s*([-*+]|\d+[.)])\s+", ln) or HEADING.match(ln):
            flush()
            if not ln.strip() or HEADING.match(ln):
                continue
            ln = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", ln)
        if not block:
            start = i
        block.append(ln)
    flush()
    return cards


def first_line_delimiter(text: str) -> str:
    """The delimiter of the header (first non-empty) line, counting only characters outside quotes."""
    line = next((ln for ln in text.splitlines() if ln.strip()), "")
    bare = re.sub(r'"[^"]*"', "", line)
    counts = {d: bare.count(d) for d in ("\t", ";", ",")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ","


def parse_table(text: str, src: str, delimiter: str | None, note=lambda m: None) -> list[dict[str, Any]]:
    """Cards from CSV/TSV. A header row naming the columns (front/question/term, back/answer/definition, text, extra,
    tags, deck, id, type) is used when present; a column it does not name fills front or back if those are missing.
    Without a header the columns are front, back, then tags."""
    text = text.lstrip("\ufeff")
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter or first_line_delimiter(text))
            if any(c.strip() for c in r)]
    if not rows:
        return []
    header = [FIELD_NAMES.get(c.strip().lower()) for c in rows[0]]
    if any(header):
        cols = list(header)
        for need in ("front", "back"):
            if need not in cols and not (need == "front" and "text" in cols) and not (need == "back" and "text" in cols):
                free = next((i for i, c in enumerate(cols) if c is None), None)
                if free is not None:
                    cols[free] = need
        body, first = rows[1:], 2
    else:
        cols, body, first = ["front", "back", "tags"], rows, 1
        note(f"{src}: no header naming the columns, so the first row ({', '.join(rows[0][:3])}) is read as a card "
             "— start the file with front,back if it is a header")
    out = []
    for n, r in enumerate(body, first):
        v = {c: r[i] for i, c in enumerate(cols) if c and i < len(r)}
        front = v.get("front") or v.get("text") or ""
        out.append(card(front, v.get("back", ""), v.get("extra", ""), v.get("tags"), v.get("deck"), v.get("id"),
                        v.get("type") or ("cloze" if "text" in v and "front" not in v else None), f"{src}:{n}"))
    return out


def parse_json(text: str, src: str) -> tuple[list[dict[str, Any]], str | None]:
    """Cards from a JSON list of objects, or {"deck": name, "cards": [...]}. Keys as in CSV headers."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise Failure(EXIT_BAD_INPUT, f"{src}: not valid JSON ({e.msg}, line {e.lineno})", "fix the file") from None
    title = None
    if isinstance(data, dict):
        title = data.get("deck") if isinstance(data.get("deck"), str) else None
        data = data.get("cards", data.get("notes"))
    if not isinstance(data, list):
        raise Failure(EXIT_BAD_INPUT, f"{src}: expected a list of cards, or an object with a \"cards\" list",
                      'use [{"front": "...", "back": "..."}]')
    out = []
    for n, item in enumerate(data, 1):
        if isinstance(item, (list, tuple)) and 1 <= len(item) <= 3:
            item = dict(zip(("front", "back", "tags"), item))
        if not isinstance(item, dict):
            raise Failure(EXIT_BAD_INPUT, f"{src}: card {n} is not an object", 'use {"front": "...", "back": "..."}')
        v = {FIELD_NAMES[k.strip().lower()]: val for k, val in item.items()
             if isinstance(k, str) and k.strip().lower() in FIELD_NAMES}
        for k, val in v.items():
            if not (val is None or isinstance(val, (str, int, float)) or (k == "tags" and isinstance(val, list))):
                raise Failure(EXIT_BAD_INPUT, f"{src}: card {n}: {k!r} must be text", f"got {type(val).__name__}")
        strs = {k: ("" if val is None else val if isinstance(val, (str, list)) else str(val)) for k, val in v.items()}
        front = strs.get("front") or strs.get("text") or ""
        out.append(card(front if isinstance(front, str) else "", strs.get("back", ""), strs.get("extra", ""),
                        strs.get("tags"), strs.get("deck"), strs.get("id"),
                        strs.get("type") or ("cloze" if "text" in strs and "front" not in strs else None),
                        f"{src}:card {n}"))
    return out, title


def read_cards(path: str, fmt: str | None, note=lambda m: None) -> tuple[list[dict[str, Any]], str | None, Path]:
    if path == "-":
        text, src, base = sys.stdin.read().lstrip("\ufeff"), "stdin", Path.cwd()
    else:
        p = Path(path).expanduser()
        try:
            raw = p.read_bytes()
        except OSError as e:
            raise Failure(EXIT_BAD_INPUT, f"cannot read {path}: {e.strerror or e}", "check the path") from None
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
        src, base = p.name, p.parent
    ext = (fmt or Path(src).suffix.lstrip(".") or "md").lower()
    if ext in ("md", "markdown", "txt"):
        cards, title = parse_markdown(text, src)
    elif ext in ("csv", "tsv", "tab"):
        cards, title = parse_table(text, src, "\t" if ext in ("tsv", "tab") else None, note), None
    elif ext == "json":
        cards, title = parse_json(text, src)
    else:
        raise Failure(EXIT_BAD_INPUT, f"{src}: unknown input format {ext!r}", "use .md, .csv, .tsv or .json, or --format")
    for c in cards:
        c["base"] = base  # media are found next to the file the card came from
    return cards, title, base


# ------------------------------------------------------------------ fields

MATH = re.compile(r"\\\((.+?)\\\)|\\\[(.+?)\\\]|\$\$(.+?)\$\$|(?<![\\$\w])\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\w)", re.S)


class Renderer:
    """Markdown fields to the HTML Anki shows. Math ($...$, $$...$$, \\(...\\), \\[...\\]) goes to MathJax untouched;
    images and [sound:...] references are collected as media."""

    def __init__(self, markdown: bool, media_dir: Path | None) -> None:
        self.md = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(["table", "strikethrough"])
        self.markdown, self.media_dir = markdown, media_dir
        self.media: dict[str, Path] = {}      # name in the deck → file on disk
        self.missing: list[tuple[str, str]] = []
        self.remote: set[str] = set()

    def field(self, text: str, where: str, base: Path) -> str:
        if not text:
            return ""
        if not self.markdown:
            html = text
        else:
            keep: list[str] = []

            def stash(m: re.Match) -> str:
                inline, display, dd, dollar = m.groups()
                keep.append(f"\\[{display or dd}\\]" if (display or dd) is not None else f"\\({inline or dollar}\\)")
                return f"\ue000{len(keep) - 1}\ue001"  # private-use characters: Markdown leaves them alone
            # Math only outside code: fenced blocks and `code spans` keep their dollars.
            src = "".join(part if i % 2 else MATH.sub(stash, part)
                          for i, part in enumerate(re.split(r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`)", text)))
            src = re.sub(r"\{\{(c\d+)::", lambda m: "\ue002" + m[1] + "\ue003", src)  # cloze markers too
            html = self.md.render(src).strip()
            html = re.sub("\ue002(c\\d+)\ue003", r"{{\1::", html)
            html = re.sub("\ue000(\\d+)\ue001",
                          lambda m: keep[int(m[1])].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), html)
            if html.count("<p>") == 1 and html.startswith("<p>") and html.endswith("</p>"):
                html = html[3:-4]
        return self.collect(html, where, base)

    def collect(self, html: str, where: str, base: Path) -> str:
        def img(m: re.Match) -> str:
            src = m[2]
            if re.match(r"^(https?:|data:)", src, re.I):
                self.remote.add(src)
                return m[0]
            return m[1] + self.add(src, where, base) + m[3]
        html = re.sub(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', img, html, flags=re.I)
        return re.sub(r"\[sound:([^\]]+)\]", lambda m: f"[sound:{self.add(m[1], where, base)}]", html)

    def add(self, ref: str, where: str, base: Path) -> str:
        """The file a field refers to, as its name inside the deck. Only image, audio and video files inside the
        input's folder or --media are packed: a card can never pull in another file from the disk."""
        from urllib.parse import unquote
        import html as html_lib
        rel = unquote(html_lib.unescape(ref))
        name = Path(rel).name
        if Path(rel).suffix.lower() not in MEDIA_EXT:
            raise Failure(EXIT_BAD_INPUT, f"{where}: {rel!r} is not an image, audio or video file",
                          "media must be " + ", ".join(sorted(MEDIA_EXT)))
        roots = ([self.media_dir] if self.media_dir else []) + [base]
        candidates = []
        for root in roots:
            for c in (root / rel, root / name):
                with contextlib.suppress(OSError, ValueError):
                    if c.resolve().is_relative_to(root.resolve()):
                        candidates.append(c)
        if Path(rel).is_absolute() or ".." in Path(rel).parts or re.match(r"^[A-Za-z]:", rel):
            raise Failure(EXIT_BAD_INPUT, f"{where}: {rel!r} points outside the input's folder",
                          "copy the file next to the cards or into --media, and refer to it by a relative path")
        path = next((c for c in candidates if c.is_file()), None)
        if path is None:
            self.missing.append((rel, where))
            return name
        if name in self.media and self.media[name].resolve() != path.resolve():
            raise Failure(EXIT_BAD_INPUT, f"two different media files are both named {name!r} ({self.media[name]}, {path})",
                          "rename one of them: Anki keeps media in one flat folder")
        self.media[name] = path
        return name


# ------------------------------------------------------------------ building

def plan(cards: list[dict[str, Any]], args: argparse.Namespace, default_deck: str, r: Renderer,
         note) -> list[dict[str, Any]]:
    """Validated notes: kind, deck, rendered fields, tags and guid. Problems are noted and the card skipped."""
    global_tags = [t for t in re.split(r"[,\s]+", args.tags or "") if t]
    seen: dict[str, str] = {}
    out = []
    for c in cards:
        kind = c["type"] or ("cloze" if CLOZE_RE.search(c["front"]) and not c["back"] else args.type)
        if kind not in TYPES:
            note(f"{c['where']}: unknown type {c['type']!r}; skipped (use basic, reversed or cloze)")
            continue
        if kind == "cloze":
            if not CLOZE_RE.search(c["front"]):
                note(f"{c['where']}: a cloze card needs {{{{c1::...}}}} in its text; skipped")
                continue
            extra = "\n\n".join(x for x in (c["back"], c["extra"]) if x)
        else:
            if not c["front"] or not c["back"]:
                note(f"{c['where']}: {'no question' if not c['front'] else 'no answer'}; skipped")
                continue
            extra = c["extra"]
        text = " ".join(c["front"].split())
        if kind == "cloze":  # the hidden answers are content: fixing one updates the note, like a basic answer
            text = re.sub(r"\{\{(c\d+)::.*?\}\}", r"{{\1}}", text)
        key = c["id"] or text
        guid = note_guid(key, "cloze" if kind == "cloze" else "basic", args.seed)
        if guid in seen:
            note(f"{c['where']}: the same card as {seen[guid]}; skipped (give it an id to keep both)")
            continue
        seen[guid] = c["where"]
        tags = []
        for t in global_tags + [str(t) for t in c["tags"]]:
            t = re.sub(r"\s+", "_", t.strip())
            if t and t not in tags:
                tags.append(t)
        base = c.get("base") or Path.cwd()
        fields = [r.field(c["front"], c["where"], base)] \
            + ([] if kind == "cloze" else [r.field(c["back"], c["where"], base)]) + [r.field(extra, c["where"], base)]
        out.append({"kind": kind, "deck": c["deck"] or default_deck, "fields": fields, "tags": tags, "guid": guid,
                    "where": c["where"], "front": c["front"], "back": c["back"] if kind != "cloze" else ""})
    return out


def deck_name_ok(name: str) -> bool:
    parts = name.split("::")
    return bool(name.strip()) and all(p.strip() for p in parts) and not re.search(r'["\x00-\x1f]', name)


def write_package(notes: list[dict[str, Any]], media: dict[str, Path], out: Path, seed: str) -> dict[str, Any]:
    decks: dict[str, genanki.Deck] = {}
    models: dict[str, genanki.Model] = {}
    for n in notes:
        if n["deck"] not in decks:
            decks[n["deck"]] = genanki.Deck(stable_id("deck", n["deck"], seed), n["deck"])
        if n["kind"] not in models:
            models[n["kind"]] = model_for(n["kind"], seed)
        decks[n["deck"]].add_note(genanki.Note(model=models[n["kind"]], fields=n["fields"], tags=n["tags"],
                                               guid=n["guid"], sort_field=n["fields"][0]))
    pkg = genanki.Package(list(decks.values()), media_files=[str(p) for p in media.values()])
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".apkg", dir=out.parent)
    os.close(fd)
    umask = os.umask(0)
    os.umask(umask)
    saved = tempfile.tempdir
    try:
        with tempfile.TemporaryDirectory() as scratch:
            tempfile.tempdir = scratch  # genanki builds the collection in a temp file it never deletes
            # genanki names media by their path's basename, which is the name the fields use.
            pkg.write_to_file(tmp)
        os.chmod(tmp, 0o666 & ~umask)  # mkstemp makes it private; a deck is an ordinary file
        os.replace(tmp, out)
    finally:
        tempfile.tempdir = saved
        with contextlib.suppress(OSError):
            os.unlink(tmp)
    return {"decks": {name: d.deck_id for name, d in decks.items()},
            "note_types": {m.name: m.model_id for m in models.values()}}


# ------------------------------------------------------------------ inspecting

def inspect_package(path: Path) -> dict[str, Any]:
    """Decks, note types, notes, cards and media of an .apkg, read from its collection (any Anki schema version
    that keeps a legacy collection.anki2; newer exports that only have collection.anki21b are reported as such)."""
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise Failure(EXIT_BAD_INPUT, f"{path} is not an .apkg file ({e})", "give a deck built by Anki or this tool")
    with zf:
        names = set(zf.namelist())
        if "collection.anki21b" in names:  # its collection.anki2 is a placeholder that says "update Anki"
            raise Failure(EXIT_BAD_INPUT, f"{path} is in the format of newer Anki exports (collection.anki21b)",
                          "open it in Anki; this tool reads decks it built and legacy exports")
        coll = next((n for n in ("collection.anki21", "collection.anki2") if n in names), None)
        if coll is None:
            raise Failure(EXIT_BAD_INPUT, f"{path} has no collection inside", "is it an Anki deck package?")
        try:
            media_map = json.loads(zf.read("media") or b"{}") if "media" in names else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            media_map = {}
        if not isinstance(media_map, dict):
            media_map = {}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db_path = Path(tmp) / "c.db"
                db_path.write_bytes(zf.read(coll))
                db = sqlite3.connect(db_path)
                try:
                    col = db.execute("select decks, models from col").fetchone()
                    decks = {int(k): v["name"] for k, v in json.loads(col[0]).items()}
                    models = {int(k): v for k, v in json.loads(col[1]).items()}
                    notes = db.execute("select id, guid, mid, flds, tags from notes order by id").fetchall()
                    cards = db.execute("select nid, did from cards").fetchall()
                finally:
                    db.close()
        except (sqlite3.DatabaseError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            raise Failure(EXIT_BAD_INPUT, f"{path}: the collection inside cannot be read ({e})",
                          "open it in Anki") from None
    per_deck: dict[str, int] = {}
    for _, did in cards:
        per_deck[decks.get(did, str(did))] = per_deck.get(decks.get(did, str(did)), 0) + 1
    per_model: dict[str, int] = {}
    for _, _, mid, _, _ in notes:
        name = models.get(mid, {}).get("name", str(mid))
        per_model[name] = per_model.get(name, 0) + 1
    sample = [{"guid": g, "note_type": models.get(mid, {}).get("name"), "fields": f.split("\x1f"), "tags": t.split()}
              for _, g, mid, f, t in notes[:5]]
    return {"file": str(path), "notes": len(notes), "cards": len(cards),
            "decks": [{"name": decks.get(d, str(d)), "id": d, "cards": per_deck.get(decks.get(d, str(d)), 0)}
                      for d in sorted({did for _, did in cards})],
            "note_types": [{"name": m["name"], "id": mid, "notes": per_model.get(m["name"], 0),
                            "kind": "cloze" if m.get("type") == 1 else "standard"}
                           for mid, m in models.items() if per_model.get(m["name"])],
            "media": sorted(media_map.values()), "sample": sample}


# ------------------------------------------------------------------ commands

def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def strip_html(s: str) -> str:
    """Field HTML as one line for previews: images as [image name], tags dropped."""
    s = re.sub(r'<img\b[^>]*?\bsrc="([^"]+)"[^>]*>', r" [image \1] ", s, flags=re.I)
    s = re.sub(r"<(br|/p|/li|/div|/tr|hr)\b[^>]*>", " ", s, flags=re.I)
    return " ".join(re.sub(r"<[^>]+>", "", s).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").split())


def cmd_build(args: argparse.Namespace, note) -> tuple[int, str]:
    cards: list[dict[str, Any]] = []
    title = None
    for path in args.inputs:
        got, t, _ = read_cards(path, args.format_in, note)
        cards += got
        title = title or t
    default_deck = args.deck or title or (Path(args.inputs[0]).stem if args.inputs[0] != "-" else "Default deck")
    if not deck_name_ok(default_deck):
        raise Failure(EXIT_BAD_INPUT, f"{default_deck!r} is not a usable deck name", 'use "Name" or "Name::Subdeck"')
    if not cards:
        raise Failure(EXIT_NO_CARDS, "no cards found in the input",
                      "use Q:/A: blocks, ## question headings, cloze paragraphs, CSV front,back rows, or a JSON list")
    media_dir = Path(args.media).expanduser() if args.media else None
    if media_dir and not media_dir.is_dir():
        raise Failure(EXIT_BAD_INPUT, f"--media {args.media}: no such folder", "give the folder that holds the images")
    r = Renderer(not args.html, media_dir)
    notes = plan(cards, args, default_deck, r, note)
    for n in notes:
        if not deck_name_ok(n["deck"]):
            raise Failure(EXIT_BAD_INPUT, f"{n['where']}: {n['deck']!r} is not a usable deck name", 'use "Name::Subdeck"')
    if not notes:
        raise Failure(EXIT_NO_CARDS, f"none of the {len(cards)} cards found could be used", "see the notes above")
    for url in sorted(r.remote):
        note(f"{url} is not inside the deck; Anki shows it only when online")
    ids = {"decks": {d: stable_id("deck", d, args.seed) for d in dict.fromkeys(n["deck"] for n in notes)},
           "note_types": {model_for(k, args.seed).name: model_for(k, args.seed).model_id
                          for k in dict.fromkeys(n["kind"] for n in notes)}}
    summary = {"notes": len(notes), "cards": sum(2 if n["kind"] == "reversed" else
                                                 len(set(re.findall(r"\{\{c([1-9]\d*)::", n["fields"][0]))) or 1
                                                 if n["kind"] == "cloze" else 1 for n in notes),
               "skipped": len(cards) - len(notes), **ids, "media": sorted(r.media),
               "missing_media": [{"file": f, "where": w} for f, w in r.missing]}
    if args.dry_run:
        summary["preview"] = [{"type": n["kind"], "deck": n["deck"], "front": n["front"], "back": n["back"],
                               "fields": n["fields"], "tags": n["tags"], "guid": n["guid"], "where": n["where"]}
                              for n in notes]
        if args.json:
            return (EXIT_MEDIA if r.missing else EXIT_OK), json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
        lines = [f"# {plural(len(notes), 'note')}, {plural(summary['cards'], 'card')} (dry run; nothing written)"]
        for d, i in ids["decks"].items():
            lines.append(f"deck {safe(d)}: id {i}")
        for m, i in ids["note_types"].items():
            lines.append(f"note type {m}: id {i}")
        lines.append("")
        for i, n in enumerate(notes, 1):
            lines.append(f"{i}. [{n['kind']}] {safe(strip_html(n['fields'][0]))[:120]}")
            if n["kind"] != "cloze":
                lines.append(f"   → {safe(strip_html(n['fields'][1]))[:120]}")
            if n["tags"] or n["deck"] != default_deck:
                lines.append("   " + " · ".join(x for x in (n["deck"] if n["deck"] != default_deck else "",
                                                         " ".join(n["tags"])) if x))
        for f, w in r.missing:
            lines.append(f"missing media: {safe(f)} ({safe(w)})")
        return (EXIT_MEDIA if r.missing else EXIT_OK), "\n".join(lines) + "\n"
    if r.missing:
        f, w = r.missing[0]
        raise Failure(EXIT_MEDIA, f"media file {f!r} not found ({w})" + (f" and {len(r.missing) - 1} more" if len(r.missing) > 1 else ""),
                      "pass --media DIR with the folder that holds it, or fix the reference")
    out = Path(args.output).expanduser()
    if out.suffix.lower() != ".apkg":
        raise Failure(EXIT_BAD_INPUT, f"-o {args.output}: the file name must end in .apkg", "e.g. -o deck.apkg")
    try:
        write_package(notes, r.media, out, args.seed)
        check = inspect_package(out)
    except Failure:
        raise
    except Exception as e:  # noqa: BLE001 - disk full, permissions, genanki failures
        raise Failure(EXIT_WRITE, f"cannot write {out}: {e}", "check the folder and free space") from None
    if check["notes"] != len(notes):
        raise Failure(EXIT_WRITE, f"{out} holds {check['notes']} notes, expected {len(notes)}", "a bug; report it")
    summary.update(file=str(out), cards=check["cards"])
    if args.json:
        return EXIT_OK, json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    lines = [f"{out}: {plural(len(notes), 'note')}, {plural(check['cards'], 'card')}"
             + (f", {plural(len(r.media), 'media file')}" if r.media else "")
             + (f" ({summary['skipped']} skipped)" if summary["skipped"] else "")]
    lines += [f"deck {safe(d)}: id {i}" for d, i in ids["decks"].items()]
    lines += [f"note type {m}: id {i}" for m, i in ids["note_types"].items()]
    lines.append("Import it in Anki with File → Import (AnkiDroid: Import). "
                 "Rebuilding and importing again updates these cards.")
    return EXIT_OK, "\n".join(lines) + "\n"


def cmd_inspect(args: argparse.Namespace, note) -> tuple[int, str]:
    info = inspect_package(Path(args.file).expanduser())
    if args.json:
        return EXIT_OK, json.dumps(info, ensure_ascii=False, indent=2) + "\n"
    lines = [f"# {safe(info['file'])}: {info['notes']} notes, {info['cards']} cards"]
    lines += [f"deck {safe(d['name'])} (id {d['id']}): {d['cards']} cards" for d in info["decks"]]
    lines += [f"note type {safe(m['name'])} (id {m['id']}, {m['kind']}): {m['notes']} notes" for m in info["note_types"]]
    if info["media"]:
        lines.append("media: " + safe(", ".join(info["media"][:20])) + (" …" if len(info["media"]) > 20 else ""))
    for s in info["sample"]:
        lines.append(f"- {safe(strip_html(s['fields'][0]))[:100]}")
    return EXIT_OK, "\n".join(lines) + "\n"


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="anki_deck.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="build an .apkg from Markdown, CSV/TSV or JSON cards")
    b.add_argument("inputs", nargs="+", metavar="INPUT", help="card files (.md, .csv, .tsv, .json), or - for stdin")
    b.add_argument("-o", "--output", metavar="FILE.apkg", help="the deck file to write (required unless --dry-run)")
    b.add_argument("--deck", help='deck name; "Parent::Child" for a subdeck (default: the H1 title, the JSON '
                                  '"deck", or the file name)')
    b.add_argument("--type", choices=TYPES, default="basic",
                   help="note type for question/answer cards (default basic; cards with {{c1::...}} and no answer "
                        "are cloze anyway)")
    b.add_argument("--tags", help="tags added to every card, comma- or space-separated")
    b.add_argument("--media", metavar="DIR", help="folder with the images and audio the cards reference "
                                                  "(default: next to the input file)")
    b.add_argument("--seed", default="", help="mixed into every id: a different seed makes a separate deck and note "
                                              "types (default none)")
    b.add_argument("--format", dest="format_in", choices=["md", "csv", "tsv", "json"],
                   help="input format (default: from the file extension)")
    b.add_argument("--html", action="store_true", help="fields are HTML already: no Markdown conversion")
    b.add_argument("--dry-run", action="store_true", help="show the parsed cards and ids; write nothing")
    b.add_argument("--json", action="store_true", help="print the result as JSON")
    b.add_argument("-q", "--quiet", action="store_true", help="no notes on stderr")
    i = sub.add_parser("inspect", help="list what an .apkg contains")
    i.add_argument("file")
    i.add_argument("--json", action="store_true", help="print the result as JSON")
    i.add_argument("-q", "--quiet", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = make_parser().parse_args(argv)
    note = (lambda m: None) if args.quiet else (lambda m: sys.stderr.write(f"note: {safe(m)}\n"))

    def fail(code: int, message: str, hint: str = "") -> int:
        sys.stderr.write(f"error: {safe(message)}" + (f" — {safe(hint)}" if hint else "") + "\n")
        return code

    if args.command == "build":
        if not args.dry_run and not args.output:
            return fail(EXIT_BAD_INPUT, "-o FILE.apkg is required", "or use --dry-run to only look at the cards")
        if args.inputs.count("-") > 1:
            return fail(EXIT_BAD_INPUT, "stdin (-) can be read only once")
    try:
        code, text = (cmd_build if args.command == "build" else cmd_inspect)(args, note)
    except Failure as e:
        return fail(e.code, str(e), e.hint)
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except BrokenPipeError:
        with contextlib.suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("error: interrupted\n")
        sys.exit(130)
    except Exception as e:  # noqa: BLE001 - a crash must not look like "no cards"
        sys.stderr.write(f"error: unexpected {type(e).__name__}: {e} — a bug; report it with the command line\n")
        sys.exit(EXIT_WRITE)
