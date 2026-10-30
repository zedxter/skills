#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Search scholarly literature without a key: arXiv, Crossref, Europe PMC, PubMed and bioRxiv/medRxiv preprints,
merged and deduplicated by DOI, arXiv id, PMID and title, with abstracts, citation counts (Crossref, Europe PMC) and
open-access links (arXiv, Europe PMC, PubMed Central, Unpaywall with --email). Standard library only.

    uv run scholar.py "retrieval augmented generation" --since 2024           # all four free sources
    uv run scholar.py "base editing sickle cell" --source europepmc,biorxiv --sort date -n 20
    uv run scholar.py "graph neural networks" --source arxiv,crossref --sort citations -f markdown
    uv run scholar.py doi 10.1038/nature14539 arXiv:1706.03762 PMID:26017442  # full records, open-access links
    uv run scholar.py bibtex 10.1038/nature14539 1706.03762                    # BibTeX from the registries

Exit codes:
    0 results · 1 no results, or an identifier not found · 2 bad input · 4 rate-limited · 6 network error, or a
    source down or blocking the request · 7 a source answered something unreadable, or an unexpected failure ·
    130 interrupted
"""

import argparse
import calendar
import contextlib
import html
import http.client
import json
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

EXIT_OK, EXIT_EMPTY, EXIT_BAD_INPUT, EXIT_RATE, EXIT_NETWORK, EXIT_SOURCE = 0, 1, 2, 4, 6, 7

UA_BASE = "zedxter-skills-scholar-search/1.0 (+https://github.com/zedxter/skills{contact})"
DOI_ORG = "https://doi.org"
CROSSREF = "https://api.crossref.org"
ARXIV = "https://export.arxiv.org/api/query"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
UNPAYWALL = "https://api.unpaywall.org/v2"
OPENALEX = "https://api.openalex.org/works"
S2 = "https://api.semanticscholar.org/graph/v1/paper/search"
ZOTERO = "http://localhost:23119/api"
# Minimum seconds between requests to one host: arXiv asks for one request every 3 s; Crossref allows a few a
# second; NCBI allows 3 a second without a key. Requests to one host never overlap.
INTERVAL = {"doi.org": 0.2, "api.crossref.org": 0.35, "export.arxiv.org": 3.1, "eutils.ncbi.nlm.nih.gov": 0.34,
            "www.ebi.ac.uk": 0.2, "api.unpaywall.org": 0.1, "api.openalex.org": 0.2, "api.semanticscholar.org": 1.1}
ACROSS_RUNS = {"export.arxiv.org"}  # spaced across concurrent runs on this machine too, through a lock file
FREE = ["arxiv", "europepmc", "pubmed", "crossref"]
SOURCES = FREE + ["biorxiv", "openalex", "semanticscholar"]
KEYS = {"openalex": "OPENALEX_API_KEY", "semanticscholar": "S2_API_KEY"}
# Crossref also registers figures, peer reviews, datasets, whole journals and issues; those are dropped. ("other"
# stays: some publishers register book chapters that way.)
CROSSREF_SKIP = {"component", "peer-review", "dataset", "database", "grant", "journal", "journal-issue",
                 "journal-volume", "proceedings", "proceedings-series", "book-series", "book-set", "report-series",
                 "standard-series", "report-component"}
DOI_RE = re.compile(r"\b(10\.\d{4,9}/\S+)", re.I)
ARXIV_ID_RE = re.compile(r"^(?:arxiv:)?((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?)$", re.I)
PMID_ID_RE = re.compile(r"^(?:pmid:?\s*)?(\d{1,9})$", re.I)
MAX_ABSTRACT = 4000
STOP = {"the", "and", "for", "with", "from", "into", "via", "are", "was", "its", "this", "that", "what", "how", "why",
        "new", "using", "based", "about", "between", "among", "toward", "towards"}


class Failure(Exception):
    def __init__(self, code: int, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.hint = code, hint


# ------------------------------------------------------------------ text

def safe(s: Any) -> str:
    """One printable line: no control, C1 or bidi characters, whitespace collapsed (a title cannot fake a row)."""
    return " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f‪-‮⁦-⁩]", " ", str(s if s is not None else "")).split())


TAG = re.compile(r"</?([A-Za-z][\w.-]*:)?([A-Za-z][\w.-]*)(?:\s[^<>]*)?/?>")
INLINE = {"i", "b", "em", "strong", "sub", "sup", "u", "sc", "italic", "bold", "scp", "span", "a", "underline",
          "small", "monospace", "inline-formula", "named-content", "styled-content"}


def clean(s: Any, markup: bool = True) -> str | None:
    """Plain text: entities decoded, whitespace collapsed, and with markup=True (JSON sources, where abstracts carry
    JATS or HTML) tags dropped; inline ones (<i>, <sub>) without leaving a space. Text from parsed XML is already
    free of tags, so a '<' there is a real less-than sign ('aged <65 years') and stays."""
    if not isinstance(s, str):
        return None
    if markup:
        s = TAG.sub(lambda m: "" if m[2].lower() in INLINE else " ", s)
    s = html.unescape(s)
    s = " ".join(s.split())
    s = re.sub(r"\s+([,.;:!?)])", r"\1", s)
    return s or None


def clean_abstract(s: Any, markup: bool = True) -> str | None:
    s = clean(s, markup)
    if s:
        s = re.sub(r"^(abstract|summary)\b[\s.:]*", "", s, flags=re.I)
    return s[:MAX_ABSTRACT] if s else None


def title_key(title: str | None) -> str:
    """Lowercase words without accents or punctuation, for spotting the same work in two sources."""
    t = unicodedata.normalize("NFKD", title or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^\w]+|_", " ", t).split())


def clean_doi(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", s.strip(), flags=re.I)
    s = s.rstrip(".,;)]}>")
    return s if re.fullmatch(r"10\.\d{4,9}/\S+", s) else None


def norm_date(y: Any, m: Any = None, d: Any = None) -> str | None:
    """'YYYY', 'YYYY-MM' or 'YYYY-MM-DD' from parts; months may be names ('Oct')."""
    try:
        year = int(str(y)[:4])
    except (TypeError, ValueError):
        return None
    if not 1000 <= year <= 2999:
        return None
    if m in (None, ""):
        return f"{year:04d}"
    month = None
    if str(m).isdigit():
        month = int(m)
    else:
        names = {n.lower(): i for i, n in enumerate(calendar.month_abbr) if n}
        month = names.get(str(m)[:3].lower())
    if not month or not 1 <= month <= 12:
        return f"{year:04d}"
    if d in (None, "") or not str(d).isdigit() or not 1 <= int(d) <= 31:
        return f"{year:04d}-{month:02d}"
    return f"{year:04d}-{month:02d}-{int(d):02d}"


def date_bound(s: str, end: bool) -> str:
    """'2024' → '2024-01-01' (or '2024-12-31' as an end); '2024-06' → '2024-06-01'/'2024-06-30'; full dates as is."""
    m = re.fullmatch(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", s or "")
    if not m:
        raise ValueError(s)
    y, mo, d = int(m[1]), int(m[2] or (12 if end else 1)), m[3]
    if not 1 <= mo <= 12:
        raise ValueError(s)
    last = calendar.monthrange(y, mo)[1]
    day = int(d) if d else (last if end else 1)
    if not 1 <= day <= last:
        raise ValueError(s)
    return f"{y:04d}-{mo:02d}-{day:02d}"


# ------------------------------------------------------------------ network

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_a, **_k):  # keyed and local requests never follow a redirect
        return None


NO_REDIRECT = urllib.request.build_opener(NoRedirect)
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)  # Zotero: never via http_proxy


def slot_path(host: str) -> Path:
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_CACHE_HOME")
    return Path(base or Path.home() / ".cache") / "zedxter-skills" / f"scholar-{host}-slot"


@contextlib.contextmanager
def locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as fh:
        if os.name == "nt":
            import msvcrt
            for _ in range(200):
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                with contextlib.suppress(OSError):
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def machine_slot(host: str, interval: float) -> float:
    """The next free time to call host from this machine, reserved under a file lock so that concurrent runs
    queue up (best effort: 0 if the cache folder is unwritable)."""
    try:
        path = slot_path(host)
        with locked(path.with_name(path.name + ".lock")):
            now = time.time()
            try:
                last = float(path.read_text() or 0)
            except (OSError, ValueError):
                last = 0.0
            slot = max(now, last + interval) if last <= now + 60 else now
            path.write_text(f"{slot:.3f}")
        return slot
    except (OSError, RuntimeError):
        return 0.0


class Net:
    def __init__(self, timeout: float, email: str | None, note) -> None:
        self.timeout, self.note, self.email = timeout, note, email
        self.ua = UA_BASE.format(contact=f"; mailto:{email}" if email else "")
        self.last: dict[str, float] = {}
        self.locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self.guard = threading.Lock()

    def _slot(self, host: str) -> threading.Lock:
        with self.guard:
            return self.locks[host]

    def get(self, url: str, accept: str = "application/json", headers: dict[str, str] | None = None,
            follow: bool = True) -> tuple[int, bytes]:
        """(status, body). 4xx other than 401/403/429 come back; 429 is retried once then exit 4; 5xx is retried
        once then exit 6; 401/403 (blocked, or a bad key) are exit 6 at once."""
        host = urllib.parse.urlsplit(url).hostname or ""
        for attempt in (1, 2):
            with self._slot(host):  # one request at a time per host, spaced by INTERVAL
                wait = self.last.get(host, 0) + INTERVAL.get(host, 0.5) - time.monotonic()
                if host in ACROSS_RUNS:
                    wait = max(wait, machine_slot(host, INTERVAL[host]) - time.time())
                if wait > 0:
                    time.sleep(wait)
                req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept": accept, **(headers or {})})
                try:
                    opener = urllib.request.urlopen if follow else NO_REDIRECT.open
                    with opener(req, timeout=self.timeout) as resp:
                        return resp.status, resp.read(10_000_001)
                except urllib.error.HTTPError as e:
                    err = e
                except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
                    err = e
                finally:
                    self.last[host] = time.monotonic()
            if isinstance(err, urllib.error.HTTPError):
                if err.code == 429 or (err.code == 503 and host == "export.arxiv.org"):
                    ra = (err.headers or {}).get("Retry-After", "")
                    secs = int(ra) if ra.isdigit() else 10
                    if attempt == 1 and secs <= 60:
                        self.note(f"{host} rate-limited; waiting {secs} s once")
                        time.sleep(secs)
                        continue
                    raise Failure(EXIT_RATE, f"{host} rate limit (HTTP {err.code})",
                                  "wait a few minutes before searching this source again; do not loop") from None
                if err.code >= 500:
                    if attempt == 1:
                        time.sleep(3)
                        continue
                    raise Failure(EXIT_NETWORK, f"{host} is not answering (HTTP {err.code})",
                                  "the source is down; try again later or use other sources") from None
                if err.code in (401, 403):
                    raise Failure(EXIT_NETWORK, f"{host} refused the request (HTTP {err.code})",
                                  "the source blocks this network, or its API key is wrong") from None
                body = b""
                with contextlib.suppress(Exception):
                    body = err.read(200_000)
                return err.code, body
            reason = getattr(err, "reason", err)
            if "CERTIFICATE_VERIFY_FAILED" in str(reason):
                raise Failure(EXIT_NETWORK, f"TLS certificate verification failed ({reason})",
                              "on a python.org macOS build run 'Install Certificates.command', or use uv's "
                              "managed Python") from None
            if attempt == 2:
                raise Failure(EXIT_NETWORK, f"{host}: {reason}", "check the connection (HTTPS_PROXY is honoured)") from None
            time.sleep(2)
        raise AssertionError("unreachable")

    def json(self, url: str, what: str, ok404: bool = False, **kw: Any) -> Any:
        """Parsed JSON of a 200 answer; None for a 404 when ok404; anything else is exit 7."""
        status, body = self.get(url, **kw)
        if status == 404 and ok404:
            return None
        if status != 200:
            snippet = safe(body[:160].decode("utf-8", "replace"))
            raise Failure(EXIT_SOURCE, f"{what} answered HTTP {status}" + (f": {snippet}" if snippet else ""),
                          "simplify the query (plain words), or leave this source out")
        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise Failure(EXIT_SOURCE, f"{what} answered something that is not JSON",
                          "the API may have changed or be failing; try again later") from None

    def xml(self, url: str, what: str) -> ET.Element:
        status, body = self.get(url, accept="application/xml, application/atom+xml")
        if status != 200:
            raise Failure(EXIT_SOURCE, f"{what} answered HTTP {status}", "simplify the query, or leave this source out")
        try:
            return ET.fromstring(body)
        except ET.ParseError:
            raise Failure(EXIT_SOURCE, f"{what} answered unreadable XML", "try again later") from None


# ------------------------------------------------------------------ records

FIELDS = ("title", "authors", "year", "date", "venue", "type", "doi", "arxiv", "pmid", "pmcid", "url", "oa_url",
          "citations", "abstract", "tldr", "sources", "alt_dois", "in_zotero")


def record(source: str, rank: int, **kw: Any) -> dict[str, Any]:
    r: dict[str, Any] = dict.fromkeys(FIELDS)
    r.update(authors=[], sources=[source], alt_dois=[], _rank=rank)
    r.update({k: v for k, v in kw.items() if v not in (None, "", [])})
    r["doi"] = clean_doi(r["doi"])
    if r["doi"] and r["doi"].lower().startswith("10.48550/arxiv."):  # arXiv's own DataCite DOI names the preprint
        r["arxiv"] = r["arxiv"] or r["doi"][len("10.48550/arxiv."):]
    if r["arxiv"]:
        r["arxiv"] = re.sub(r"v\d+$", "", r["arxiv"])
    if r["date"] and not r["year"]:
        r["year"] = int(r["date"][:4])
    if r["oa_url"] is None and r["arxiv"]:
        r["oa_url"] = f"https://arxiv.org/pdf/{r['arxiv']}"
    if r["url"] is None:
        r["url"] = (f"https://doi.org/{r['doi']}" if r["doi"] else f"https://arxiv.org/abs/{r['arxiv']}" if r["arxiv"]
                    else f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/" if r["pmid"] else None)
    return r


def is_preprint(r: dict[str, Any]) -> bool:
    return (r.get("type") in ("preprint", "posted-content") or (r.get("doi") or "").lower().startswith("10.48550/")
            or r.get("sources") == ["arxiv"] or r.get("sources") == ["biorxiv"])


def keys(r: dict[str, Any]) -> list[str]:
    out = []
    if r["doi"]:
        out.append("doi:" + r["doi"].lower())
    if r["arxiv"]:
        out.append("arxiv:" + r["arxiv"].lower())
    if r["pmid"]:
        out.append(f"pmid:{r['pmid']}")
    t = title_key(r["title"])
    if t:  # a short title ("Deep learning") is the same work only in the same year
        out.append("t:" + t + ("" if len(t.split()) >= 6 else f":{r['year']}"))
    return out


# Whose metadata wins when sources disagree: registries carry full author names and the venue as published.
PRIORITY = {"doi.org": 0, "crossref": 1, "openalex": 2, "semanticscholar": 3, "pubmed": 4, "europepmc": 5,
            "arxiv": 6, "biorxiv": 7}


def combine(members: list[dict[str, Any]]) -> dict[str, Any]:
    """One record from several sources' copies of a work: the published version's metadata first, the largest
    citation count, the longest abstract, every identifier."""
    order = sorted(members, key=lambda r: (is_preprint(r), PRIORITY.get(r["sources"][0], 9), r["_rank"]))
    out = dict(order[0])
    for r in order[1:]:
        for f in ("title", "date", "venue", "type", "doi", "arxiv", "pmid", "pmcid", "url", "oa_url", "tldr", "in_zotero"):
            if out.get(f) in (None, "") and r.get(f) not in (None, ""):
                out[f] = r[f]
        if not out["authors"]:
            out["authors"] = r["authors"]
    out["year"] = int(out["date"][:4]) if out.get("date") else next((r["year"] for r in order if r["year"]), None)
    counts = [r["citations"] for r in members if isinstance(r.get("citations"), int)]
    out["citations"] = max(counts) if counts else None
    out["abstract"] = max((r["abstract"] for r in members if r.get("abstract")), key=len, default=None)
    out["sources"] = list(dict.fromkeys(s for r in sorted(members, key=lambda r: r["_rank"]) for s in r["sources"]))
    dois = list(dict.fromkeys(d for r in order for d in [r.get("doi"), *r.get("alt_dois", [])] if d))
    out["alt_dois"] = [d for d in dois if d.lower() != (out["doi"] or "").lower()]
    out["_rank"] = min(r["_rank"] for r in members)
    if out["doi"]:  # the DOI is the landing page every reader can resolve
        out["url"] = f"https://doi.org/{out['doi']}"
    return out


def merge(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group records that share a DOI, arXiv id, PMID or title, and combine each group."""
    groups: list[list[dict[str, Any]] | None] = []
    where: dict[str, int] = {}
    def fits(r: dict[str, Any], group: list[dict[str, Any]]) -> bool:
        """A title alone joins r to a group only if no identifier contradicts it: a different PMID, or a different
        DOI between two published versions (a preprint's DOI differs from its journal version's, legitimately)."""
        for m in group:
            if r["pmid"] and m["pmid"] and r["pmid"] != m["pmid"]:
                return False
            if (r["doi"] and m["doi"] and r["doi"].lower() != m["doi"].lower()
                    and not is_preprint(r) and not is_preprint(m)):
                return False
        return True

    for r in sorted(records, key=lambda r: r["_rank"]):
        ks = keys(r)
        hit = sorted({where[k] for k in ks if k in where
                      and (not k.startswith("t:") or fits(r, groups[where[k]]))})  # type: ignore[arg-type]
        if not hit:
            groups.append([r])
            gi = len(groups) - 1
        else:
            gi = hit[0]
            groups[gi].append(r)  # type: ignore[union-attr]
            for other in hit[1:]:
                groups[gi].extend(groups[other])  # type: ignore[union-attr, arg-type]
                groups[other] = None
                for k, v in list(where.items()):
                    if v == other:
                        where[k] = gi
        for m in groups[gi]:  # type: ignore[union-attr]
            for k in keys(m):
                where[k] = gi
    return [combine(g) for g in groups if g]


