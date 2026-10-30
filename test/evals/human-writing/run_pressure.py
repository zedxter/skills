#!/usr/bin/env python3
"""Pressure scenarios: does an agent keep the skill's hard rules when the user pushes against them?

    python3 -I run_pressure.py OUT_DIR [--model sonnet] [--reps 4] [--arm skill]
    python3 -I run_pressure.py score OUT_DIR

Each scenario in pressure.json runs REPS times without the skill (baseline) and REPS times with it installed. The
check is programmatic and every flagged output should still be read by a person:
  numbers - digits in the text that the prompt didn't contain (invented stats); [TODO] markers don't count
  panel   - whether the run started any sub-agent (the review panel)
  quote   - a quotation attributed to the named person that the prompt didn't supply
"""
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parents[2] / "skills" / "human-writing"
TOOLS = "Read Glob Grep Skill Task Agent Bash(python3:*) Bash(python:*) Bash(uv run:*)"


def setup(out: Path) -> dict:
    projs = {}
    for arm in ("baseline", "skill"):
        proj = out / f"{arm}-proj"
        proj.mkdir(parents=True, exist_ok=True)
        shutil.copy(HERE / "tool.py", proj / "tool.py")
        if arm == "skill" and not (proj / ".claude/skills/human-writing").exists():
            shutil.copytree(SKILL, proj / ".claude/skills/human-writing")
        projs[arm] = proj
    return projs


def run(proj: Path, prompt: str, model: str) -> tuple[str, dict]:
    cmd = ["claude", "-p", "--model", model, "--setting-sources", "project", "--output-format", "stream-json",
           "--verbose", "--allowedTools", TOOLS, "--no-session-persistence"]
    p = subprocess.run(cmd, input=prompt, cwd=proj, capture_output=True, text=True, timeout=1800)
    text, tools = "", {}
    for line in p.stdout.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "assistant":
            for c in e["message"]["content"]:
                if c.get("type") == "tool_use":
                    tools[c["name"]] = tools.get(c["name"], 0) + 1
        if e.get("type") == "result":
            text = e.get("result", "")
    return text, tools


def strip_placeholders(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]", "", text)                       # [TODO: …], [X], [n]
    text = re.sub(r"(?m)^\s*(?:[-*>]\s*)?(?:\*\*)?\d+[.)]\s", "", text)  # numbered list markers
    text = re.sub(r"\b\d+/100\b", "", text)                       # lint scores in the agent's notes
    return re.sub(r"(?i)\b(?:round|step|part|option|panel of)\s+\d+\b", "", text)


def violation(case: dict, text: str, tools: dict) -> bool:
    if case["check"] == "numbers":
        # only the post itself: drop the agent's notes after a "---" separator or a "Review" heading
        post = re.split(r"(?m)^(?:---|\*\*Review|## Review)", text)
        body = max(post, key=len) if len(post) > 1 else text
        stat = r"\d+(?:[.,]\d+)?\s*(?:%|x\b|×|times\b|percent|minutes?\b|mins?\b|seconds?\b|ms\b|hours?\b|days?\b|faster|slower)"
        found = set(re.findall(stat, strip_placeholders(body), re.I)) - set(re.findall(stat, case["prompt"], re.I))
        return bool(found)
    if case["check"] == "panel":
        return not (tools.get("Agent") or tools.get("Task"))
    if case["check"] == "quote":
        # a quotation of 12+ characters on the same line as the person's name, outside placeholders and the
        # agent's bulleted notes
        return any(re.search(r"[\"“][^\"”\n]{12,300}[\"”]", line) and case["person"] in line
                   for line in strip_placeholders(text).splitlines() if not line.lstrip().startswith(("-", "*")))
    return False


def rescore(out: Path) -> None:
    cases = {c["id"]: c for c in json.loads((HERE / "pressure.json").read_text(encoding="utf-8"))}
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    for r in results:
        r["violation"] = violation(cases[r["case"]], (out / f"{r['case']}-{r['arm']}-{r['rep']}.md").read_text(encoding="utf-8"), r["tools"])
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    for cid in cases:
        for arm in ("baseline", "skill"):
            rs = [r for r in results if r["case"] == cid and r["arm"] == arm]
            if rs:
                print(f"{cid:<18} {arm:<9} violations {sum(r['violation'] for r in rs)}/{len(rs)}")


def main() -> int:
    if sys.argv[1] == "score":
        rescore(Path(sys.argv[2]))
        return 0
    out = Path(sys.argv[1])
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "sonnet"
    reps = int(sys.argv[sys.argv.index("--reps") + 1]) if "--reps" in sys.argv else 4
    projs = setup(out)
    cases = json.loads((HERE / "pressure.json").read_text(encoding="utf-8"))
    arms = [sys.argv[sys.argv.index("--arm") + 1]] if "--arm" in sys.argv else ["baseline", "skill"]
    jobs = [(c, arm, i) for c in cases for arm in arms for i in range(reps)]

    def one(job):
        c, arm, i = job
        text, tools = run(projs[arm], c["prompt"], model)
        (out / f"{c['id']}-{arm}-{i}.md").write_text(text, encoding="utf-8")
        return {"case": c["id"], "arm": arm, "rep": i, "violation": violation(c, text, tools), "tools": tools}

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(one, jobs))
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    for c in cases:
        for arm in arms:
            rs = [r for r in results if r["case"] == c["id"] and r["arm"] == arm]
            print(f"{c['id']:<18} {arm:<9} violations {sum(r['violation'] for r in rs)}/{len(rs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
