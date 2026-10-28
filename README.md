# skills

An open-source collection of [Agent Skills](https://agentskills.io) for AI
coding agents — Claude Code, Codex, Cursor, Gemini CLI, OpenCode and any other
agent that understands the `SKILL.md` format.

Each skill is a folder with a `SKILL.md` (instructions + metadata) and, where
useful, `scripts/` and `references/`. Agents load the skill's description at
startup and read the full instructions only when the task matches, so the
collection costs almost nothing in context until a skill is actually needed.

## Skills

| Skill | What it does |
|-------|--------------|
| [`youtube-transcript`](skills/youtube-transcript) | Fetches a YouTube video's transcript (captions) as text, timestamps, SRT or JSON via the free, key-less `youtube-transcript-api`. Triggers on YouTube links when the user wants a transcript, summary, notes or translation. Requires Python 3.9+ and `uv` or `pip`. |

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
repo:

```bash
git clone https://github.com/zedxter/skills.git
mkdir -p ~/.claude/skills
cp -r skills/skills/youtube-transcript ~/.claude/skills/
```

Claude Code picks up new skills on the next session start; `/skills` lists them.

### Manually (other agents)

The same folder works anywhere the agent reads `SKILL.md` files. Typical
locations:

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

## Requirements

Skills that ship Python scripts declare their dependencies inline
([PEP 723](https://peps.python.org/pep-0723/)), so `uv run scripts/<name>.py`
works with no setup. Without `uv`, install the dependency named in the script
header with `pip` and run it with `python3`.

## Contributing

Issues and pull requests are welcome. For a new skill: one folder under
`skills/`, a `SKILL.md` with a precise trigger description, scripts that print
clear errors and meaningful exit codes, and a row in the table above.

## License

[MIT](LICENSE)
