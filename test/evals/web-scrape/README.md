# web-scrape evals

## Triggers

`prompts.json` has 12 prompts: 7 must load the skill (one in Russian) and 5 are near misses that must not.

Must load:
- `t1-products`: Get all books with their prices from https://books.toscrape.com into a CSV.
- `t2-table`: Pull the table on https://en.wikipedia.org/wiki/List_of_largest_cities as CSV.
- `t3-ru`: Собери со страницы https://remoteok.com все вакансии: название, компания, зарплата — в таблицу.
- `t4-pagination`: Collect the title, link and date of every post on https://blog.python.org, following the older-posts pages.
- `t5-detail`: Scrape the quotes on https://quotes.toscrape.com and open each author's page to add their birth date.
- `t6-json`: Turn the speakers list on https://us.pycon.org/2025/speakers/ into JSON with name and talk title.
- `t7-jsonld`: Extract all the events from the schema.org data on https://www.meetup.com/find/?keywords=python as JSONL.

Must not:
- `n1-article`: Summarise the article at https://simonwillison.net/2024/Dec/31/llms-in-2024/
- `n2-code`: Write a Scrapy spider for an e-commerce site.
- `n3-crawl`: Download the full documentation site at https://docs.astral.sh/uv/ for offline reading.
- `n4-local-csv`: Parse sales.csv in this folder and give me totals per month.
- `n5-legal`: Is web scraping legal in the EU?

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill web-scrape --all-skills
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill web-scrape --all-skills
```

Leave out `--all-skills` to install only this skill. Add `--skills-dir DIR` to test another copy of the skills, such as an older commit.

## Results from 2026-10-31

In `results/2026-10-31/`, one run per prompt. The new description adds a "Not for …" sentence naming the neighbouring skills, and Russian trigger phrases.

| Run | Claude Sonnet | Codex |
|---|---|---|
| Only this skill, old description | 12/12 | 10/12 |
| All 20 skills, old description | 12/12 | 11/12 |
| All 20 skills, new description | 12/12 | 11/12 |

With only one skill installed, a near miss has nowhere better to go, so the whole-collection runs are the fairer test of the description. There is one run per prompt, so a difference of one or two prompts between runs is within run-to-run noise: across the ten skills tested on this date, the new descriptions scored 110/120 on both agents with the whole collection installed, against 114 (Claude) and 113 (Codex) for the old ones. The new wording follows CONTRIBUTING.md; it did not measurably change routing once the neighbouring skills were installed.

### Misses

- isolated, Codex: `n2-code` loaded it
- isolated, Codex: `n3-crawl` loaded it
- all-before, Codex: `n2-code` loaded it
- all-after, Codex: `n2-code` loaded it
