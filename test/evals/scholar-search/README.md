# scholar-search evals

## Triggers

`prompts.json` has 12 prompts. Seven must load the skill:

- papers on a topic
- related work for a paper
- a Russian request for recent open-access papers
- the most cited papers on a topic
- recent bioRxiv/medRxiv preprints
- BibTeX for a DOI and an arXiv id
- a free PDF for a DOI

Five must not, though they are close:

- checking a `.bib` file (that is `cite-check`)
- science news
- explaining attention
- writing a BibTeX parser
- formatting a reference in APA

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill scholar-search
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill scholar-search
```

Results from 2026-10-30 are in `results/2026-10-30/`.

| Description | Claude Sonnet | Codex |
|---|---|---|
| First ("Searches scholarly literature… Use when…") | 10/12 | 11/12 |
| Final ("Use whenever an answer should name real scholarly papers instead of recalling them from memory…") | **12/12** | **11/12** |

With the first description, Claude missed two prompts:
- the most cited GNN papers, which it answered from memory with unchecked citation counts;
- the free PDF, where it called Unpaywall directly.

Leading the description with "instead of recalling them from memory" fixed both.

Codex's one miss is the same on both descriptions: `n1-verify-refs`. Only scholar-search is installed in the eval sandbox, so Codex opened its SKILL.md, read "use cite-check", and looked for `cite-check`. It did not run a search. In the repo, both skills are installed side by side.
