#!/usr/bin/env python3
"""Generate the "model default" corpus: each prompt in slop_prompts.json answered by several models with no skill
and no extra instructions, plus a "humanized" set where Sonnet is told to avoid AI clichés (hard cases).

    python3 -I generate_slop.py OUT_DIR

Writes OUT_DIR/<genre>-<id>-<model>.md. Claude Code runs with project settings only and no tools; Codex runs
with a clean config in its read-only sandbox. The outputs are model text, not human text: the slop corpus.
"""
import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
HUMANIZE = (" Write it like an experienced human writer would: no clichés, no buzzwords, no em dashes, "
            "no 'it's not X, it's Y', no lists of three, no motivational ending.")


def claude(prompt: str, model: str, cwd: str) -> str:
    p = subprocess.run(["claude", "-p", "--model", model, "--setting-sources", "project", "--output-format", "json",
                        "--allowedTools", "", "--no-session-persistence"], input=prompt + " Reply with the text only.",
                       cwd=cwd, capture_output=True, text=True, timeout=600)
    return json.loads(p.stdout).get("result", "")


def codex(prompt: str, cwd: str) -> str:
    p = subprocess.run(["codex", "exec", "--ignore-user-config", "--skip-git-repo-check", "-s", "read-only", "-C", cwd,
                        "--json", prompt + " Reply with the text only; don't create files."],
                       capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    msgs = [json.loads(l)["item"]["text"] for l in p.stdout.splitlines()
            if '"agent_message"' in l and '"item.completed"' in l]
    return msgs[-1] if msgs else ""


def main() -> int:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    cwd = tempfile.mkdtemp(prefix="slopgen-")
    jobs = []
    for c in json.loads((HERE / "slop_prompts.json").read_text(encoding="utf-8")):
        base = f"{c['genre']}-{c['id'].split('-')[1]}"
        jobs += [(f"{base}-sonnet", lambda c=c: claude(c["prompt"], "sonnet", cwd)),
                 (f"{base}-haiku", lambda c=c: claude(c["prompt"], "haiku", cwd)),
                 (f"{base}-codex", lambda c=c: codex(c["prompt"], cwd)),
                 (f"{base}-sonnet-humanized", lambda c=c: claude(c["prompt"] + HUMANIZE, "sonnet", cwd))]

    def run(job):
        name, fn = job
        try:
            text = fn()
        except Exception as e:  # noqa: BLE001
            text = ""
            print(f"{name}: {e}", file=sys.stderr)
        (out / f"{name}.md").write_text(text.strip() + "\n", encoding="utf-8")
        return name, len(text.split())

    with ThreadPoolExecutor(6) as pool:
        for name, words in pool.map(run, jobs):
            print(f"{name:<36} {words:>5} words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
