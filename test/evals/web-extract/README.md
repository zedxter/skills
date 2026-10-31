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

In `results/2026-10-31/`, one run per prompt per row. The new description adds a "Not for …" sentence naming the neighbouring skills and Russian trigger phrases, and shortens the feature list to stay under 1,024 characters.

| Run | Claude Sonnet | Codex |
|---|---|---|
| Only this skill, old description | 11/12 | 11/12 |
| All 20 skills, old description, run 1 | 10/12 | 11/12 |
| All 20 skills, old description, run 2 | 10/12 | 10/12 |
| All 20 skills, new description, run 1 | 9/12 | 11/12 |
| All 20 skills, new description, run 2 | 10/12 | 11/12 |

With only one skill installed, a near miss has nowhere better to go, so the whole-collection runs are the fairer test of the description. The two runs of the same description show how much a score moves by chance. Across the ten skills tested on this date, with the whole collection installed, the old descriptions scored 114 and 111 of 120 on Claude Sonnet and 113 and 110 on Codex; the new ones scored 110 and 113 on Claude Sonnet and 113 and 111 on Codex. Two runs of the same description differ by as much as old and new do, so these runs don't show the new wording changing routing either way.

With the whole collection installed, the dead-link prompt (t4) went to wayback in every run, and the has-it-changed prompt (t6) went there in every Claude run, while Codex usually read both skills. wayback's description owns dead links and past versions, and the new web-extract description now hands past versions to it, so t4 and t6 test an overlap between the two skills more than web-extract's routing. The Wikipedia table (t5) goes to wiki-lookup first; the run counts as a pass when the agent then reads this skill, as wiki-lookup's own instructions tell it to for tables.

### Misses

- isolated, Claude Sonnet: `n1-crawl` loaded it
- isolated, Codex: `n1-crawl` loaded it
- before-1, Claude Sonnet: `t4-dead-link` did not load it (wayback)
- before-1, Claude Sonnet: `t6-changed` did not load it (wayback)
- before-1, Codex: `t4-dead-link` did not load it (wayback)
- before-2, Claude Sonnet: `t4-dead-link` did not load it (wayback)
- before-2, Claude Sonnet: `t6-changed` did not load it (wayback)
- before-2, Codex: `t4-dead-link` did not load it (wayback)
- before-2, Codex: `t6-changed` did not load it (wayback)
- after-1, Claude Sonnet: `t4-dead-link` did not load it (wayback)
- after-1, Claude Sonnet: `t5-table` did not load it (wiki-lookup)
- after-1, Claude Sonnet: `t6-changed` did not load it (wayback)
- after-1, Codex: `t4-dead-link` did not load it (wayback)
- after-2, Claude Sonnet: `t4-dead-link` did not load it (wayback)
- after-2, Claude Sonnet: `t6-changed` did not load it (wayback)
- after-2, Codex: `t4-dead-link` did not load it (wayback)
