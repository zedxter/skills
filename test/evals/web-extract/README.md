# web-extract evals

## Triggers

`prompts.json` has 12 prompts: 7 must load the skill (one in Russian) and 5 are near misses that must not.

Must load:
- `t1-summarise`: Summarise this article for me: https://simonwillison.net/2024/Dec/31/llms-in-2024/
- `t2-403`: My fetch tool got a 403 on https://www.npmjs.com/package/zod. Can you read the page anyway?
- `t3-ru`: Вытащи текст из этой статьи и перескажи коротко: https://habr.com/ru/articles/800000/
- `t4-dead-link`: This link is dead now: http://example.org/old/docs/page.html. Can you get me what it used to say?
- `t5-table`: Extract the table of versions and release dates from https://en.wikipedia.org/wiki/History_of_Python
- `t6-changed`: Has https://openai.com/policies/usage-policies changed since I last checked it?
- `t7-pdf`: Read this PDF and give me the key points: https://arxiv.org/pdf/1706.03762

Must not:
- `n1-crawl`: Crawl the whole docs site at https://docs.astral.sh/uv/ and make me notes on every page.
- `n2-search`: Search the web for the best Python HTTP client libraries.
- `n3-code`: Write a Python script that downloads a web page with requests and parses it with BeautifulSoup.
- `n4-login`: Log in to my account on example.com and check my order status.
- `n5-explain`: What does HTTP status 403 mean?

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill web-extract --all-skills
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill web-extract --all-skills
```

Leave out `--all-skills` to install only this skill. Add `--skills-dir DIR` to test another copy of the skills, such as an older commit.

## Results from 2026-10-31

In `results/2026-10-31/`, one run per prompt. The new description adds a "Not for …" sentence naming the neighbouring skills, and Russian trigger phrases.

| Run | Claude Sonnet | Codex |
|---|---|---|
| Only this skill, old description | 11/12 | 11/12 |
| All 20 skills, old description | 10/12 | 11/12 |
| All 20 skills, new description | 9/12 | 10/12 |

With only one skill installed, a near miss has nowhere better to go, so the whole-collection runs are the fairer test of the description. There is one run per prompt, so a difference of one or two prompts between runs is within run-to-run noise: across the ten skills tested on this date, the new descriptions scored 110/120 on both agents with the whole collection installed, against 114 (Claude) and 113 (Codex) for the old ones. The new wording follows CONTRIBUTING.md; it did not measurably change routing once the neighbouring skills were installed.

With the whole collection installed, the dead-link prompt (t4) went to wayback, the Wikipedia table (t5) to wiki-lookup and the has-it-changed prompt (t6) to wayback. wayback and wiki-lookup own those tasks too, so these misses are overlaps between neighbouring skills rather than wrong routing.

### Misses

- isolated, Claude Sonnet: `n1-crawl` loaded it
- isolated, Codex: `n1-crawl` loaded it
- all-before, Claude Sonnet: `t4-dead-link` did not load it (wayback)
- all-before, Claude Sonnet: `t6-changed` did not load it (wayback)
- all-before, Codex: `t4-dead-link` did not load it (wayback)
- all-after, Claude Sonnet: `t4-dead-link` did not load it (wayback)
- all-after, Claude Sonnet: `t5-table` did not load it (wiki-lookup)
- all-after, Claude Sonnet: `t6-changed` did not load it (wayback)
- all-after, Codex: `t4-dead-link` did not load it (wayback)
- all-after, Codex: `t6-changed` did not load it (wayback)
