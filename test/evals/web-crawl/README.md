# web-crawl evals

## Triggers

`prompts.json` has 12 prompts: 7 must load the skill (one in Russian) and 5 are near misses that must not.

Must load:
- `t1-docs-offline`: Download the whole uv documentation site (docs.astral.sh/uv) into one Markdown file I can read offline.
- `t2-map`: What pages does https://fastapi.tiangolo.com have? Map the site.
- `t3-ru`: Собери все статьи из раздела https://jvns.ca/categories/ в один файл и сделай по ним конспект.
- `t4-depth`: Crawl https://htmx.org/docs/ two levels deep and summarise the main concepts.
- `t5-section`: Read every page of the Pydantic migration guide under https://docs.pydantic.dev/latest/migration/ and list the breaking changes.
- `t6-kb`: Turn the whole help centre at https://help.example.com into searchable notes.
- `t7-blog`: Go through all the posts on https://danluu.com and tell me which topics come up most.

Must not:
- `n1-one-page`: Summarise this one page: https://docs.astral.sh/uv/getting-started/installation/
- `n2-scrape`: Get all product names and prices from https://books.toscrape.com into a CSV.
- `n3-code`: Write a web crawler in Go with a worker pool.
- `n4-search`: Search the web for Python packaging tutorials.
- `n5-wayback`: What did https://example.com look like in 2015?

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill web-crawl --all-skills
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill web-crawl --all-skills
```

Leave out `--all-skills` to install only this skill. Add `--skills-dir DIR` to test another copy of the skills, such as an older commit.

## Results from 2026-10-31

In `results/2026-10-31/`, one run per prompt. The new description adds a "Not for …" sentence naming the neighbouring skills, and Russian trigger phrases.

| Run | Claude Sonnet | Codex |
|---|---|---|
| Only this skill, old description | 12/12 | 11/12 |
| All 20 skills, old description | 12/12 | 12/12 |
| All 20 skills, new description | 12/12 | 11/12 |

With only one skill installed, a near miss has nowhere better to go, so the whole-collection runs are the fairer test of the description. There is one run per prompt, so a difference of one or two prompts between runs is within run-to-run noise: across the ten skills tested on this date, the new descriptions scored 110/120 on both agents with the whole collection installed, against 114 (Claude) and 113 (Codex) for the old ones. The new wording follows CONTRIBUTING.md; it did not measurably change routing once the neighbouring skills were installed.

### Misses

- isolated, Codex: `n2-scrape` loaded it
- all-after, Codex: `n2-scrape` loaded it
