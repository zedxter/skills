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

In `results/2026-10-31/`, one run per prompt per row. The new description adds a "Not for …" sentence naming the neighbouring skills and Russian trigger phrases, and shortens the feature list to stay under 1,024 characters.

| Run | Claude Sonnet | Codex |
|---|---|---|
| Only this skill, old description | 12/12 | 10/12 |
| All 20 skills, old description, run 1 | 12/12 | 11/12 |
| All 20 skills, old description, run 2 | 11/12 | 11/12 |
| All 20 skills, new description, run 1 | 12/12 | 11/12 |
| All 20 skills, new description, run 2 | 12/12 | 11/12 |

With only one skill installed, a near miss has nowhere better to go, so the whole-collection runs are the fairer test of the description. The two runs of the same description show how much a score moves by chance. Across the ten skills tested on this date, with the whole collection installed, the old descriptions scored 114 and 111 of 120 on Claude Sonnet and 113 and 110 on Codex; the new ones scored 110 and 113 on Claude Sonnet and 113 and 111 on Codex. Two runs of the same description differ by as much as old and new do, so these runs don't show the new wording changing routing either way.

Codex reads this skill for the write-a-Scrapy-spider near miss (n2) in every run, before writing the code itself.

### Misses

- isolated, Codex: `n2-code` loaded it
- isolated, Codex: `n3-crawl` loaded it
- before-1, Codex: `n2-code` loaded it
- before-2, Claude Sonnet: `t1-products` did not load it (no skill)
- before-2, Codex: `n2-code` loaded it
- after-1, Codex: `n2-code` loaded it
- after-2, Codex: `n2-code` loaded it
