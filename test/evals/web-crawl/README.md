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

In `results/2026-10-31/`, one run per prompt per row. The new description adds a "Not for …" sentence naming the neighbouring skills and Russian trigger phrases.

| Run | Claude Sonnet | Codex |
|---|---|---|
| Only this skill, old description | 12/12 | 11/12 |
| All 20 skills, old description, run 1 | 12/12 | 12/12 |
| All 20 skills, old description, run 2 | 12/12 | 11/12 |
| All 20 skills, new description, run 1 | 12/12 | 11/12 |
| All 20 skills, new description, run 2 | 12/12 | 11/12 |

With only one skill installed, a near miss has nowhere better to go, so the whole-collection runs are the fairer test of the description. The two runs of the same description show how much a score moves by chance. Across the ten skills tested on this date, with the whole collection installed, the old descriptions scored 114 and 111 of 120 on Claude Sonnet and 113 and 110 on Codex; the new ones scored 110 and 113 on Claude Sonnet and 113 and 111 on Codex. Two runs of the same description differ by as much as old and new do, so these runs don't show the new wording changing routing either way.

On the scrape near miss (n2), Codex read both web-crawl and web-scrape before choosing web-scrape in the two new-description runs; in the single-skill run and in old-description run 2 it read only web-crawl.

### Misses

- isolated, Codex: `n2-scrape` loaded it
- before-2, Codex: `n2-scrape` loaded it
- after-1, Codex: `n2-scrape` loaded it
- after-2, Codex: `n2-scrape` loaded it
