#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["bibtexparser>=2.0.0b7,<3", "pypdf>=6.1,<7"]
# ///
"""Verify references against free scholarly sources: every identifier given (DOI via doi.org, arXiv, PMID) is
resolved, a title (or the whole reference string) is searched in Crossref, Europe PMC and arXiv, and the record
found is compared strictly: same title words, same first author and no unknown co-authors, year within one,
same venue. Verdict per entry: verified, mismatch (which fields differ), not_found, unverifiable or unparsed.

    uv run cite_check.py refs.bib                          # a BibTeX file
    uv run cite_check.py references.md --email you@uni.edu # Markdown/plain text; email adds open-access links
    uv run cite_check.py paper.pdf --only issues           # the bibliography of a PDF, problems only
    uv run cite_check.py refs.bib -f bibtex -o checked.bib # BibTeX with cite_check fields added
    uv run cite_check.py --doi 10.1038/nature14539         # does this DOI exist, and what is it?

Exit codes:
    0 every reference verified · 1 some are not verified · 2 bad input · 4 rate-limited (results so far are
    printed, and saved with --resume) · 6 network error or a source down or blocked · 7 the input could not be
    parsed, or an unexpected failure · 130 interrupted
"""

import argparse
import contextlib
import hashlib
import html
import io
import json
import logging
import os
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

EXIT_OK, EXIT_ISSUES, EXIT_BAD_INPUT, EXIT_RATE, EXIT_NETWORK, EXIT_PARSE = 0, 1, 2, 4, 6, 7

try:
    import bibtexparser
    import pypdf
    from bibtexparser import middlewares as bibmw
except ImportError as e:
    sys.stderr.write(f"error: missing dependency {e.name}; run with `uv run cite_check.py ...`\n")
    sys.exit(EXIT_BAD_INPUT)

UA_BASE = "zedxter-skills-cite-check/1.0 (+https://github.com/zedxter/skills{contact})"
DOI_ORG = "https://doi.org"
CROSSREF = "https://api.crossref.org"
ARXIV = "https://export.arxiv.org/api/query"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
UNPAYWALL = "https://api.unpaywall.org/v2"
OPENALEX = "https://api.openalex.org/works"
S2 = "https://api.semanticscholar.org/graph/v1/paper/search"
# Minimum seconds between requests per host within a run: arXiv asks for one request every 3 s; Crossref's
# polite pool (with mailto) allows a few per second; NCBI allows 3 per second without a key.
INTERVAL = {"doi.org": 0.2, "api.crossref.org": 0.35, "export.arxiv.org": 3.1, "eutils.ncbi.nlm.nih.gov": 0.34,
            "www.ebi.ac.uk": 0.2, "api.unpaywall.org": 0.2, "api.openalex.org": 0.2, "api.semanticscholar.org": 1.1}
TITLE_CLOSE = 0.75     # a record this similar (0–1) to the cited title is "the same work cited differently"
MAX_REF_CHARS = 2000
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"{}]+)", re.I)
ARXIV_RE = re.compile(r"(?:arxiv(?:\.org/(?:abs|pdf)/|[:\s]+))((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?)",
                      re.I)
