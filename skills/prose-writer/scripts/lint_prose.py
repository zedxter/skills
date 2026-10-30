#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Lint English prose in Markdown for the patterns that make text read as machine-written: stock AI
vocabulary, staged contrasts ("not X, it's Y"), colon reveals, self-answered questions, forced triads,
dash and bold habits, metronomic rhythm, chat residue, recap endings. Offline, no model, no API key.

    uv run lint_prose.py draft.md                          # article profile, text report
    uv run lint_prose.py post.md --genre post              # social post (LinkedIn and similar)
    uv run lint_prose.py README.md --genre docs -f json    # technical documentation
    cat draft.md | uv run lint_prose.py - --voice voice-profile.md
    uv run lint_prose.py --list-rules

The linter names patterns and quotes the line. It does not guess whether a model wrote the text.

Exit codes:
    0 score at or above the threshold and no error-level finding · 1 score below the threshold or an
    error-level finding · 2 bad input (missing file, bad option, unreadable --config or --voice) · 7 the
    input is not UTF-8 text
"""

import argparse
import bisect
import json
import re
import statistics
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

EXIT_OK, EXIT_FAIL, EXIT_BAD_INPUT, EXIT_PARSE = 0, 1, 2, 7
GENRES = ("article", "post", "docs")
SEVERITY_WEIGHT = {"error": 10.0, "warning": 3.0, "info": 0.5}
DEFAULT_THRESHOLD = 75


# --------------------------------------------------------------------------------------------- rules

@dataclass(frozen=True)
class Rule:
    id: str
    article: str | None  # severity per genre; None = rule off for that genre
    post: str | None
    docs: str | None
    summary: str
    hint: str

    def severity(self, genre: str) -> str | None:
        return getattr(self, genre)


RULES = {r.id: r for r in [
    # Staging instead of stating
    Rule("not-x-but-y", "warning", "warning", "info", "Staged contrast: \"not X, but Y\" / \"It's not X. It's Y.\" (budget: 1 per piece)",
         "State Y directly. Keep a contrast only when the reader really believes X."),
    Rule("negative-list", "warning", "warning", "info", "Negative listing: \"Not X. Not Y. Z.\"", "Say Z."),
    Rule("colon-reveal", "warning", "warning", "info", "Colon reveal: a short setup, a colon, then a lowercase punchline",
         "Write a plain sentence. Use colons for lists, labels and quotes."),
    Rule("rhetorical", "warning", "warning", "info", "Self-answered or staged question (\"The result? …\", \"Sound familiar?\")",
         "Make the statement. Keep questions you actually leave to the reader."),
    Rule("throat-clearing", "warning", "warning", "info", "Throat-clearing opener (\"Here's the thing\", \"Let's be honest\", \"Plot twist\")",
         "Cut it and start with the point."),
    Rule("faux-insight", "error", "error", "warning", "Faux-insight setup (\"what nobody tells you\", \"most people don't realize\")",
         "Drop the setup; let the claim stand on its own evidence."),
    Rule("metadiscourse", "warning", "warning", "info", "Writing about the text instead of the subject (\"as you can see\", \"let's unpack\")",
         "Delete the aside or replace it with the fact it points at."),
    Rule("kicker", "warning", "warning", None, "Paragraphs keep ending on a short punchy line",
         "End on the last concrete point. Vary how paragraphs close."),
    Rule("recap-ending", "warning", "warning", None, "Summary ending (\"In conclusion\", \"Ultimately\", \"The bottom line\")",
         "End on the last concrete point, takeaway or next action."),
    # Rhythm by rule
    Rule("triad", "warning", "warning", None, "Groups of three over budget (1 per 250 words)", "Use two items, or four, or the one that matters."),
    Rule("fragment-stack", "warning", "warning", None, "Three or more fragments in a row (\"Fast. Cheap. Reliable.\")",
         "Write a full sentence that says how and why."),
    Rule("repeated-opener", "warning", "warning", "info", "Three or more sentences in a row start with the same word",
         "Restructure one or two of them."),
    Rule("uniform-rhythm", "warning", "warning", None, "Sentence lengths are too even across the piece",
         "Mix short and long sentences where the content calls for it."),
    Rule("em-dash", "warning", "warning", "warning", "More dashes than the budget for this length",
         "Use a period, comma, colon or parentheses for most of them."),
    # Inflation and borrowed authority
    Rule("ai-vocab", "warning", "warning", "warning", "Word that has become an AI marker (delve, leverage, robust, seamless…)",
         "Use the plain word, or a number or name that shows the thing."),
    Rule("stock-phrase", "warning", "warning", "warning", "Stock phrase (\"it's worth noting\", \"when it comes to\", \"plays a crucial role\")",
         "Cut it or replace it with the specific fact."),
    Rule("vague-declarative", "warning", "warning", "warning", "Vague declarative (\"the implications are significant\")",
         "Name the implication."),
    Rule("sales-language", "warning", "warning", "warning", "Sales adjective (\"world-class\", \"blazing fast\", \"effortless\")",
         "Show the number or behaviour that earns the adjective."),
    Rule("ing-rider", "warning", "warning", "warning", "Shallow -ing clause tacked on to explain meaning (\", highlighting…\")",
         "Say what actually follows, or cut the clause."),
    Rule("weasel", "warning", "warning", "warning", "Unattributed authority (\"experts agree\", \"studies show\")",
         "Name and link the source, or drop the claim."),
    Rule("copula-avoidance", "info", "info", "info", "\"serves as\" / \"stands as\" / \"boasts\" where \"is\" or \"has\" works",
         "Prefer is / has."),
    Rule("hedge-stack", "info", "info", "info", "Stacked hedges (\"could potentially\", three qualifiers in one sentence)",
         "Keep one hedge, the one that is true."),
    Rule("intensifier", "info", "info", "info", "Intensifiers over budget (very, really, truly, literally, fundamentally…)",
         "Cut them; let the fact carry the weight."),
    Rule("passive", "info", "info", "info", "Passive voice in more than a quarter of sentences", "Name who does what."),
    # Formatting
    Rule("bold-inline", "warning", "warning", None, "Bold sprinkled inside sentences (over budget)", "Let sentence structure carry emphasis."),
    Rule("bold-label-list", "warning", "warning", None, "List of \"**Label:** text\" items", "Write the list as prose, or drop the labels."),
    Rule("emoji-heading", "warning", "warning", "warning", "Emoji in a heading", "Remove it."),
    Rule("emoji-bullet", "warning", "info", "warning", "Emoji used as bullets", "Use plain list markers or prose."),
    Rule("heading-density", "warning", None, None, "Heading over a section of only a sentence or two", "Merge the section into its neighbour."),
    Rule("title-case-heading", "info", None, "info", "Heading in Title Case", "Use sentence case."),
    Rule("code-fence-lang", "warning", None, "warning", "Code block without a language tag", "Add the language after the opening fence."),
    # Leftovers from the chat
    Rule("chat-residue", "error", "error", "error", "Chatbot residue (\"Great question\", \"I hope this helps\", knowledge-cutoff notes)",
         "Delete it."),
    Rule("engagement-bait", "warning", "warning", None, "Engagement bait (\"tag someone\", \"follow for more\", \"Agree?\")",
         "Ask a specific question, or end without a call to action."),
    # Docs only
    Rule("docs-minimizer", None, None, "warning", "Minimizing word in docs (simply, just, easy, obviously)",
         "Delete it. What is easy for you is the step where the reader is stuck."),
    Rule("docs-future", None, None, "info", "Future tense in documentation (\"this will create\")", "Use present tense: \"this creates\"."),
    Rule("docs-please", None, None, "info", "\"please\" in instructions", "Give the instruction directly."),
    Rule("docs-latin", None, None, "info", "Latin abbreviation (e.g., i.e., etc., via)", "Use \"for example\", \"that is\", \"and so on\", \"through\"."),
    # Posts only
    Rule("post-opening-question", None, "warning", None, "Post opens with a question", "Open with a statement or a number; move the question to the end."),
    Rule("post-hook-fold", None, "info", None, "First sentence runs past the ~210-character \"see more\" fold", "Shorten the opening line."),
    Rule("post-length", None, "error", None, "Post is longer than LinkedIn's 3,000-character limit", "Cut it, or publish it as an article."),
    Rule("post-hashtags", None, "warning", None, "More than three hashtags", "Keep 0–2, at the end."),
]}


# ------------------------------------------------------------------------------------------ word lists

def _phrases(*items: str) -> list[str]:
    return list(items)


AI_VOCAB = _phrases(
    "delve", "delves", "delved", "delving", "tapestry", "testament", "realm", "realms", "beacon", "multifaceted",
    "meticulous", "meticulously", "intricate", "intricacies", "paramount", "pivotal", "transformative", "elevate",
    "elevates", "elevating", "embark", "embarks", "embarking", "supercharge", "supercharged", "supercharges",
    "harness", "harnessing", "harnesses", "ever-evolving", "ever-changing", "leverage", "leverages", "leveraging",
    "leveraged", "utilize", "utilizes", "utilizing", "utilization", "facilitate", "facilitates", "facilitating",
    "empower", "empowers", "empowering", "streamline", "streamlines", "streamlined", "streamlining", "robust",
    "seamless", "seamlessly", "cutting-edge", "game-changer", "game changer", "unleash", "unleashes", "unleashing",
    "foster", "fosters", "fostering", "bolster", "bolsters", "underscore", "underscores", "underscoring",
    "showcase", "showcases", "showcasing", "holistic", "synergy", "synergies", "groundbreaking", "revolutionize",
    "revolutionizes", "revolutionizing", "nuanced", "vibrant", "bustling", "commendable", "noteworthy",
    "invaluable", "interplay", "unwavering", "indelible", "enduring legacy", "rich tapestry", "myriad",
    "plethora", "endeavor", "endeavors", "navigating", "unlock", "unlocks", "unlocking",
)

STOCK_PHRASES = _phrases(
    r"it'?s worth noting", r"it is worth noting", r"it'?s important to (?:note|remember|understand)",
    r"it is important to (?:note|remember|understand)", r"at the end of the day", r"in today'?s (?:fast-paced|digital|modern|world)",
    r"in the ever-(?:evolving|changing)", r"in the age of", r"when it comes to", r"at its core", r"the reality is",
    r"the truth is", r"let'?s dive (?:in|into)", r"dive deep(?:er)? into", r"deep dive", r"without further ado",
    r"plays? an? (?:crucial|vital|pivotal|key|significant|important) role", r"stands as an?", r"in the realm of",
    r"marks? an? (?:pivotal|significant|major|new) (?:moment|milestone|shift|chapter|turning point)",
    r"navigate the (?:complexities|challenges|landscape|world)", r"unlock (?:the|your) (?:full )?potential",
    r"this changes everything", r"buckle up", r"look no further", r"rest assured", r"the [\w-]+ landscape",
    r"on a journey", r"take (?:it|your [\w-]+) to the next level", r"in a world where", r"feel free to",
    r"the power of", r"a wide (?:range|array|variety) of", r"a (?:rich|vibrant) (?:history|tapestry)",
    r"(?:serves|stands) as a (?:reminder|testament)", r"nothing short of", r"a must-read",
)

THROAT_CLEARING = _phrases(
    r"here'?s the thing", r"here is the thing", r"here'?s what", r"here'?s why", r"here'?s how", r"here'?s the kicker",
    r"let me be (?:clear|honest)", r"let'?s be (?:honest|real|clear)", r"i'?ll be honest", r"to be (?:honest|fair),",
    r"the uncomfortable truth", r"make no mistake", r"spoiler(?: alert)?:", r"plot twist", r"real talk",
    r"hot take:", r"unpopular opinion:", r"let that sink in", r"read that again", r"that'?s the whole thing",
    r"but here'?s the (?:catch|twist|problem)", r"the kicker:",
)

FAUX_INSIGHT = _phrases(
    r"what (?:most people|nobody|no one|everyone|people) (?:gets? wrong|miss(?:es)?|tells? you|talks? about|realizes?|understands?)",
    r"the part (?:everyone|most people|nobody|no one) (?:misses|skips|ignores|gets wrong)",
    r"most people (?:don'?t|do not|never) (?:realize|understand|know|notice|see)",
    r"here'?s what (?:nobody|no one|most people|they)", r"nobody (?:tells you|talks about|is talking about)",
    r"what they don'?t tell you", r"the dirty (?:little )?secret", r"the secret nobody",
)

METADISCOURSE = _phrases(
    r"as you can see", r"as we(?:'ve| have) (?:seen|discussed)", r"as (?:mentioned|noted|discussed) (?:earlier|above|before|previously)",
    r"in other words", r"this (?:distinction|point|part|detail|nuance) (?:matters|is key|is crucial)",
    r"that last (?:part|point|bit) (?:matters|is key|is important)", r"the key (?:point|takeaway|insight|thing) (?:is|here)",
    r"let'?s (?:take a (?:closer )?look|explore|break (?:it|this|that) down|unpack|walk through)",
    r"in this (?:article|post|piece|essay),? (?:we|i)(?:'ll| will)", r"the rest of this (?:article|post|piece|essay)",
    r"by the end of this (?:article|post|piece)", r"keep reading", r"more on (?:that|this) (?:later|below)",
    r"with that (?:said|in mind)", r"having said that", r"now,? let'?s",
)

CHAT_RESIDUE = _phrases(
    r"great question", r"i hope this helps", r"hope this helps", r"certainly!", r"absolutely!", r"as an ai\b",
    r"as a (?:large )?language model", r"as of my (?:last )?(?:knowledge|training)", r"my knowledge cut-?off",
    r"i (?:can'?t|cannot) browse", r"happy to help", r"i'?d be happy to", r"is there anything else",
    r"here(?:'s| is) (?:a|an|the|your) (?:revised|updated|rewritten|polished|improved|refined) (?:version|draft|text)",
    r"sure,? here(?:'s| is)", r"i hope you (?:found|find) this",
)

VAGUE = _phrases(
    r"the (?:implications|reasons|stakes|benefits|possibilities|consequences|results|opportunities) are (?:significant|clear|structural|enormous|endless|profound|huge|immense|real|vast)",
    r"this is (?:huge|massive|a big deal)", r"the possibilities are endless", r"the future is (?:bright|here|now)",
    r"only time will tell", r"(?:it|this) remains to be seen", r"one thing is (?:clear|certain)", r"the rest is history",
)

SALES = _phrases(
    r"game-changing", r"best-in-class", r"world-class", r"next-generation", r"state-of-the-art", r"unparalleled",
    r"revolutionary", r"effortless(?:ly)?", r"blazing(?:ly)?[- ]fast", r"lightning[- ]fast", r"industry-leading",
    r"must-have", r"second to none", r"unmatched",
)

ENGAGEMENT_BAIT = _phrases(
    r"tag (?:someone|a friend|a colleague)", r"follow (?:me )?for more", r"repost if", r"share if you agree",
    r"like if you", r"drop a comment", r"smash (?:that|the) like", r"who else\b[^.?!]*\?", r"comment [\"“']?\w+[\"”']? (?:below )?(?:to|and i'?ll)",
    r"\bagree\?\s*$", r"♻️",
)

ING_RIDERS = r",\s+(?:highlighting|underscoring|showcasing|reflecting|emphasizing|emphasising|demonstrating|illustrating|signaling|signalling|cementing|solidifying|paving the way|fostering|contributing to|reinforcing|setting the stage|ushering in)\b"
COPULA = r"\b(?:serves as|stands as|acts as|functions as)\s+(?:a|an|the)\b|\bboasts\s+(?:a|an|over|more|some)\b"
WEASEL = (r"\b(?:experts|analysts|researchers|scientists|critics|observers) (?:say|agree|believe|argue|suggest|warn)\b"
          r"|\b(?:studies|research|surveys) (?:show|shows|suggest|suggests|indicate|indicates|prove|proves)\b"
          r"|\bmany (?:people |developers |engineers |experts )?(?:argue|believe|say|feel|agree)\b"
          r"|\bit (?:is|has been) (?:widely|generally|commonly) (?:believed|accepted|known|acknowledged|regarded)\b"
          r"|\bindustry (?:reports|experts|observers)\b|\bsome (?:say|argue|would argue)\b|\bwidely regarded as\b"
          r"|\baccording to (?:experts|many|some)\b")
HEDGE_PAIRS = r"\b(?:could|may|might) (?:potentially|possibly|perhaps)\b|\bperhaps (?:possibly|potentially)\b"
HEDGES = {"might", "may", "could", "possibly", "perhaps", "potentially", "arguably", "somewhat", "generally", "typically",
          "likely", "seemingly", "seems", "appears", "presumably", "conceivably", "relatively", "fairly"}
INTENSIFIERS = {"very", "really", "truly", "incredibly", "extremely", "absolutely", "literally", "actually", "basically",
                "essentially", "fundamentally", "crucially", "importantly", "ultimately", "deeply", "highly", "totally",
                "completely", "remarkably", "undeniably"}
DOCS_MINIMIZERS = r"\b(?:simply|easy|easily|obviously|clearly|of course|straightforward|trivially|merely|effortlessly|painless(?:ly)?)\b|\bjust\b(?! as\b|-in-time| in time)"
RHETORICAL_PHRASES = _phrases(
    r"what if i told you", r"think about it", r"sound familiar\?", r"why does (?:this|it|that) matter\?",
    r"so what does (?:this|that|it) mean\?", r"the (?:result|answer|catch|twist|kicker|problem|solution)\?",
    r"(?:have you )?ever wondered", r"guess what[.!?]", r"why\? because",
)
RECAP_OPENERS = r"^(?:in conclusion|in summary|to sum up|to summarize|to summarise|ultimately|overall|all in all|at the end of the day|the bottom line|in short|the takeaway|in closing)\b"
COLON_LABELS = {"note", "notes", "warning", "caution", "tip", "important", "example", "examples", "default", "usage", "output",
                "update", "edit", "tl;dr", "tldr", "p.s", "ps", "q", "a", "pros", "cons", "source", "sources", "credit",
                "caveat", "result", "input", "returns", "see also", "step", "fix", "problem", "solution", "why", "how",
                "before", "after", "error", "todo", "status", "date", "time", "cost", "price", "version", "author"}
PASSIVE = re.compile(r"\b(?:is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?(?:\w+ed|built|done|made|given|taken|shown|written|known|"
                     r"seen|found|left|run|set|sent|kept|held|brought|bought|caught|taught|thought|told|sold|paid|put|"
                     r"read|led|lost|meant|chosen|driven|broken|spoken|stolen|frozen|hidden|ridden|forgotten|gotten|begun)\b", re.I)
EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF⭐⬆⬇⤴⤵‼⁉]")
DASHES = re.compile(r"—|(?<=\s)–(?=\s)|(?<=\s)--(?=\s)|(?<=\w)--(?=\w)")


def _compile(phrases: list[str]) -> re.Pattern:
    alts = [p.replace("'", "['’]") for p in sorted(set(phrases), key=len, reverse=True)]
    return re.compile(r"(?<![\w-])(?:" + "|".join(alts) + r")(?![\w-])", re.I)


# ------------------------------------------------------------------------------------ markdown model

@dataclass
class Block:
    kind: str  # para | heading | item
    start: int
    end: int
    level: int = 0


@dataclass
class Sentence:
    start: int
    end: int
    text: str
    words: int
    block: int  # index into Document.blocks


@dataclass
class Document:
    text: str
    md: str  # code, front matter, URLs, quotes, tables, HTML masked; Markdown syntax kept
    prose: str  # additionally heading/list markers and emphasis characters masked
    blocks: list[Block]
    sentences: list[Sentence]
    fences: list[tuple[int, str]]  # (offset of the opening fence, info string)
    line_starts: list[int]
    words: int = 0

    def pos(self, offset: int) -> tuple[int, int]:
        line = bisect.bisect_right(self.line_starts, offset) - 1
        return line + 1, offset - self.line_starts[line] + 1


FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
HEADING = re.compile(r"^ {0,3}(#{1,6})\s+")
LIST_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d{1,3}[.)])\s+(?:\[[ xX]\]\s+)?")
HR = re.compile(r"^ {0,3}([-*_])(?:\s*\1){2,}\s*$")
TABLE = re.compile(r"^\s*\||^\s*:?-{3,}:?\s*\|")
QUOTE = re.compile(r"^ {0,3}>")
ABBREV = re.compile(r"\b(?:e\.g|i\.e|etc|vs|Mr|Mrs|Ms|Dr|St|approx|No|Fig|cf|a\.m|p\.m|U\.S|U\.K)\.", re.I)
INNER_DOT = re.compile(r"(?<=\w)[.](?=\w)")
SENTENCE = re.compile(r"[^.!?]+(?:[.!?]+[\"”’)\]]*|$)")
WORD = re.compile(r"[A-Za-z0-9][\w'’-]*")


def _blank(s: str) -> str:
    return re.sub(r"[^\n]", " ", s)


def _mask(line: str, pattern: str, keep_group: int | None = None, flags: int = 0) -> str:
    def repl(m: re.Match) -> str:
        if keep_group is None:
            return " " * len(m.group(0))
        s, e = m.span(keep_group)
        s, e = s - m.start(), e - m.start()
        g = m.group(0)
        return " " * s + g[s:e] + " " * (len(g) - e)
    return re.sub(pattern, repl, line, flags=flags)


def _mask_inline(line: str) -> str:
    line = _mask(line, r"<!--.*?-->")
    line = _mask(line, r"!\[[^\]]*\]\([^)]*\)")
    line = _mask(line, r"\[([^\]]+)\]\([^)]*\)", keep_group=1)
    line = _mask(line, r"\[([^\]]+)\]\[[^\]]*\]", keep_group=1)
    line = _mask(line, r"(`+)(?:(?!\1).)+?\1")
    line = _mask(line, r"<https?://[^>]+>")
    line = _mask(line, r"\bhttps?://[^\s)>\]]+")
    line = _mask(line, r"</?[A-Za-z][^>]*>")
    return line


def _to_prose(md_line: str) -> str:
    line = md_line
    m = HEADING.match(line) or LIST_ITEM.match(line)
    if m:
        line = " " * m.end() + line[m.end():]
    line = re.sub(r"\*+", lambda m: " " * len(m.group(0)), line)
    line = re.sub(r"(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])", lambda m: " " * len(m.group(0)), line)
    return line


def parse(text: str) -> Document:
    """Split Markdown into masked layers, blocks and sentences.

    >>> d = parse("# Title\\n\\nOne sentence here. Two `code.x` here!\\n\\n```py\\nx = 1\\n```\\n- item one\\n")
    >>> [b.kind for b in d.blocks], [s.text for s in d.sentences]
    (['heading', 'para', 'item'], ['One sentence here.', 'Two here!', 'item one'])
    >>> d.fences
    [(48, 'py')]
    """
    lines = text.split("\n")
    md_lines: list[str] = []
    kinds: list[str] = []  # per line: blank | masked | heading | item | cont | para
    fences: list[tuple[int, str]] = []
    offset, in_fence, fence, in_comment = 0, False, "", False
    front = lines and lines[0].strip() == "---"
    front_end = -1
    if front:
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                front_end = i
                break
    for i, line in enumerate(lines):
        kind = "para"
        if i <= front_end:
            md, kind = _blank(line), "masked"
        elif in_fence:
            md, kind = _blank(line), "masked"
            if re.match(r"^ {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*$", line):
                in_fence = False
        elif in_comment:
            md, kind = _blank(line), "masked"
            if "-->" in line:
                in_comment = False
        elif m := FENCE_OPEN.match(line):
            in_fence, fence = True, m.group(1)
            fences.append((offset, m.group(2).strip()))
            md, kind = _blank(line), "masked"
        elif line.strip().startswith("<!--") and "-->" not in line:
            in_comment, md, kind = True, _blank(line), "masked"
        elif not line.strip():
            md, kind = line, "blank"
        elif HR.match(line) or TABLE.match(line) or QUOTE.match(line):
            md, kind = _blank(line), "masked"
        else:
            md = _mask_inline(line)
            if HEADING.match(line):
                kind = "heading"
            elif LIST_ITEM.match(line):
                kind = "item"
            elif line.startswith((" ", "\t")):
                kind = "cont"
        md_lines.append(md)
        kinds.append(kind)
        offset += len(line) + 1

    md_text = "\n".join(md_lines)
    prose_text = "\n".join(_to_prose(x) for x in md_lines)
    line_starts, pos = [], 0
    for line in lines:
        line_starts.append(pos)
        pos += len(line) + 1

    blocks: list[Block] = []
    current: Block | None = None
    for i, kind in enumerate(kinds):
        start, end = line_starts[i], line_starts[i] + len(lines[i])
        if kind in ("blank", "masked"):
            current = None
        elif kind == "heading":
            hm = HEADING.match(lines[i])
            blocks.append(Block("heading", start, end, len(hm.group(1)) if hm else 1))
            current = None
        elif kind == "item":
            current = Block("item", start, end)
            blocks.append(current)
        elif current is not None:  # continuation of a paragraph or list item (lazy continuation included)
            current.end = end
        else:
            current = Block("para", start, end)
            blocks.append(current)

    split_view = INNER_DOT.sub("·", ABBREV.sub(lambda m: m.group(0)[:-1] + "·", prose_text))
    sentences: list[Sentence] = []
    for bi, b in enumerate(blocks):
        if b.kind == "heading":
            continue
        for m in SENTENCE.finditer(split_view, b.start, b.end):
            raw = prose_text[m.start():m.end()]
            stripped = raw.strip()
            if not WORD.search(stripped):
                continue
            s = m.start() + (len(raw) - len(raw.lstrip()))
            sentences.append(Sentence(s, s + len(stripped), re.sub(r"\s+", " ", stripped), len(WORD.findall(stripped)), bi))
    words = sum(s.words for s in sentences)
    return Document(text, md_text, prose_text, blocks, sentences, fences, line_starts, words)


# -------------------------------------------------------------------------------------------- linting

@dataclass
class Finding:
    rule: str
    severity: str
    line: int
    col: int
    text: str
    message: str
    hint: str


@dataclass
class Settings:
    genre: str = "article"
    disabled: set[str] = field(default_factory=set)
    severity: dict[str, str] = field(default_factory=dict)
    allow_words: set[str] = field(default_factory=set)
    extra_vocab: list[str] = field(default_factory=list)
    extra_phrases: list[str] = field(default_factory=list)


class Linter:
    def __init__(self, doc: Document, settings: Settings):
        self.doc, self.cfg = doc, settings
        self.findings: list[Finding] = []
        self.stats: dict[str, object] = {}

    # helpers
    def sev(self, rule_id: str) -> str | None:
        if rule_id in self.cfg.disabled:
            return None
        return self.cfg.severity.get(rule_id) or RULES[rule_id].severity(self.cfg.genre)

    def add(self, rule_id: str, start: int, end: int, message: str = "", severity: str | None = None) -> None:
        if not self.sev(rule_id):
            return
        sev = severity or self.sev(rule_id)
        if not sev:
            return
        snippet = re.sub(r"\s+", " ", self.doc.text[start:end]).strip()
        if snippet.lower().strip(".,!?:;\"'“”’") in self.cfg.allow_words:
            return
        line, col = self.doc.pos(start)
        rule = RULES[rule_id]
        self.findings.append(Finding(rule_id, sev, line, col, snippet[:120], message or rule.summary, rule.hint))

    def scan(self, rule_id: str, pattern: re.Pattern | str, *, layer: str = "prose", skip_linked: bool = False) -> int:
        if not self.sev(rule_id):
            return 0
        rx = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern, re.I | re.M)
        hay = getattr(self.doc, layer)
        n = 0
        for b in self.doc.blocks:
            for m in rx.finditer(hay, b.start, b.end):
                if skip_linked:
                    ln = self.doc.pos(m.start())[0] - 1
                    full = self.doc.text.split("\n")[ln]
                    if "](" in full or "http" in full:
                        continue
                self.add(rule_id, m.start(), m.end())
                n += 1
        return n

    def para_sentences(self) -> list[Sentence]:
        return [s for s in self.doc.sentences if self.doc.blocks[s.block].kind == "para"]

    # rule groups
    def run(self) -> None:
        g = self.cfg.genre
        allow = self.cfg.allow_words
        vocab = [w for w in AI_VOCAB + self.cfg.extra_vocab if w.lower() not in allow]
        if vocab:
            self._vocab(_compile([re.escape(w).replace(r"\ ", " ").replace(r"\-", "-") for w in vocab]))
        self.scan("stock-phrase", _compile(STOCK_PHRASES + [re.escape(p) for p in self.cfg.extra_phrases]))
        self.scan("faux-insight", _compile(FAUX_INSIGHT))
        self.scan("metadiscourse", _compile(METADISCOURSE))
        self.scan("chat-residue", _compile(CHAT_RESIDUE))
        self.scan("vague-declarative", _compile(VAGUE))
        self.scan("sales-language", _compile(SALES))
        self.scan("engagement-bait", re.compile(_compile(ENGAGEMENT_BAIT).pattern, re.I | re.M))
        self.scan("ing-rider", ING_RIDERS)
        self.scan("copula-avoidance", COPULA)
        self.scan("weasel", WEASEL, skip_linked=True)
        self.scan("hedge-stack", HEDGE_PAIRS)
        if g == "docs":
            self.scan("docs-minimizer", DOCS_MINIMIZERS)
            self.scan("docs-please", r"\bplease\b")
            self.scan("docs-latin", r"(?<!\w)(?:e\.g\.|i\.e\.|etc\.|via\b)")
            self.scan("docs-future", r"\b(?:will|['’]ll)\s+(?:be\s+)?(?!not\b)[a-z]+")
        self._throat_clearing()
        self._contrasts()
        self._colon_reveal()
        self._rhetorical()
        self._rhythm()
        self._triads()
        self._dashes()
        self._hedges_and_intensifiers()
        self._passive()
        self._formatting()
        self._endings()
        if g == "post":
            self._post_rules()

    def _vocab(self, rx: re.Pattern) -> None:
        # Pre-2022 human prose uses these words too; one of them is weak evidence, a cluster is strong.
        d = self.doc
        hits = []
        for b in d.blocks:
            hits += [m.span() for m in rx.finditer(d.prose, b.start, b.end)]
        budget = max(1, d.words // 400)
        self.stats["ai_vocab"] = {"count": len(hits), "budget": budget}
        over = len(hits) > budget
        for s, e in hits:
            self.add("ai-vocab", s, e, f"AI-marker word ({len(hits)} in this piece, budget {budget})",
                     severity=None if over else "info")

    def _throat_clearing(self) -> None:
        rx = re.compile(r"^(?:but |and |so )?(?:" + _compile(THROAT_CLEARING).pattern + ")", re.I)
        for s in self.doc.sentences:
            if m := rx.match(s.text):
                self.add("throat-clearing", s.start, s.start + m.end())

    def _contrasts(self) -> None:
        d = self.doc
        inline = re.compile(
            r"\bnot (?:just|only|merely|simply) [^.!?;]{1,80}?,? but (?:also )?\w+"
            r"|\b(?:isn['’]t|is not|wasn['’]t|was not|aren['’]t|are not)\s+(?:just\s+|only\s+|about\s+)?[^.!?;,]{1,60}[,;—–]\s*(?:it['’]s|it is|they['’]re|they are|this is|that['’]s)\b"
            r"|\b(?:is|are|was|were)\s+not\s+(?:a|an|the|about)?\s*[^.!?;,]{1,50}?\s+but\s+(?:a|an|the|about)?\s*\w+"
            r"|,\s+not\s+(?:a|an)\s+[\w-]+[.!]", re.I)
        hits: list[tuple[int, int]] = []
        for b in d.blocks:
            hits += [m.span() for m in inline.finditer(d.prose, b.start, b.end)]
        first = re.compile(r"^(?:but |and )?(?:[\w'’-]+\s){0,4}?(?:[\w-]+['’]s not|is not|isn['’]t|wasn['’]t|was not|are not|aren['’]t|is no longer)\b", re.I)
        second = re.compile(r"^(?:it['’]s|it is|it was|this is|that['’]s|they['’]re|the (?:real )?\w+ is)\b", re.I)
        sents = d.sentences
        for a, b in zip(sents, sents[1:]):
            if a.words <= 14 and first.match(a.text) and second.match(b.text) and b.block - a.block <= 1:
                if not any(s <= a.start < e for s, e in hits):
                    hits.append((a.start, b.end))
        budget = 1 if self.cfg.genre != "docs" else 2
        self.stats["contrasts"] = {"count": len(hits), "budget": budget}
        if len(hits) > budget:
            for s, e in sorted(hits):
                self.add("not-x-but-y", s, e, f"Staged contrast ({len(hits)} in this piece, budget {budget})")
        neg = re.compile(r"^not (?:a|an|the|just|only|because)?\b", re.I)
        run: list[Sentence] = []
        for s in sents + [None]:
            if s is not None and neg.match(s.text) and s.words <= 8:
                run.append(s)
                continue
            if len(run) >= 2:
                self.add("negative-list", run[0].start, run[-1].end)
            run = []

    def _colon_reveal(self) -> None:
        rx = re.compile(r"^((?:[\w'’-]+\s+){0,5}[\w'’-]+):\s+([a-z][^:]*)$")
        for s in self.doc.sentences:
            if self.doc.blocks[s.block].kind == "item":
                continue
            m = rx.match(s.text)
            if not m:
                continue
            head = m.group(1).lower()
            tail_words = len(WORD.findall(m.group(2)))
            if head in COLON_LABELS or head.split()[-1] in COLON_LABELS or re.match(r"here['’]?s|here is", head) or head.split()[0] in ("step",) or tail_words > 14 or s.text.endswith("?"):
                continue
            self.add("colon-reveal", s.start, s.end)

    def _rhetorical(self) -> None:
        d = self.doc
        phrase_rx = _compile(RHETORICAL_PHRASES)
        flagged: set[int] = set()
        for b in d.blocks:
            for m in phrase_rx.finditer(d.prose, b.start, b.end):
                self.add("rhetorical", m.start(), m.end())
                flagged.add(m.start())
        sents = [s for s in d.sentences if d.blocks[s.block].kind == "para"]
        for q, a in zip(sents, sents[1:]):
            if q.text.endswith("?") and not a.text.endswith("?") and a.words <= 8 and a.block - q.block <= 1:
                if not any(q.start <= f < q.end for f in flagged) and not q.text.startswith(("\"", "“")):
                    self.add("rhetorical", q.start, a.end, "Question answered by the next short sentence")

    def _rhythm(self) -> None:
        sents = self.para_sentences()
        lengths = [s.words for s in sents]
        if len(lengths) >= 2:
            mean = statistics.mean(lengths)
            cv = statistics.pstdev(lengths) / mean if mean else 0.0
            self.stats["sentences"] = {"count": len(lengths), "mean_words": round(mean, 1), "spread": round(cv, 2)}
            if len(lengths) >= 15 and cv < 0.3:
                self.add("uniform-rhythm", sents[0].start, sents[0].end,
                         f"Sentence lengths are too even (spread {cv:.2f}; human prose usually sits above 0.3)")
        run: list[Sentence] = []
        for s in sents + [None]:
            if s is not None and s.words <= 4 and (not run or s.block - run[-1].block <= 1):
                run.append(s)
                continue
            if len(run) >= 3:
                self.add("fragment-stack", run[0].start, run[-1].end)
            run = [s] if s is not None and s.words <= 4 else []
        run = []
        for s in sents + [None]:
            m = WORD.match(s.text) if s is not None else None
            if m and run and m.group(0).lower() == WORD.match(run[0].text).group(0).lower():  # type: ignore[union-attr]
                run.append(s)
                continue
            if len(run) >= 3:
                opener = run[0].text.split()[0]
                self.add("repeated-opener", run[0].start, run[-1].end, f"{len(run)} sentences in a row start with \"{opener}\"")
            run = [s] if m else []
        # paragraphs that end on a short line after a long one
        by_block: dict[int, list[Sentence]] = {}
        for s in sents:
            by_block.setdefault(s.block, []).append(s)
        eligible = [ss for ss in by_block.values() if len(ss) >= 2]
        kickers = [ss[-1] for ss in eligible if ss[-1].words <= 6 and ss[-2].words >= 12]
        self.stats["kickers"] = {"count": len(kickers), "paragraphs": len(eligible)}
        if len(kickers) >= 2 and len(kickers) / max(len(eligible), 1) >= 0.25:
            for k in kickers:
                self.add("kicker", k.start, k.end, f"Paragraph ends on a short punchline ({len(kickers)} of {len(eligible)} paragraphs)")

    def _triads(self) -> None:
        item = r"[\w'’-]+(?: [\w'’-]+){0,2}"
        rx = re.compile(rf"(?<!, )(?<!,)\b({item}), (?!such as|including|like|for example|e\.g)({item}),? (?:and|or) ({item})\b")
        hits = []
        for s in self.doc.sentences:
            if self.doc.blocks[s.block].kind != "para":
                continue
            seg = self.doc.prose[s.start:s.end]
            for m in rx.finditer(seg):
                if not re.search(r",\s*$", seg[:m.start()]):
                    hits.append((s.start + m.start(), s.start + m.end()))
        budget = max(1, self.doc.words // 250)
        self.stats["triads"] = {"count": len(hits), "budget": budget}
        if len(hits) > budget:
            for s, e in hits:
                self.add("triad", s, e, f"Group of three ({len(hits)} in this piece, budget {budget})")

    def _dashes(self) -> None:
        d, g = self.doc, self.cfg.genre
        hits = []
        for b in d.blocks:
            hits += [m.span() for m in DASHES.finditer(d.prose, b.start, b.end)]
        per = {"article": 150, "post": 250, "docs": 200}[g]
        budget = d.words // per if g == "post" else max(1, d.words // per)
        self.stats["dashes"] = {"count": len(hits), "budget": budget}
        if len(hits) > budget:
            for s, e in hits:
                ctx_s, ctx_e = max(s - 25, 0), min(e + 25, len(d.text))
                line, col = d.pos(s)
                sev = self.sev("em-dash")
                if sev:
                    self.findings.append(Finding("em-dash", sev, line, col, d.text[ctx_s:ctx_e].replace("\n", " ").strip(),
                                                 f"Dash ({len(hits)} in this piece, budget {budget})", RULES["em-dash"].hint))

    def _hedges_and_intensifiers(self) -> None:
        d = self.doc
        for s in d.sentences:
            words = [w.lower() for w in WORD.findall(s.text)]
            if sum(w in HEDGES for w in words) >= 3:
                self.add("hedge-stack", s.start, s.end, "Three or more hedges in one sentence")
        rx = re.compile(r"\b(?:" + "|".join(sorted(INTENSIFIERS - self.cfg.allow_words)) + r")\b", re.I)
        hits = []
        for b in d.blocks:
            hits += [m.span() for m in rx.finditer(d.prose, b.start, b.end)]
        budget = max(2, d.words // 120)
        self.stats["intensifiers"] = {"count": len(hits), "budget": budget}
        if len(hits) > budget:
            for s, e in hits:
                self.add("intensifier", s, e, f"Intensifier ({len(hits)} in this piece, budget {budget})")

    def _passive(self) -> None:
        sents = [s for s in self.doc.sentences if self.doc.blocks[s.block].kind == "para"]
        passive = [s for s in sents if PASSIVE.search(s.text)]
        ratio = len(passive) / len(sents) if sents else 0.0
        self.stats["passive"] = {"count": len(passive), "share": round(ratio, 2)}
        if len(sents) >= 8 and ratio > 0.25:
            for s in passive:
                m = PASSIVE.search(self.doc.prose, s.start, s.end)
                if m:
                    self.add("passive", m.start(), m.end(), f"Passive voice ({len(passive)} of {len(sents)} sentences)")

    def _formatting(self) -> None:
        d, g = self.doc, self.cfg.genre
        lines = d.text.split("\n")
        md_lines = d.md.split("\n")
        # headings
        headings = [(i, b) for i, b in enumerate(d.blocks) if b.kind == "heading"]
        for hi, (bi, b) in enumerate(headings):
            htext = d.prose[b.start:b.end].strip()
            if EMOJI.search(htext):
                self.add("emoji-heading", b.start, b.end)
            words = htext.split()
            caps = [w for w in words if len(w) > 3 and w[0].isalpha()]
            if len(words) >= 3 and len(caps) >= 2 and all(w[0].isupper() for w in caps) and not htext.isupper():
                self.add("title-case-heading", b.start, b.end)
            if hi + 1 < len(headings) and headings[hi + 1][1].level <= b.level:
                body = [s for s in d.sentences if bi < s.block < headings[hi + 1][0]]
                if sum(s.words for s in body) < 40 and any(d.blocks[x].kind != "heading" for x in range(bi + 1, headings[hi + 1][0])):
                    self.add("heading-density", b.start, b.end, f"Heading over {sum(s.words for s in body)} words")
        # emoji bullets
        emoji_lines = [i for i, b in enumerate(d.blocks) if b.kind in ("item", "para") and EMOJI.match(d.text[b.start:b.end].lstrip(" -*+0123456789.)"))]
        if len(emoji_lines) >= 2:
            for i in emoji_lines:
                self.add("emoji-bullet", d.blocks[i].start, d.blocks[i].end)
        # bold
        bold = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
        labels, inline = [], []
        for b in d.blocks:
            if b.kind == "heading":
                continue
            seg_start = b.start
            first_char = len(d.md[b.start:b.end]) - len(LIST_ITEM.sub("", d.md[b.start:b.end], count=1))
            for m in bold.finditer(d.md, b.start, b.end):
                is_label = m.start() - seg_start <= first_char + 1 and re.match(r"\s*[:.—–-]|.*[:.]$", d.md[m.end():m.end() + 2] if not m.group(2).endswith((":", ".")) else ":")
                if is_label and b.kind == "item":
                    labels.append(m.span())
                elif not is_label:
                    inline.append(m.span())
        if len(labels) >= 3:
            for s, e in labels:
                self.add("bold-label-list", s, e, f"Bold label ({len(labels)} labelled list items)")
        budget = max(2, d.words // 400)
        self.stats["bold_inline"] = {"count": len(inline), "budget": budget}
        if len(inline) > budget:
            for s, e in inline:
                self.add("bold-inline", s, e, f"Bold inside a sentence ({len(inline)} in this piece, budget {budget})")
        # code fences
        for off, info in d.fences:
            if not info:
                self.add("code-fence-lang", off, off + 3)
        del lines, md_lines

    def _endings(self) -> None:
        d = self.doc
        paras = [b for b in d.blocks if b.kind == "para"]
        rx = re.compile(RECAP_OPENERS, re.I)
        for b in paras:
            seg = d.prose[b.start:b.end].lstrip()
            if seg[:15].lower().startswith(("in conclusion", "to sum up", "in summary", "to summarize", "to summarise")) or (b is paras[-1] and rx.match(seg)):
                off = b.start + (len(d.prose[b.start:b.end]) - len(seg))
                self.add("recap-ending", off, off + min(len(seg), 60))

    def _post_rules(self) -> None:
        d = self.doc
        body = d.text.strip()
        if len(body) > 3000:
            self.add("post-length", 0, min(len(d.text), 40), f"Post is {len(body)} characters; LinkedIn allows 3,000")
        if d.sentences:
            first = d.sentences[0]
            if first.text.endswith("?"):
                self.add("post-opening-question", first.start, first.end)
            if first.end - d.text.find(body[:1]) > 210:
                self.add("post-hook-fold", first.start, first.end, f"First sentence ends at character {first.end}")
        tags = list(re.finditer(r"(?<![\w#&])#[A-Za-z]\w*", d.md))
        if len(tags) > 3:
            for m in tags:
                self.add("post-hashtags", m.start(), m.end(), f"{len(tags)} hashtags")


def score(findings: list[Finding], words: int) -> int:
    """0–100; penalty per finding, scaled to a 500-word reference length.

    >>> score([], 800)
    100
    >>> score([Finding("x", "warning", 1, 1, "", "", "")] * 5, 500)
    85
    """
    penalty = sum(SEVERITY_WEIGHT[f.severity] for f in findings)
    return max(0, round(100 - penalty * 500 / max(words, 300)))


# ------------------------------------------------------------------------------------------ settings

def load_voice(path: Path, cfg: Settings) -> None:
    """Read `lint-allow-rules:` and `lint-allow-words:` lines from a voice profile."""
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*[-*]?\s*`?(lint-allow-rules|lint-allow-words)`?\s*:\s*(.*)$", line, re.I)
        if not m:
            continue
        values = {v.strip().strip("`\"'").lower() for v in m.group(2).split(",") if v.strip().strip("`\"'")}
        if m.group(1).lower() == "lint-allow-rules":
            unknown = values - RULES.keys()
            if unknown:
                raise ValueError(f"unknown rule id(s) in {path}: {', '.join(sorted(unknown))}")
            cfg.disabled |= values
        else:
            cfg.allow_words |= values


def load_config(path: Path, cfg: Settings) -> None:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    rules = data.get("rules", {})
    disabled = set(rules.get("disable", []))
    overrides = {k: v for k, v in data.get("severity", {}).items()}
    unknown = (disabled | overrides.keys()) - RULES.keys()
    if unknown:
        raise ValueError(f"unknown rule id(s) in {path}: {', '.join(sorted(unknown))}")
    bad = {v for v in overrides.values() if v not in ("error", "warning", "info")}
    if bad:
        raise ValueError(f"severity must be error, warning or info in {path}, got: {', '.join(sorted(bad))}")
    words = data.get("words", {})
    cfg.disabled |= disabled
    cfg.severity |= overrides
    cfg.allow_words |= {w.lower() for w in words.get("allow", [])}
    cfg.extra_vocab += list(words.get("ai_vocab", []))
    cfg.extra_phrases += list(words.get("stock_phrases", []))


# ---------------------------------------------------------------------------------------------- output

def lint(text: str, cfg: Settings) -> dict:
    doc = parse(text)
    linter = Linter(doc, cfg)
    linter.run()
    findings = sorted(linter.findings, key=lambda f: (f.line, f.col, f.rule))
    counts = {s: sum(f.severity == s for f in findings) for s in ("error", "warning", "info")}
    return {"genre": cfg.genre, "words": doc.words, "score": score(findings, doc.words), "counts": counts,
            "stats": linter.stats, "findings": [asdict(f) for f in findings]}


def render_text(name: str, result: dict, threshold: int) -> str:
    ok = result["score"] >= threshold and result["counts"]["error"] == 0
    c = result["counts"]
    out = [f"{name}: score {result['score']}/100 ({result['genre']}, {result['words']} words) — "
           f"{'PASS' if ok else 'FAIL'} (threshold {threshold}; {c['error']} error, {c['warning']} warning, {c['info']} info)"]
    for f in result["findings"]:
        out.append(f"  {f['line']}:{f['col']}  {f['severity']:<7}  {f['rule']:<18}  \"{f['text']}\"")
        out.append(f"      {f['message']} → {f['hint']}")
    st = result["stats"]
    parts = []
    if "sentences" in st:
        parts.append(f"sentences {st['sentences']['count']}, mean {st['sentences']['mean_words']} words, spread {st['sentences']['spread']}")
    for key in ("ai_vocab", "dashes", "contrasts", "triads", "intensifiers", "bold_inline"):
        if key in st:
            parts.append(f"{key.replace('_', ' ')} {st[key]['count']}/{st[key]['budget']}")
    if "passive" in st:
        parts.append(f"passive {st['passive']['share']:.0%}")
    if parts:
        out.append("stats: " + " · ".join(parts))
    return "\n".join(out)


def list_rules(fmt: str) -> str:
    if fmt == "json":
        return json.dumps([asdict(r) for r in RULES.values()], indent=2)
    rows = [f"{'rule':<22}{'article':<9}{'post':<9}{'docs':<9}summary"]
    for r in RULES.values():
        rows.append(f"{r.id:<22}{r.article or '-':<9}{r.post or '-':<9}{r.docs or '-':<9}{r.summary}")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file", nargs="?", help="Markdown or plain-text file, or - for stdin")
    p.add_argument("-g", "--genre", choices=GENRES, default="article")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.add_argument("-t", "--threshold", type=int, default=DEFAULT_THRESHOLD, help="minimum passing score (0–100)")
    p.add_argument("--voice", type=Path, help="voice profile with lint-allow-rules / lint-allow-words lines")
    p.add_argument("--config", type=Path, help="TOML with [rules] disable, [severity] overrides, [words] lists")
    p.add_argument("--list-rules", action="store_true", help="print the rules and their severity per genre")
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return EXIT_BAD_INPUT if e.code else EXIT_OK
    if args.list_rules:
        print(list_rules(args.format))
        return EXIT_OK
    if not args.file:
        print("error: no input file — pass a path, or - for stdin", file=sys.stderr)
        return EXIT_BAD_INPUT
    if not 0 <= args.threshold <= 100:
        print("error: --threshold must be between 0 and 100", file=sys.stderr)
        return EXIT_BAD_INPUT
    cfg = Settings(genre=args.genre)
    try:
        if args.config:
            load_config(args.config, cfg)
        if args.voice:
            load_voice(args.voice, cfg)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
        print(f"error: {e} — fix the --config or --voice file", file=sys.stderr)
        return EXIT_BAD_INPUT
    try:
        raw = sys.stdin.buffer.read() if args.file == "-" else Path(args.file).read_bytes()
    except OSError as e:
        print(f"error: cannot read {args.file}: {e.strerror} — check the path", file=sys.stderr)
        return EXIT_BAD_INPUT
    try:
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        print(f"error: {args.file} is not UTF-8 text — convert it first", file=sys.stderr)
        return EXIT_PARSE
    result = lint(text, cfg)
    name = "stdin" if args.file == "-" else args.file
    passed = result["score"] >= args.threshold and result["counts"]["error"] == 0
    if args.format == "json":
        print(json.dumps({"file": name, **result, "threshold": args.threshold, "pass": passed}, ensure_ascii=False, indent=2))
    else:
        print(render_text(name, result, args.threshold))
    return EXIT_OK if passed else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
