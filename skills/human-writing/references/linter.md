# The linter

`scripts/lint_prose.py` in this skill's folder. Standard library only, no network; `uv run` or `python` both work. Paths to drafts are wherever the user's files are.

```bash
uv run scripts/lint_prose.py draft.md                           # article profile
uv run scripts/lint_prose.py post.md --genre post               # LinkedIn and other social posts
uv run scripts/lint_prose.py docs/install.md --genre docs -f json
uv run scripts/lint_prose.py draft.md --voice voice-profile.md  # the author's habits are allowed
uv run scripts/lint_prose.py - < draft.md                       # stdin (POSIX shells and cmd.exe; in PowerShell pass a path)
uv run scripts/lint_prose.py --list-rules                       # rule ids and severity per genre
```

| Flag | Default | Meaning |
|---|---|---|
| `-g, --genre` | `article` | `article`, `post` or `docs`. Changes which rules run and their budgets. |
| `-f, --format` | `text` | `json` returns `{file, genre, words, score, threshold, pass, counts, stats, findings[]}`; each finding has `rule, severity, line, col, text, message, hint`. |
| `-t, --threshold` | 75 | Minimum passing score, 0–100. |
| `--voice FILE` | — | Reads `lint-allow-rules: a, b` and `lint-allow-words: x, y` lines from a voice profile. |
| `--config FILE` | — | TOML: `[rules] disable = [...]`, `[severity] rule = "error"`, `[words] ai_vocab / stock_phrases / allow = [...]`. |

It skips code blocks, inline code, front matter, URLs, tables, HTML and block quotes, so quoting someone else's words doesn't count against the author. Rules with a budget (dashes, contrasts, triads, AI vocabulary, intensifiers, inline bold) scale with length and report only the excess; AI vocabulary within budget shows as `info`. Article-only shape rules flag more lists or headings than articles usually have, and a piece with almost no names, numbers or dates.

## Acting on findings

- `error` (chat residue; faux-insight setups in articles and posts; a post over 3,000 characters) must go.
- `warning` should usually go. Rewrite the sentence around its point; a synonym swap leaves the same shape.
- `info` is for judgement: passive voice in docs, a single hedge.
- Don't contort a sentence to raise the score.

## What it can and can't tell you

Measured on the corpora in `test/data/prose/` (2026-10-30):

- It passes all 40 human-written texts: 18F, Go and Rust blog posts, 18F guides and Rust RFCs.
- It fails about two thirds of the articles and posts that Sonnet, Haiku and Codex wrote with no style instructions.
- It fails almost none of their docs. A model's README is often just a README; the linter looks for tells, not authorship.
- It almost never fails model text written under an instruction to avoid AI clichés.

So a pass means "no obvious tells", not "good" and not "human". Emptiness, invented facts and a missing point are the review panel's job.

Exit codes: `0` pass · `1` below the threshold or an `error` finding · `2` bad input (missing file, bad option, unreadable `--config` or `--voice`) · `7` the input is not UTF-8.