PMID_RE = re.compile(r"\bPMID:?\s*(\d{1,9})\b", re.I)
YEAR_RE = re.compile(r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)")
STOP = {"a", "an", "the", "of", "for", "and", "in", "on", "to", "with", "at", "by", "from", "via", "into"}
PARTICLES = {"van", "der", "den", "de", "la", "von", "di", "da", "du", "del", "dos", "des", "ten", "ter", "al", "el",
             "bin", "ibn", "st", "zu"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
VENUE_GENERIC = {"proceedings", "proc", "conference", "conf", "annual", "international", "intl", "ieee", "acm", "cvf",
                 "symposium", "workshop", "meeting", "th", "st", "nd", "rd"}


class Failure(Exception):
    def __init__(self, code: int, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.hint = code, hint


# ------------------------------------------------------------------ text helpers

def norm(s: str | None) -> str:
    """Comparable text: lower case, accents, LaTeX commands, braces and markup gone, punctuation as spaces.

    >>> norm("Déjà {V}u: the \\\\textit{Return} -- of   ‘Things’ <i>here</i> p < 0.05")
    'deja vu the return of things here p 0 05'
    """
    s = re.sub(r"\\[a-zA-Z]+\s*", " ", s or "").replace("{", "").replace("}", "")
    s = re.sub(r"</?[a-zA-Z][\w:-]*(?:\s[^<>]*)?/?>", " ", html.unescape(s))  # tags, not "p < 0.05"
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[\W_]", " ", s.casefold())
    return " ".join(s.split())


def content_words(s: str) -> list[str]:
    return [w for w in norm(s).split() if w not in STOP]


SUBTITLE_SPLIT = re.compile(r"\s*[:?!]\s+|\.\s+|\s+[-–—]+\s+")


def main_title(s: str) -> str:
    """The part of a title before its subtitle separator."""
    return SUBTITLE_SPLIT.split(s.strip(), maxsplit=1)[0]


def similarity(a: str | None, b: str | None) -> float:
    x, y = norm(a), norm(b)
    return SequenceMatcher(None, x, y).ratio() if x and y else 0.0


def same_title(cited: str | None, found: str | None) -> bool:
    """Whether two titles are the same title. Allowed differences: case, accents, punctuation, hyphenation,
    articles and prepositions, and a subtitle that one side omits when the main title has 3+ content words.

    >>> same_title("Deep Learning.", "Deep learning"), same_title("Pre-training of deep transformers", "Pretraining of Deep Transformers")
    (True, True)
    >>> same_title("Deep learning for protein structure prediction", "Deep learning")
    False
    >>> same_title("Deep residual learning for video recognition", "Deep Residual Learning for Image Recognition")
    False
    >>> same_title("BERT: Pre-training of deep bidirectional transformers", "BERT")
    False
    >>> same_title("Attention is all you need", "Attention Is All You Need: a transformer")
    True
    """
    a, b = norm(cited), norm(found)
    if not a or not b:
        return False
    if a == b or a.replace(" ", "") == b.replace(" ", "") or content_words(a) == content_words(b):
        return True
    for x, y in ((cited or "", found or ""), (found or "", cited or "")):
        mx = main_title(x)
        if mx != x.strip() and len(content_words(mx)) >= 3 and content_words(mx) == content_words(y):
            return True
    return False


def title_in_text(found: str, raw: str) -> tuple[bool, float]:
    """For a reference that is only a string: does the found title stand in it whole (followed by punctuation, not
    by more title words)? Returns (same, similarity of the closest stretch of the string).

    >>> title_in_text("Deep learning", "LeCun Y, Bengio Y. Deep learning. Nature. 2015;521:436.")[0]
    True
    >>> title_in_text("Deep learning", "Smith J. Deep learning approaches for quantum chemistry. Nature 2015.")[0]
    False
    """
    hay = unicodedata.normalize("NFKD", raw)
    hay = "".join(c for c in hay if not unicodedata.combining(c)).casefold()
    for t in (found, main_title(found)):
        tokens = norm(t).split()
        if not tokens or (t != found and len(content_words(t)) < 3):
            continue
        pat = r"(?<![^\W_])" + r"[\W_]+".join(re.escape(w) for w in tokens) + r"(?![^\W_])"
        for m in re.finditer(pat, hay):
            rest = hay[m.end():].lstrip()
            if not rest or rest[0] in ".,;?!\"”»)]’'" or rest[0].isdigit():
                return True, 1.0
            if rest[0] == ":" and len(content_words(t)) >= 3:  # the citation adds a subtitle the record lacks
                return True, 1.0
    words, n = norm(raw).split(), len(norm(found).split())
    best = 0.0
    for i in range(max(1, len(words) - n + 1)):
        best = max(best, SequenceMatcher(None, " ".join(words[i:i + n + 1]), norm(found)).ratio())
    return False, best


def name_keys(name: str, given_family: bool = False) -> set[str]:
    """Family-name tokens of an author written as 'Family, Given', 'Given Family', or 'Family GE' (PubMed).

    >>> sorted(name_keys("LeCun, Yann")), sorted(name_keys("Kaiming He")), sorted(name_keys("Hinton GE"))
    (['lecun'], ['he'], ['hinton'])
    >>> sorted(name_keys("de Azevedo WF Jr")), sorted(name_keys("Bitencourt-Ferreira G")), sorted(name_keys("Quoc Le"))
    (['azevedo'], ['bitencourt', 'ferreira'], ['le'])
    """
    name = re.sub(r"[{}]", "", name).strip()
    if "," in name:
        parts = [p.strip() for p in name.split(",")]
        if len(parts) >= 2 and norm(parts[1]) in SUFFIXES:  # "Smith, Jr., John"
            parts.pop(1)
        tokens = re.findall(r"[^\s.]+", parts[0])
    else:
        tokens = [t for t in re.findall(r"[^\s]+", name) if norm(t) not in SUFFIXES]
        if not tokens:
            return set()
        last = tokens[-1].rstrip(".,")
        if not given_family and len(tokens) > 1 and (re.fullmatch(r"[A-Z]{1,3}", last) or tokens[-1].endswith(".")):
            tokens = tokens[:-1]  # "Hinton GE", "Bengio Y."
            while len(tokens) > 1 and re.fullmatch(r"[A-Z]\.?", tokens[-1]):
                tokens = tokens[:-1]
        else:
            fam = [tokens[-1]]
            i = len(tokens) - 2
            while i >= 0 and norm(tokens[i]) in PARTICLES:
                fam.insert(0, tokens[i])
                i -= 1
            tokens = fam
    keys = set(norm(" ".join(tokens)).split())
    return (keys - PARTICLES) or keys


def same_person(a: str, b: str, b_given_family: bool = False) -> bool:
    return bool(name_keys(a) & name_keys(b, b_given_family))


VENUE_STOP = {"of", "the", "and", "on", "in", "for", "an", "de", "la", "et", "und", "fur"}


def venue_tokens(v: str) -> list[str]:
    v = re.sub(r"\([^)]*\)", " ", v)  # acronyms in parentheses: "(CVPR)"
    return [w for w in norm(v).split() if w not in VENUE_STOP and w not in VENUE_GENERIC and not w.isdigit()]


def acronym(v: str) -> bool:
    v = v.strip()
    return bool(re.fullmatch(r"[A-Za-z]{2,10}", v)) and any(c.isupper() for c in v[1:])


def venue_match(a: str, b: str) -> bool | None:
    """Same journal or conference? None when one side is a bare acronym that cannot be compared.

    >>> venue_match("Proc. Natl. Acad. Sci. U.S.A.", "Proceedings of the National Academy of Sciences of the United States of America")
    True
    >>> venue_match("Nature", "Nature Communications"), venue_match("Physical Review A", "Physical Review B")
    (False, False)
    >>> venue_match("Proceedings of the IEEE Conference on Computer Vision and Pattern Recognition", "2016 IEEE Conference on Computer Vision and Pattern Recognition (CVPR)")
    True
    >>> venue_match("NeurIPS", "Advances in Neural Information Processing Systems") is None
    True
    """
    if acronym(a) or acronym(b):
        return None
    x, y = venue_tokens(a), venue_tokens(b)
    if not x or not y:
        return None
    if x == y:
        return True
    if len(x) != len(y):
        return False

    def abbreviates(s: str, w: str) -> bool:  # "natl" for "national": same first letter, letters in order
        if s[0] != w[0]:
            return False
        it = iter(w)
        return all(c in it for c in s)
    return all(abbreviates(p, q) or abbreviates(q, p) for p, q in zip(x, y))


# ------------------------------------------------------------------ input

class Ref(dict):
    """One reference: key, kind (bib, text, flags), raw text, and known fields."""


def clean_doi(s: str) -> str:
    s = urllib.parse.unquote(s.strip())
    s = re.sub(r"(?i)^(?:https?://)?(?:www\.|dx\.)?doi\.org/|^doi:?\s*", "", s.strip())
    s = s.rstrip(".,;]}>'\"")
    while s.endswith(")") and s.count("(") < s.count(")"):
        s = s[:-1]
    return s


def join_broken_dois(text: str) -> str:
    return re.sub(r"(10\.\d{4,9}/\S*[./-])\s*\n\s*(?=\S)", r"\1", text)


def ids_from_text(text: str) -> dict[str, str]:
    out = {}
    text = join_broken_dois(text)
    m = DOI_RE.search(text)
    if m:
        out["doi"] = clean_doi(m.group(1))
    m = ARXIV_RE.search(text)
    if m:
        out["arxiv"] = re.sub(r"v\d+$", "", m.group(1))
    m = PMID_RE.search(text)
    if m:
        out["pmid"] = m.group(1)
    return out


def parse_bibtex(text: str, note) -> list[Ref]:
    logging.getLogger("bibtexparser").setLevel(logging.CRITICAL)
    try:
        lib = bibtexparser.parse_string(text, append_middleware=[bibmw.LatexDecodingMiddleware()])
        raw_lib = bibtexparser.parse_string(text)  # identifiers must not be LaTeX-decoded ("--", "~", "%")
    except Exception as e:  # noqa: BLE001 - the parser raises various errors on broken input
        raise Failure(EXIT_PARSE, f"the BibTeX could not be parsed: {e}") from None
    raw_fields: dict[str, dict[str, str]] = {}
    for e in raw_lib.entries:
        raw_fields.setdefault(e.key, {k.lower(): str(v.value).strip() for k, v in e.fields_dict.items()})
    refs, seen = [], set()
    for e in lib.entries:
        f = {k.lower(): str(v.value).strip() for k, v in e.fields_dict.items()}
        rf = raw_fields.get(e.key, f)
        authors = [a.strip() for a in re.split(r"\s+and\s+", f.get("author", "")) if a.strip() and a.strip() != "others"]
        venue = f.get("journal") or f.get("booktitle") or f.get("journaltitle") or None
        r = Ref(key=e.key, kind="bib", type=e.entry_type.lower(), raw="", title=f.get("title") or None,
                authors=authors, year=(YEAR_RE.search(f.get("year", "") or f.get("date", "")) or [None, None])[1],
                venue=venue)
        if e.key in seen:
            r["parse_error"] = f"duplicate key {e.key}"
        seen.add(e.key)
        if rf.get("doi"):
            r["doi"] = clean_doi(rf["doi"])
        eprint = re.sub(r"(?i)^arxiv:\s*", "", rf.get("eprint", "")).strip()
        if eprint and ("arxiv" in (rf.get("archiveprefix", "") + rf.get("eprinttype", "")).lower()
                       or re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", eprint)):
            r["arxiv"] = re.sub(r"v\d+$", "", eprint)
        if re.fullmatch(r"\d{1,9}", rf.get("pmid", "")):
            r["pmid"] = rf["pmid"]
        for k, v in ids_from_text(" ".join(rf.get(k, "") for k in ("url", "note", "howpublished", "journal"))).items():
            r.setdefault(k, v)
        if venue and re.search(r"(?i)\barxiv\b", venue):
            r["venue"] = None  # "arXiv preprint arXiv:…" is not a venue to compare
        refs.append(r)
    failed = list(getattr(lib, "failed_blocks", []))
    for b in failed:
        start = getattr(b, "start_line", None)
        refs.append(Ref(key=f"line-{start + 1 if isinstance(start, int) else '?'}", kind="bib",
                        raw=getattr(b, "raw", "")[:300], parse_error="this BibTeX entry is malformed"))
    if not refs:
        raise Failure(EXIT_PARSE, "no BibTeX entries found", "is this a .bib file?")
    if failed:
        note(f"{len(failed)} BibTeX entr{'y' if len(failed) == 1 else 'ies'} could not be parsed; reported as unparsed")
    return refs


AUTHOR_START = re.compile(r"^[A-Z][\w'’\-]+(?:,\s*[A-Z]|\s+[A-Z]{1,3}[,.\s]|\s+[A-Z]\.)")


def split_references(text: str, note=lambda m: None) -> list[str]:
    """Reference strings from plain text or Markdown: numbered items ([1], 1., 1) in sequence), list items,
    paragraphs, or lines grouped by where an author list starts."""
    text = join_broken_dois(text.replace("\r", ""))
    marks, expected = [], 1
    for m in re.finditer(r"(?m)^\s*(?:\[(\d{1,4})\]|(\d{1,4})[.)])\s+", text):
        if int(m.group(1) or m.group(2)) == expected:  # "521. 436-444" on a continuation line is not item 521
            marks.append(m)
            expected += 1
    if len(marks) >= 2:
        items = [text[m.end():(marks[i + 1].start() if i + 1 < len(marks) else len(text))] for i, m in enumerate(marks)]
    elif re.search(r"(?m)^\s*[-*+]\s+\S", text):
        items = re.split(r"(?m)^\s*[-*+]\s+", text)[1:]
    else:
        paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        if len(paras) > 1:
            items = paras
        else:  # one block: a new reference starts where a line begins with an author ("Smith, J." / "Smith J")
            items, cur = [], []
            for line in text.splitlines():
                if cur and AUTHOR_START.match(line.strip()) and YEAR_RE.search(" ".join(cur)):
                    items.append(" ".join(cur))
                    cur = []
                if line.strip():
                    cur.append(line.strip())
            if cur:
                items.append(" ".join(cur))
    out, dropped = [], 0
    for it in items:
        s = " ".join(it.split())[:MAX_REF_CHARS]
        if len(s) >= 20 and (YEAR_RE.search(s) or DOI_RE.search(s) or ARXIV_RE.search(s)):
            out.append(s)
        elif len(s) >= 20:
            dropped += 1
    if dropped:
        note(f"{dropped} text block(s) without a year, DOI or arXiv id were not treated as references")
    return out


def refs_from_text(text: str, note=lambda m: None) -> list[Ref]:
    refs = []
    for i, s in enumerate(split_references(text, note), 1):
        r = Ref(key=f"ref{i}", kind="text", raw=s, title=None, authors=[], year=(YEAR_RE.search(s) or [None, None])[1],
                venue=None)
        r.update(ids_from_text(s))
        m = re.search(r"[\"“«]([^\"“”«»]{10,300})[\"”»]", s)  # a quoted title
        if m:
            r["title"] = m.group(1).strip(" .,")
        refs.append(r)
    return refs


def guess_title(raw: str) -> str:
    """The most title-like sentence of a reference string, for sources that need a title rather than a citation."""
    best, best_len = raw, 0
    for s in (s.strip() for s in re.split(r"(?<=[.?!])\s+", raw) if s.strip()):
        words = s.split()
        if len(words) < 3:
            continue
        if len(re.findall(r"\b[A-Z][a-z'’\-]+,?\s+[A-Z]{1,3}\b", s)) >= 2:  # an author list
            continue
        if sum(c.isdigit() for c in s) > len(s) * 0.2:
            continue
        if len(words) > best_len:
            best, best_len = s.rstrip("."), len(words)
    return best


def bibliography_of_pdf(data: bytes) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:300])
    except Exception as e:  # noqa: BLE001 - a broken PDF can fail anywhere inside pypdf
        raise Failure(EXIT_PARSE, f"the PDF could not be read: {e}") from None
    heads = list(re.finditer(r"(?im)^\s*(?:\d+\.?\s*)?(references|bibliography|works cited|literature cited|"
                             r"список литературы|литература|библиография)\s*$", text))
    if not heads:
        raise Failure(EXIT_PARSE, "no References or Bibliography section found in the PDF",
                      "copy the reference list into a text file and check that instead")
    late = [h for h in heads if h.start() >= len(text) * 0.3] or heads
    start = late[0].end()  # the main list: the first heading in the later part, not an appendix's own list
    end = re.search(r"(?im)^\s*(?:appendix|appendices|supplementary (?:material|information)|приложение)\b", text[start:])
    return text[start:start + end.start()] if end else text[start:]


def read_refs(args: argparse.Namespace, note) -> tuple[list[Ref], str | None]:
    """References and, for a .bib input, its decoded text (for -f bibtex)."""
    if args.doi or args.title:
        r = Ref(key="ref1", kind="flags", raw="", title=args.title.strip() if args.title else None,
                authors=[a.strip() for a in (args.author or "").split(";") if a.strip()], year=args.year, venue=args.venue)
        if args.doi:
            r["doi"] = clean_doi(args.doi)
        return [r], None
    if not args.input:
        raise Failure(EXIT_BAD_INPUT, "nothing to check", "pass a .bib, .md, .txt or .pdf file, -, or --doi/--title")
    if args.input == "-":
        data, suffix = sys.stdin.buffer.read(50_000_001), ""
    else:
        p = Path(args.input).expanduser()
        if not p.is_file():
            raise Failure(EXIT_BAD_INPUT, f"input not found: {args.input}")
        if p.stat().st_size > 50_000_000:
            raise Failure(EXIT_BAD_INPUT, "input larger than 50 MB")
        data, suffix = p.read_bytes(), p.suffix.lower()
    if len(data) > 50_000_000:
        raise Failure(EXIT_BAD_INPUT, "input larger than 50 MB")
    if suffix == ".pdf" or data[:5] == b"%PDF-":
        refs = refs_from_text(bibliography_of_pdf(data), note)
        bib_text = None
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
            note("the input is not UTF-8; read as Latin-1")
        if suffix == ".bib" or re.search(r"(?m)^\s*@\w+\s*\{", text):
            return parse_bibtex(text, note), text
        refs, bib_text = refs_from_text(text, note), None
    if not refs:
        raise Failure(EXIT_PARSE, "no references found in the input",
                      "put one reference per line, per numbered item ([1], 1.) or per paragraph")
    return refs, bib_text


# ------------------------------------------------------------------ network

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_a, **_k):  # keyed requests never follow a redirect
        return None