def sort_records(recs: list[dict[str, Any]], how: str, order: list[str]) -> list[dict[str, Any]]:
    def first_source(r: dict[str, Any]) -> int:
        return min((order.index(s) for s in r["sources"] if s in order), default=99)

    base = sorted(recs, key=lambda r: (r["_rank"], -len(r["sources"]), first_source(r)))
    if how == "date":
        return sorted(base, key=lambda r: r["date"] or str(r["year"] or ""), reverse=True)
    if how == "citations":
        return sorted(base, key=lambda r: r["citations"] if isinstance(r["citations"], int) else -1, reverse=True)
    return base


def in_range(r: dict[str, Any], since: str | None, until: str | None) -> bool:
    d = r["date"] or (f"{r['year']:04d}" if r["year"] else None)
    if not d:
        return True
    return not ((since and d < since[:len(d)]) or (until and d > until[:len(d)]))


# ------------------------------------------------------------------ sources

ARXIV_STOP = {"a", "an", "the", "of", "on", "in", "for", "and", "to", "with", "by", "from", "via", "at", "is"}
ARXIV_FIELDS = r"(?:ti|au|abs|co|jr|cat|rn|id|all|submittedDate)"
# A source's own syntax: Boolean operators, arXiv or Europe PMC fields (ti:x, AUTH:x; a colon followed directly by
# a term) and PubMed tags ([ti]). A title with a colon ("Transformers: a survey") is plain words.
SYNTAX = re.compile(rf"\b(AND|OR|NOT|ANDNOT)\b|(^|[\s(]){ARXIV_FIELDS}:\S|\b[A-Z_]{{2,}}:\S|\[\w+\]")


