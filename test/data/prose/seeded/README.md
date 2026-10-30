# Seeded-defect texts for the review panel

Each text has defects planted on purpose, and a ledger that the fact checker receives. Run the panel from `references/review-orchestration.md` on a text and check that every planted defect is reported by the lens that owns it. The token below is fake.

## docs-01-lint-howto.md

A how-to for `lint_prose.py` itself, so the source checker and newcomer tester can work against real code. The ledger is part of the trap: row 2 marks a wrong value as `verified`.

| # | Planted defect | Line | Owning lens |
|---|---|---|---|
| D1 | `--strict` flag that doesn't exist | 10 | source-checker, newcomer-tester |
| D2 | Default threshold given as 80 (code: 75), and the ledger wrongly says verified | 13 | fact-checker, source-checker |
| D3 | "catches 97% of AI-generated text": no source, and the tool doesn't detect authorship | 15 | fact-checker, hostile-expert |
| D4 | `--genre linkedin` (valid: article, post, docs) | 22 | source-checker, newcomer-tester |
| D5 | A GitHub token pasted into the CI example | 32 | publication-safety |
| D6 | "Why it matters" section in stock AI phrasing | 37–39 | slop-hunter, cold-reader |
| D7 | No prerequisites: uv, where the script lives, what the draft must be | 7 | newcomer-tester, cold-reader |

### Result, 2026-10-30

Full docs panel (7 reviewers, one round, separate sub-agents on a mid-size model, each given only its brief, the text and its lens inputs):

- **Planted defects:** all 7 were caught by an owning lens, and every reviewer returned valid JSON in the schema.
- **Extra defect:** the source checker found one we didn't plant. `docs/*.md` expands to several files, but the script takes one, so the CI step exits 2.
- **Lens overlap:** the cold reader, hostile expert and slop hunter also reported the token as a blocker, outside their lens. The merge step dedupes these; agreement across lenses raises priority.

Still to do for the acceptance criteria in issue #25: five more seeded texts (articles and posts) and a single-agent fallback run for comparison.
