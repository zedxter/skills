# youtube-audio evals

## Triggers

`prompts.json` has 12 prompts: 7 must load the skill (one in Russian) and 5 are near misses that must not.

Must load:
- `t1-mp3`: Convert https://youtu.be/dQw4w9WgXcQ to mp3.
- `t2-offline`: Download just the audio of this lecture so I can listen offline: https://www.youtube.com/watch?v=zjkBMFhNj_g
- `t3-ru`: Скачай звук из этого видео в m4a: https://www.youtube.com/watch?v=aircAruvnKk
- `t4-section`: Rip 12:30 to 45:00 of https://www.youtube.com/watch?v=8pTEmbeENF4 as an mp3.
- `t5-chapters`: This mix has chapters. Split its audio into separate tracks: https://www.youtube.com/watch?v=jfKfPfyJRdk
- `t6-info`: What audio formats and chapters does https://youtu.be/kCc8FmEb1nY have? Don't download anything.
- `t7-playlist`: Save the whole playlist https://www.youtube.com/playlist?list=PLZHQObOWTQDNU6R1_67000Dx_ZCJB-3pi as audio files.

Must not:
- `n1-summarise`: Summarise https://www.youtube.com/watch?v=jNQXAC9IVRw
- `n2-search`: Find YouTube videos with lo-fi music for studying.
- `n3-local-convert`: Convert song.wav in this folder to mp3.
- `n4-podcast-mp3`: Give me the mp3 link of the latest Lex Fridman podcast episode.
- `n5-audacity`: How do I normalise audio levels in Audacity?

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill youtube-audio --all-skills
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill youtube-audio --all-skills
```

Leave out `--all-skills` to install only this skill. Add `--skills-dir DIR` to test another copy of the skills, such as an older commit.

## Results from 2026-10-31

In `results/2026-10-31/`, one run per prompt. The new description adds a "Not for …" sentence naming the neighbouring skills, and Russian trigger phrases.

| Run | Claude Sonnet | Codex |
|---|---|---|
| Only this skill, old description | 12/12 | 11/12 |
| All 20 skills, old description | 12/12 | 12/12 |
| All 20 skills, new description | 12/12 | 12/12 |

With only one skill installed, a near miss has nowhere better to go, so the whole-collection runs are the fairer test of the description. There is one run per prompt, so a difference of one or two prompts between runs is within run-to-run noise: across the ten skills tested on this date, the new descriptions scored 110/120 on both agents with the whole collection installed, against 114 (Claude) and 113 (Codex) for the old ones. The new wording follows CONTRIBUTING.md; it did not measurably change routing once the neighbouring skills were installed.

### Misses

- isolated, Codex: `n4-podcast-mp3` loaded it