def query_words(q: str) -> list[str]:
    """The plain words of a query (no operators, field prefixes or stopwords), for has_all_words."""
    if SYNTAX.search(q):
        return []
    return [w for w in title_key(q.replace('"', " ")).split() if len(w) > 2 and w not in STOP]


def singular(w: str) -> str:
    """'batteries' → 'battery', 'studies' → 'study', 'boxes' → 'box', 'networks' → 'network' (good enough to match)."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and re.search(r"(s|x|z|ch|sh)es$", w):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def has_all_words(r: dict[str, Any], q: str) -> bool:
    text = [singular(t) for t in title_key(f"{r['title'] or ''} {r['abstract'] or ''}").split()]
    return all(any(t.startswith(singular(w)) for t in text) for w in query_words(q))


def crossref_date(item: dict[str, Any]) -> str | None:
    for k in ("published", "issued", "published-print", "published-online", "posted", "created"):
        with contextlib.suppress(TypeError, IndexError, KeyError, AttributeError):
            parts = item[k]["date-parts"][0]
            d = norm_date(*(parts + [None, None])[:3])
            if d:
                return d
    return None


def crossref_record(it: dict[str, Any], rank: int, source: str = "crossref") -> dict[str, Any]:
    first = lambda v: v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else None)  # noqa: E731
    authors = []
    for a in it.get("author") or []:
        if isinstance(a, dict):
            name = " ".join(x for x in (a.get("given"), a.get("family")) if isinstance(x, str)) or a.get("name")
            if name:
                authors.append(clean(name))
    count = it.get("is-referenced-by-count")
    return record(source, rank, title=clean(first(it.get("title"))), authors=[a for a in authors if a],
                  date=crossref_date(it), venue=clean(first(it.get("container-title")) or first(it.get("group-title"))),
                  type=it.get("type"), doi=it.get("DOI"), citations=count if isinstance(count, int) else None,
                  abstract=clean_abstract(it.get("abstract")))


def search_crossref(net: Net, q: str, o: argparse.Namespace) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"query": q[:1000], "rows": o.fetch,
                              "select": "DOI,title,author,issued,published,published-print,published-online,posted,"
                                        "created,container-title,group-title,type,is-referenced-by-count,abstract"}
    filters = ([f"from-pub-date:{o.since}"] if o.since else []) + ([f"until-pub-date:{o.until}"] if o.until else [])
    if filters:
        params["filter"] = ",".join(filters)
    if net.email:
        params["mailto"] = net.email
    d = net.json(f"{CROSSREF}/works?{urllib.parse.urlencode(params)}", "Crossref")
    items = (d.get("message") or {}).get("items") if isinstance(d, dict) else None
    if not isinstance(items, list):
        raise Failure(EXIT_SOURCE, "Crossref answered without a result list", "try again later")
    out = [crossref_record(it, i) for i, it in enumerate(x for x in items if isinstance(x, dict))]
    out = [r for r in out if r["title"] and r["type"] not in CROSSREF_SKIP]
    # Crossref matches any one word, so its own date or citation order surfaces famous unrelated papers: take
    # its best matches by relevance, keep those with every query word, and let the caller sort them.
    return [r for r in out if has_all_words(r, q)] if o.sort != "relevance" else out


def arxiv_query(q: str) -> str:
    """Plain words → all:w1 AND all:w2; quoted phrases stay phrases; field prefixes (ti:, au:, cat:) and explicit
    AND/OR/ANDNOT pass through."""
    field = re.compile(rf"^\(*{ARXIV_FIELDS}:\S")
    tokens = [t if t.startswith('"') or field.match(t) else t.strip(":;,.") for t in re.findall(r'"[^"]+"|\S+', q)]
    explicit = any(t in ("AND", "OR", "ANDNOT") for t in tokens)
    # all:a matches every paper and swamps the ranking: drop stopwords between ANDed words
    tokens = [t for t in tokens if t and (explicit or t.lower() not in ARXIV_STOP)]
    if explicit:
        return " ".join(t if t in ("AND", "OR", "ANDNOT") or field.match(t) else f"all:{t}" for t in tokens)
    return " AND ".join(t if field.match(t) else f"all:{t}" for t in tokens)


def arxiv_records(root: ET.Element, rank0: int = 0) -> list[dict[str, Any]]:
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    out = []
    for e in root.findall("a:entry", ns):
        aid = (e.findtext("a:id", "", ns) or "").rsplit("/abs/", 1)[-1]
        title = " ".join((e.findtext("a:title", "", ns) or "").split())
        if title == "Error":
            raise Failure(EXIT_SOURCE, f"arXiv rejected the query: {safe(e.findtext('a:summary', '', ns))}",
                          "use plain words, or arXiv field syntax such as ti:transformer AND cat:cs.CL")
        if not title or not aid:
            continue
        out.append(record("arxiv", rank0 + len(out), arxiv=aid, title=clean(title, False), doi=e.findtext("arxiv:doi", None, ns),
                          authors=[" ".join((a.findtext("a:name", "", ns) or "").split()) for a in e.findall("a:author", ns)],
                          date=(e.findtext("a:published", "", ns) or "")[:10] or None, type="preprint",
                          venue=clean(e.findtext("arxiv:journal_ref", None, ns), False) or "arXiv",
                          abstract=clean_abstract(e.findtext("a:summary", None, ns), False)))
    return out


def search_arxiv(net: Net, q: str, o: argparse.Namespace) -> list[dict[str, Any]]:
    sq = arxiv_query(q)
    if o.since or o.until:
        lo = (o.since or "1991-01-01").replace("-", "")
        hi = (o.until or "2999-12-31").replace("-", "")
        sq = f"({sq}) AND submittedDate:[{lo}0000 TO {hi}2359]"
    params = {"search_query": sq, "max_results": o.fetch, "sortOrder": "descending",
              "sortBy": "submittedDate" if o.sort == "date" else "relevance"}
    return arxiv_records(net.xml(f"{ARXIV}?{urllib.parse.urlencode(params)}", "arXiv"))


def pubmed_records(root: ET.Element, order: list[str]) -> list[dict[str, Any]]:
    """Journal articles (PubmedArticle) and NCBI Bookshelf records such as GeneReviews (PubmedBookArticle)."""
    text = lambda e: clean("".join(e.itertext()), False) if e is not None else None  # noqa: E731
    out = {}
    for art in [*root.iter("PubmedArticle"), *root.iter("PubmedBookArticle")]:
        book = art.tag == "PubmedBookArticle"
        a = art.find("BookDocument" if book else "MedlineCitation/Article")
        pmid = art.findtext("BookDocument/PMID" if book else "MedlineCitation/PMID")
        if not pmid or a is None:
            continue
        title = text(a.find("ArticleTitle")) or (text(a.find("Book/BookTitle")) if book else None)
        authors = []
        for au in a.findall("AuthorList/Author") or (a.findall("Book/AuthorList/Author") if book else []):
            name = " ".join(x for x in (au.findtext("ForeName") or au.findtext("Initials"), au.findtext("LastName")) if x)
            authors.append(name or au.findtext("CollectiveName") or "")
        pd = a.find("Book/PubDate" if book else "Journal/JournalIssue/PubDate")
        date = None
        if pd is not None:
            date = norm_date(pd.findtext("Year"), pd.findtext("Month"), pd.findtext("Day")) or \
                norm_date((pd.findtext("MedlineDate") or "")[:4])
        parts = []
        for t in a.findall("Abstract/AbstractText"):
            body = "".join(t.itertext()).strip()
            label = t.get("Label")
            parts.append(f"{label.capitalize()}: {body}" if label and body else body)
        id_path = "PubmedBookData/ArticleIdList/ArticleId" if book else "PubmedData/ArticleIdList/ArticleId"
        ids = {i.get("IdType"): (i.text or "").strip() for i in art.findall(id_path)}
        doi = ids.get("doi") or next((e.text for e in a.findall("ELocationID") if e.get("EIdType") == "doi"), None)
        pmc = ids.get("pmc")
        ptypes = [p.text or "" for p in a.findall("PublicationTypeList/PublicationType")]
        venue = text(a.find("Book/BookTitle")) if book else clean(a.findtext("Journal/Title"), False)
        out[pmid] = record("pubmed", order.index(pmid) if pmid in order else len(order), pmid=pmid, doi=doi,
                           pmcid=pmc, title=title.rstrip(".") if title else None, authors=[x for x in authors if x],
                           date=date, venue=venue if venue != title else None,
                           type="book-chapter" if book else "review" if "Review" in ptypes else "journal-article",
                           abstract=clean_abstract(" ".join(p for p in parts if p), False),
                           oa_url=f"https://pmc.ncbi.nlm.nih.gov/articles/{pmc}/" if pmc else None)
    return sorted(out.values(), key=lambda r: r["_rank"])


def eutils_params(net: Net, **kw: Any) -> str:
    return urllib.parse.urlencode({**kw, "tool": "zedxter-skills", **({"email": net.email} if net.email else {})})


def search_pubmed(net: Net, q: str, o: argparse.Namespace) -> list[dict[str, Any]]:
    p: dict[str, Any] = {"db": "pubmed", "term": q[:2000], "retmode": "json", "retmax": o.fetch,
                         "sort": "pub_date" if o.sort == "date" else "relevance"}
    if o.since or o.until:
        p.update(datetype="pdat", mindate=(o.since or "1800-01-01").replace("-", "/"),
                 maxdate=(o.until or "3000-12-31").replace("-", "/"))
    d = net.json(f"{EUTILS}/esearch.fcgi?{eutils_params(net, **p)}", "PubMed")
    res = d.get("esearchresult") if isinstance(d, dict) else None
    if not isinstance(res, dict):
        raise Failure(EXIT_SOURCE, "PubMed answered without a result list", "try again later")
    if res.get("ERROR"):
        raise Failure(EXIT_SOURCE, f"PubMed rejected the query: {safe(res['ERROR'])}", "use plain words")
    ids = [i for i in res.get("idlist", []) if str(i).isdigit()]
    return fetch_pubmed(net, ids) if ids else []


def fetch_pubmed(net: Net, ids: list[str]) -> list[dict[str, Any]]:
    root = net.xml(f"{EUTILS}/efetch.fcgi?{eutils_params(net, db='pubmed', id=','.join(ids), retmode='xml')}", "PubMed")
    return pubmed_records(root, ids)


def europepmc_record(r: dict[str, Any], rank: int, source: str) -> dict[str, Any]:
    authors = [a.get("fullName") or " ".join(x for x in (a.get("firstName"), a.get("lastName")) if x)
               for a in ((r.get("authorList") or {}).get("author") or []) if isinstance(a, dict)]
    if not any(authors):
        authors = [a.strip() for a in (r.get("authorString") or "").rstrip(".").split(",") if a.strip()]
    venue = (((r.get("journalInfo") or {}).get("journal") or {}).get("title")
             or (r.get("bookOrReportDetails") or {}).get("publisher"))
    oa = None
    urls = ((r.get("fullTextUrlList") or {}).get("fullTextUrl") or [])
    free = [u for u in urls if isinstance(u, dict) and u.get("availabilityCode") in ("OA", "F") and u.get("url")]
    free.sort(key=lambda u: (u.get("documentStyle") != "pdf", u.get("site") != "Europe_PMC"))
    if free:
        oa = free[0]["url"]
    elif r.get("pmcid") and r.get("isOpenAccess") == "Y":
        oa = f"https://europepmc.org/article/PMC/{r['pmcid']}"
    ptypes = ((r.get("pubTypeList") or {}).get("pubType") or [])
    preprint = r.get("source") == "PPR"
    count = r.get("citedByCount")
    return record(source, rank, title=(clean(r.get("title")) or "").rstrip(".") or None,
                  authors=[clean(a) for a in authors if a and clean(a)], doi=r.get("doi"), pmid=r.get("pmid"),
                  pmcid=r.get("pmcid"), date=r.get("firstPublicationDate") or norm_date(r.get("pubYear")),
                  venue=clean(venue), type="preprint" if preprint else (str(ptypes[0]).lower() if ptypes else None),
                  citations=int(count) if str(count).isdigit() else None, abstract=clean_abstract(r.get("abstractText")),
                  oa_url=oa, url=f"https://europepmc.org/article/{r.get('source')}/{r.get('id')}" if r.get("id") else None)


def europepmc(net: Net, query: str, o: argparse.Namespace, source: str, rows: int | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"query": query, "format": "json", "pageSize": rows or o.fetch, "resultType": "core"}
    sort = getattr(o, "sort", None)
    if sort in ("date", "citations"):
        params["sort"] = "P_PDATE_D desc" if sort == "date" else "CITED desc"
    d = net.json(f"{EUROPEPMC}?{urllib.parse.urlencode(params)}", "Europe PMC")
    res = ((d.get("resultList") or {}).get("result")) if isinstance(d, dict) else None
    if not isinstance(res, list):
        raise Failure(EXIT_SOURCE, "Europe PMC answered without a result list", "try again later")
    return [europepmc_record(r, i, source) for i, r in enumerate(x for x in res if isinstance(x, dict)) if r.get("title")]


def epmc_query(q: str, o: argparse.Namespace, extra: str = "") -> str:
    # Plain words are searched in titles and abstracts: across full texts every paper mentions everything.
    q = q[:1500]
    parts = [f"TITLE_ABS:({q})" if query_words(q) else f"({q})"] + ([extra] if extra else [])
    if o.since or o.until:
        parts.append(f"FIRST_PDATE:[{o.since or '1800-01-01'} TO {o.until or '2999-12-31'}]")
    return " AND ".join(parts)


def search_europepmc(net: Net, q: str, o: argparse.Namespace) -> list[dict[str, Any]]:
    return europepmc(net, epmc_query(q, o), o, "europepmc")


def search_biorxiv(net: Net, q: str, o: argparse.Namespace) -> list[dict[str, Any]]:
    # bioRxiv's own API has no search; Europe PMC indexes bioRxiv and medRxiv preprints within days.
    return europepmc(net, epmc_query(q, o, 'SRC:PPR AND (PUBLISHER:"bioRxiv" OR PUBLISHER:"medRxiv")'), o, "biorxiv")


def inverted_abstract(idx: Any) -> str | None:
    if not isinstance(idx, dict):
        return None
    pos = sorted((p, w) for w, ps in idx.items() if isinstance(ps, list) for p in ps if isinstance(p, int))
    return clean_abstract(" ".join(w for _, w in pos))


def search_openalex(net: Net, q: str, o: argparse.Namespace) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"search": q[:500], "per-page": o.fetch, "api_key": os.environ[KEYS["openalex"]]}
    filters = ([f"from_publication_date:{o.since}"] if o.since else []) + ([f"to_publication_date:{o.until}"] if o.until else [])
    if filters:
        params["filter"] = ",".join(filters)
    if o.sort in ("date", "citations"):
        params["sort"] = "publication_date:desc" if o.sort == "date" else "cited_by_count:desc"
    d = net.json(f"{OPENALEX}?{urllib.parse.urlencode(params)}", "OpenAlex", follow=False)
    res = d.get("results") if isinstance(d, dict) else None
    if not isinstance(res, list):
        raise Failure(EXIT_SOURCE, "OpenAlex answered without a result list", "try again later")
    out = []
    for i, w in enumerate(x for x in res if isinstance(x, dict)):
        ids = w.get("ids") or {}
        pmid = str(ids.get("pmid") or "").rsplit("/", 1)[-1] or None
        count = w.get("cited_by_count")
        out.append(record("openalex", i, title=clean(w.get("title")), doi=w.get("doi"), pmid=pmid,
                          authors=[clean((a.get("author") or {}).get("display_name")) or "" for a in w.get("authorships") or []],
                          date=w.get("publication_date"), type=w.get("type"),
                          venue=clean(((w.get("primary_location") or {}).get("source") or {}).get("display_name")),
                          citations=count if isinstance(count, int) else None,
                          oa_url=(w.get("open_access") or {}).get("oa_url"),
                          abstract=inverted_abstract(w.get("abstract_inverted_index"))))
    return [r for r in out if r["title"]]


def search_semanticscholar(net: Net, q: str, o: argparse.Namespace) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"query": q[:500], "limit": o.fetch,
                              "fields": "title,year,venue,authors,externalIds,url,citationCount,abstract,"
                                        "openAccessPdf,publicationDate,tldr,publicationTypes"}
    if o.since or o.until:
        params["publicationDateOrYear"] = f"{o.since or ''}:{o.until or ''}"
    d = net.json(f"{S2}?{urllib.parse.urlencode(params)}", "Semantic Scholar", follow=False,
                 headers={"x-api-key": os.environ[KEYS["semanticscholar"]]})
    res = d.get("data", []) if isinstance(d, dict) else None
    if not isinstance(res, list):
        raise Failure(EXIT_SOURCE, "Semantic Scholar answered without a result list", "try again later")
    out = []
    for i, p in enumerate(x for x in res if isinstance(x, dict)):
        ext = p.get("externalIds") or {}
        count = p.get("citationCount")
        out.append(record("semanticscholar", i, title=clean(p.get("title")), doi=ext.get("DOI"), arxiv=ext.get("ArXiv"),
                          pmid=str(ext["PubMed"]) if ext.get("PubMed") else None, url=p.get("url"),
                          authors=[clean(a.get("name")) or "" for a in p.get("authors") or []],
                          date=p.get("publicationDate") or norm_date(p.get("year")), venue=clean(p.get("venue")),
                          citations=count if isinstance(count, int) else None,
                          oa_url=(p.get("openAccessPdf") or {}).get("url") or None,
                          abstract=clean_abstract(p.get("abstract")), tldr=clean((p.get("tldr") or {}).get("text"))))
    return [r for r in out if r["title"]]


SEARCH = {"arxiv": search_arxiv, "crossref": search_crossref, "europepmc": search_europepmc,
          "pubmed": search_pubmed, "biorxiv": search_biorxiv, "openalex": search_openalex,
          "semanticscholar": search_semanticscholar}


# ------------------------------------------------------------------ enrichment

def unpaywall(net: Net, doi: str) -> str | None:
    q = urllib.parse.quote(doi, safe="/")
    d = net.json(f"{UNPAYWALL}/{q}?email={urllib.parse.quote(net.email or '')}", "Unpaywall", ok404=True)
    loc = d.get("best_oa_location") if isinstance(d, dict) else None
    if isinstance(loc, dict):
        return loc.get("url_for_pdf") or loc.get("url")
    return None


class ZoteroUnavailable(Exception):
    pass


def zotero_get(params: dict[str, Any]) -> Any:
    url = f"{ZOTERO}/users/0/items?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "Zotero-API-Version": "3"})
    try:
        with LOCAL.open(req, timeout=10) as resp:
            return json.loads(resp.read(20_000_000))
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            raise ZoteroUnavailable("Zotero's local API is off — turn on Zotero → Settings → Advanced → "
                                    "'Allow other applications on this computer to communicate with Zotero'") from None
        raise ZoteroUnavailable(f"Zotero answered HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ZoteroUnavailable("Zotero is not running (it needs Zotero 7 open on this computer)") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ZoteroUnavailable("Zotero answered something that is not JSON") from None


def zotero_has(r: dict[str, Any]) -> bool:
    want_dois = {d.lower() for d in [r["doi"], *r["alt_dois"]] if d}
    want_title = title_key(r["title"])
    for q in [d for d in [r["doi"]] if d] + ([r["title"][:200]] if r["title"] else []):
        items = zotero_get({"q": q, "qmode": "everything", "limit": 25, "itemType": "-attachment"})
        for it in items if isinstance(items, list) else []:
            data = it.get("data") if isinstance(it, dict) else None
            if not isinstance(data, dict):
                continue
            dois = {clean_doi(data.get("DOI")) or ""} | {m.lower() for m in re.findall(r"10\.\d{4,9}/\S+", str(data.get("extra") or ""))}
            if want_dois & {d.lower() for d in dois if d}:
                return True
            if want_title and title_key(data.get("title")) == want_title:
                return True
    return False


# ------------------------------------------------------------------ lookup by identifier

def parse_id(s: str) -> tuple[str, str] | None:
    s = s.strip()
    if m := re.fullmatch(r"(?:https?://)?arxiv\.org/(?:abs|pdf)/(.+?)(?:\.pdf)?/?", s, re.I):
        s = m[1]
    if doi := clean_doi(s):
        return "doi", doi
    if m := ARXIV_ID_RE.fullmatch(s):
        return "arxiv", re.sub(r"v\d+$", "", m[1])
    if m := PMID_ID_RE.fullmatch(s):
        return "pmid", m[1]
    if m := re.fullmatch(r"(?:https?://)?pubmed\.ncbi\.nlm\.nih\.gov/(\d{1,9})/?", s, re.I):
        return "pmid", m[1]
    return None


def by_doi(net: Net, doi: str) -> dict[str, Any] | None:
    """The registry's record (CSL-JSON from doi.org covers Crossref, DataCite, mEDRA, JaLC...), or None if the DOI
    does not exist. A DOI registered without metadata comes back as a bare record."""
    q = urllib.parse.quote(doi, safe="/:;()")
    status, body = net.get(f"{DOI_ORG}/{q}", accept="application/vnd.citationstyles.csl+json")
    if status == 200:
        with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError, AttributeError, TypeError):
            d = json.loads(body)
            r = crossref_record(d, 0, "doi.org")
            r["date"] = r["date"] or crossref_date(d)
            if r["title"]:
                return r
    if status == 404:
        return None
    s2, _ = net.get(f"{DOI_ORG}/api/handles/{q}")
    return None if s2 == 404 else record("doi.org", 0, doi=doi)


def lookup(net: Net, kind: str, ident: str, note=lambda m: None) -> dict[str, Any] | None:
    """The work an identifier names, completed from the other sources (a failing extra source is skipped)."""
    if kind == "doi":
        base = by_doi(net, ident)
    elif kind == "arxiv":
        found = arxiv_records(net.xml(f"{ARXIV}?{urllib.parse.urlencode({'id_list': ident})}", "arXiv"))
        base = found[0] if found else None
    else:
        found = fetch_pubmed(net, [ident])
        base = found[0] if found else None
    if base is None:
        return None
    members = [base]
    doi = base["doi"]
    registered = doi and not doi.lower().startswith("10.48550/")  # not arXiv's own DataCite DOI

    def crossref_work() -> list[dict[str, Any]]:
        d = net.json(f"{CROSSREF}/works/{urllib.parse.quote(doi, safe='/')}"
                     + (f"?mailto={urllib.parse.quote(net.email)}" if net.email else ""), "Crossref", ok404=True)
        return [crossref_record(d["message"], 0)] if isinstance(d, dict) and isinstance(d.get("message"), dict) else []

    extras = []
    if base["arxiv"] and kind != "arxiv":
        extras.append(("arXiv", lambda: arxiv_records(
            net.xml(f"{ARXIV}?{urllib.parse.urlencode({'id_list': base['arxiv']})}", "arXiv"))))
    if registered:
        extras.append(("Crossref", crossref_work))
    if registered or base["pmid"]:
        q = f'DOI:"{doi}"' if registered else f"EXT_ID:{base['pmid']} AND SRC:MED"
        extras.append(("Europe PMC", lambda: europepmc(net, q, argparse.Namespace(), "europepmc", rows=1)))
    for name, fetch in extras:
        try:
            members += fetch()
        except Failure as e:
            note(f"{ident}: {name} skipped ({e}); the record may lack its citations or abstract")
    r = combine(members)
    r["sources"] = list(dict.fromkeys([{"doi": "doi.org", "pmid": "pubmed"}.get(kind, kind)] + r["sources"]))
    return r


# ------------------------------------------------------------------ BibTeX

def bibtex_value(s: Any) -> str:
    return re.sub(r"[{}\\\x00-\x1f\x7f]", "", " ".join(str(s).split()))


def bib_fields(entry: str) -> tuple[str, str, list[tuple[str, str]]] | None:
    """(type, key, [(field, raw value)]) of one BibTeX entry, split at top-level commas."""
    m = re.match(r"\s*@(\w+)\s*\{\s*([^,]*),", entry)
    if not m:
        return None
    body, i, depth, start, fields = entry[m.end():], 0, 0, 0, []
    end = len(body)
    while i < len(body):
        c = body[i]
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                end = i
                break
            depth -= 1
        elif c == '"' and depth == 0:
            j = body.find('"', i + 1)
            i = j if j > 0 else i
        elif c == "," and depth == 0:
            fields.append(body[start:i])
            start = i + 1
        i += 1
    fields.append(body[start:end])
    out = []
    for f in fields:
        if "=" in f:
            k, v = f.split("=", 1)
            if k.strip():
                out.append((k.strip().lower(), " ".join(v.split())))
    return m[1].lower(), m[2].strip(), out


def unbrace(v: str) -> str:
    return v.strip().strip('{}"').strip()


def cite_key(fields: dict[str, str], used: set[str]) -> str:
    author = unbrace(fields.get("author", "")).split(" and ")[0]
    family = author.split(",")[0] if "," in author else (author.split() or [""])[-1]
    words = [w for w in title_key(unbrace(fields.get("title", ""))).split()
             if w not in {"a", "an", "the", "on", "of", "in", "for", "and", "to", "with"}]
    raw = f"{family}{unbrace(fields.get('year', ''))}{words[0] if words else ''}"
    raw = "".join(c for c in unicodedata.normalize("NFKD", raw) if not unicodedata.combining(c))
    key = re.sub(r"[^a-z0-9]", "", raw.lower()) or "ref"
    base, n = key, 0
    while key in used:
        key = base + "abcdefghijklmnopqrstuvwxyz"[n % 26] + ("" if n < 26 else str(n // 26))
        n += 1
    used.add(key)
    return key


def tidy_bibtex(entry: str, used: set[str]) -> str | None:
    parsed = bib_fields(entry)
    if not parsed:
        return None
    kind, _, fields = parsed
    fd = dict(fields)
    lines = [f"@{kind}{{{cite_key(fd, used)},"]
    lines += [f"  {k} = {fd[k]}," for k, _ in fields if k in fd and k not in ("keywords", "copyright")]
    return "\n".join(lines).rstrip(",") + "\n}\n"


def local_bibtex(r: dict[str, Any], used: set[str]) -> str:
    fields = {"title": r["title"], "author": " and ".join(r["authors"]), "year": r["year"],
              "journal" if r["type"] == "journal-article" else "howpublished": r["venue"] if r["venue"] != "arXiv" else None,
              "doi": r["doi"], "eprint": r["arxiv"], "archiveprefix": "arXiv" if r["arxiv"] else None,
              "pmid": r["pmid"], "url": r["url"]}
    fd = {k: "{" + bibtex_value(v) + "}" for k, v in fields.items() if v}
    kind = "article" if r["type"] == "journal-article" else "misc"
    return "\n".join([f"@{kind}{{{cite_key(fd, used)},"] + [f"  {k} = {v}," for k, v in fd.items()]).rstrip(",") + "\n}\n"


def bibtex_for(net: Net, r: dict[str, Any] | None, doi: str | None, used: set[str]) -> str | None:
    """BibTeX from the DOI's registry (Crossref, DataCite for arXiv...), else built from the record."""
    doi = doi or (r and r["doi"]) or (f"10.48550/arXiv.{r['arxiv']}" if r and r["arxiv"] else None)
    if doi:
        status, body = net.get(f"{DOI_ORG}/{urllib.parse.quote(doi, safe='/:;()')}", accept="application/x-bibtex")
        text = body.decode("utf-8", "replace") if status == 200 else ""
        if text.lstrip().startswith("@"):
            entry = tidy_bibtex(text, used)
            if entry:
                return entry
    return local_bibtex(r, used) if r and r["title"] else None


