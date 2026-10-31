#!/usr/bin/env python3
"""Repository checks, used by CI and by the weekly liveness workflow (standard library; needs uv on PATH).

    python3 test/ops.py validate                 # every skill: spec, frontmatter, size, agent neutrality, README row
    python3 test/ops.py test [SUITE ...]         # offline test suites, each with its own dependencies
    python3 test/ops.py probe [SKILL ...] --json results.json   # live smoke tests against the real sources
    python3 test/ops.py report results/*.json    # one Markdown report from probe results (for the liveness issue)

A probe is a short real command per skill, judged by its exit code: 0 is "ok"; 4 (blocked or rate-limited) and 6
(network) are "blocked" - the source refused this machine's network, which on CI runners is often a datacenter-IP
policy rather than a broken skill; anything else, a timeout included, is "broken". When the command is ok, the
skill's live pytest suite (its *_LIVE=1 tests) runs too and must pass at least one test; when its only failures are
the script exiting 4 or 6, the suite counts as "blocked", like the command.
"""

import argparse
import ast
import contextlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
MAX_SKILL_LINES = 500
MAX_COMPATIBILITY = 500
# Agent product names, tool names and install paths that must not appear in a skill (the README's install table is
# the only agent-specific text). A script line that needs a path for a functional reason ends in "# agent-dirs".
AGENT_SPECIFIC = re.compile(r"(?i:\.claude\b|\.codex\b|\.gemini\b|\bopencode\b|/plugin\b|slash command)|"
                            r"\b(?:Claude Code|Claude|Codex|Cursor|Gemini CLI|Windsurf|Copilot|"
                            r"WebFetch|WebSearch)\b")
