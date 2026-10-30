# Evals for `human-writing`

Three evals that need a live agent and cost real tokens, so they are not part of the offline test suite (`test/test_human_writing.py`). Run them before changing the skill's description, workflow or reviewer briefs. Results from each run go into `results/<date>/`.

| Script | Question | Rough cost |
|---|---|---|
| `run_triggers.py` | Does the agent load the skill for writing tasks and leave it alone otherwise? | 10 short runs per agent |
| `run_panel.py` | Does the review panel catch the defects planted in `test/data/prose/seeded/`? | ~40 reviewer runs; `single` ~6 |
| `run_outcome.py` | Is a text written with the skill better than one written without it, judged blind? | 6 writing runs + 3 judge runs, a few dollars |

```bash
python3 -I run_triggers.py claude /tmp/out --model sonnet [--user-settings]
python3 -I run_triggers.py codex /tmp/out [--clean]
python3 -I run_panel.py panel /tmp/out --model sonnet
python3 -I run_panel.py single /tmp/out --model sonnet
python3 -I run_outcome.py /tmp/out --model opus --judge opus [--only o3-readme]
```

The scripts install a copy of the skill into a scratch project, run Claude Code (`claude -p`) or Codex (`codex exec`) headless with read-only tools or a read-only sandbox, and parse the JSON event streams. They never run an agent with permission checks disabled.

## Results, 2026-10-30

### Triggers (`prompts.json`: 6 writing tasks that should load the skill, 4 that shouldn't)

| Agent and setup | Correct |
|---|---|
| Claude Code, Sonnet, original description | 4/10 (loaded the skill on none of the 6 writing tasks) |
| Claude Code, Sonnet, final description | 10/10 (twice) |
| Claude Code, Opus, final description | 10/10 |
| Claude Code, Sonnet and Opus, user settings with the superpowers plugin and 120 skills | 10/10 each; superpowers' brainstorming never took over |
| Codex, clean config | 9/10: also read SKILL.md for "what does --batch-size do?", then answered without it |
| Codex with the user's config (superpowers plugin) | 8/10: read SKILL.md on two unrelated questions, then answered without it |

The description was the cause of the original misses. A "what it does, then when to use it" description was ignored by Sonnet. Leading with "use this whenever you are about to write or rewrite prose…", naming the README and "sounds like ChatGPT" cases, and listing what the skill is not for fixed it. Codex's extra loads come from superpowers' rule to open any skill that might apply. They cost tokens but didn't change the answers.

### Review panel (`test/data/prose/seeded/`: 6 texts, 40 planted defects)

| Mode | Caught by the lens that owns the defect | Findings in total |
|---|---|---|
| Panel: one isolated reviewer per lens (Sonnet) | 40/40 | 248 |
| Single-agent fallback: all lenses in one run (Sonnet) | 40/40 | 178 |

Both modes caught every planted defect. The single-agent run reported fewer findings, and this eval doesn't measure whether the extra panel findings are useful or noise. Planted defects are blunt (a wrong number, a leaked token, a slop paragraph), so the eval shows the briefs work. It can't tell the two modes apart on subtle problems. The main argument for isolation remains that a writer reviewing its own draft keeps the context that made the draft look clear, and this eval doesn't test that because the reviewers never wrote the texts.

One scoring note: "missing prerequisites" has no line to point at, so defects can list keywords; both modes reported it, anchored to different lines.

### Outcome (`outcome/topics.json`: a LinkedIn post, a dev.to article, a README; Opus writes, Opus judges blind)

| Topic | Overall | Reads more human | More useful | Unsupported claims: plain / skill | Skill run: reviewers, cost |
|---|---|---|---|---|---|
| LinkedIn post | skill | skill | skill | 4 / 0 | 9 sub-agents, $1.13 |
| dev.to article | skill | skill | plain | 8 / 3 | 10 sub-agents, $2.64 |
| README | plain | skill | plain | 5 / 1 | 4 sub-agents, $1.01 |

- **Wins.** The skill won 2 of 3 overall and all 3 on reading like a person.
- **Unsupported claims.** These are the judge's count of claims the material didn't support. The skill made far fewer than the plain agent but not zero: in the article, a general claim about Chrome DevTools without a link, and a slight extension of the author's words; in the README, a documentation link added from outside the material.
- **Cost.** A plain run cost $0.07–0.11. With the skill, the interview, linting and review panel cost 10–25 times more.
- **Fixes made because of this eval:**
  - The skill now treats general technical knowledge as a fact that needs a cited source, and the fact-checker brief has a check for it.
  - A request for "the final text only" no longer skips the review panel. The first README run skipped it; the rerun above has the fix.
- **Noise.** Each cell is one sample. Rerunning a topic can flip a close call, as the README did between runs (skill, then plain overall).