# ------------------------------------------------------------------ output

def short_authors(authors: list[str]) -> str:
    if not authors:
        return ""
    if len(authors) <= 3:
        return ", ".join(authors)
    return ", ".join(authors[:3]) + f" et al. ({len(authors)} authors)"


def ids_line(r: dict[str, Any]) -> str:
    parts = ([f"doi:{r['doi']}"] if r["doi"] else []) + ([f"arXiv:{r['arxiv']}"] if r["arxiv"] else []) \
        + ([f"PMID:{r['pmid']}"] if r["pmid"] else []) + ([r["pmcid"]] if r["pmcid"] else [])
    return " · ".join(parts)


def table(recs: list[dict[str, Any]], header: str, snippet: int) -> str:
    lines = [header, ""]
    for i, r in enumerate(recs, 1):
        lines.append(f"{i}. {safe(r['title'])}")
        meta = [short_authors(r["authors"]), str(r["date"] or r["year"] or "n.d."), r["venue"] or "",
                "preprint" if is_preprint(r) and r["venue"] != "arXiv" else "",
                f"cited {r['citations']}" if r["citations"] is not None else "",
                "in your Zotero" if r["in_zotero"] else ""]
        lines.append("   " + safe(" · ".join(m for m in meta if m)))
        if ids_line(r):
            lines.append("   " + safe(ids_line(r)))
        lines.append("   " + safe(r["url"] or ""))
        if r["oa_url"] and r["oa_url"] != r["url"]:
            lines.append("   open access: " + safe(r["oa_url"]))
        text = r["tldr"] or r["abstract"]
        if text and snippet:
            s = safe(text)
            lines.append("   " + (s if len(s) <= snippet else s[:snippet].rsplit(" ", 1)[0] + "…"))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def md_escape(s: Any) -> str:
    return re.sub(r"([\\`*_\[\]<>|])", r"\\\1", safe(s))


