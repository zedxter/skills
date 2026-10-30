#!/usr/bin/env python3
"""Outcome eval: the same writing tasks done by Claude Code without and with the human-writing skill, judged blind.

    python3 -I run_outcome.py OUT_DIR [--model opus] [--judge opus] [--only TOPIC]

For each topic in outcome/topics.json, two headless Claude Code runs get the identical prompt: one in a project
without the skill, one with it installed under .claude/skills/. Neither may ask questions. A separate judge run sees
both texts in random order as A and B, plus the material, and returns which reads more like a specific expert
wrote it and which is more useful, and lists every claim in each text that the material does not support. The
skill run may start reviewer sub-agents; tools are limited to reading, sub-agents and running the linter.
"""
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parents[2] / "skills" / "human-writing"
TOOLS = "Read Glob Grep Skill Task Agent Bash(python3:*) Bash(python:*) Bash(uv run:*)"


def claude(prompt: str, cwd: Path, model: str, tools: str) -> tuple[str, float]:
    cmd = ["claude", "-p", "--model", model, "--setting-sources", "project", "--output-format", "stream-json",
           "--verbose", "--allowedTools", tools, "--no-session-persistence"]
    p = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, timeout=1800)
    claude.last_stream = p.stdout
    for line in reversed(p.stdout.splitlines()):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("type") == "result":
            return data.get("result", ""), data.get("total_cost_usd", 0.0)
    return "ERROR: " + p.stdout[-500:] + p.stderr[-500:], 0.0


def tools_used(stream: str) -> dict:
    counts: dict = {}
    for line in stream.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "assistant":
            for c in e["message"]["content"]:
                if c.get("type") == "tool_use":
                    counts[c["name"]] = counts.get(c["name"], 0) + 1
    return counts


JUDGE = """You are judging two texts written from the same material for the same task. You don't know how either was produced.

Task given to the writers: {task}

Material the writers had (the only source of facts):
{material}

Text A:
<<<
{a}
>>>

Text B:
<<<
{b}
>>>

Judge as an experienced editor who reads a lot of {kind}. Answer with JSON only:
{{"human": "A" | "B" | "tie", "human_why": "one or two sentences: which reads more like a specific person who did the work wrote it",
 "useful": "A" | "B" | "tie", "useful_why": "one or two sentences: which serves the reader better",
 "overall": "A" | "B" | "tie",
 "unsupported_A": ["each factual claim in A that the material does not support (numbers, names, causes, results); [TODO] markers are not claims"],
 "unsupported_B": ["same for B"],
 "ai_tells_A": ["quoted phrases that read as machine-written"], "ai_tells_B": ["same for B"]}}"""


def main() -> int:
    out = Path(sys.argv[1])
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "opus"
    judge_model = sys.argv[sys.argv.index("--judge") + 1] if "--judge" in sys.argv else "opus"
    out.mkdir(parents=True, exist_ok=True)
    plain, skilled = out / "plain-proj", out / "skill-proj"
    plain.mkdir(exist_ok=True)
    if not (skilled / ".claude/skills/human-writing").exists():
        shutil.copytree(SKILL, skilled / ".claude/skills/human-writing")
    rng = random.Random(25)
    summary = []
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    for t in json.loads((HERE / "outcome" / "topics.json").read_text(encoding="utf-8")):
        if only and t["id"] != only:
            continue
        prompt = (f"{t['task']} Everything you know is below; don't ask me questions. If something is missing, "
                  f"mark it [TODO] rather than making it up. Reply with the final text only.\n\n{t['material']}")
        texts = {}
        for name, proj in (("plain", plain), ("skill", skilled)):
            text, cost = claude(prompt, proj, model, TOOLS)
            texts[name] = text
            (out / f"{t['id']}-{name}.md").write_text(text, encoding="utf-8")
            (out / f"{t['id']}-{name}.jsonl").write_text(claude.last_stream, encoding="utf-8")
            summary.append({"topic": t["id"], "run": name, "cost_usd": cost, "tools": tools_used(claude.last_stream)})
        order = ["plain", "skill"]
        rng.shuffle(order)
        kind = {"o1": "LinkedIn posts", "o2": "developer blog posts", "o3": "README files"}[t["id"][:2]]
        verdict_raw, _ = claude(JUDGE.format(task=t["task"], material=t["material"], a=texts[order[0]], b=texts[order[1]],
                                             kind=kind), plain, judge_model, "Read")
        try:
            body = verdict_raw[verdict_raw.find("{"):verdict_raw.rfind("}") + 1]
            v = json.loads(body)
        except json.JSONDecodeError:
            v = {"error": verdict_raw[:300]}
        label = {"A": order[0], "B": order[1], "tie": "tie"}
        result = {"topic": t["id"], "order": order,
                  "human": label.get(v.get("human"), "?"), "useful": label.get(v.get("useful"), "?"),
                  "overall": label.get(v.get("overall"), "?"),
                  "unsupported": {order[0]: v.get("unsupported_A", []), order[1]: v.get("unsupported_B", [])},
                  "ai_tells": {order[0]: v.get("ai_tells_A", []), order[1]: v.get("ai_tells_B", [])},
                  "why": {"human": v.get("human_why"), "useful": v.get("useful_why")}}
        (out / f"{t['id']}-verdict.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"{t['id']}: overall={result['overall']} human={result['human']} useful={result['useful']} "
              f"unsupported plain={len(result['unsupported']['plain'])} skill={len(result['unsupported']['skill'])}")
    (out / f"costs{'-' + only if only else ''}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
