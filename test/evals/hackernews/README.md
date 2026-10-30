# hackernews evals

## Triggers

`prompts.json` has 12 prompts.

Seven must load the skill:
- HN's opinion of a tool
- the front page
- summarising a thread by URL
- a Russian request for the most discussed Rust threads
- recent Show HN posts
- an HN user
- whether a paper was discussed on HN

Five must not:
- Reddit
- applying to Y Combinator
- building an HN clone
- general news
- reading an article

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill hackernews
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill hackernews
```

Results from 2026-10-30 are in `results/2026-10-30/`:

| Agent | Correct |
|---|---|
| Claude Sonnet | 12/12 |
| Codex | 12/12 |

The description did not change after the reviews, so these results stand.