def markdown(recs: list[dict[str, Any]]) -> str:
    lines = []
    for i, r in enumerate(recs, 1):
        url = safe(r["url"] or "").replace(")", "%29").replace(" ", "%20")
        title = f"[{md_escape(r['title'])}]({url})" if url else md_escape(r["title"])
        tail = [f"*{md_escape(r['venue'])}*" if r["venue"] else "", "preprint" if is_preprint(r) and r["venue"] != "arXiv" else "",
                ids_line(r) and md_escape(ids_line(r)),
                f"cited {r['citations']}" if r["citations"] is not None else "",
                f"[open access]({safe(r['oa_url']).replace(')', '%29').replace(' ', '%20')})" if r["oa_url"] else ""]
        lines.append(f"{i}. {md_escape(short_authors(r['authors']))} ({r['year'] or 'n.d.'}). {title}. "
                     + ". ".join(t for t in tail if t))
    return "\n".join(lines) + "\n"


def public(r: dict[str, Any]) -> dict[str, Any]:
    return {k: r.get(k) for k in FIELDS}


def emit(text: str, path: str | None) -> None:
    if path:
        with open(os.path.expanduser(path), "w", encoding="utf-8") as fh:
            fh.write(text)
        return
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except BrokenPipeError:
        with contextlib.suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


