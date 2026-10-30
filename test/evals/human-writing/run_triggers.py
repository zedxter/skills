#!/usr/bin/env python3
"""Trigger evals for the human-writing skill: does each agent load the skill when it should, and only then?

    python3 -I run_triggers.py claude OUT_DIR [--model sonnet] [--user-settings]   # --user-settings: load ~/.claude plugins and hooks too
    python3 -I run_triggers.py codex OUT_DIR [--clean]   # --clean: ignore ~/.codex config and plugins

Each prompt runs in a scratch project that has only this skill installed at the project level (Claude Code:
.claude/skills/, Codex: .agents/skills/) plus tool.py and CHANGELOG.md. Claude Code runs with project settings
only and read-only tools; Codex runs in its read-only sandbox. A run counts as "loaded" when the agent invokes
the skill or reads its SKILL.md. Costs real tokens: about 20 short agent runs per agent.
"""
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parents[2] / "skills" / "human-writing"


def project(root: Path, agent: str) -> Path:
    proj = root / f"{agent}-proj"
    if not proj.exists():
        skills = proj / (".claude/skills" if agent == "claude" else ".agents/skills")
        skills.mkdir(parents=True)
        shutil.copytree(SKILL, skills / "human-writing")
        for f in ("tool.py", "CHANGELOG.md"):
            shutil.copy(HERE / f, proj / f)
    return proj


def skills_used(agent: str, events: list[dict]) -> list[str]:
    names = []
    for e in events:
        if agent == "claude" and e.get("type") == "assistant":
            for c in e["message"]["content"]:
                if c.get("type") == "tool_use" and c["name"] == "Skill":
                    names.append(str(c.get("input", {}).get("skill", "?")))
        if agent == "codex":
            cmd = e.get("item", {}).get("command", "") if e.get("type") == "item.completed" else ""
            names += [m for m in __import__("re").findall(r"skills/([\w:-]+)/SKILL\.md", cmd)]
    return names


def loaded(agent: str, events: list[dict]) -> bool:
    for e in events:
        if agent == "claude" and e.get("type") == "assistant":
            for c in e["message"]["content"]:
                if c.get("type") == "tool_use":
                    inp = json.dumps(c.get("input", {}))
                    if (c["name"] == "Skill" and "human-writing" in inp) or "human-writing/SKILL.md" in inp:
                        return True
        if agent == "codex":
            item = e.get("item", {})
            if item.get("type") == "command_execution" and "human-writing/" in item.get("command", ""):
                return True
    return False


def run(agent: str, proj: Path, case: dict, out: Path, model: str | None, clean: bool, user: bool = False) -> dict:
    if agent == "claude":
        cmd = ["claude", "-p", case["prompt"], "--setting-sources", "user,project" if user else "project",
               "--output-format", "stream-json",
               "--verbose", "--allowedTools", "Read Glob Grep Skill", "--no-session-persistence"]
        cmd += ["--model", model] if model else []
    else:
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "-s", "read-only", "-C", str(proj), case["prompt"]]
        cmd[2:2] = ["--ignore-user-config"] if clean else []
        cmd += ["-m", model] if model else []
    try:
        p = subprocess.run(cmd, cwd=proj, capture_output=True, text=True, timeout=420, stdin=subprocess.DEVNULL)
        raw = p.stdout
    except subprocess.TimeoutExpired as e:
        raw = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
    (out / f"{agent}-{case['id']}.jsonl").write_text(raw, encoding="utf-8")
    events = []
    for line in raw.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    got = loaded(agent, events)
    return {"id": case["id"], "expect": case["expect"], "loaded": got, "ok": got == case["expect"],
            "skills": skills_used(agent, events)}


def main() -> int:
    agent, out = sys.argv[1], Path(sys.argv[2])
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else None
    clean = "--clean" in sys.argv
    user = "--user-settings" in sys.argv
    out.mkdir(parents=True, exist_ok=True)
    proj = project(out, agent)
    cases = json.loads((HERE / "prompts.json").read_text(encoding="utf-8"))
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda c: run(agent, proj, c, out, model, clean, user), cases))
    (out / f"{agent}-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    for r in results:
        print(f"{'ok ' if r['ok'] else 'BAD'} {r['id']:<18} expect={r['expect']!s:<5} loaded={r['loaded']!s:<5} skills={r['skills']}")
    print(f"{agent}: {sum(r['ok'] for r in results)}/{len(results)} correct")
    return 0


if __name__ == "__main__":
    sys.exit(main())
