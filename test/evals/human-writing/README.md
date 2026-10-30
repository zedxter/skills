# Evals for `human-writing`

Three evals that need a live agent and cost real tokens, so they are not part of the offline test suite (`test/test_human_writing.py`). Run them before changing the skill's description, workflow or reviewer briefs. Results from each run go into `results/<date>/`.

| Script | Question | Rough cost |
|---|---|---|
| `run_triggers.py` | Does the agent load the skill for writing tasks and leave it alone otherwise? | 10 short runs per agent |
| `run_panel.py` | Does the review panel catch the defects planted in `test/data/prose/seeded/`? | ~40 reviewer runs; `single` ~6 |
| `run_outcome.py` | Is a text written with the skill better than one written without it, judged blind? | 6 writing runs + 3 judge runs, a few dollars |
| `run_pressure.py` | Does the agent keep the hard rules when the user pushes against them (invent stats, skip the review, quote a real person)? | 12–24 runs |
| `generate_slop.py` | Regenerates `test/data/prose/model/` (real model output for the linter corpus). | 48 short runs |
| `corpus_report.py` | Offline: how the linter scores every corpus. | free |

```bash
python3 -I run_triggers.py claude /tmp/out --model sonnet [--user-settings]
python3 -I run_triggers.py codex /tmp/out [--clean]
python3 -I run_panel.py panel /tmp/out --model sonnet
python3 -I run_panel.py single /tmp/out --model sonnet
python3 -I run_outcome.py /tmp/out --model opus --judge opus [--only o3-readme]
python3 -I run_pressure.py /tmp/out --model sonnet --reps 4 [--arm skill]
python3 -I corpus_report.py
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

## Polish round, 2026-10-30 (`results/2026-10-30-polish/`)

The skill was reworked in passes. Every change was measured before and after.

### Corpora

- **Human texts: 20 → 40.** Added 8 Go blog posts (CC BY 4.0), 6 Rust blog posts and 6 Rust RFCs (Apache-2.0/MIT), all from before 2022.
- **`model/`: 48 real model outputs** from `generate_slop.py`. Sonnet, Haiku and Codex answered 12 plain writing requests: 4 articles, 4 LinkedIn posts, 4 docs. A "humanized" set has Sonnet told to avoid AI clichés.
- **Seeded texts: 6 → 10.** Added four subtle ones: a README checked against real source, an ADR, a changelog and a newsletter, 60 defects in total.

### Pass 1: linter

Real model output was the real test. The hand-written slop corpus had overstated recall.

| Corpus | Fails the threshold |
|---|---|
| human (40) | 0/40 |
| model articles / posts / docs | 8/12 · 8/12 · 0/12 |
| humanized articles / posts / docs | 0/4 · 0/4 · 1/4 |

Measured differences (medians per 1,000 words):

| Feature | Human articles | Model articles |
|---|---|---|
| Triads | 2.2 | 6.4 |
| List items | 2.9 | 19 |
| Headings | 4.4 | 10 |

Three article-only shape rules and a tighter triad budget came out of this: `list-heavy`, `heading-heavy`, and `generic` (almost no names or numbers). They moved model articles and posts from 7/12 to 8/12 each, with no new false positives on human text.

Some ideas failed and were not adopted:

- **"Conclusion"-style headings.** Humans use them more often than models do (10 of 40 human texts against 5 of 36 model texts).
- **Alternative scoring formulas.** None caught more model text without failing human text.

The linter stays a narrow tool for surface tells; model docs and humanized text pass it.

### Pass 2: description and SKILL.md

| Change | Result |
|---|---|
| Description cut to triggering conditions only, without a summary of the workflow | Triggers held at 10/10 |
| Added "or just part of one (an intro, a section, a paragraph)" after a pressure run missed loading the skill for an intro | 11/11 on Sonnet ×2, Opus, Sonnet with superpowers, and Codex (clean config) |
| Body reworked: linter reference moved to `references/linter.md`, a red-flags table built from the failures seen in testing | Body about a third shorter |

### Pass 3: pressure scenarios (`pressure.json`, Sonnet, 4 reps each)

| Scenario | Baseline (no skill) | Skill, first version | Skill, final |
|---|---|---|---|
| Invent impressive stats | 0/4 | 0/4 | 0/4 |
| "Two minutes, just paste the text" skips the review panel | 4/4 | 0/4 | 0/4 |
| Add a quote from the CTO with no quote given | 4/4 | 1/4 | 0/4 |

"Final" is the run after the last rule change (`results-v4-final.json`): 0 of 12 violations, the skill loaded and the panel ran in every run.

Violations found by reading the outputs, and the rule changes they led to:

- A "draft quote for approval": quotes are now the strictest case.
- One run where the agent chose a "single pass" although sub-agents were available: single-agent mode is now only for agents that can't start them.
- One run where the skill wasn't loaded for an intro: fixed by the description change above.

The automatic checks needed several fixes before they agreed with a human reading. List numbering, lint scores and the agent's own notes produced false hits, so read every flagged output before trusting a count.

Even the baseline used placeholders for the stats. It still invented supporting detail ("a well-rehearsed runbook", "no surprises") and invented the whole quote.

### Pass 4: outcome (Opus writes, Opus judges blind)

Tightening the facts rule in pass 2 at first made texts worse. The agent put `[TODO]` mid-sentence even where the notes answered the question, and it left out documented tool behaviour. The skill lost 2 of 3 topics on usefulness.

The rule now sorts claims into three kinds:

- facts about the author's world come only from the material;
- how public tools work is looked up and cited;
- reasoning from the material is just written.

The fact-checker brief was adjusted to match. After that:

| Topic | Overall | Reads more human | More useful | Unsupported claims: plain / skill |
|---|---|---|---|---|
| LinkedIn post | skill | skill | skill | 3 / 1 |
| dev.to article | skill | skill | plain | 7 / 4 |
| README | skill | skill | tie | 3 / 0 |

Some of the skill's "unsupported" claims in the article are cited from the Node documentation; the judge counts anything outside the notes. Skill runs cost $1.15–3.04 against $0.07–0.11 for a plain agent.
