#!/usr/bin/env python3
"""Run the human-writing review panel on the seeded-defect texts and score which planted defects it catches.

    python3 -I run_panel.py panel  OUT_DIR [--model sonnet] [--only CASE]   # one isolated reviewer per lens
    python3 -I run_panel.py single OUT_DIR [--model sonnet] [--only CASE]   # single-agent fallback, all lenses in one run
    python3 -I run_panel.py score  OUT_DIR                                  # score saved results again

Cases, inputs and planted defects are in test/data/prose/seeded/cases.json. Every reviewer runs as a separate
headless Claude Code process with read-only tools, and sees only its brief, the line-numbered text and the inputs
its lens needs (references/review-orchestration.md). A defect counts as caught when a finding points at its line
(±1) or quotes it; "owner" means a lens listed for that defect caught it. Costs real tokens: about 40 reviewer runs.
"""
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "skills" / "human-writing"
SEEDED = ROOT / "test" / "data" / "prose" / "seeded"
LINTER = SKILL / "scripts" / "lint_prose.py"


def numbered(text: str) -> str:
    return "\n".join(f"{i:>4}  {line}" for i, line in enumerate(text.split("\n"), 1))


def lens_inputs(lens: str, name: str, case: dict, text: str) -> str:
    parts = [f"Audience: {case['audience']}", f"Platform: {case['platform']}"]
    if lens == "hostile-expert":
        parts.append(f"Field: {case['field']}")
    if lens == "fact-checker":
        parts.append("Facts ledger:\n" + (SEEDED / f"{name}.ledger.md").read_text(encoding="utf-8"))
        if case.get("sources"):
            parts.append("Source files the ledger cites (paths relative to the current directory; read them): "
                         + ", ".join(case["sources"]))
    if lens == "slop-hunter":
        lint = subprocess.run([sys.executable, "-I", str(LINTER), "-", "-g", case["genre"], "-f", "json"],
                              input=text, capture_output=True, text=True).stdout
        parts += [f"Genre: {case['genre']}", "Linter JSON output for this text:\n" + lint]
    if lens == "publication-safety":
        parts.append(f"Author: {case['author']}")
    if lens == "voice-keeper":
        parts.append("Voice profile:\n" + (SEEDED / case["voice"]).read_text(encoding="utf-8"))
    if lens == "platform-editor":
        parts.append(f"Goal: {case.get('goal', 'understanding')}")
    if lens == "newcomer-tester":
        parts += [f"Document type: {case['type']}", "Stated prerequisites: none are stated on the page.",
                  f"Run policy: {case['run_policy']}."]
    if lens == "source-checker":
        help_text = subprocess.run([sys.executable, "-I", str(LINTER), "--help"], capture_output=True, text=True).stdout
        parts += [f"Document type: {case['type']}",
                  "Source of truth: skills/human-writing/scripts/lint_prose.py (read it) and its --help output:\n" + help_text]
    return "\n\n".join(parts)


def claude(prompt: str, model: str) -> str:
    cmd = ["claude", "-p", "--model", model, "--setting-sources", "project", "--output-format", "json",
           "--allowedTools", "Read Glob Grep", "--no-session-persistence"]
    try:
        p = subprocess.run(cmd, input=prompt, cwd=ROOT, capture_output=True, text=True, timeout=900)
        return json.loads(p.stdout).get("result", "")
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return f"ERROR: {e}"


