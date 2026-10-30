# cite-check evals

## Triggers

`prompts.json` has 12 prompts. Seven must load the skill:

- a `.bib` to check
- whether a DOI is real
- a chatbot's source list
- a Russian request to check a reference list
- checking a thesis bibliography before submission
- whether a well-known paper exists
- finding missing DOIs

Five must not. They are close to the skill's subject but outside it:

- finding papers on a topic
- formatting references in APA
- summarising an arXiv paper
- writing a BibTeX parsing function
- proofreading

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill cite-check
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill cite-check
```

Results from 2026-10-30 are in `results/2026-10-30/`.

| Agent | Correct |
|---|---|
| Claude Sonnet | 11/12 |
| Codex | 11/12 |

Both agents miss the same prompt, `t6-one-paper`: "Does the paper 'Attention Is All You Need' by Vaswani et al. 2017 really exist, and what is its arXiv id?" Both answer it from memory, because the paper is famous. The prompt stays in the set as an honest boundary case. Every negative prompt passes.

## Strictness (offline)

`test/test_cite_check.py` builds fabricated references the reviewers found had passed an earlier version, and requires each to come back as something other than `verified`:

- a real DOI with an invented title
- a near-identical title ("video" for "image")
- invented co-authors
- the wrong journal (Nature Communications for Nature)
- an invented DOI next to a real arXiv id
- a real short title extended into a fake one
- free-text references that embed a real title

Real references still verify, including arXiv authors with short family names (He, Ng). `CITE_LIVE=1` runs the ticket's smoke test against the real services: three real DOIs and one fake.
