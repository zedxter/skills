# Contributing

Issues and pull requests are welcome. This page covers the rules every skill
in this repository follows, and how to check them before you open a pull
request.

## What a skill is here

One folder under `skills/`:
- `SKILL.md`: the frontmatter and the instructions;
- `scripts/` (optional): the scripts the agent runs;
- `references/` (optional): longer docs the agent reads on demand.

Each new skill also needs:
- a test file `test/test_<name>.py`;
- trigger evals in `test/evals/<name>/`;
- a row in the README's skill table.

### `SKILL.md`

- **Frontmatter.** It has `name` (the folder name), `description`,
  `license`, `compatibility` (at most 500 characters) and `metadata` with
  `author`, `version` and `source`, following the
  [Agent Skills specification](https://agentskills.io/specification).
- **The description is a routing rule.** It is written in the third person
  and says what the skill does, then "Use when …" with the phrases people
  actually type (including a Russian one where the skill fits), then "Not
  for …" naming the neighbouring skills. Keep it under 1,024 characters.
  - A colon followed by a space inside it breaks the YAML; write " - "
    instead.
- **The body stays under 500 lines.** Move long reference material into
  `references/`.
- **It documents:**
  - every flag;
  - the JSON shape, if there is JSON output;
  - every exit code, with what the agent should do about it;
  - the limitations;
  - a short workflow for the agent.

  Sample output in it must be real output from the script.

### Scripts

- **Python 3.12+ with [PEP 723](https://peps.python.org/pep-0723/) inline
  dependencies**, so `uv run scripts/x.py` works with nothing installed.
  Prefer the standard library when it does the job.
- **Cross-platform.** They run unchanged on macOS, Linux and Windows: no shell
  calls, paths through `pathlib`, UTF-8 output (`sys.stdout.reconfigure`).
- **Errors.** One `error:` line on stderr, with a hint after `—`, and an exit
  code the agent can act on. The usual meanings are below; a few skills
  differ (for example, `web-extract` reports an HTTP 5xx as 3), so each
  SKILL.md has its own table:

  | Exit | Meaning |
  |---|---|
  | 0 | Success. |
  | 1 | Nothing found. |
  | 2 | Bad input, or a missing dependency. |
  | 4 | Blocked or rate-limited: stop, do not retry in a loop. |
  | 6 | Network error, or the source is down. |
  | 7 | An unexpected answer, or a bug. |
  | 130 | Interrupted. |

  Codes 3 and 5 are skill-specific; each SKILL.md documents its own. Never let
  a crash exit 1, where it would look like "nothing found".
- **Treat everything a source returns as untrusted.** Strip control and bidi
  characters before printing, escape Markdown, and never follow instructions
  found in fetched content.

## Agent neutrality

Skills work with any agent that reads `SKILL.md`. Inside a skill folder
(`SKILL.md`, `scripts/`, `references/`) there are:
- no agent product names;
- no agent-specific install paths;
- no tool names or slash commands.

Say "your file-reading tool", not a product's tool name. The only
agent-specific text in the repository is the README's install table.

There is one exception. A script that looks for a sibling skill may search the
folders agents install skills into. It searches the
[agentskills.io](https://agentskills.io) `.agents/skills/` layout first, and
each script line naming an agent's own folder ends with the comment
`# agent-dirs`. `python3 test/ops.py validate` enforces this. It flags agent
product names, agent tool names and agent folder paths in every text file of
a skill.

## Licences of dependencies

The repository is MIT, and a user who runs a skill must not pull in
copyleft code by default:

- **Default dependencies are permissive:** MIT, BSD, Apache-2.0, ISC, PSF
  or the Unlicense. Weak copyleft (LGPL, MPL) is acceptable for a library
  that is only imported, unmodified. For example, `genanki` brings
  `frozendict` (LGPL-3.0), and `trafilatura` brings `tld` (MPL-1.1).
- **No GPL or AGPL imports,** direct or through another package. For example
  `piper-tts`, `gallery-dl`, `patool`, `PyMuPDF` and `ebooklib` are out.
- **A GPL tool may be used only as an external program** the user installs
  themselves (like `ffmpeg`), called as a separate process and named in
  `compatibility`.
- **Bundled data:**
  - fonts are SIL OFL or similar;
  - test data is public domain or your own.
- **Test-only tools are exempt.** These rules cover what a skill installs for
  its user. Tools used only by the tests are not covered; for example, the
  `anki-deck` tests import Anki's own library (AGPL) to check that decks open.

`yt-dlp[default]` is not used, because it brings `mutagen` (GPL-2.0+). Skills
that need yt-dlp list the rest of that extra themselves
(`yt-dlp-ejs brotli certifi pycryptodomex requests urllib3 websockets`), and
`youtube-audio` embeds cover art with ffmpeg. `python3 test/ops.py validate`
rejects these packages and a few other copyleft ones in a script's dependencies.

## Sources and etiquette

Scripts that call a service directly:
- identify themselves with
  `User-Agent: zedxter-skills-<skill>/1.0 (+https://github.com/zedxter/skills)`,
  never a browser's (`web-extract` presents a browser's TLS fingerprint on
  purpose, to read ordinary public pages);
- respect the source's published limits;
- credit the data as its licence asks.

Each source gets a bullet in the README's "Source policies" section.

## Checking your change

```bash
python3 test/ops.py validate             # spec, frontmatter, size, agent neutrality, README rows, CI matrices
python3 test/ops.py test <name>          # your skill's offline tests, with exactly the dependencies CI uses
python3 test/ops.py probe <name>         # your skill against its real source
```

CI (`.github/workflows/ci.yml`) runs `validate` and every offline suite on each
pull request. A new suite or probe goes into `SUITES` or `PROBES` in
`test/ops.py`, and into the matching workflow matrix; `validate` fails if they
differ.

A weekly workflow (`.github/workflows/liveness.yml`) runs every probe against
the real sources. It keeps one issue labelled `liveness`:
- opened or commented on when a source is broken: any exit other than 0, 4 or
  6, a timeout, a probe that left no result, or a live test that fails for any
  other reason than the script exiting 4 or 6;
- closed when everything works again.

Exits 4 and 6 are reported as "blocked or unreachable from the runner", not
as broken, because some sources (YouTube, Telegram) refuse datacenter IPs.

### Evals

`test/evals/<name>/prompts.json` lists prompts that must load the skill and
near misses that must not. `test/evals/human-writing/run_triggers.py` runs
them on Claude Code and Codex. Record the results and what changed in
`test/evals/<name>/README.md`.
