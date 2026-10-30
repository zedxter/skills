#!/usr/bin/env python3
"""Outcome eval for pdf-typeset: the same four briefs done by an agent without the skill and with it.

    python3 -I run_outcome.py claude OUT_DIR [--model sonnet] [--only report,invoice]
    python3 -I run_outcome.py codex OUT_DIR [--only …] [--arm skill]   # --arm: rerun one arm, keep the other

Each run gets a scratch project with the brief's input file. Both arms get the same tools: file read/write and
running python/uv/pip. The skill arm also has the skill installed at the project level (Claude Code:
.claude/skills/, Codex: .agents/skills/). Afterwards every PDF is checked and rendered to PNG with the skill's own
checker (the same yardstick for both arms) into OUT_DIR/<agent>-<brief>-<arm>/png/, ready for blind ranking.
Costs real tokens: 8 agent runs per agent.
"""
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SKILL = ROOT / "skills" / "pdf-typeset"
CHECKER = SKILL / "scripts" / "pdf.py"
BRIEFS = json.loads((HERE / "briefs.json").read_text(encoding="utf-8"))
CLAUDE_TOOLS = "Read Write Edit Glob Grep Bash(python3:*) Bash(python:*) Bash(uv:*) Bash(pip:*) Bash(pip3:*) Bash(ls:*)"


def project(out: Path, agent: str, brief: dict, arm: str) -> Path:
    proj = out / f"{agent}-{brief['id']}-{arm}"
    if proj.exists():
        shutil.rmtree(proj)
    proj.mkdir(parents=True)
    for name, content in brief["files"].items():
        (proj / name).write_text(content, encoding="utf-8")
    if arm == "skill":
        dest = proj / (".claude/skills" if agent == "claude" else ".agents/skills") / "pdf-typeset"
        shutil.copytree(SKILL, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return proj


def run(agent: str, out: Path, brief: dict, arm: str, model: str | None) -> dict:
    proj = project(out, agent, brief, arm)
    prompt = brief["prompt"] + " Save it as out.pdf in this folder. Work without asking questions."
    if agent == "claude":
        cmd = ["claude", "-p", prompt, "--setting-sources", "project", "--output-format", "stream-json", "--verbose",
               "--allowedTools", CLAUDE_TOOLS, "--no-session-persistence"] + (["--model", model] if model else [])
    else:
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "--ignore-user-config", "-s", "workspace-write",
               "-c", "sandbox_workspace_write.network_access=true", "-C", str(proj), prompt] + (["-m", model] if model else [])
    try:
        p = subprocess.run(cmd, cwd=proj, capture_output=True, text=True, timeout=1500, stdin=subprocess.DEVNULL)
        raw = p.stdout
    except subprocess.TimeoutExpired as e:
        raw = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
    (proj / "run.jsonl").write_text(raw, encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines() if line.startswith("{")]
    cost = next((e.get("total_cost_usd") for e in events if e.get("type") == "result"), None)
    used_skill = "pdf-typeset" in raw
    pdf = proj / "out.pdf"
    result = {"agent": agent, "brief": brief["id"], "arm": arm, "pdf": pdf.exists(), "cost_usd": cost,
              "skill_used": used_skill}
    if pdf.exists():
        c = subprocess.run(["uv", "run", "-q", str(CHECKER), "check", str(pdf), "--png", str(proj / "png"), "--dpi", "80",
                            "-f", "json", "-q"], capture_output=True, text=True, timeout=600)
        try:
            report = json.loads(c.stdout)
            result.update(pages=report["pages"], errors=sorted({f["rule"] for f in report["findings"] if f["severity"] == "error"}),
                          warnings=sorted({f["rule"] for f in report["findings"] if f["severity"] == "warning"}),
                          fonts=report.get("fonts"))
        except (json.JSONDecodeError, KeyError):
            result["check_failed"] = c.stderr[-300:]
    return result


def main() -> int:
    agent, out = sys.argv[1], Path(sys.argv[2]).resolve()
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else None
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    out.mkdir(parents=True, exist_ok=True)
    arms = sys.argv[sys.argv.index("--arm") + 1].split(",") if "--arm" in sys.argv else ["plain", "skill"]
    jobs = [(b, arm) for b in BRIEFS if not only or b["id"] in only for arm in arms]
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda j: run(agent, out, j[0], j[1], model), jobs))
    (out / f"{agent}-results{'-' + '-'.join(arms) if len(arms) == 1 else ''}.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
