# web-browse evals

## Triggers

`prompts.json` has 12 prompts: 7 must load the skill (one in Russian) and 5 are near misses that must not.

Must load:
- `t1-click`: Open https://news.ycombinator.com, click 'More' twice and tell me the titles on the third page.
- `t2-login`: Log in to my staging admin at https://staging.example.test with the test account and check whether the user list shows emails.
- `t3-ru`: Открой https://httpbin.org/forms/post, заполни форму тестовыми данными, отправь и скажи, что покажет страница после отправки.
- `t4-spa`: This single-page app only shows data after you pick a date in its calendar: https://example.com/app. Pick tomorrow and tell me what appears.
- `t5-site-search`: Search for 'standing desk' on https://www.ikea.com/de/en/ and open the first result. What's the price?
- `t6-screenshot`: Take a screenshot of https://tailwindcss.com after clicking the Docs link.
- `t7-wizard`: Click through the setup wizard at http://localhost:3000 and tell me at which step it breaks.

Must not:
- `n1-read`: Read this article https://simonwillison.net/2024/Dec/31/llms-in-2024/ and summarise it.
- `n2-code`: Write Playwright tests for my login page.
- `n3-search`: Search the web for the best browser automation tools.
- `n4-chrome`: Why is my Chrome using so much memory?
- `n5-scrape`: Get all books with prices from https://books.toscrape.com into a CSV.

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill web-browse --all-skills
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill web-browse --all-skills
```

Leave out `--all-skills` to install only this skill. Add `--skills-dir DIR` to test another copy of the skills, such as an older commit.

## Results from 2026-10-31

In `results/2026-10-31/`, one run per prompt. The new description adds a "Not for …" sentence naming the neighbouring skills, and Russian trigger phrases.

| Run | Claude Sonnet | Codex |
|---|---|---|
| Only this skill, old description | 12/12 | 10/12 |
| All 20 skills, old description | 12/12 | 12/12 |
| All 20 skills, new description | 11/12 | 12/12 |

With only one skill installed, a near miss has nowhere better to go, so the whole-collection runs are the fairer test of the description. There is one run per prompt, so a difference of one or two prompts between runs is within run-to-run noise: across the ten skills tested on this date, the new descriptions scored 110/120 on both agents with the whole collection installed, against 114 (Claude) and 113 (Codex) for the old ones. The new wording follows CONTRIBUTING.md; it did not measurably change routing once the neighbouring skills were installed.

### Misses

- isolated, Codex: `n4-chrome` loaded it
- isolated, Codex: `n5-scrape` loaded it
- all-after, Claude Sonnet: `t2-login` did not load it (no skill)
