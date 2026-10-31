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

In `results/2026-10-31/`, one run per prompt per row. The new description adds a "Not for …" sentence naming the neighbouring skills and Russian trigger phrases, and shortens the feature list to stay under 1,024 characters.

| Run | Claude Sonnet | Codex |
|---|---|---|
| Only this skill, old description | 12/12 | 10/12 |
| All 20 skills, old description, run 1 | 12/12 | 12/12 |
| All 20 skills, old description, run 2 | 12/12 | 11/12 |
| All 20 skills, new description, run 1 | 11/12 | 12/12 |
| All 20 skills, new description, run 2 | 12/12 | 11/12 |

With only one skill installed, a near miss has nowhere better to go, so the whole-collection runs are the fairer test of the description. The two runs of the same description show how much a score moves by chance. Across the ten skills tested on this date, with the whole collection installed, the old descriptions scored 114 and 111 of 120 on Claude Sonnet and 113 and 110 on Codex; the new ones scored 110 and 113 on Claude Sonnet and 113 and 111 on Codex. Two runs of the same description differ by as much as old and new do, so these runs don't show the new wording changing routing either way.

### Misses

- isolated, Codex: `n4-chrome` loaded it
- isolated, Codex: `n5-scrape` loaded it
- before-2, Codex: `n4-chrome` loaded it
- after-1, Claude Sonnet: `t2-login` did not load it (no skill)
- after-2, Codex: `n4-chrome` loaded it