AGENT_DIRS_MARK = re.compile(r"#\s*agent-dirs\s*$")
TEXT_FILES = {".md", ".py", ".typ", ".txt", ".json", ".yaml", ".yml", ".toml"}
SKILLS_REF = "skills-ref==0.1.1"
# GPL/AGPL packages (or extras that pull them in) a skill must not install; see CONTRIBUTING.md.
COPYLEFT = {"mutagen", "piper-tts", "gallery-dl", "patool", "pymupdf", "ebooklib", "fitz"}
RUN = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
PY = ["python", "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

# Offline suites: test file, its dependencies, and modules with doctests. CI installs nothing else.
SUITES = {
    "anki-deck": (["genanki==0.13.1", "markdown-it-py>=3,<5", "anki"], ["skills/anki-deck/scripts/anki_deck.py"]),
    "cite-check": (["bibtexparser>=2.0.0b7", "pypdf"], ["skills/cite-check/scripts/cite_check.py"]),
    "hackernews": ([], ["skills/hackernews/scripts/hn.py"]),
    "human-writing": ([], ["skills/human-writing/scripts/lint_prose.py"]),
    "pdf-typeset": (["typst", "markdown-it-py", "mdit-py-plugins", "pyyaml", "pypdf", "pypdfium2", "pillow"],
                    ["skills/pdf-typeset/scripts/pdf.py"]),
    "podcast-search": (["feedparser"], ["skills/podcast-search/scripts/podcast.py"]),
    "scholar-search": ([], ["skills/scholar-search/scripts/scholar.py"]),
    "telegram-channel": (["beautifulsoup4"], ["skills/telegram-channel/scripts/channel.py"]),
    "transcribe-audio": (["faster-whisper", "yt-dlp", "yt-dlp-ejs", "brotli", "certifi", "pycryptodomex", "requests", "urllib3", "websockets"],
                         ["skills/transcribe-audio/scripts/transcribe.py"]),
    "wayback": ([], ["skills/wayback/scripts/wayback.py"]),
    "weather": ([], []),
    "web-browse": (["playwright", "curl_cffi", "trafilatura", "lxml_html_clean", "pypdf"], []),
    "web-crawl": (["curl_cffi", "trafilatura", "lxml_html_clean", "pypdf"], []),
    "web-extract": (["curl_cffi", "trafilatura", "lxml_html_clean", "pypdf"], []),
    "web-scrape": (["curl_cffi", "trafilatura", "lxml_html_clean", "pypdf", "cssselect"], []),
    "web-search": (["ddgs>=9,<10", "curl_cffi", "trafilatura", "lxml_html_clean", "pypdf"], []),
    "wiki-lookup": ([], ["skills/wiki-lookup/scripts/wiki.py"]),
    "youtube-audio": (["yt-dlp", "yt-dlp-ejs", "brotli", "certifi", "pycryptodomex", "requests", "urllib3", "websockets"], ["skills/youtube-audio/scripts/audio.py"]),
    "youtube-search": (["yt-dlp"], ["skills/youtube-search/scripts/search.py"]),
    "youtube-transcript": (["youtube-transcript-api>=1.0"], []),
}
# Network tests inside the offline suites are switched off in CI; the probes cover the network.
OFFLINE_ENV = {"WEB_EXTRACT_OFFLINE": "1"}

# Live probes: (skill, arguments to its script, the environment flag of its live pytest suite or None).
PROBES = [
    ("cite-check", ["cite_check.py", "--doi", "10.1038/nature14539", "-q"], "CITE_LIVE"),
    ("hackernews", ["hn.py", "item", "1", "-q"], "HN_LIVE"),
    ("podcast-search", ["podcast.py", "search", "history of science", "-n", "3"], "PODCAST_LIVE"),
    ("scholar-search", ["scholar.py", "doi", "10.1038/nature14539", "-q"], "SCHOLAR_LIVE"),
    ("telegram-channel", ["channel.py", "@telegram", "-n", "3"], "TELEGRAM_LIVE"),
    ("wayback", ["wayback.py", "check", "example.com"], "WAYBACK_LIVE"),
    ("weather", ["weather.py", "52.52,13.41", "--days", "1", "-q"], "WEATHER_LIVE"),
    ("wiki-lookup", ["wiki.py", "summary", "Marie Curie", "-q"], "WIKI_LIVE"),
    ("web-search", ["search.py", "python programming language", "-n", "3"], None),
    ("web-extract", ["extract.py", "https://example.com/"], None),
    ("web-scrape", ["scrape.py", "https://quotes.toscrape.com/", "--probe"], None),
    ("web-crawl", ["crawl.py", "https://example.com/", "--map", "--max-pages", "3"], None),
    ("youtube-search", ["search.py", "python tutorial", "-n", "3"], None),
    ("youtube-transcript", ["transcript.py", "jNQXAC9IVRw"], None),
    ("youtube-audio", ["audio.py", "jNQXAC9IVRw", "--info"], None),
]
LIVE_SUITE_DEPS = {"cite-check": SUITES["cite-check"][0], "podcast-search": SUITES["podcast-search"][0],
                   "telegram-channel": SUITES["telegram-channel"][0]}


def frontmatter(text: str) -> dict[str, str] | None:
    """Top-level keys of a SKILL.md frontmatter, and the keys under metadata (as metadata.<key>)."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return None
    out: dict[str, str] = {}
    section = None
    for line in m[1].split("\n"):
        if km := re.match(r"^([a-z_-]+):\s*(.*)$", line):
            section = km[1]
            out[km[1]] = km[2].strip().strip('"')
        elif section == "metadata" and (km := re.match(r"^\s+([a-z_-]+):\s*(.*)$", line)):
            out[f"metadata.{km[1]}"] = km[2].strip().strip('"')
    return out


def crash_guard_problem(source: str) -> str | None:
    """What is wrong with a script's `if __name__ == "__main__":` guard, or None (also for a module without one).

    The guard must catch KeyboardInterrupt and Exception, and the Exception handler must not exit 1, which
    means "nothing found" (or "findings") everywhere."""
    tree = ast.parse(source)
    consts: dict[str, int] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        names = [t.id for t in target.elts if isinstance(t, ast.Name)] if isinstance(target, ast.Tuple) else (
            [target.id] if isinstance(target, ast.Name) else [])
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "range" and value.args:
            values = list(range(*[a.value for a in value.args if isinstance(a, ast.Constant)]))
        elif isinstance(value, ast.Tuple):
            values = [e.value if isinstance(e, ast.Constant) else None for e in value.elts]
        else:
            values = [value.value if isinstance(value, ast.Constant) else None]
        consts.update((n, v) for n, v in zip(names, values) if isinstance(v, int))
    guards = [n for n in tree.body if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
              and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__"
              and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in n.test.comparators)]
    if not guards:
        return None
    handlers: dict[str, ast.ExceptHandler] = {}
    for node in ast.walk(guards[0]):
        if isinstance(node, ast.Try):
            for h in node.handlers:
                types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
                for t in types:
                    if isinstance(t, ast.Name):
                        handlers.setdefault(t.id, h)
    if "BaseException" in handlers:
        handlers.setdefault("KeyboardInterrupt", handlers["BaseException"])
        handlers.setdefault("Exception", handlers["BaseException"])
    missing = [name for name in ("KeyboardInterrupt", "Exception") if name not in handlers]
    if missing:
        return f"the __main__ block does not catch {' or '.join(missing)} (exit 130 and 7)"
    exits = [c for c in ast.walk(handlers["Exception"]) if isinstance(c, ast.Call) and c.args
             and isinstance(c.func, ast.Attribute) and c.func.attr in ("exit", "_exit")]
    if not exits:
        return "the __main__ block's Exception handler does not exit with a code"
    for c in exits:
        arg = c.args[0]
        code = arg.value if isinstance(arg, ast.Constant) else consts.get(arg.id) if isinstance(arg, ast.Name) else None
        if code in (0, 1):
            return f"the __main__ block's Exception handler exits {code}; a crash must exit 7"
    return None


def check_skill(skill: Path, readme: str) -> list[str]:
    """Problems with one skill folder, as messages."""
    problems = []
    name = skill.name
    md = skill / "SKILL.md"
    if not md.is_file():
        return [f"{name}: no SKILL.md"]
    text = md.read_text(encoding="utf-8")
    fm = frontmatter(text)
    if fm is None:
        return [f"{name}: SKILL.md has no frontmatter"]
    for key in ("name", "description", "license", "compatibility", "metadata.author", "metadata.version",
                "metadata.source"):
        if not fm.get(key):
            problems.append(f"{name}: frontmatter lacks {key}")
    if fm.get("name") and fm["name"] != name:
        problems.append(f"{name}: frontmatter name is {fm['name']!r}, not the folder name")
    if len(fm.get("compatibility", "")) > MAX_COMPATIBILITY:
        problems.append(f"{name}: compatibility is {len(fm['compatibility'])} characters (limit {MAX_COMPATIBILITY})")
    lines = len(text.splitlines())
    if lines >= MAX_SKILL_LINES:
        problems.append(f"{name}: SKILL.md has {lines} lines (keep it under {MAX_SKILL_LINES}; move detail to references/)")
    for f in sorted(x for x in skill.rglob("*") if x.is_file() and x.suffix in TEXT_FILES):
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
            if AGENT_SPECIFIC.search(line) and not (f.suffix == ".py" and AGENT_DIRS_MARK.search(line)):
                problems.append(f"{f.relative_to(ROOT)}:{n}: agent-specific text ({AGENT_SPECIFIC.search(line)[0]!r}); "
                                "say it agent-neutrally")
    for script in sorted(skill.rglob("scripts/*.py")):
        source = script.read_text(encoding="utf-8")
        if problem := crash_guard_problem(source):
            problems.append(f"{script.relative_to(ROOT)}: {problem}; see CONTRIBUTING.md")
        header = re.search(r"^# /// script\n(.*?)^# ///$", source, re.S | re.M)
        deps = re.search(r"^# dependencies = (.*)$", header[1], re.M) if header else None
        for bad in re.findall(r"\"([A-Za-z0-9_.-]+(?:\[[^\]]*\])?)", deps[1] if deps else ""):
            if bad.lower() in COPYLEFT or bad.lower().startswith("yt-dlp[default"):
                problems.append(f"{script.relative_to(ROOT)}: depends on {bad}, which is or brings GPL/AGPL code; "
                                "see CONTRIBUTING.md")
    if f"(skills/{name})" not in readme:
        problems.append(f"{name}: no row in README.md")
    r = subprocess.run(["uvx", "--from", SKILLS_REF, "agentskills", "validate", str(skill)], **RUN)
    if r.returncode != 0:
        problems.append(f"{name}: agentskills validate failed: {(r.stdout + r.stderr).strip()[-400:]}")
    return problems


def matrix(workflow: str, key: str) -> list[str]:
    """The values of one matrix list in a workflow file (a plain "key:" followed by "- item" lines)."""
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    m = re.search(rf"^(\s*){key}:\n((?:\1\s+- .+\n)+)", text, re.M)
    return re.findall(r"- (\S+)", m[2]) if m else []


def check_marketplace() -> list[str]:
    """.claude-plugin/marketplace.json lists every skill once, as ./skills/<name>, with web-extract as a dependency
    exactly where the skill's scripts load it."""
    where = ".claude-plugin/marketplace.json"
    try:
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        plugins = market["plugins"]
        names = [e["name"] for e in plugins]
        if not isinstance(plugins, list) or not all(isinstance(n, str) for n in names):
            raise TypeError("plugins must be a list of objects with a string name")
    except (OSError, ValueError, KeyError, TypeError) as e:
        return [f"{where}: unreadable ({type(e).__name__}: {e}); it needs a plugins list of objects with a name"]
    problems = [f"{where}: plugin {n} is listed more than once" for n in sorted({n for n in names if names.count(n) > 1})]
    skills = {d.name for d in SKILLS.iterdir() if (d / "SKILL.md").is_file()}
    if set(names) != skills:
        problems.append(f"{where}: plugins {sorted(set(names) ^ skills)} differ from the skill folders; "
                        "keep one plugin per skill")
    for e in plugins:
        name = e["name"]
        if e.get("source") != f"./skills/{name}":
            problems.append(f"{where}: plugin {name} has source {e.get('source')!r}, not './skills/{name}'")
        loads = any("def load_web_extract(" in f.read_text(encoding="utf-8")
                    for f in (SKILLS / name / "scripts").glob("*.py")) if name in skills else False
        deps = e.get("dependencies") or []
        if not isinstance(deps, list):
            problems.append(f"{where}: plugin {name} has dependencies {deps!r}; it must be a list")
            deps = []
        # A dependency is "name", "name@marketplace" or {"name": ...}.
        dep_names = {d.split("@")[0] if isinstance(d, str) else d.get("name") if isinstance(d, dict) else None
                     for d in deps}
        if loads != ("web-extract" in dep_names):
            problems.append(f"{where}: plugin {name} " + ("loads web-extract but does not list it in dependencies"
                                                           if loads else "lists web-extract but never loads it"))
    return problems


def check_repo() -> list[str]:
    """The workflows run every suite and probe listed here, and every test file has a suite."""
    problems = []
    for workflow, key, want in (("ci.yml", "suite", list(SUITES)), ("liveness.yml", "skill", [p[0] for p in PROBES])):
        have = matrix(workflow, key)
        if sorted(have) != sorted(want):
            problems.append(f".github/workflows/{workflow}: the {key} matrix {sorted(set(want) ^ set(have))} differs from "
                            "test/ops.py; keep them the same")
    problems += check_marketplace()
    for f in sorted((ROOT / "test").glob("test_*.py")):
        name = f.stem[5:].replace("_", "-")
        if name not in SUITES:
            problems.append(f"{f.relative_to(ROOT)}: no entry in SUITES in test/ops.py, so CI does not run it")
    return problems


def cmd_validate(args: argparse.Namespace) -> int:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    skills = sorted(p for p in SKILLS.iterdir() if p.is_dir())
    problems = [p for s in skills for p in check_skill(s, readme)] + check_repo()
    for p in problems:
        print(f"::error::{p}" if os.environ.get("GITHUB_ACTIONS") else f"error: {p}")
    print(f"{len(skills)} skills checked, {len(problems)} problem(s)")
    return 1 if problems else 0


def uv_pytest(test_file: str, deps: list[str], doctests: list[str], env: dict[str, str]) -> int:
    cmd = ["uv", "run", "-q", "--no-project", "--with", "pytest"]
    for d in deps:
        cmd += ["--with", d]
    cmd += PY + [test_file] + (["--doctest-modules", *doctests] if doctests else [])
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, env={**os.environ, **env}).returncode


