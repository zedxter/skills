# wiki-lookup evals

## Triggers

`prompts.json` has 12 prompts. Seven must load the skill:

- what Wikipedia says, with a link to cite
- a Russian request for a reference summary from Russian Wikipedia
- one section of a German article given by URL
- a Wikidata population figure
- an article's title in other language editions
- an article whose fetch failed with 403
- a disambiguation case

Five must not:

- today's news
- scholarly papers
- writing a Wikipedia editing bot
- a programming question
- fiction about the planet Mercury

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill wiki-lookup
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill wiki-lookup
```

Results from 2026-10-30 are in `results/2026-10-30/`.

| Description | Claude Sonnet | Codex |
|---|---|---|
| First | 12/12 | 11/12 |
| Final (adds "prefer it to web-extract and web-search whenever the source is Wikipedia or Wikidata" and "Not for general web or news search") | **12/12** | **11/12** |

Codex's miss is the same with both descriptions: `n3-edit`, "write a script that logs into Wikipedia with my bot account and fixes typos". Codex opened SKILL.md "to check the Wikipedia API guidance", then wrote its own editing script and never ran the skill. Reading the skill for the API etiquette is arguably reasonable; the description already says "Not for … editing Wikipedia".
