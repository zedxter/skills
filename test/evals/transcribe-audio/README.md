# transcribe-audio evals

## Triggers

`prompts.json` has 12 prompts: 7 must load the skill (one in Russian) and 5 are near misses that must not.

Must load:
- `t1-meeting`: Transcribe meeting.m4a in this folder.
- `t2-subtitles`: Make English subtitles (.srt) for lecture.mp4.
- `t3-ru`: Расшифруй голосовое сообщение voice.ogg и сделай краткое содержание.
- `t4-no-captions`: This YouTube video has no captions: https://www.youtube.com/watch?v=8pTEmbeENF4. What does the speaker say?
- `t5-podcast-url`: Transcribe this podcast episode: https://traffic.megaphone.fm/GLT1412515089.mp3
- `t6-section`: What is said in interview.wav between 10:00 and 25:00? Give me timestamped lines.
- `t7-translate`: Translate the Spanish speech in clase.mp3 into English text.

Must not:
- `n1-has-captions`: Get the transcript of https://www.youtube.com/watch?v=jNQXAC9IVRw
- `n2-convert`: Convert lecture.mp4 to mp3.
- `n3-podcast-search`: Find a podcast about the history of Unix.
- `n4-record-code`: Write a Python script that records audio from the microphone.
- `n5-text`: Summarise notes.txt in this folder.

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill transcribe-audio --all-skills
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill transcribe-audio --all-skills
```

Leave out `--all-skills` to install only this skill. Add `--skills-dir DIR` to test another copy of the skills, such as an older commit.

## Results from 2026-10-31

In `results/2026-10-31/`, one run per prompt. The new description adds a "Not for …" sentence naming the neighbouring skills, and Russian trigger phrases.

| Run | Claude Sonnet | Codex |
|---|---|---|
| Only this skill, old description | 11/12 | 11/12 |
| All 20 skills, old description | 12/12 | 12/12 |
| All 20 skills, new description | 12/12 | 11/12 |

With only one skill installed, a near miss has nowhere better to go, so the whole-collection runs are the fairer test of the description. There is one run per prompt, so a difference of one or two prompts between runs is within run-to-run noise: across the ten skills tested on this date, the new descriptions scored 110/120 on both agents with the whole collection installed, against 114 (Claude) and 113 (Codex) for the old ones. The new wording follows CONTRIBUTING.md; it did not measurably change routing once the neighbouring skills were installed.

### Misses

- isolated, Claude Sonnet: `n1-has-captions` loaded it
- isolated, Codex: `n1-has-captions` loaded it
- all-after, Codex: `n2-convert` loaded it