def cmd_test(args: argparse.Namespace) -> int:
    names = args.suites or list(SUITES)
    unknown = [n for n in names if n not in SUITES]
    if unknown:
        print(f"error: unknown suite(s): {', '.join(unknown)}; known: {', '.join(SUITES)}")
        return 2
    failed = []
    for n in names:
        deps, doctests = SUITES[n]
        if uv_pytest(f"test/test_{n.replace('-', '_')}.py", deps, doctests, OFFLINE_ENV) != 0:
            failed.append(n)
    print(f"{len(names) - len(failed)}/{len(names)} suites passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


def verdict(code: int | str) -> str:
    return "ok" if code == 0 else "blocked" if code in (4, 6) else "broken"


def run_probe(cmd: list[str], timeout: int, env: dict[str, str] | None = None) -> tuple[int | str, str]:
    """(exit code or "timeout", the whole output)."""
    try:
        r = subprocess.run(cmd, cwd=ROOT, timeout=timeout, env={**os.environ, **(env or {})}, **RUN)
    except subprocess.TimeoutExpired:
        return "timeout", f"no answer within {timeout} s"
    return r.returncode, (r.stdout + "\n" + r.stderr).strip()


def live_verdict(code: int | str, out: str) -> str:
    r"""A live pytest run: "ok" when it passed at least one test (all skipped or none selected is not a pass);
    "blocked" when every failure is a script exiting 4 or 6 (pytest prints "assert 6 == 0" for the live tests'
    `returncode == 0` checks), the same split as the commands; otherwise "broken".

    >>> live_verdict(1, "E       assert 6 == 0\n1 failed")
    'blocked'
    >>> live_verdict(1, "E       assert 6 == 0\nE       assert 7 == 0\n2 failed")
    'broken'
    >>> live_verdict(0, "3 skipped")
    'broken'
    """
    if code == 0 and re.search(r"\b\d+ passed\b", out):
        return "ok"
    exits = re.findall(r"\bassert (\d+) == 0\b", out)
    failed = re.search(r"\b(\d+) failed\b", out)
    if exits and failed and all(e in ("4", "6") for e in exits) and len(exits) >= int(failed[1]):
        return "blocked"
    return "broken"


def cmd_probe(args: argparse.Namespace) -> int:
    chosen = [p for p in PROBES if not args.skills or p[0] in args.skills]
    results = []
    for skill, argv, live_env in chosen:
        script = next(SKILLS.joinpath(skill, "scripts").glob(argv[0]))
        start = time.monotonic()
        code, out = run_probe(["uv", "run", "-q", str(script), *argv[1:]], 300)
        results.append({"skill": skill, "kind": "command", "command": " ".join(["scripts/" + argv[0], *argv[1:]]),
                        "exit": code, "verdict": verdict(code), "output": out[-600:],
                        "seconds": round(time.monotonic() - start, 1)})
        print(f"{results[-1]['verdict']:8} {skill:20} command    exit {code}", flush=True)
        # The live suite repeats what the command found when the source refused this network: skip it then.
        if live_env and not args.commands_only and code == 0:
            test_file = f"test/test_{skill.replace('-', '_')}.py"
            cmd = ["uv", "run", "-q", "--no-project", "--with", "pytest"]
            for d in LIVE_SUITE_DEPS.get(skill, []):
                cmd += ["--with", d]
            start = time.monotonic()
            code, out = run_probe(cmd + PY + ["-rs", "-k", "live", test_file], 900, {live_env: "1"})
            results.append({"skill": skill, "kind": "live tests", "command": f"{live_env}=1 pytest -k live {test_file}",
                            "exit": code, "verdict": live_verdict(code, out), "output": out[-600:],
                            "seconds": round(time.monotonic() - start, 1)})
            print(f"{results[-1]['verdict']:8} {skill:20} live tests exit {code}", flush=True)
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 1 if any(r["verdict"] == "broken" for r in results) else 0


def quiet(text: str) -> str:
    """Third-party output made safe to quote in an issue: no control or bidi characters, no fence breaks, no
    @mentions or #references that would notify people or link other issues."""
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", " ", text)
    text = re.sub(r"[`~]{3,}", "''", text)
    return text.replace("@", "@\u200b").replace("#", "#\u200b")


def cmd_report(args: argparse.Namespace) -> int:
    results = []
    for f in args.files:
        with contextlib.suppress(OSError, json.JSONDecodeError):
            results += json.loads(Path(f).read_text(encoding="utf-8"))
    # A probe that left no result (it crashed or the job failed) is broken, not silently absent.
    seen = {r["skill"] for r in results}
    for skill, argv, _ in PROBES:
        if skill not in seen:
            results.append({"skill": skill, "kind": "command", "command": "scripts/" + " ".join(argv), "exit": "none",
                            "verdict": "broken", "output": "the probe left no result: see the job log"})
    groups = {v: [r for r in results if r["verdict"] == v] for v in ("broken", "blocked")}
    bad = {r["skill"] for r in groups["broken"] + groups["blocked"]}
    ok = sorted({r["skill"] for r in results} - bad)
    lines = [f"Liveness probe of {time.strftime('%Y-%m-%d', time.gmtime())}: "
             f"{len({r['skill'] for r in groups['broken']})} broken, "
             f"{len({r['skill'] for r in groups['blocked']} - {r['skill'] for r in groups['broken']})} blocked, "
             f"{len(ok)} ok.", ""]
    if groups["broken"]:
        lines += ["## Broken", "The source changed or the script has a bug: these need a fix.", ""]
        for r in groups["broken"]:
            lines += [f"- **{r['skill']}** ({r['kind']}): `{r['command']}` exited {r['exit']}",
                      "  ~~~~", *["  " + quiet(ln) for ln in r.get("output", "").splitlines()[-8:]], "  ~~~~"]
        lines.append("")
    if groups["blocked"]:
        lines += ["## Blocked or unreachable from the runner",
                  "Exit 4 or 6: the source refused or did not answer GitHub's network. Often a datacenter-IP "
                  "policy (YouTube, Telegram); check from another network before changing anything.", ""]
        for r in groups["blocked"]:
            last = quiet(r.get("output", "").splitlines()[-1]) if r.get("output") else ""
            lines.append(f"- {r['skill']}: `{r['command']}` exited {r['exit']}" + (f": `{last}`" if last else ""))
        lines.append("")
    if ok:
        lines += ["## OK", ", ".join(ok), ""]
    text = "\n".join(lines)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 1 if groups["broken"] else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="check every skill folder")
    t = sub.add_parser("test", help="run offline test suites")
    t.add_argument("suites", nargs="*")
    pr = sub.add_parser("probe", help="run live probes")
    pr.add_argument("skills", nargs="*")
    pr.add_argument("--json", help="write the results to this file")
    pr.add_argument("--commands-only", action="store_true", help="skip the live pytest suites")
    r = sub.add_parser("report", help="Markdown report from probe results")
    r.add_argument("files", nargs="*", help="probe result files (missing probes count as broken)")
    r.add_argument("-o", "--output")
    args = p.parse_args()
    return {"validate": cmd_validate, "test": cmd_test, "probe": cmd_probe, "report": cmd_report}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