# ------------------------------------------------------------------ commands

def rows(source: str, args: argparse.Namespace) -> int:
    """How many results to ask a source for, so that -n remain after merging, filtering and sorting."""
    want = args.n
    if source == "crossref":  # some rows are dropped (figures, reviews; outside relevance, partial matches)
        return 100 if args.sort != "relevance" else min(100, max(want * 2, 20))
    if args.sort == "citations" and source not in ("arxiv", "pubmed"):  # arXiv and PubMed have no counts
        want = max(want * 2, 20)
    if args.oa_only:
        want = max(want * 2, 20)
    return min(100, want)


def add_oa(recs: list[dict[str, Any]], want: int, oa_only: bool, net: Net, note) -> None:
    """Unpaywall links for the leading results that have a DOI but no open-access link yet (needs --email)."""
    if not net.email:
        return
    tries, have = 0, 0
    for r in recs:
        if not r["oa_url"] and r["doi"] and tries < want * (3 if oa_only else 1):
            tries += 1
            try:
                r["oa_url"] = unpaywall(net, r["doi"])
            except Failure as e:
                note(f"Unpaywall failed: {e}; open-access links come from the sources only")
                return
        if r["oa_url"] or not oa_only:
            have += 1
        if have >= want:
            return

def run_search(args: argparse.Namespace, net: Net, note) -> tuple[int, str | None]:
    sources = args.source
    want = args.n
    got: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, Failure] = {}
    ex = ThreadPoolExecutor(max_workers=len(sources))
    try:  # no `with`: on Ctrl-C it would wait for every source's pending request
        futures = {name: ex.submit(SEARCH[name], net, args.query, argparse.Namespace(**{**vars(args), "fetch": rows(name, args)}))
                   for name in sources}
        for name, fut in futures.items():
            try:
                got[name] = fut.result()
            except Failure as e:
                errors[name] = e
            except Exception as e:  # noqa: BLE001 - one source's surprise must not lose the others
                errors[name] = Failure(EXIT_SOURCE, f"unexpected {type(e).__name__}: {e}", "a bug; report it")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    for name, e in errors.items() if len(errors) < len(sources) else ():
        note(f"{name} failed: {e}" + (f" — {e.hint}" if e.hint else "") + "; results come from the other sources")
    recs = [r for name in sources for r in got.get(name, [])]
    recs = [r for r in merge(recs) if in_range(r, args.since, args.until)]
    recs = sort_records(recs, args.sort, sources)
    add_oa(recs, want, args.oa_only, net, note)
    if args.oa_only:
        if not net.email:
            note("--oa-only without --email: only arXiv, Europe PMC and PubMed Central links are known, so other "
                 "open-access papers are left out too")
        recs = [r for r in recs if r["oa_url"]]
    recs = recs[:want]
    if args.zotero:
        try:
            for r in recs:
                r["in_zotero"] = zotero_has(r)
        except ZoteroUnavailable as e:
            note(f"--zotero: {e}")
            for r in recs:
                r["in_zotero"] = None
    if not recs:
        if errors:  # "no results" would read as "nothing exists" when a source could not be asked
            first = next(iter(errors.values()))
            code = EXIT_RATE if any(e.code == EXIT_RATE for e in errors.values()) else first.code
            what = "every source failed" if len(errors) == len(sources) else "no results, and some sources failed"
            raise Failure(code, f"{what}: " + "; ".join(f"{k}: {v}" for k, v in errors.items()), first.hint)
        raise Failure(EXIT_EMPTY, "no results", "broaden the query, drop --since/--until or --oa-only, "
                                                "or try other --source values")
    counts = {name: (len(got[name]) if name in got else f"failed: {errors[name]}") for name in sources}
    header = (f"# {safe(args.query)} · {len(recs)} result{'s' if len(recs) != 1 else ''} · sorted by {args.sort}"
              + (f" · {args.since or '…'} to {args.until or 'now'}" if args.since or args.until else "")
              + "\n# from " + ", ".join(f"{k} {v if isinstance(v, int) else 'failed'}" for k, v in counts.items()))
    if args.format == "json":
        text = json.dumps({"query": args.query, "sort": args.sort, "since": args.since, "until": args.until,
                           "sources": counts, "count": len(recs), "results": [public(r) for r in recs]},
                          ensure_ascii=False, indent=2) + "\n"
    elif args.format == "markdown":
        text = markdown(recs)
    elif args.format == "bibtex":
        used: set[str] = set()
        entries = []
        for r in recs:
            try:
                entries.append(bibtex_for(net, r, None, used))
            except Failure as e:  # the registry is down: the record's own metadata still makes an entry
                note(f"doi.org failed ({e}); BibTeX built from the search record")
                entries.append(local_bibtex(r, used))
        text = "\n".join(b for b in entries if b)
    else:
        text = table(recs, header, args.snippet)
    return EXIT_OK, text