NO_REDIRECT = urllib.request.build_opener(NoRedirect)


class Net:
    def __init__(self, args: argparse.Namespace, note) -> None:
        self.timeout, self.note, self.email = args.timeout, note, args.email
        self.ua = UA_BASE.format(contact=f"; mailto:{self.email}" if self.email else "")
        self.last: dict[str, float] = {}

    def get(self, url: str, accept: str = "application/json", headers: dict[str, str] | None = None,
            follow: bool = True) -> tuple[int, bytes]:
        """(status, body). 404/400/406 come back; 429 is retried once then exit 4; 5xx is retried once then exit 6;
        401/403 (blocked) are exit 6 at once."""
        host = urllib.parse.urlsplit(url).hostname or ""
        for attempt in (1, 2):
            wait = self.last.get(host, 0) + INTERVAL.get(host, 0.5) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last[host] = time.monotonic()
            req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept": accept, **(headers or {})})
            try:
                opener = urllib.request.urlopen if follow else NO_REDIRECT.open
                with opener(req, timeout=self.timeout) as resp:
                    return resp.status, resp.read(5_000_001)
            except urllib.error.HTTPError as e:
                if e.code == 429 or (e.code == 503 and host == "export.arxiv.org"):
                    ra = (e.headers or {}).get("Retry-After", "")
                    secs = int(ra) if ra.isdigit() else 15
                    if attempt == 1 and secs <= 60:
                        self.note(f"{host} rate-limited; waiting {secs} s once")
                        time.sleep(secs)
                        continue
                    raise Failure(EXIT_RATE, f"{host} rate limit (HTTP {e.code})",
                                  "wait a few minutes, then run again with --resume FILE") from None
                if e.code >= 500:
                    if attempt == 1:
                        time.sleep(3)
                        continue
                    raise Failure(EXIT_NETWORK, f"{host} is not answering (HTTP {e.code})",
                                  "the source is down; run again later with --resume FILE") from None
                if e.code in (401, 403):
                    raise Failure(EXIT_NETWORK, f"{host} refused the request (HTTP {e.code})",
                                  "the source blocks this network or key; run again later with --resume FILE") from None
                body = b""
                with contextlib.suppress(Exception):
                    body = e.read(200_000)
                return e.code, body
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                reason = getattr(e, "reason", e)
                if "CERTIFICATE_VERIFY_FAILED" in str(reason):
                    raise Failure(EXIT_NETWORK, f"TLS certificate verification failed ({reason})",
                                  "on a python.org macOS build run 'Install Certificates.command', or use uv's "
                                  "managed Python") from None
                if attempt == 2:
                    raise Failure(EXIT_NETWORK, f"{host}: {reason}",
                                  "check the connection (HTTPS_PROXY is honoured); run again with --resume") from None
                time.sleep(2)
        raise AssertionError("unreachable")

    def json(self, url: str, **kw: Any) -> Any:
        status, body = self.get(url, **kw)
        if status != 200:
            return None
        with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError):
            return json.loads(body)
        return None


