# pdf-typeset evals

Two evals, each run on Claude Code (Sonnet) and Codex. Results from 2026-10-30 are in `results/2026-10-30/`.

## Triggers

`prompts.json` has 11 prompts: 6 that must load the skill (including a Russian letter) and 5 that must not
(merge PDFs, extract tables, fill a PDF form, an HTML page, a question about PDF/A). The runner is shared with
human-writing:

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill pdf-typeset
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill pdf-typeset
```

| Agent | Correct |
|---|---|
| Claude Sonnet | 11/11 |
| Codex | 11/11 |

## Outcome

`briefs.json` has four briefs: a Russian report from notes, an invoice from JSON, a one-page CV from plain text,
and a one-page handout for a leadership meeting. Each brief runs twice per agent:

- **plain**: the agent with no skill;
- **skill**: the same agent with the skill installed at the project level.

Both arms get the same tools: read and write files, and run python, uv and pip.

```bash
python3 -I test/evals/pdf-typeset/run_outcome.py claude OUT --model sonnet
python3 -I test/evals/pdf-typeset/run_outcome.py codex OUT
python3 -I test/evals/pdf-typeset/run_outcome.py claude OUT --model sonnet --arm skill   # re-run one arm
```

Every PDF is checked with the skill's own checker, so both arms are measured with the same yardstick, and rendered
to PNG. A fresh judge sub-agent then ranks each pair blind: A/B labels shuffled, no names, no file paths. It
weighs fidelity to the input first (it recomputes invoice totals), then typography and layout, then fitness for
the use.

### What the plain agents produced

These are the same plain outputs in every round (`outcome-*-round1.json`).

- **Claude, report:** no PDF. Headless Chrome needed an approval the run could not give, and the agent did not
  try a Python route.
- **Claude, the other three:** set in Helvetica, which is not embedded.
- **Codex, all four:** a non-embedded Helvetica in each (three of them mix it with embedded Roboto or DejaVu
  Sans).

None of the plain PDFs declares a language or is tagged.

### Rounds

The judge's first verdict exposed real weaknesses in the templates, so this ran as RED → fix → re-run.

| Round | Skill-arm changes before it | Skill wins (Claude / Codex) |
|---|---|---|
| 1 | — | 2/4 / 1/4 (Claude's report won by default: no plain PDF) |
| 2 | CV dates (a real bug: "Mar 2021" printed as "Mar 202"); bigger type; one-pager fits its type size to the page; key-figure cards; invoice sizing | 3/4 / 1/4 |
| 3 | Non-breaking spaces (number–unit, digit groups, Slavic short words); CV header rhythm; invoice payment block at the foot; roomier tables | **4/4 / 3/4**, with Codex's CV a tie |

In round 3 the judge found no factual or arithmetic error in any pair. Every invoice recomputes to
7,037.50 + 1,337.13 = 8,374.63.

Every skill-arm PDF passed the checks except one. Claude's report was flagged `missing-glyph` for the hyphens
Typst draws at line breaks. The agent looked at the PNGs, judged it a false positive and said so. The checker now
ignores those hyphens (with a regression test), and the same PDF passes.

Cost per run on Sonnet:

| Arm | Cost |
|---|---|
| skill | $0.09–0.28 |
| plain | $0.14–0.26 |

### Caveats

- The plain arm was not re-run between rounds, so the skill was tuned while its opponent stayed fixed. Each
  round used a new judge and a new shuffle, but the judge model was the same.
- Four briefs per agent is a small sample. Round 3 decides the acceptance criterion; rounds 1 and 2 show how
  close it was.