def run_ids(args: argparse.Namespace, net: Net, note) -> tuple[int, str | None]:
    parsed = []
    for s in args.ids:
        p = parse_id(s)
        if not p:
            raise Failure(EXIT_BAD_INPUT, f"{s!r} is not a DOI, arXiv id or PMID",
                          "use 10.xxxx/..., arXiv:2301.00001 or PMID:12345678")
        parsed.append((s, *p))
    found: list[Any] = []
    missing: list[str] = []
    failed: dict[str, Failure] = {}
    used: set[str] = set()
    for s, kind, ident in parsed:
        try:
            if args.command == "bibtex":
                doi = ident if kind == "doi" else f"10.48550/arXiv.{ident}" if kind == "arxiv" else None
                entry = bibtex_for(net, None, doi, used) if doi else None
                if not entry:  # no BibTeX from the registry: build it from the record
                    r = lookup(net, kind, ident, note)
                    entry = bibtex_for(net, r, None, used) if r else None
                (found if entry else missing).append(entry or s)
                continue
            r = lookup(net, kind, ident, note)
        except Failure as e:  # one identifier's outage must not lose the others
            failed[s] = e
            continue
        if r is None:
            missing.append(s)
            continue
        if not r["title"]:
            note(f"{s}: registered, but its registry serves no metadata")
        if net.email and r["doi"] and not r["oa_url"]:
            with contextlib.suppress(Failure):
                r["oa_url"] = unpaywall(net, r["doi"])
        found.append(r)
    for s in missing:
        note(f"{s}: not found")
    for s, e in failed.items():
        note(f"{s}: could not be looked up: {e}")
    if not found:
        if failed:
            first = next(iter(failed.values()))
            raise Failure(first.code, "could not look up " + ", ".join(failed), first.hint)
        raise Failure(EXIT_EMPTY, "not found: " + ", ".join(missing), "check the identifier for typos")
    if args.command == "bibtex":
        text = "\n".join(found)
    elif args.format == "json":
        text = json.dumps({"results": [public(r) for r in found], "not_found": missing,
                           "failed": {k: str(v) for k, v in failed.items()}}, ensure_ascii=False, indent=2) + "\n"
    elif args.format == "markdown":
        text = markdown(found)
    else:
        text = table(found, f"# {len(found)} record{'s' if len(found) != 1 else ''}", args.snippet)
    code = next(iter(failed.values())).code if failed else EXIT_EMPTY if missing else EXIT_OK
    return code, text