# ------------------------------------------------------------------ sources

def record(source: str, **kw: Any) -> dict[str, Any]:
    r = {"source": source, "doi": None, "title": None, "authors": [], "year": None, "venue": None, "type": None,
         "url": None, "arxiv": None, "pmid": None}
    r.update({k: v for k, v in kw.items() if v not in (None, "", [])})
    return r


def csl_year(issued: Any) -> str | None:
    with contextlib.suppress(TypeError, IndexError, KeyError):
        y = issued["date-parts"][0][0]
        return str(y) if y else None
    return None


def csl_record(d: dict[str, Any], source: str) -> dict[str, Any]:
    title = d.get("title")
    title = title[0] if isinstance(title, list) and title else title
    venue = d.get("container-title")
    venue = venue[0] if isinstance(venue, list) and venue else venue
    authors = [f"{a.get('family', '')}, {a.get('given', '')}".strip(", ") if a.get("family") else a.get("literal", "")
               for a in d.get("author") or [] if isinstance(a, dict)]
    year = None
    for k in ("issued", "published-print", "published-online", "published", "created"):
        year = csl_year(d.get(k))
        if year:
            break
    doi = d.get("DOI")
    return record(source, doi=doi, title=title if isinstance(title, str) else None, authors=authors, year=year,
                  venue=venue if isinstance(venue, str) else None, type=d.get("type"),
                  url=f"https://doi.org/{doi}" if doi else d.get("URL"))


