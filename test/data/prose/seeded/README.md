# Seeded-defect texts for the review panel

Each text has defects planted on purpose, and a ledger (`<name>.ledger.md`) that the fact checker receives. The panel from `references/review-orchestration.md` should report every planted defect from the lens that owns it. All names, numbers, hosts and tokens are made up; the GitHub token is fake.

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

### Result

See `test/evals/human-writing/README.md`. The whole set of six texts and 40 defects was caught 40/40 by the panel and by the single-agent fallback on 2026-10-30.

## The other texts

`cases.json` lists every text: its genre, panel lenses, the inputs each lens gets (audience, platform, field, author, goal, sources, run policy, voice profile) and the planted defects, each with a unique quote and the lenses that own it. `test/test_human_writing.py` checks that file stays consistent; `test/evals/human-writing/run_panel.py` runs and scores the panel.

| Text | Genre | Planted defects |
|---|---|---|
| `article-02-postgres-index` | dev.to article | publishes the draft, six tags, cross-post without canonical URL, a number that contradicts the ledger, wrong arithmetic, an internal hostname, a slop paragraph, an overclaim |
| `article-03-monolith` | engineering-blog opinion | a strawman, a number that contradicts the ledger, an unsourced statistic, an unannounced project, an overgeneralisation, a recap ending |
| `article-04-git-undo` | how-to site | a question opener, an unsourced statistic, a destructive command without warning, a multi-action step with force-push, a false price, minimizers, a cheerleading sign-off |
| `post-01-team-growth` | LinkedIn post | a question opener, a wrong headcount, a faux-insight setup, a confidential contract value, a link in the body, five hashtags, a second idea |
| `post-02-oncall` | LinkedIn post with a voice profile | "half" for a one-third cut, a colleague's health detail, an off-voice paragraph, a misattributed quote, comment-gate bait |
