"""Tests for skills/human-writing/scripts/lint_prose.py.

Run: uv run --no-project --with pytest python -I -m pytest test/test_human_writing.py -q \
         --doctest-modules skills/human-writing/scripts/lint_prose.py

Offline. test/data/prose/human/ holds 20 human-written texts (18F blog posts from 2017–2018 and 18F guide pages, CC0 /
US public domain); test/data/prose/slop/ holds 10 texts written in the default style of a chat model.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "human-writing" / "scripts" / "lint_prose.py"
DATA = ROOT / "test" / "data" / "prose"
sys.path.insert(0, str(SCRIPT.parent))
import lint_prose as lp  # noqa: E402


def rules_in(text: str, genre: str = "article") -> set[str]:
    return {f["rule"] for f in lp.lint(text, lp.Settings(genre=genre))["findings"]}


def run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], input=stdin, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)


FILLER = ("We moved the build to a bigger runner in March and the queue dropped from forty minutes to six. "
          "The cost went up by about three hundred dollars a month, which finance signed off on after one meeting. ")

# rule, genre, text that must trigger it, rewrite that must not
RULE_CASES = [
    ("not-x-but-y", "article", "It's not about the model. It's about the eval. This isn't just a tool, it's a shift.",
     "The eval matters more than the model."),
    ("negative-list", "article", "Not a framework. Not a library. A set of scripts.", "It is a set of scripts."),
    ("colon-reveal", "article", "The best part: it learns from every run.", "It learns from every run, which is the part I like."),
    ("rhetorical", "article", "The result? Faster builds.", "Builds got faster."),
    ("rhetorical", "article", "Why did the deploy fail? Bad config. " + FILLER, "The deploy failed because of a bad config value. " + FILLER),
    ("throat-clearing", "article", "Here's the thing. Caching hides bugs.", "Caching hides bugs."),
    ("faux-insight", "article", "What most people get wrong is the cache.", "We got the cache wrong."),
    ("metadiscourse", "article", "As you can see, the cache matters.", "The cache cut p95 latency from 900 ms to 120 ms."),
    ("recap-ending", "article", FILLER + "\n\nIn conclusion, caching is important.", FILLER + "\n\nNext month we will try the same on the API."),
    ("triad", "article", "It is fast, cheap, and simple. We want clear, short, and honest docs.", "It is fast and cheap."),
    ("fragment-stack", "article", "Fast. Cheap. Reliable.", "It is fast and cheap, and it has not failed since March."),
    ("repeated-opener", "article", "We tried Redis. We tried Memcached. We tried nothing at all.",
     "We tried Redis first. Memcached came next, and in the end the plain dictionary won."),
    ("em-dash", "article", "It works — mostly — and ships — today.", "It works, mostly, and ships today."),
    ("ai-vocab", "article", "We delve into a robust, seamless tapestry.", "We look at the cache."),
    ("stock-phrase", "article", "It's worth noting that when it comes to caching, timing matters.", "Cache timing matters."),
    ("vague-declarative", "article", "The implications are significant.", "The change doubles the storage bill."),
    ("sales-language", "article", "A world-class, blazing fast cache.", "A cache that answers in 2 ms."),
    ("ing-rider", "article", "The team added retries, highlighting its commitment to reliability.", "The team added retries, so a dropped connection no longer fails the job."),
    ("weasel", "article", "Experts agree that caches fail.", "Our caches failed twice in May."),
    ("copula-avoidance", "article", "The service serves as a hub for jobs.", "The service is a hub for jobs."),
    ("hedge-stack", "article", "This could potentially help.", "This may help."),
    ("bold-label-list", "article", "- **Fast:** yes.\n- **Cheap:** yes.\n- **Safe:** yes.\n", "- Fast.\n- Cheap.\n- Safe.\n"),
    ("bold-inline", "article", "This is **very** **really** **truly** important.", "This is important."),
    ("emoji-heading", "article", "# Release notes 🚀\n\nText here.", "# Release notes\n\nText here."),
    ("emoji-bullet", "article", "- 🔄 Sync\n- ⚡ Speed\n", "- Sync\n- Speed\n"),
    ("heading-density", "article", "## One\n\nShort.\n\n## Two\n\nAlso short.\n", "## One\n\n" + FILLER * 2 + "\n\n## Two\n\n" + FILLER * 2),
    ("list-heavy", "article", "Intro. " + FILLER * 5 + "\n\n" + "".join(f"- point {i} here\n" for i in range(12)),
     "Intro. " + FILLER * 5 + "\n\n- one point\n- two point\n"),
    ("heading-heavy", "article", "".join(f"## Part {i}\n\n" + FILLER + "\n\n" for i in range(6)),
     "## Part one\n\n" + FILLER * 6),
    ("generic", "article", "The team wanted a better way to work. " * 40, FILLER * 6),
    ("title-case-heading", "article", "## How We Built The Cache\n\nText.", "## How we built the cache\n\nText."),
    ("code-fence-lang", "docs", "Run:\n\n```\nls\n```\n", "Run:\n\n```bash\nls\n```\n"),
    ("chat-residue", "article", "Great question! I hope this helps.", "The answer is 42."),
    ("engagement-bait", "post", "Shipped it. Tag someone who needs this.", "Shipped it on Tuesday."),
    ("docs-minimizer", "docs", "Simply run the installer. It's easy.", "Run the installer."),
    ("docs-future", "docs", "This will create a file.", "This creates a file."),
    ("docs-please", "docs", "Please restart the server.", "Restart the server."),
    ("docs-cheer", "docs", "Run the installer. Happy coding!", "Run the installer, then run `acme --version` to check it."),
    ("docs-latin", "docs", "Use a tool, e.g. curl.", "Use a tool, for example curl."),
    ("post-opening-question", "post", "Ever shipped on a Friday? We did, and it broke checkout.", "We shipped on a Friday and broke checkout."),
    ("post-hashtags", "post", "Shipped. #a #b #c", "Shipped. #a #b"),
    ("post-length", "post", "word " * 700, "word " * 100),
    ("post-hook-fold", "post", "This opening sentence keeps going and going " * 6 + ".", "Short opening."),
]


@pytest.mark.parametrize("rule,genre,before,after", RULE_CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(RULE_CASES)])
def test_rule_fires_on_before_and_not_on_after(rule, genre, before, after):
    assert rule in rules_in(before, genre)
    assert rule not in rules_in(after, genre)


def test_every_rule_has_a_case():
    assert set(lp.RULES) - {c[0] for c in RULE_CASES} == {"intensifier", "passive", "kicker", "uniform-rhythm"}


def test_density_rules():
    intensifiers = "It was very, really, truly, literally, actually, basically important. " * 2
    assert "intensifier" in rules_in(intensifiers)
    passive = " ".join(f"The file number {i} was deleted." for i in range(10))
    assert "passive" in rules_in(passive)
    assert "kicker" in rules_in(KICKER)
    even = " ".join(f"The service number {i} handles about one hundred requests." for i in range(16))
    assert "uniform-rhythm" in rules_in(even)


def test_markdown_noise_is_skipped():
    text = ("---\ntitle: Delve robust seamless\n---\n\n"
            "This line is here so the masked parts sit between real sentences.\n\n```python\n# delve into a robust tapestry\n```\n\n"
            "You can run `leverage --robust` from the shell, or read https://example.com/robust/seamless first.\n\n"
            "The guide is on [the project site](https://example.com/delve/robust/tapestry) for now.\n\n"
            "> Experts agree this is a game-changer — a robust tapestry.\n\n"
            "| delve | robust |\n|---|---|\n| seamless | tapestry |\n\n"
            "The full reference lives in [the project docs](https://example.com/seamless) on the website.\n")
    assert rules_in(text) == set()


KICKER = ("We rebuilt the deploy pipeline over the winter and cut the median release time by a lot. It worked.\n\n"
          "The new runner pool cost more than we planned for in the first quarter of the year. Finance agreed.\n\n")


def test_markdown_structure_edge_cases():
    # a leading --- rule followed by prose is not front matter
    assert "ai-vocab" in rules_in("---\n\nThis paragraph will delve into robust, seamless tapestry.\n\n---\n\nMore.")
    # indented code, nested fences, tables without a leading pipe, comments spanning lines
    assert "ai-vocab" not in rules_in("Intro text here.\n\n    def leverage(robust):\n        return seamless\n")
    assert "ai-vocab" not in rules_in("- item\n  - nested\n\n    ```\n    leverage robust delve\n    ```\n")
    assert "ai-vocab" in rules_in("- item\n\n    This continuation will delve into a robust, seamless tapestry.\n")
    assert "ai-vocab" not in rules_in("Name | Role\n--- | ---\nAlice | leverages robust synergy\n")
    assert "ai-vocab" not in rules_in("Text here. <!-- start\nwe delve into a robust tapestry\n-->\n")
    assert lp.lint("<!-- a\nb -->Real prose that runs on.", lp.Settings())["words"] == 5
    # a backtick "fence" whose info string has a backtick is inline code, not a fence
    assert lp.lint("```js``` starts a block, and this sentence goes on.", lp.Settings())["words"] >= 6
    # setext headings
    assert "title-case-heading" in rules_in("My Great Title Here\n===================\n\nBody text.")
    # sentence splitting around URLs and "no."
    assert len(lp.parse("See https://example.com. Then go home now.").sentences) == 2
    assert len(lp.parse("I said no. We left early. See No. 5 here.").sentences) == 3


def test_verification_regressions():
    # an HTML comment opener in inline code or plain prose doesn't hide the rest of the text
    assert "ai-vocab" in rules_in("Use `<!--` to open a comment.\n\nThis will delve into a robust, seamless tapestry.")
    assert "ai-vocab" in rules_in("The token <!-- opens a comment.\n\nThis will delve into a robust, seamless tapestry.")
    # front matter with blank lines, comments or odd lines is still front matter
    for fm in ("---\ntitle: Hello\n\nauthor: Me\n---\nBody text here.", "---\n# note\ntitle: Hello\n---\nBody text here.",
               "---\n[weird]\ntitle: Hello\n---\nBody text here."):
        assert lp.lint(fm, lp.Settings())["words"] == 3
    # a finding spanning two sentences doesn't hide a phrase finding inside it
    assert {"rhetorical", "weasel"} <= rules_in("Did we ship it? Experts agree we did.\n\nMore words here.")
    # indented code with a fence line inside, and indented code inside list items
    assert lp.lint("Intro.\n\n    Example:\n\n    ```\n    code\n\nText after.\n\nMore text.", lp.Settings())["words"] >= 5
    assert "ai-vocab" not in rules_in("1. Step one:\n\n       npm install leverage robust\n\n2. Step two.")
    assert "ai-vocab" not in rules_in("- item\n\n## Heading\n\n    leverage = robust\n")


def test_overlapping_phrase_rules_report_once():
    for text in ("Here's what nobody tells you about caching.", "Some text.\n\nAt the end of the day, it works.",
                 "It serves as a testament to effort."):
        found = [f["rule"] for f in lp.lint(text, lp.Settings())["findings"] if f["rule"] in lp.PHRASE_PRIORITY]
        assert len(found) == 1, found


def test_misc_regressions():
    assert "bold-inline" not in rules_in("Call __init__ and __repr__ and __eq__ and __hash__ now.")
    cfg = lp.Settings(allow_words=set(lp.INTENSIFIERS))
    assert not [f for f in lp.lint("This is a very simple test.", cfg)["findings"] if f["rule"] == "intensifier"]
    front = "---\ntitle: " + "x" * 300 + "\n---\nShort opener here.\n\nMore text."
    assert "post-hook-fold" not in rules_in(front, "post")


@pytest.mark.parametrize("text", ["x " + "`" * 3000, "[" * 200_000, "**a " * 50_000, "<!--" * 50_000, "<a" * 100_000,
                                  "".join("`" * n + "x" for n in range(3, 300)) + "`a` " * 35_000])
def test_pathological_input_is_fast(text):
    start = time.monotonic()
    lp.lint(text, lp.Settings())
    assert time.monotonic() - start < 10


def test_genre_switches_rules():
    assert "docs-minimizer" not in rules_in("Simply run it.", "article")
    assert "kicker" in rules_in(KICKER, "article")
    assert "kicker" not in rules_in(KICKER, "docs")
    assert lp.RULES["post-length"].severity("article") is None


def severities(text: str, rule: str, genre: str = "article") -> list[str]:
    return [f["severity"] for f in lp.lint(text, lp.Settings(genre=genre))["findings"] if f["rule"] == rule]


def test_budgets_allow_one_occurrence():
    body = FILLER * 6  # ~230 words
    assert "not-x-but-y" not in rules_in(body + "It's not about the model. It's about the eval.")
    assert "triad" not in rules_in(body + "It is fast, cheap, and simple.")
    assert "emoji-bullet" not in rules_in("- 🔄 Sync\n- Speed\n")
    assert "bold-label-list" not in rules_in("- **Fast:** yes.\n- **Cheap:** yes.\n")
    assert "intensifier" not in rules_in(body + "It was very fast.")
    assert severities(body + "A robust cache.", "ai-vocab") == ["info"]
    assert set(severities("We delve into a robust, seamless tapestry.", "ai-vocab")) == {"warning"}
    one_passive = " ".join([f"Team {i} shipped the change on time." for i in range(9)] + ["The file was deleted."])
    assert "passive" not in rules_in(one_passive)
    one_kicker = KICKER.split("\n\n")[0] + "\n\n" + FILLER + "\n\n" + FILLER + "\n\n" + FILLER
    assert "kicker" not in rules_in(one_kicker)


def test_heading_density_ignores_parent_headings():
    text = "## Setup\n\nShort intro.\n\n### Linux\n\n" + FILLER * 2 + "\n\n### macOS\n\n" + FILLER * 2
    assert "heading-density" not in rules_in(text)


def test_less_common_paths():
    assert "weasel" not in rules_in("Studies show it helps ([source](https://example.com/study)).")
    assert "recap-ending" in rules_in(FILLER + "\n\nUltimately, the queue matters.")
    assert "recap-ending" not in rules_in("Ultimately, the queue matters.\n\n" + FILLER)
    assert "hedge-stack" in rules_in("This might perhaps possibly help.")
    assert "docs-future" not in rules_in("This will not change the file.", "docs")
    cfg = lp.Settings(allow_words={"it's worth noting"})
    assert "stock-phrase" not in {f["rule"] for f in lp.lint("It's worth noting the cache.", cfg)["findings"]}


def test_dash_budget_scales_with_length():
    long_text = (FILLER * 4 + "It works — mostly. ") * 3
    assert "em-dash" not in rules_in(long_text)


def test_allow_words_and_disabled_rules():
    cfg = lp.Settings(allow_words={"robust"}, disabled={"chat-residue"})
    found = {f["rule"] for f in lp.lint("A robust cache. Great question!", cfg)["findings"]}
    assert found == set()


def test_score():
    assert lp.lint("", lp.Settings())["score"] == 100
    assert lp.score([lp.Finding("x", "error", 1, 1, "", "", "")] * 3, 300) == 50


# ------------------------------------------------------------------------------------------------ corpora

HUMAN = sorted((DATA / "human").glob("*.md"))
SLOP = sorted((DATA / "slop").glob("*.md"))


def genre_of(path: Path) -> str:
    return path.name.split("-")[0]


def test_corpora_present():
    assert len(HUMAN) >= 40 and len(SLOP) >= 10


@pytest.mark.parametrize("path", HUMAN, ids=lambda p: p.name)
def test_human_text_passes(path):
    result = lp.lint(path.read_text(encoding="utf-8"), lp.Settings(genre=genre_of(path)))
    assert result["counts"]["error"] == 0
    assert result["score"] >= lp.DEFAULT_THRESHOLD, [f["rule"] for f in result["findings"]]


@pytest.mark.parametrize("path", SLOP, ids=lambda p: p.name)
def test_slop_fails(path):
    r = lp.lint(path.read_text(encoding="utf-8"), lp.Settings(genre=genre_of(path)))
    assert r["score"] < lp.DEFAULT_THRESHOLD or r["counts"]["error"] > 0


MODEL = sorted((DATA / "model").glob("*.md"))


def test_model_output_articles_and_posts_mostly_fail():
    # Real model output (generate_slop.py). Docs from a model are often fine and "humanized" output is a hard case,
    # so only articles and posts written with no style instruction are held to a floor; see the evals README.
    plain = [p for p in MODEL if not p.stem.endswith("humanized") and genre_of(p) in ("article", "post")]
    failed = 0
    for path in plain:
        r = lp.lint(path.read_text(encoding="utf-8"), lp.Settings(genre=genre_of(path)))
        failed += r["score"] < lp.DEFAULT_THRESHOLD or r["counts"]["error"] > 0
    assert len(plain) >= 20 and failed / len(plain) >= 0.6


def test_seeded_cases_are_consistent():
    cases = json.loads((DATA / "seeded" / "cases.json").read_text(encoding="utf-8"))
    briefs = {p.stem for p in (SCRIPT.parents[1] / "references" / "reviewers").glob("*.md")}
    assert len(cases) >= 6
    for name, case in cases.items():
        text = (DATA / "seeded" / f"{name}.md").read_text(encoding="utf-8")
        assert (DATA / "seeded" / f"{name}.ledger.md").exists()
        assert set(case["lenses"]) <= briefs and case["genre"] in lp.GENRES
        if "voice" in case:
            assert (DATA / "seeded" / case["voice"]).exists()
        for d in case["defects"]:
            assert text.count(d["quote"]) == 1, (name, d["id"])
            assert set(d["lenses"]) <= set(case["lenses"]), (name, d["id"])


# -------------------------------------------------------------------------------------------------- CLI

def test_cli_pass_and_fail(tmp_path):
    good = tmp_path / "good.md"
    good.write_text(FILLER * 3, encoding="utf-8")
    assert run(str(good)).returncode == 0
    r = run(str(DATA / "slop" / "article-01-observability.md"))
    assert r.returncode == 1 and "FAIL" in r.stdout and "ai-vocab" in r.stdout


def test_cli_json_shape():
    r = run("-", "-g", "post", "-f", "json", stdin="Great question! Tag someone.")
    data = json.loads(r.stdout)
    assert r.returncode == 1
    assert {"file", "genre", "words", "score", "threshold", "pass", "counts", "stats", "findings"} <= data.keys()
    assert {"rule", "severity", "line", "col", "text", "message", "hint"} == data["findings"][0].keys()
    assert data["file"] == "stdin" and data["pass"] is False


def test_cli_bad_input(tmp_path):
    assert run(str(tmp_path / "missing.md")).returncode == 2
    assert run().returncode == 2
    assert run("x.md", "--threshold", "200").returncode == 2
    assert run("x.md", "--genre", "poem").returncode == 2
    bad_cfg = tmp_path / "bad.toml"
    bad_cfg.write_text('[rules]\ndisable = ["no-such-rule"]\n', encoding="utf-8")
    assert run("-", "--config", str(bad_cfg), stdin="x").returncode == 2
    latin1 = tmp_path / "latin1.md"
    latin1.write_bytes("caf\xe9".encode("latin-1"))
    assert run(str(latin1)).returncode == 7


@pytest.mark.parametrize("toml", ['rules = "x"\n', '[severity]\nkicker = 1\n', '[words]\nallow = [1]\n',
                                  '[rules]\ndisable = "kicker"\n', '[words]\nallow = "robust"\n'])
def test_cli_bad_config_types(tmp_path, toml):
    cfg = tmp_path / "bad.toml"
    cfg.write_text(toml, encoding="utf-8")
    r = run("-", "--config", str(cfg), stdin="Text.")
    assert r.returncode == 2 and "error:" in r.stderr


def test_cli_voice_and_config(tmp_path):
    voice = tmp_path / "voice.md"
    voice.write_text("# Voice\n\n- lint-allow-rules: em-dash, chat-residue\n- lint-allow-words: robust\n", encoding="utf-8")
    cfg = tmp_path / "rules.toml"
    cfg.write_text('[words]\nai_vocab = ["synergize"]\n[severity]\nweasel = "error"\n', encoding="utf-8")
    text = "A robust cache — it works. Great question! We synergize. Experts agree."
    data = json.loads(run("-", "-f", "json", "--voice", str(voice), "--config", str(cfg), stdin=text).stdout)
    found = {(f["rule"], f["text"].lower()) for f in data["findings"]}
    assert ("ai-vocab", "synergize") in found
    assert not {r for r, _ in found} & {"em-dash", "chat-residue"}
    assert ("ai-vocab", "robust") not in found
    assert any(f["rule"] == "weasel" and f["severity"] == "error" for f in data["findings"])


def test_cli_crlf_and_bom(tmp_path):
    f = tmp_path / "win.md"
    f.write_bytes(b"\xef\xbb\xbf# Title\r\n\r\nWe delve into it.\r\n\r\n```\r\ncode\r\n```\r\n")
    data = json.loads(run(str(f), "-f", "json", "-g", "docs").stdout)
    found = {(x["rule"], x["line"], x["text"]) for x in data["findings"]}
    assert ("ai-vocab", 3, "delve") in found
    assert ("code-fence-lang", 5, "```") in found


def test_cli_list_rules():
    r = run("--list-rules", "-f", "json")
    assert r.returncode == 0
    assert {x["id"] for x in json.loads(r.stdout)} == set(lp.RULES)