def by_doi(net: Net, doi: str) -> tuple[str, dict[str, Any] | None]:
    """('ok', record) | ('missing', None): not registered | ('nometa', None): registered, no metadata served."""
    q = urllib.parse.quote(doi, safe="/:;()")
    status, body = net.get(f"{DOI_ORG}/{q}", accept="application/vnd.citationstyles.csl+json")
    if status == 200:
        with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            rec = csl_record(json.loads(body), "doi.org")
            if rec.get("title"):
                return "ok", rec
    if status == 404:
        return "missing", None
    s2, _ = net.get(f"{DOI_ORG}/api/handles/{q}")
    return ("missing", None) if s2 == 404 else ("nometa", None)


def crossref_search(net: Net, query: str) -> list[dict[str, Any]]:
    params = {"query.bibliographic": query[:600], "rows": 5,
              "select": "DOI,title,author,issued,published-print,published-online,container-title,type,publisher"}
    if net.email:
        params["mailto"] = net.email
    d = net.json(f"{CROSSREF}/works?{urllib.parse.urlencode(params)}")
    items = (d or {}).get("message", {}).get("items", []) if isinstance(d, dict) else []
    return [csl_record(it, "crossref") for it in items if isinstance(it, dict)]


def arxiv_entries(net: Net, query: str) -> list[dict[str, Any]]:
    status, body = net.get(f"{ARXIV}?{query}", accept="application/atom+xml")
    if status != 200:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    out = []
    for e in root.findall("a:entry", ns):
        aid = re.sub(r"v\d+$", "", (e.findtext("a:id", "", ns) or "").rsplit("/abs/", 1)[-1])
        title = " ".join((e.findtext("a:title", "", ns) or "").split())
        if not title or title == "Error":
            continue
        out.append(record("arxiv", arxiv=aid, title=title, doi=e.findtext("arxiv:doi", None, ns),
                          authors=[" ".join((a.findtext("a:name", "", ns) or "").split()) for a in e.findall("a:author", ns)],
                          year=(e.findtext("a:published", "", ns) or "")[:4] or None, venue="arXiv",
                          url=f"https://arxiv.org/abs/{aid}", type="preprint"))
    return out


def by_arxiv(net: Net, aid: str) -> dict[str, Any] | None:
    found = arxiv_entries(net, urllib.parse.urlencode({"id_list": aid}))
    return found[0] if found else None


def arxiv_search(net: Net, title: str) -> list[dict[str, Any]]:
    words = " AND ".join(f'ti:"{w}"' for w in norm(title).split()[:12] if len(w) > 2)
    return arxiv_entries(net, urllib.parse.urlencode({"search_query": words, "max_results": 5})) if words else []


