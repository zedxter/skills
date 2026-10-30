# web-search evals

## Triggers

`prompts.json` has 12 prompts: 7 must load the skill (one in Russian) and 5 are near misses that must not.

Must load:
- `t1-version`: What's the current stable version of PostgreSQL and when was it released? Look it up.
- `t2-compare`: Find three recent articles comparing Bun and Deno performance.
- `t3-ru`: Найди в интернете, что писали про AI Act ЕС за последнюю неделю.
- `t4-fallback`: My built-in web search is rate-limited. Search for the official docs page about Python's asyncio TaskGroup.
- `t5-factcheck`: Check this claim against a few sources: 'Honey never spoils.'
- `t6-news`: Search the news for outages reported at Cloudflare this month.
- `t7-docs`: Look up who maintains the uv package manager and give me a link to its documentation.

Must not:
- `n1-read-url`: Summarise this article: https://simonwillison.net/2024/Dec/31/llms-in-2024/
- `n2-youtube`: Find YouTube videos about Rust async.
- `n3-grep`: Search this repository for TODO comments.
- `n4-hn`: What does Hacker News think about Zig?
- `n5-explain`: Explain what a B-tree is, with a small example.

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill web-search --all-skills
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill web-search --all-skills
```

Leave out `--all-skills` to install only this skill. Add `--skills-dir DIR` to test another copy of the skills, such as an older commit.

## Results from 2026-10-31

In `results/2026-10-31/`, one run per prompt. The new description adds a "Not for …" sentence naming the neighbouring skills, and Russian trigger phrases.

| Run | Claude Sonnet | Codex |
|---|---|---|
| Only this skill, old description | 7/12 | 8/12 |
| All 20 skills, old description | 8/12 | 8/12 |
| All 20 skills, new description | 6/12 | 8/12 |

With only one skill installed, a near miss has nowhere better to go, so the whole-collection runs are the fairer test of the description. There is one run per prompt, so a difference of one or two prompts between runs is within run-to-run noise: across the ten skills tested on this date, the new descriptions scored 110/120 on both agents with the whole collection installed, against 114 (Claude) and 113 (Codex) for the old ones. The new wording follows CONTRIBUTING.md; it did not measurably change routing once the neighbouring skills were installed.

Most misses on the plain look-up prompts (t1, t2, t5, t6, t7) are the agent using its own built-in web search instead, which both Claude Code and Codex have. The description sends the agent here when that tool is missing or thin, so this is the intended split, not a routing bug. The Russian prompt (t3) loaded the skill in five runs out of six. With only this skill installed, Codex also used it for the YouTube and Hacker News near misses; with the whole collection installed those went to youtube-search and hackernews.

### Misses

- isolated, Claude Sonnet: `t1-version` did not load it (no skill)
- isolated, Claude Sonnet: `t2-compare` did not load it (no skill)
- isolated, Claude Sonnet: `t5-factcheck` did not load it (no skill)
- isolated, Claude Sonnet: `t6-news` did not load it (no skill)
- isolated, Claude Sonnet: `t7-docs` did not load it (no skill)
- isolated, Codex: `t1-version` did not load it (no skill)
- isolated, Codex: `t7-docs` did not load it (no skill)
- isolated, Codex: `n2-youtube` loaded it
- isolated, Codex: `n4-hn` loaded it
- all-before, Claude Sonnet: `t1-version` did not load it (no skill)
- all-before, Claude Sonnet: `t5-factcheck` did not load it (no skill)
- all-before, Claude Sonnet: `t6-news` did not load it (no skill)
- all-before, Claude Sonnet: `t7-docs` did not load it (no skill)
- all-before, Codex: `t1-version` did not load it (no skill)
- all-before, Codex: `t5-factcheck` did not load it (human-writing)
- all-before, Codex: `t6-news` did not load it (no skill)
- all-before, Codex: `t7-docs` did not load it (no skill)
- all-after, Claude Sonnet: `t1-version` did not load it (no skill)
- all-after, Claude Sonnet: `t2-compare` did not load it (no skill)
- all-after, Claude Sonnet: `t3-ru` did not load it (no skill)
- all-after, Claude Sonnet: `t5-factcheck` did not load it (no skill)
- all-after, Claude Sonnet: `t6-news` did not load it (no skill)
- all-after, Claude Sonnet: `t7-docs` did not load it (no skill)
- all-after, Codex: `t1-version` did not load it (no skill)
- all-after, Codex: `t5-factcheck` did not load it (no skill)
- all-after, Codex: `t6-news` did not load it (no skill)
- all-after, Codex: `t7-docs` did not load it (no skill)
