# youtube-search evals

## Triggers

`prompts.json` has 12 prompts: 7 must load the skill (one in Russian) and 5 are near misses that must not.

Must load:
- `t1-most-watched`: Find the most-watched YouTube talks about Rust async from the last year.
- `t2-recent`: Any new YouTube videos this week reviewing the Framework Laptop 16?
- `t3-ru`: Найди на YouTube лекции про теорию категорий для программистов, подлиннее.
- `t4-channel`: What has the YouTube channel @3blue1brown posted recently?
- `t5-to-summarise`: I want a good YouTube tutorial on Kubernetes networking to summarise. Find one.
- `t6-interviews`: Find interviews with Andrej Karpathy on YouTube, newest first.
- `t7-short`: Search YouTube for sourdough starter videos under 10 minutes.

Must not:
- `n1-summarise-link`: Summarise this video: https://www.youtube.com/watch?v=jNQXAC9IVRw
- `n2-mp3`: Convert https://youtu.be/dQw4w9WgXcQ to mp3.
- `n3-podcast`: Find a podcast episode about the history of Unix.
- `n4-embed`: How do I embed a YouTube video in my React app?
- `n5-web-news`: Search the web for news about YouTube's new monetisation policy.

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill youtube-search --all-skills
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill youtube-search --all-skills
```

Leave out `--all-skills` to install only this skill. Add `--skills-dir DIR` to test another copy of the skills, such as an older commit.

## Results from 2026-10-31

In `results/2026-10-31/`, one run per prompt per row. The new description adds a "Not for …" sentence naming the neighbouring skills and Russian trigger phrases.

| Run | Claude Sonnet | Codex |
|---|---|---|
| Only this skill, old description | 11/12 | 10/12 |
| All 20 skills, old description, run 1 | 12/12 | 12/12 |
| All 20 skills, old description, run 2 | 12/12 | 12/12 |
| All 20 skills, new description, run 1 | 12/12 | 12/12 |
| All 20 skills, new description, run 2 | 12/12 | 12/12 |

With only one skill installed, a near miss has nowhere better to go, so the whole-collection runs are the fairer test of the description. The two runs of the same description show how much a score moves by chance. Across the ten skills tested on this date, with the whole collection installed, the old descriptions scored 114 and 111 of 120 on Claude Sonnet and 113 and 110 on Codex; the new ones scored 110 and 113 on Claude Sonnet and 113 and 111 on Codex. Two runs of the same description differ by as much as old and new do, so these runs don't show the new wording changing routing either way.

### Misses

- isolated, Claude Sonnet: `n3-podcast` loaded it
- isolated, Codex: `n1-summarise-link` loaded it
- isolated, Codex: `n3-podcast` loaded it