def by_pmid(net: Net, pmid: str) -> dict[str, Any] | None:
    params = {"db": "pubmed", "retmode": "json", "tool": "zedxter-skills", "id": pmid,
              **({"email": net.email} if net.email else {})}
    d = net.json(f"{EUTILS}/esummary.fcgi?{urllib.parse.urlencode(params)}")
    try:
        r = d["result"][pmid]
    except (TypeError, KeyError):
        return None
    if not isinstance(r, dict) or r.get("error"):
        return None
    doi = next((a.get("value") for a in r.get("articleids", []) if a.get("idtype") == "doi"), None)
    return record("pubmed", pmid=pmid, doi=doi, title=r.get("title", "").rstrip("."), venue=r.get("fulljournalname"),
                  authors=[a.get("name", "") for a in r.get("authors", [])],
                  year=(YEAR_RE.search(r.get("pubdate", "")) or [None, None])[1],
                  url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", type="journal-article")


def europepmc_search(net: Net, title: str) -> list[dict[str, Any]]:
    q = 'TITLE:"' + re.sub(r'["\\]', " ", title)[:300] + '"'
    d = net.json(f"{EUROPEPMC}?{urllib.parse.urlencode({'query': q, 'format': 'json', 'pageSize': 5, 'resultType': 'lite'})}")
    out = []
    for r in ((d or {}).get("resultList", {}).get("result", []) if isinstance(d, dict) else []):
        out.append(record("europepmc", title=(r.get("title") or "").rstrip("."), doi=r.get("doi"), pmid=r.get("pmid"),
                          year=r.get("pubYear"), venue=r.get("journalTitle"),
                          authors=[a.strip() for a in (r.get("authorString") or "").rstrip(".").split(",") if a.strip()],
                          url=f"https://europepmc.org/article/{r.get('source')}/{r.get('id')}", type=r.get("pubType")))
    return out


def openalex_search(net: Net, title: str) -> list[dict[str, Any]]:
    key = os.environ.get("OPENALEX_API_KEY")
    if not key:
        return []
    d = net.json(f"{OPENALEX}?{urllib.parse.urlencode({'search': title[:300], 'per-page': 5, 'api_key': key})}",
                 follow=False)
    out = []
    for w in ((d or {}).get("results", []) if isinstance(d, dict) else []):
        doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
        out.append(record("openalex", title=w.get("title"), doi=doi, year=str(w.get("publication_year") or "") or None,
                          authors=[(a.get("author") or {}).get("display_name", "") for a in w.get("authorships", [])],
                          venue=((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
                          url=w.get("id"), type=w.get("type")))
    return out


def s2_search(net: Net, title: str) -> list[dict[str, Any]]:
    key = os.environ.get("S2_API_KEY")
    if not key:
        return []
    params = {"query": title[:300], "limit": 5, "fields": "title,year,venue,authors,externalIds,url"}
    d = net.json(f"{S2}?{urllib.parse.urlencode(params)}", headers={"x-api-key": key}, follow=False)
    out = []
    for p in ((d or {}).get("data", []) if isinstance(d, dict) else []):
        ext = p.get("externalIds") or {}
        out.append(record("semanticscholar", title=p.get("title"), doi=ext.get("DOI"), arxiv=ext.get("ArXiv"),
                          year=str(p.get("year") or "") or None, venue=p.get("venue"), url=p.get("url"),
                          authors=[a.get("name", "") for a in p.get("authors") or []]))
    return out


def unpaywall(net: Net, doi: str) -> str | None:
    if not net.email:
        return None
    d = net.json(f"{UNPAYWALL}/{urllib.parse.quote(doi, safe='/')}?email={urllib.parse.quote(net.email)}")
    loc = (d or {}).get("best_oa_location") if isinstance(d, dict) else None
    if isinstance(loc, dict):
        return loc.get("url_for_pdf") or loc.get("url")
    return None


# ------------------------------------------------------------------ comparing

def has_title_text(ref: Ref) -> bool:
    return bool(ref.get("title")) or (ref.get("kind") == "text" and bool(ref.get("raw")))


def title_check(ref: Ref, rec: dict[str, Any]) -> tuple[bool | None, float]:
    """(same, similarity); same is None when the reference gives no title to compare."""
    if not rec.get("title"):
        return None, 0.0
    if ref.get("title"):
        return same_title(ref["title"], rec["title"]), similarity(ref["title"], rec["title"])
    if ref.get("kind") == "text" and ref.get("raw"):
        return title_in_text(rec["title"], ref["raw"])
    return None, 0.0


def rank(ref: Ref, rec: dict[str, Any]) -> float:
    same, sim = title_check(ref, rec)
    bonus = 0.0
    if rec.get("authors") and ref.get("authors") and same_person(ref["authors"][0], rec["authors"][0],
                                                                  rec.get("source") == "arxiv"):
        bonus += 0.05
    if ref.get("year") and rec.get("year") == ref.get("year"):
        bonus += 0.02
    return (1.0 if same else sim) + bonus


def compare(ref: Ref, rec: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Fields that differ between the reference and the record, and notes about accepted differences."""
    diffs, notes = [], []
    same, sim = title_check(ref, rec)
    if same is False:
        diffs.append({"field": "title", "given": ref.get("title") or ref.get("raw"), "found": rec.get("title"),
                      "similarity": round(sim, 2)})
    gf = rec.get("source") == "arxiv"
    found_authors = rec.get("authors") or []
    cited = [a for a in ref.get("authors") or [] if not re.fullmatch(r"(?i)others|et\.? al\.?", a.strip())]
    if found_authors and cited:
        unknown = [a for a in cited if not any(same_person(a, f, gf) for f in found_authors)]
        if not same_person(cited[0], found_authors[0], gf) or (unknown and len(found_authors) >= len(cited)):
            d = {"field": "authors", "given": "; ".join(cited[:4]), "found": "; ".join(found_authors[:4])}
            if unknown:
                d["not_in_record"] = unknown[:6]
            diffs.append(d)
    elif found_authors and ref.get("kind") == "text":
        keys = name_keys(found_authors[0], gf)
        if keys and not keys & set(norm(ref.get("raw", "")).split()):
            diffs.append({"field": "authors", "given": None, "found": "; ".join(found_authors[:4])})
    if ref.get("year") and rec.get("year") and ref["year"] != rec["year"]:
        if abs(int(ref["year"]) - int(rec["year"])) == 1:
            notes.append(f"year {ref['year']} vs {rec['year']}: one year apart (online-first vs print is common)")
        else:
            diffs.append({"field": "year", "given": ref["year"], "found": rec["year"]})
    if ref.get("venue") and rec.get("venue") and rec.get("source") != "arxiv":
        vm = venue_match(ref["venue"], rec["venue"])
        if vm is False:
            diffs.append({"field": "venue", "given": ref["venue"], "found": rec["venue"]})
        elif vm is None:
            notes.append(f"venue {ref['venue']!r} not compared with {rec['venue']!r}")
    return diffs, notes


def search(net: Net, ref: Ref) -> tuple[dict[str, Any] | None, list[str]]:
    """The best record across sources for the cited title or string, stopping at an exact title match."""
    title = ref.get("title")
    query = ref.get("raw") if ref.get("kind") == "text" else " ".join(
        x for x in (title, " ".join(ref.get("authors", [])[:2]), ref.get("venue") or "", ref.get("year") or "") if x)
    guess = title or guess_title(ref.get("raw", ""))
    sources = [("crossref", lambda: crossref_search(net, query)), ("europepmc", lambda: europepmc_search(net, guess)),
               ("arxiv", lambda: arxiv_search(net, guess))]
    if os.environ.get("OPENALEX_API_KEY"):
        sources.append(("openalex", lambda: openalex_search(net, guess)))
    if os.environ.get("S2_API_KEY"):
        sources.append(("semanticscholar", lambda: s2_search(net, guess)))
    top, top_rank, tried = None, 0.0, []
    for name, fn in sources:
        tried.append(name)
        for rec in fn():
            r = rank(ref, rec)
            if r > top_rank:
                top, top_rank = rec, r
        if top is not None and title_check(ref, top)[0]:
            break
    return top, tried


def resolve_ids(net: Net, ref: Ref, out: dict[str, Any]) -> tuple[list[dict[str, Any]], list[tuple[str, str]], bool]:
    """(records the identifiers resolve to, identifiers that do not exist, a DOI registered without metadata)."""
    recs, missing, nometa = [], [], False
    if ref.get("doi"):
        out["sources"].append("doi.org")
        state, rec = by_doi(net, ref["doi"])
        if state == "ok":
            recs.append(rec)
        elif state == "missing":
            missing.append(("doi", ref["doi"]))
            out["notes"].append(f"DOI {ref['doi']} is not registered")
        else:
            nometa = True
            out["notes"].append(f"DOI {ref['doi']} is registered, but its agency serves no metadata to compare")
    if ref.get("arxiv"):
        out["sources"].append("arxiv")
        rec = by_arxiv(net, ref["arxiv"])
        if rec:
            recs.append(rec)
        else:
            missing.append(("arxiv", ref["arxiv"]))
            out["notes"].append(f"arXiv {ref['arxiv']} does not exist")
    if ref.get("pmid"):
        out["sources"].append("pubmed")
        rec = by_pmid(net, ref["pmid"])
        if rec:
            recs.append(rec)
        else:
            missing.append(("pmid", ref["pmid"]))
            out["notes"].append(f"PMID {ref['pmid']} does not exist")
    return recs, missing, nometa


def sparse(rec: dict[str, Any]) -> bool:
    """Too little in a record to confirm anything: no authors and a title of fewer than 3 content words."""
    return not rec.get("authors") and len(content_words(rec.get("title") or "")) < 3


def id_field(rec: dict[str, Any]) -> str:
    return {"doi.org": "doi", "arxiv": "arxiv", "pubmed": "pmid"}.get(rec.get("source", ""), "doi")


def check_ref(net: Net, ref: Ref) -> dict[str, Any]:
    given = {k: ref.get(k) for k in ("title", "authors", "year", "venue", "doi", "arxiv", "pmid") if ref.get(k)}
    if ref.get("kind") == "text":
        given["raw"] = ref.get("raw")
    out: dict[str, Any] = {"key": ref.get("key"), "input": given, "verdict": None, "match": None, "mismatches": [],
                           "notes": [], "oa_url": None, "sources": []}
    if ref.get("parse_error"):
        out.update(verdict="unparsed", notes=[ref["parse_error"]], input={"raw": ref.get("raw")})
        return out
    recs, missing, nometa = resolve_ids(net, ref, out)
    id_diffs = [{"field": f, "given": v, "found": None, "reason": "does not exist"} for f, v in missing]
    rec = recs[0] if recs else None
    if rec is not None and has_title_text(ref):
        same, sim = title_check(ref, rec)
        if same is False and sim < TITLE_CLOSE:
            # The identifier resolves, but to a different work: the strongest sign of a made-up reference.
            field = id_field(rec)
            out["notes"].append(f"the {field} resolves to a different work: {rec['title']!r}")
            found, tried = search(net, ref)
            out["sources"] += tried
            diffs, notes = compare(ref, rec)
            out.update(match=rec, verdict="mismatch", notes=out["notes"] + notes,
                       mismatches=[{"field": field, "given": ref.get(field), "found": rec.get(field) or rec.get("doi"),
                                    "reason": "resolves to a different work"}] + diffs + id_diffs)
            if found and title_check(ref, found)[0]:
                out["suggested"] = found
                out["notes"].append(f"the cited title matches {found.get('doi') or found.get('url')}")
            return finish(net, out)
    for other in recs[1:]:  # several identifiers given: they must name the same work
        if rec and other.get("title") and not same_title(rec.get("title"), other["title"]):
            f = id_field(other)
            id_diffs.append({"field": f, "given": other.get(f), "found": other["title"],
                             "reason": "names a different work than the other identifier"})
    if rec is None:
        if not has_title_text(ref):
            out["verdict"] = "not_found" if missing else "unverifiable"
            out["mismatches"] = id_diffs
            if nometa:
                out["notes"].append("give the title to have it checked against the registries")
            return out
        rec, tried = search(net, ref)
        out["sources"] += tried
        same, sim = title_check(ref, rec) if rec else (False, 0.0)
        if rec is None or (not same and sim < TITLE_CLOSE):
            out["verdict"] = "unverifiable" if nometa else "not_found"
            out["mismatches"] = id_diffs
            if rec is not None and sim >= 0.5:
                out["notes"].append(f"closest record: {rec['title']!r} ({rec.get('doi') or rec.get('url')}, "
                                    f"title similarity {sim:.2f})")
            return out
    out["match"] = rec
    diffs, notes = compare(ref, rec)
    out["mismatches"], out["notes"] = diffs + id_diffs, out["notes"] + notes
    if out["mismatches"]:
        out["verdict"] = "mismatch"
    elif sparse(rec):
        out["verdict"] = "unverifiable"
        out["notes"].append("the record found has too little metadata (no authors, a very short title) to confirm")
    else:
        out["verdict"] = "verified"
        if not has_title_text(ref):
            out["notes"].append("only the identifier was checked: no title was given to compare")
    return finish(net, out)


def finish(net: Net, out: dict[str, Any]) -> dict[str, Any]:
    rec = out.get("match") or {}
    if rec.get("doi") and out["verdict"] in ("verified", "mismatch"):
        out["oa_url"] = unpaywall(net, rec["doi"])
    if not out["oa_url"] and rec.get("arxiv"):
        out["oa_url"] = f"https://arxiv.org/pdf/{rec['arxiv']}"
    return out


# ------------------------------------------------------------------ output

def fingerprint(ref: Ref, email: str | None) -> str:
    data = {k: ref.get(k) for k in sorted(ref)} | {"_oa": bool(email)}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


def summary(results: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in results:
        out[r["verdict"]] = out.get(r["verdict"], 0) + 1
    return out


def safe(s: Any) -> str:
    """One printable line: no control, C1 or bidi characters, whitespace collapsed (titles cannot fake a row)."""
    return " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", " ", str(s if s is not None else "")).split())


def table(shown: list[dict[str, Any]], results: list[dict[str, Any]]) -> str:
    lines = []
    for r in shown:
        m = r.get("match") or {}
        lines.append(f"{r['verdict'].upper():<12} {safe(r['key'])}")
        given = r["input"].get("title") or r["input"].get("raw") or ""
        if given:
            lines.append(f"  cited:   {safe(given)[:160]}")
        if m:
            who = ""
            if m.get("authors"):
                keys = sorted(name_keys(m["authors"][0], m.get("source") == "arxiv"))
                who = (" ".join(keys).title() if keys else safe(m["authors"][0])) + (" et al." if len(m["authors"]) > 1 else "")
            link = f"https://doi.org/{m['doi']}" if m.get("doi") else m.get("url") or ""
            lines.append(f"  found:   {safe(m.get('title'))[:160]}")
            lines.append(f"           {' · '.join(safe(x) for x in (who, m.get('venue'), m.get('year')) if x)}  {safe(link)}")
        for d in r["mismatches"]:
            extra = f" ({d['reason']})" if d.get("reason") else ""
            lines.append(f"  differs: {d['field']}: cited {safe(d.get('given'))[:80]} · found {safe(d.get('found'))[:80]}{extra}")
        for n in r["notes"]:
            lines.append(f"  note:    {safe(n)}")
        if r.get("suggested"):
            s = r["suggested"]
            lines.append(f"  suggest: {safe(s.get('title'))[:120]}  {safe(s.get('doi') or s.get('url'))}")
        if r.get("oa_url"):
            lines.append(f"  open:    {safe(r['oa_url'])}")
        lines.append("")
    s = summary(results)
    lines.append("summary: " + (", ".join(f"{v} {k}" for k, v in sorted(s.items())) or "nothing checked"))
    return "\n".join(lines) + "\n"


def bibtex_value(s: str) -> str:
    return "{" + " ".join(re.sub(r"[{}\\\x00-\x1f]", " ", str(s)).split()) + "}"


def annotate_bibtex(text: str, results: list[dict[str, Any]]) -> str:
    """The original text with cite_check fields added inside each checked entry (earlier ones replaced); nothing
    else changes. A key that occurs twice is annotated once."""
    text = re.sub(r"(?m)^[ \t]*cite_check\w*[ \t]*=[ \t]*\{[^{}\n]*\},[ \t]*\r?\n", "", text)  # our own lines only
    newline = "\r\n" if "\r\n" in text else "\n"
    by_key = {r["key"]: r for r in results}
    out, pos, done = [], 0, set()
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        key = m.group(2)
        r = by_key.get(key)
        if not r or key in done or m.group(1).lower() in ("string", "preamble", "comment"):
            continue
        done.add(key)
        fields = [f"  cite_check = {bibtex_value(r['verdict'])},"]
        if r["mismatches"]:
            fields.append("  cite_check_differs = " + bibtex_value("; ".join(
                f"{d['field']}: found {d.get('found')}" for d in r["mismatches"])) + ",")
        m_ = r.get("match") or {}
        if m_.get("doi") and r["verdict"] in ("verified", "mismatch"):
            fields.append(f"  cite_check_doi = {bibtex_value(m_['doi'])},")
        if r.get("oa_url"):
            fields.append(f"  cite_check_oa = {bibtex_value(r['oa_url'])},")
        after = text[m.end():]
        # Our fields always stand on lines of their own, so a later run can find and replace them exactly.
        out.append(text[pos:m.end()] + newline + newline.join(fields) + ("" if after.startswith(("\n", "\r\n")) else newline))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def load_resume(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if path.is_dir():
        raise Failure(EXIT_BAD_INPUT, f"--resume {path} is a folder")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Failure(EXIT_BAD_INPUT, f"--resume {path} is not a progress file this script wrote",
                      "pass a new file name, or delete it") from None
    if not isinstance(data, dict):
        raise Failure(EXIT_BAD_INPUT, f"--resume {path} is not a progress file this script wrote")
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("verdict") and "key" in v}


def save_resume(path: Path, cache: dict[str, Any], note) -> None:
    """Atomic: a crash mid-write leaves the previous file."""
    try:
        fd, tmp = tempfile.mkstemp(prefix=".cite-check-", dir=str(path.parent.resolve()))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as e:
        note(f"could not save progress to {path}: {e.strerror or e}")


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", nargs="?", help=".bib, .md, .txt or .pdf file, or - for stdin")
    p.add_argument("--doi", help="check one reference: its DOI (alone: does it exist, and what is it)")
    p.add_argument("--title", help="check one reference: its title")
    p.add_argument("--author", help="check one reference: authors, separated by ';'")
    p.add_argument("--year", help="check one reference: year")
    p.add_argument("--venue", help="check one reference: journal or conference")
    p.add_argument("--email", default=os.environ.get("CITE_CHECK_EMAIL"),
                   help="your email: Unpaywall open-access links and Crossref's polite pool; sent in the User-Agent "
                        "to every source (or CITE_CHECK_EMAIL)")
    p.add_argument("-f", "--format", choices=["table", "json", "bibtex"], default="table",
                   help="table (default), json, or bibtex (the input .bib with cite_check fields added)")
    p.add_argument("-o", "--output", metavar="FILE", help="write the report to FILE")
    p.add_argument("--resume", metavar="FILE", help="save results to FILE as they come and skip entries already in it")
    p.add_argument("--only", choices=["issues"], help="issues: show only entries that are not verified")
    p.add_argument("--timeout", type=float, default=30.0, help="network timeout in seconds (default 30)")
    p.add_argument("-q", "--quiet", action="store_true", help="no progress or notes on stderr")
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

    if not 1 <= args.timeout <= 300:
        return fail(EXIT_BAD_INPUT, "--timeout must be between 1 and 300 seconds")
    if args.email and not re.fullmatch(r"[\x21-\x7e]+@[\x21-\x7e]+\.[a-zA-Z]{2,}", args.email):
        return fail(EXIT_BAD_INPUT, f"--email {args.email!r} is not an ASCII email address")
    if args.year and not re.fullmatch(r"\d{4}", args.year):
        return fail(EXIT_BAD_INPUT, "--year must be four digits")
    if args.format == "bibtex" and (not args.input or args.input == "-" or not args.input.lower().endswith(".bib")):
        return fail(EXIT_BAD_INPUT, "-f bibtex needs a .bib file as input")
    try:
        refs, bib_text = read_refs(args, note)
        cache = load_resume(Path(args.resume).expanduser()) if args.resume else {}
    except Failure as e:
        return fail(e.code, str(e), e.hint)
    net = Net(args, note)
    if not args.email:
        note("no --email: open-access links come only from arXiv, and Crossref is used without its polite pool")
    results, stopped = [], None
    started = time.monotonic()
    for i, ref in enumerate(refs, 1):
        fp = fingerprint(ref, args.email)
        if fp in cache:
            results.append(cache[fp])
            continue
        try:
            r = check_ref(net, ref)
        except Failure as e:
            stopped = e
            break
        except KeyboardInterrupt:
            stopped = Failure(130, "interrupted")
            break
        except Exception as e:  # noqa: BLE001 - one odd entry must not lose the batch
            r = {"key": ref.get("key"), "input": {"raw": ref.get("raw")}, "verdict": "error", "match": None,
                 "mismatches": [], "notes": [f"unexpected {type(e).__name__}: {e}"], "oa_url": None, "sources": []}
        results.append(r)
        if r["verdict"] != "error":  # errors are retried on the next run
            cache[fp] = r
            if args.resume:
                save_resume(Path(args.resume).expanduser(), cache, note)
        if not args.quiet and len(refs) > 1:
            eta = (time.monotonic() - started) / i * (len(refs) - i)
            sys.stderr.write(f"[{i}/{len(refs)}] {r['verdict']:<12} {safe(r['key'])}"
                             + (f"  (~{eta:.0f} s left)" if i < len(refs) and eta > 5 else "") + "\n")
    shown = [r for r in results if r["verdict"] != "verified"] if args.only == "issues" else results
    if args.format == "json":
        text = json.dumps({"summary": summary(results), "total": len(refs), "checked": len(results),
                           "results": shown}, ensure_ascii=False, indent=2) + "\n"
    elif args.format == "bibtex":
        text = annotate_bibtex(bib_text or "", results)
    else:
        text = table(shown, results)
    if args.output:
        try:
            with open(Path(args.output).expanduser(), "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        except OSError as e:
            sys.stdout.write(text)
            return fail(EXIT_BAD_INPUT, f"cannot write {args.output}: {e.strerror or e}", "the report went to stdout")
    else:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except BrokenPipeError:
            with contextlib.suppress(OSError):
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    if stopped:
        left = len(refs) - len(results)
        return fail(stopped.code, f"{stopped}; stopped with {left} reference(s) unchecked",
                    stopped.hint or "run again with --resume FILE")
    return EXIT_ISSUES if any(r["verdict"] != "verified" for r in results) else EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 - exit 1 means "issues found"; a crash must not look like that
        sys.stderr.write(f"error: unexpected {type(e).__name__}: {e} — a bug; report it with the command line\n")
        sys.exit(EXIT_PARSE)
