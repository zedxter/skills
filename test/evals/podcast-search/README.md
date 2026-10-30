# podcast-search evals

## Triggers

`prompts.json` has 12 prompts: 7 must load the skill (one in Russian) and 5 are near misses that must not.

Must load:
- `t1-topic`: Find a podcast about the history of computing.
- `t2-guest-mp3`: Which episode of the Lex Fridman Podcast has Andrej Karpathy? Give me the mp3 link.
- `t3-ru`: Найди подкаст про личные финансы на русском и покажи последние выпуски.
- `t4-latest`: What are the latest five episodes of The Changelog?
- `t5-to-transcribe`: Find the episode where Kelsey Hightower talks about serverless. I want to transcribe it.
- `t6-long`: Show me episodes of Dan Carlin's Hardcore History longer than four hours.
- `t7-transcript`: Is there a transcript available for the latest episode of Software Engineering Daily?

Must not:
- `n1-transcribe-file`: Transcribe episode.mp3 in this folder.
- `n2-start-podcast`: How do I start my own podcast and get it listed on Apple Podcasts?
- `n3-youtube`: Find YouTube videos about the history of computing.
- `n4-audiobooks`: Recommend a few good audiobooks about Roman history.
- `n5-code`: Write an RSS feed parser in Python.

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill podcast-search --all-skills
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill podcast-search --all-skills
```

Leave out `--all-skills` to install only this skill. Add `--skills-dir DIR` to test another copy of the skills, such as an older commit.

## Results from 2026-10-31

In `results/2026-10-31/`, one run per prompt. The new description adds a "Not for …" sentence naming the neighbouring skills, and Russian trigger phrases.

| Run | Claude Sonnet | Codex |
|---|---|---|
| Only this skill, old description | 12/12 | 12/12 |
| All 20 skills, old description | 12/12 | 11/12 |
| All 20 skills, new description | 12/12 | 11/12 |

With only one skill installed, a near miss has nowhere better to go, so the whole-collection runs are the fairer test of the description. There is one run per prompt, so a difference of one or two prompts between runs is within run-to-run noise: across the ten skills tested on this date, the new descriptions scored 110/120 on both agents with the whole collection installed, against 114 (Claude) and 113 (Codex) for the old ones. The new wording follows CONTRIBUTING.md; it did not measurably change routing once the neighbouring skills were installed.

### Misses

- all-before, Codex: `n2-start-podcast` loaded it
- all-after, Codex: `n2-start-podcast` loaded it
