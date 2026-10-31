# youtube-transcript evals

## Triggers

`prompts.json` has 12 prompts: 7 must load the skill (one in Russian) and 5 are near misses that must not.

Must load:
- `t1-summarise`: Summarise this video: https://www.youtube.com/watch?v=jNQXAC9IVRw
- `t2-srt`: Get me the transcript of https://youtu.be/dQw4w9WgXcQ as SRT.
- `t3-ru`: Сделай конспект этого видео с тайм-кодами: https://www.youtube.com/watch?v=aircAruvnKk
- `t4-takeaways`: What are the key takeaways from https://www.youtube.com/watch?v=zjkBMFhNj_g ?
- `t5-quotes`: Pull a few quotes about scaling from https://youtu.be/kCc8FmEb1nY
- `t6-translate`: Translate what is said in https://www.youtube.com/watch?v=9bZkp7q19f0 into English.
- `t7-question`: Does the speaker in https://www.youtube.com/watch?v=8pTEmbeENF4 mention test-driven development?

Must not:
- `n1-search`: Find YouTube videos about Kubernetes networking.
- `n2-mp3`: Download the audio of https://youtu.be/dQw4w9WgXcQ as mp3.
- `n3-voice-memo`: Transcribe my voice memo memo.m4a in this folder.
- `n4-own-captions`: How do I add captions to my own YouTube upload?
- `n5-api-code`: Write a Python script that uses the YouTube Data API to list my playlists.

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill youtube-transcript --all-skills
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill youtube-transcript --all-skills
```

Leave out `--all-skills` to install only this skill. Add `--skills-dir DIR` to test another copy of the skills, such as an older commit.

## Results from 2026-10-31

In `results/2026-10-31/`, one run per prompt per row. The new description adds a "Not for …" sentence naming the neighbouring skills and Russian trigger phrases.

| Run | Claude Sonnet | Codex |
|---|---|---|
| Only this skill, old description | 12/12 | 10/12 |
| All 20 skills, old description, run 1 | 12/12 | 12/12 |
| All 20 skills, old description, run 2 | 12/12 | 12/12 |
| All 20 skills, new description, run 1 | 12/12 | 12/12 |
| All 20 skills, new description, run 2 | 12/12 | 12/12 |

With only one skill installed, a near miss has nowhere better to go, so the whole-collection runs are the fairer test of the description. The two runs of the same description show how much a score moves by chance. Across the ten skills tested on this date, with the whole collection installed, the old descriptions scored 114 and 111 of 120 on Claude Sonnet and 113 and 110 on Codex; the new ones scored 110 and 113 on Claude Sonnet and 113 and 111 on Codex. Two runs of the same description differ by as much as old and new do, so these runs don't show the new wording changing routing either way.

### Misses

- isolated, Codex: `n1-search` loaded it
- isolated, Codex: `n2-mp3` loaded it