def parse_json(reply: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", reply, re.S)
    body = m.group(1) if m else reply
    start = min([i for i in (body.find("{"), body.find("[")) if i != -1], default=-1)
    try:
        return json.loads(body[start:]) if start != -1 else None
    except json.JSONDecodeError:
        return None


def review(lens: str, name: str, case: dict, model: str) -> dict:
    text = (SEEDED / f"{name}.md").read_text(encoding="utf-8")
    brief = (SKILL / "references" / "reviewers" / f"{lens}.md").read_text(encoding="utf-8")
    prompt = (f"{brief}\n\n---\n\n# Your inputs\n\nText under review (line-numbered):\n\n{numbered(text)}\n\n"
              f"{lens_inputs(lens, name, case, text)}\n\nAnswer with the JSON your brief specifies and nothing else.")
    reply = claude(prompt, model)
    return parse_json(reply) or {"lens": lens, "error": reply[:500], "findings": []}


def single(name: str, case: dict, model: str) -> list:
    text = (SEEDED / f"{name}.md").read_text(encoding="utf-8")
    orchestration = (SKILL / "references" / "review-orchestration.md").read_text(encoding="utf-8")
    fallback = orchestration[orchestration.index("## 6."):orchestration.index("## 7.")]
    sections = []
    for lens in case["lenses"]:
        brief = (SKILL / "references" / "reviewers" / f"{lens}.md").read_text(encoding="utf-8")
        sections.append(f"## Lens: {lens}\n\n{brief}\n\n### Inputs for {lens}\n\n{lens_inputs(lens, name, case, text)}")
    prompt = (f"You are reviewing a text before publication without sub-agents. Follow this procedure:\n\n{fallback}\n"
              f"Run these lenses in order: {', '.join(case['lenses'])}. Each lens's brief and inputs follow.\n\n"
              + "\n\n".join(sections)
              + f"\n\n# Text under review (line-numbered)\n\n{numbered(text)}\n\n"
              "Answer with a JSON array that holds one object per lens, each in the schema from the briefs, and nothing else.")
    result = parse_json(claude(prompt, model))
    return result if isinstance(result, list) else []


def score(name: str, case: dict, results: list) -> dict:
    lines = (SEEDED / f"{name}.md").read_text(encoding="utf-8").split("\n")
    report = {}
    for d in case["defects"]:
        line = next(i for i, l in enumerate(lines, 1) if d["quote"] in l)
        dq = d["quote"].lower()
        by = set()
        for r in results:
            for f in r.get("findings", []) if isinstance(r, dict) else []:
                fq = str(f.get("quote", "")).lower()
                near = isinstance(f.get("line"), int) and abs(f["line"] - line) <= 1
                said = f"{f.get('problem', '')} {f.get('fix', '')}".lower()
                keyword = any(k in said for k in d.get("keywords", []))  # defects that are an absence have no line
                if near or keyword or (len(fq) >= 8 and (fq in dq or dq in fq)):
                    by.add(r.get("lens", "?"))
        report[d["id"]] = {"line": line, "owner": bool(by & set(d["lenses"])), "any": bool(by), "lenses": sorted(by)}
    return report


def main() -> int:
    mode, out = sys.argv[1], Path(sys.argv[2])
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "sonnet"
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    cases = json.loads((SEEDED / "cases.json").read_text(encoding="utf-8"))
    cases = {k: v for k, v in cases.items() if only in (None, k)}
    out.mkdir(parents=True, exist_ok=True)
    if mode == "panel":
        jobs = [(name, lens) for name, case in cases.items() for lens in case["lenses"]]
        with ThreadPoolExecutor(6) as pool:
            done = list(pool.map(lambda j: (j, review(j[1], j[0], cases[j[0]], model)), jobs))
        for name in cases:
            (out / f"panel-{name}.json").write_text(json.dumps([r for (n, _), r in done if n == name], indent=2))
    elif mode == "single":
        with ThreadPoolExecutor(6) as pool:
            done = list(pool.map(lambda n: (n, single(n, cases[n], model)), cases))
        for name, res in done:
            (out / f"single-{name}.json").write_text(json.dumps(res, indent=2))
    totals = {}
    for kind in ("panel", "single"):
        owner = anyc = total = 0
        for name, case in cases.items():
            f = out / f"{kind}-{name}.json"
            if not f.exists():
                continue
            rep = score(name, case, json.loads(f.read_text()))
            (out / f"score-{kind}-{name}.json").write_text(json.dumps(rep, indent=2))
            for did, r in rep.items():
                total += 1
                owner += r["owner"]
                anyc += r["any"]
                if not r["owner"]:
                    print(f"{kind:<6} {name:<28} {did} missed by owner (caught by: {', '.join(r['lenses']) or 'none'})")
        if total:
            totals[kind] = {"defects": total, "owner": owner, "any": anyc}
            print(f"{kind}: {owner}/{total} caught by an owning lens, {anyc}/{total} by any lens")
    (out / "totals.json").write_text(json.dumps(totals, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
