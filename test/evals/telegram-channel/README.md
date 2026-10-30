# telegram-channel evals

Trigger evals: does an agent load the skill for a request about a Telegram channel, and leave it alone for
nearby requests that are not about reading one? Each run installs this skill alone in a scratch project and
asks one prompt from `prompts.json` (6 that must load it, 4 that must not); the runner is shared with
human-writing:

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT_DIR --model sonnet --skill telegram-channel
python3 -I test/evals/human-writing/run_triggers.py codex OUT_DIR --clean --skill telegram-channel
```

| Prompt | Must load | Claude Sonnet | Codex |
|---|---|---|---|
| t1 "What has @durov posted lately?" | yes | ✓ | ✓ |
| t2 a t.me post link to summarise | yes | ✓ | ✓ |
| t3 «Сделай дайджест канала t.me/addmeto» | yes | ✓ | ✓ |
| t4 posts about Stories in t.me/s/telegram | yes | ✓ | ✓ |
| t5 links a channel shared | yes | ✓ | ✓ |
| t6 what a channel posted in a month | yes | ✓ | ✓ |
| n1 write a Telegram bot | no | ✓ | ✓ |
| n2 make my channel public (how-to) | no | ✓ | ✓ |
| n3 regex for Telegram usernames | no | ✓ | ✓ |
| n4 summarise a local file | no | ✓ | ✓ |

Results from 2026-10-30 in `results/2026-10-30/`: 10/10 on Claude Sonnet and on Codex with the final
description. An earlier description (before the review added Russian phrases and the "Not for" clause) also
scored 10/10 on Claude Opus, Claude Sonnet and Codex (`claude-opus-draft-description.json`).

The script itself is covered offline by `test/test_telegram_channel.py`; `TELEGRAM_LIVE=1` adds a smoke test
against https://t.me/s/telegram.
