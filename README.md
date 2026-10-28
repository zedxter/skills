# skills

An open-source collection of [Agent Skills](https://agentskills.io) for AI
coding agents — Claude Code, Codex, Cursor, Gemini CLI, OpenCode and any other
agent that understands the `SKILL.md` format.

Each skill is a folder with a `SKILL.md` (instructions + metadata) and, where
useful, `scripts/` and `references/`. Agents load the skill's description at
startup and read the full instructions only when the task matches, so the
collection costs almost nothing in context until a skill is actually needed.

All skills are **cross-platform** (macOS, Linux, Windows): the scripts are plain
Python with inline dependency metadata, no shell-specific code, UTF-8 output
everywhere.

## Skills

| Skill | What it does |
|-------|--------------|
| [`youtube-search`](skills/youtube-search) | Searches YouTube without an API key and returns title, channel, **publish date, view count, duration** and URL. Sort by date/views, filter by period, duration, min views, channel. Triggers when the user wants to find videos on a topic or needs a video to summarise but has no link. |
| [`youtube-transcript`](skills/youtube-transcript) | Fetches a YouTube video's transcript (captions) as text, timestamps, SRT or JSON via the free, key-less `youtube-transcript-api`. Triggers on YouTube links when the user wants a transcript, summary, notes or translation. |

The two chain naturally: search → pick a video by date/views/length → transcript → summary.

## Requirements

- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/) (recommended). Each script
  declares its dependencies inline ([PEP 723](https://peps.python.org/pep-0723/)),
  so `uv run scripts/<name>.py …` works with zero setup and `uv` downloads a
  suitable Python if none is installed.
- Without `uv`: install the dependency named in the script header with `pip`
  and run `python scripts/<name>.py …`.

Installing `uv`:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# macOS (Homebrew)
brew install uv
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# or: winget install astral-sh.uv
```

## Installation

### With the `skills` CLI (any supported agent)

```bash
npx skills add zedxter/skills
```

Pick the skills and target agents interactively, or install everything for one
agent non-interactively:

```bash
npx skills add zedxter/skills --all -a claude-code
```

### With OpenSkills

```bash
npx openskills install zedxter/skills
```

Then run `npx openskills sync` so the agent's instructions file (`AGENTS.md`,
`CLAUDE.md`, …) lists the installed skills.

### Manually (Claude Code)

Copy a skill folder into your personal skills directory to make it available in
every project, or into `.claude/skills/` inside a project to share it with the
repo.

macOS / Linux:

```bash
git clone https://github.com/zedxter/skills.git
mkdir -p ~/.claude/skills
cp -r skills/skills/youtube-search skills/skills/youtube-transcript ~/.claude/skills/
```

Windows (PowerShell):

```powershell
git clone https://github.com/zedxter/skills.git
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills" | Out-Null
Copy-Item -Recurse skills\skills\youtube-search, skills\skills\youtube-transcript "$env:USERPROFILE\.claude\skills\"
```

Claude Code picks up new skills on the next session start; `/skills` lists them.

### Manually (other agents)

The same folder works anywhere the agent reads `SKILL.md` files. Typical
locations (`~` is `%USERPROFILE%` on Windows):

| Agent | Directory |
|-------|-----------|
| Claude Code | `~/.claude/skills/` or `<project>/.claude/skills/` |
| OpenAI Codex | `~/.codex/skills/` |
| Cursor | `<project>/.cursor/skills/` |
| Gemini CLI | `~/.gemini/skills/` |
| OpenCode | `~/.config/opencode/skills/` |
| Generic / agentskills.io | `<project>/.agents/skills/` |

## Repository layout

```
skills/
└── <skill-name>/
    ├── SKILL.md        # frontmatter (name, description, …) + instructions
    ├── scripts/        # executable helpers the agent runs (optional)
    └── references/     # longer docs loaded on demand (optional)
```

`SKILL.md` follows the [Agent Skills specification](https://agentskills.io/specification):
`name` matches the folder name, `description` says both what the skill does and
when to use it, and the body stays short enough to be read in one go.

## Contributing

Issues and pull requests are welcome. For a new skill: one folder under
`skills/`, a `SKILL.md` with a precise trigger description, scripts that run
unchanged on macOS, Linux and Windows, print clear errors with meaningful exit
codes, and a row in the table above.

## License

[MIT](LICENSE)
