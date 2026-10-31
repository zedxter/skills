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

In `results/2026-10-31/`, one run per prompt per row. The new description adds a "Not for …" sentence naming the neighbouring skills and Russian trigger phrases.

| Run | Claude Sonnet | Codex |
|---|---|---|
| Only this skill, old description | 7/12 | 8/12 |
| All 20 skills, old description, run 1 | 8/12 | 8/12 |
| All 20 skills, old description, run 2 | 6/12 | 8/12 |
| All 20 skills, new description, run 1 | 6/12 | 8/12 |
| All 20 skills, new description, run 2 | 7/12 | 8/12 |

With only one skill installed, a near miss has nowhere better to go, so the whole-collection runs are the fairer test of the description. The two runs of the same description show how much a score moves by chance. Across the ten skills tested on this date, with the whole collection installed, the old descriptions scored 114 and 111 of 120 on Claude Sonnet and 113 and 110 on Codex; the new ones scored 110 and 113 on Claude Sonnet and 113 and 111 on Codex. Two runs of the same description differ by as much as old and new do, so these runs don't show the new wording changing routing either way.

Most misses on the plain look-up prompts (t1, t2, t5, t6, t7) are the agent using its own built-in web search, which Claude Code and Codex both have and prefer for simple look-ups. The description sends the agent here when that tool is missing or thin, so these prompts mostly measure that preference. On the Russian prompt (t3), Codex with the old description read this SKILL.md and then searched with its built-in tool anyway; with the new one it went straight to the built-in tool, so the two runs that count as misses changed nothing the user saw. With only this skill installed, Codex also used it for the YouTube and Hacker News near misses; with the whole collection installed those went to youtube-search and hackernews.

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
- before-1, Claude Sonnet: `t1-version` did not load it (no skill)
- before-1, Claude Sonnet: `t5-factcheck` did not load it (no skill)
- before-1, Claude Sonnet: `t6-news` did not load it (no skill)
- before-1, Claude Sonnet: `t7-docs` did not load it (no skill)
- before-1, Codex: `t1-version` did not load it (no skill)
- before-1, Codex: `t5-factcheck` did not load it (human-writing)
- before-1, Codex: `t6-news` did not load it (no skill)
- before-1, Codex: `t7-docs` did not load it (no skill)
- before-2, Claude Sonnet: `t1-version` did not load it (no skill)
- before-2, Claude Sonnet: `t2-compare` did not load it (no skill)
- before-2, Claude Sonnet: `t3-ru` did not load it (no skill)
- before-2, Claude Sonnet: `t5-factcheck` did not load it (no skill)
- before-2, Claude Sonnet: `t6-news` did not load it (no skill)
- before-2, Claude Sonnet: `t7-docs` did not load it (no skill)
- before-2, Codex: `t1-version` did not load it (no skill)
- before-2, Codex: `t5-factcheck` did not load it (no skill)
- before-2, Codex: `t6-news` did not load it (no skill)
- before-2, Codex: `t7-docs` did not load it (no skill)
- after-1, Claude Sonnet: `t1-version` did not load it (no skill)
- after-1, Claude Sonnet: `t2-compare` did not load it (no skill)
- after-1, Claude Sonnet: `t3-ru` did not load it (no skill)
- after-1, Claude Sonnet: `t5-factcheck` did not load it (no skill)
- after-1, Claude Sonnet: `t6-news` did not load it (no skill)
- after-1, Claude Sonnet: `t7-docs` did not load it (no skill)
- after-1, Codex: `t1-version` did not load it (no skill)
- after-1, Codex: `t3-ru` did not load it (no skill)
- after-1, Codex: `t5-factcheck` did not load it (human-writing)
- after-1, Codex: `t7-docs` did not load it (no skill)
- after-2, Claude Sonnet: `t1-version` did not load it (no skill)
- after-2, Claude Sonnet: `t2-compare` did not load it (no skill)
- after-2, Claude Sonnet: `t5-factcheck` did not load it (no skill)
- after-2, Claude Sonnet: `t6-news` did not load it (no skill)
- after-2, Claude Sonnet: `t7-docs` did not load it (no skill)
- after-2, Codex: `t1-version` did not load it (no skill)
- after-2, Codex: `t3-ru` did not load it (no skill)
- after-2, Codex: `t5-factcheck` did not load it (no skill)
- after-2, Codex: `t7-docs` did not load it (no skill)