def make_parser(command: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"scholar.py{'' if command == 'search' else ' ' + command}", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    if command == "search":
        p.add_argument("query", nargs="+", help="search words; quoted phrases and each source's own syntax work")
        p.add_argument("--source", default=",".join(FREE),
                       help=f"comma-separated: {', '.join(SOURCES)} (default: {','.join(FREE)}); openalex needs "
                            "OPENALEX_API_KEY, semanticscholar needs S2_API_KEY")
        p.add_argument("--since", help="published on or after: YYYY, YYYY-MM or YYYY-MM-DD")
        p.add_argument("--until", help="published on or before: YYYY, YYYY-MM or YYYY-MM-DD")
        p.add_argument("--sort", choices=["relevance", "date", "citations"], default="relevance",
                       help="relevance (default), date (newest first), citations (most cited first)")
        p.add_argument("-n", type=int, default=10, help="results to return, 1-100 (default 10)")
        p.add_argument("--oa-only", action="store_true", help="only results with a known open-access link")
        p.add_argument("--zotero", action="store_true",
                       help="mark results already in your Zotero library (Zotero 7 running, local API on)")
    else:
        p.add_argument("ids", nargs="+", help="DOIs, arXiv ids (arXiv:1706.03762) or PMIDs (PMID:26017442)")
    if command != "bibtex":
        p.add_argument("-f", "--format", choices=["table", "json", "markdown"] + (["bibtex"] if command == "search" else []),
                       default="table", help="output format (default table)")
        p.add_argument("--snippet", type=int, default=240, help="abstract characters in the table, 0 for none")
    p.add_argument("--email", default=os.environ.get("SCHOLAR_EMAIL"),
                   help="your email: Unpaywall open-access links and Crossref's polite pool; sent to every source "
                        "in the User-Agent (or SCHOLAR_EMAIL)")
    p.add_argument("-o", "--output", metavar="FILE", help="write to FILE instead of stdout")
    p.add_argument("--timeout", type=float, default=30.0, help="network timeout in seconds (default 30)")
    p.add_argument("-q", "--quiet", action="store_true", help="no notes on stderr")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv.pop(0) if argv and argv[0] in ("search", "doi", "bibtex") else "search"
    args = make_parser(command).parse_args(argv)
    args.command = command
    note = (lambda m: None) if args.quiet else (lambda m: sys.stderr.write(f"note: {safe(m)}\n"))

    def fail(code: int, message: str, hint: str = "") -> int:
        sys.stderr.write(f"error: {safe(message)}" + (f" — {safe(hint)}" if hint else "") + "\n")
        return code

    if not 1 <= args.timeout <= 300:
        return fail(EXIT_BAD_INPUT, "--timeout must be between 1 and 300 seconds")
    if args.email and not re.fullmatch(r"[\x21-\x7e]+@[\x21-\x7e]+\.[a-zA-Z]{2,}", args.email):
        return fail(EXIT_BAD_INPUT, f"--email {args.email!r} is not an ASCII email address")
    if getattr(args, "snippet", 0) < 0:
        return fail(EXIT_BAD_INPUT, "--snippet must be 0 or more")
    if command == "search":
        args.query = " ".join(" ".join(args.query).split())
        if not args.query:
            return fail(EXIT_BAD_INPUT, "the query is empty")
        if not 1 <= args.n <= 100:
            return fail(EXIT_BAD_INPUT, "-n must be between 1 and 100")
        names = [s.strip().lower() for s in args.source.split(",") if s.strip()]
        names = list(dict.fromkeys(FREE + ["biorxiv"] + [s for s in KEYS if os.environ.get(KEYS[s])]
                                   if names == ["all"] else names))
        bad = [s for s in names if s not in SOURCES]
        if bad or not names:
            return fail(EXIT_BAD_INPUT, f"unknown source {', '.join(bad) or '(none)'}", f"use {', '.join(SOURCES)} or all")
        for s in names:
            if s in KEYS and not os.environ.get(KEYS[s]):
                return fail(EXIT_BAD_INPUT, f"{s} needs {KEYS[s]} in the environment",
                            "it has no usable free tier; leave it out or set the key")
        args.source = names
        try:
            args.since = date_bound(args.since, False) if args.since else None
            args.until = date_bound(args.until, True) if args.until else None
        except ValueError as e:
            return fail(EXIT_BAD_INPUT, f"bad date {e}", "use YYYY, YYYY-MM or YYYY-MM-DD")
        if args.since and args.until and args.since > args.until:
            return fail(EXIT_BAD_INPUT, "--since is after --until")
    net = Net(args.timeout, args.email, note)
    try:
        code, text = run_search(args, net, note) if command == "search" else run_ids(args, net, note)
    except Failure as e:
        return fail(e.code, str(e), e.hint)
    if text:
        try:
            emit(text, args.output)
        except OSError as e:
            emit(text, None)
            return fail(EXIT_BAD_INPUT, f"cannot write {args.output}: {e.strerror or e}", "the output went to stdout")
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("error: interrupted\n")
        sys.stderr.flush()
        os._exit(130)  # source threads may still be sleeping between requests
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
        sys.exit(EXIT_SOURCE)
    except Exception as e:  # noqa: BLE001 - exit 1 means "no results"; a crash must not look like that
        sys.stderr.write(f"error: unexpected {type(e).__name__}: {(str(e).splitlines() or [''])[0][:300]} "
                         "— a bug; report it with the command line\n")
        sys.exit(EXIT_SOURCE)
