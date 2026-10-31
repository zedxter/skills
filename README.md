# utility-belt

[![CI](https://github.com/zedxter/utility-belt/actions/workflows/ci.yml/badge.svg)](https://github.com/zedxter/utility-belt/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

20 [Agent Skills](https://agentskills.io) that let a coding agent search the web, read and scrape pages,
work with YouTube and podcasts, find and check papers, read Wikipedia, Hacker News and Telegram channels,
make PDFs and Anki decks, and write and review prose. None of them needs an API key or an account; a few
accept optional keys ([Limits](#limits-and-safety)). They help where an agent's built-in tools stop: a
fetch that a plain HTTP client can't get, a page that needs JavaScript, a video transcript, a reference
that has to be checked against Crossref.

The skills are tested on Claude Code and Codex and should work in Cursor, Gemini CLI, OpenCode and any
other agent that reads `SKILL.md` and can run shell commands. The scripts are written for macOS, Linux
and Windows; CI runs them on Linux. Most skills need network access, so an agent sandbox that blocks it,
such as Codex's default, has to allow it.

## Quick start

1. Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/). The scripts run with it, and it
   brings its own Python if you have none.
2. Install the skills with the [`skills` CLI](https://github.com/vercel-labs/skills) (needs Node.js 22.20
   or later):

   ```bash
   npx skills add zedxter/utility-belt
   ```

   It asks which skills to install and for which agents, and installs into the current project unless
   you add `-g`. Each installed skill adds its description to every session, so pick the ones you use.
   If you pick `web-crawl`, `web-scrape`, `web-search` or `web-browse`, pick `web-extract` too: they call
   its script.
3. Start a new agent session from a new terminal, so `uv` is on PATH, and ask something like "What's the
   weather in Berlin tomorrow?". The agent should load the `weather` skill and answer with a forecast for
   the place it found. If it answers from memory, name the skill: "use the weather skill".

A few skills also need ffmpeg, a browser or a speech model; [Requirements](#requirements) lists them.

## What you can ask

Ask for the result. The agent usually picks the skills from the request and chains them; if it doesn't,
name the skill ("use web-extract"). Typical routes:

| You ask | What usually runs |
|---|---|
| "Find the most-watched talk on Rust async from this year and summarise it" | `youtube-search`, then `youtube-transcript` (or `transcribe-audio` if the video has no captions) |
| "This link is dead. What did the page say when the paper cited it?" | `wayback` |
| "Read the docs section of this site and make notes" | `web-crawl` |
| "Are the references in `paper.bib` real?" | `cite-check` |
| "What does HN think of this library?" | `hackernews`, then `web-extract` for the linked articles |
| "Make flashcards from this lecture" | `youtube-transcript`, then `anki-deck` |

## Skills

The flags below are options the agent passes. Each link opens the skill's folder; its `SKILL.md` is the
full documentation the agent reads.

### Web

| Skill | What it does |
|---|---|
| [`web-search`](skills/web-search) | Web search through DuckDuckGo, Bing, Brave and other engines, or your own SearXNG. `--read` returns what the top pages say. |
| [`web-extract`](skills/web-extract) | A page or PDF as clean Markdown. A browser-like TLS handshake gets it through to many pages that refuse plain HTTP clients; hard bot walls are reported, not bypassed. Renders JavaScript pages in a local Chromium when needed; falls back to a dated Wayback Machine copy. |
| [`web-crawl`](skills/web-crawl) | A whole site or section in one Markdown file, a folder of pages, or JSON/JSONL, within depth, page and time limits. Honours robots.txt. `--map` lists the URLs. |
| [`web-scrape`](skills/web-scrape) | Repeating items (listings, tables, JSON-LD) as JSON, CSV or a Markdown table, across pages and into detail pages. |
| [`web-browse`](skills/web-browse) | A real Chromium, driven one step at a time: open, click, type, read, screenshot. Logins can persist in a profile folder you choose. |
| [`wayback`](skills/wayback) | Wayback Machine snapshots: the capture closest to a date, when a page changed, what it said, Save Page Now. |

### YouTube and podcasts

| Skill | What it does |
|---|---|
| [`youtube-search`](skills/youtube-search) | YouTube search with publish date, views and duration, sorted and filtered. |
| [`youtube-transcript`](skills/youtube-transcript) | A video's captions as text, timestamps, SRT or JSON. |
| [`youtube-audio`](skills/youtube-audio) | Audio of a video, playlist or channel as m4a, mp3, opus, flac or wav, with chapters and cover art. |
| [`transcribe-audio`](skills/transcribe-audio) | Speech-to-text on your machine with faster-whisper, for files and URLs: text, timestamps, SRT, VTT or JSON. |
| [`podcast-search`](skills/podcast-search) | Podcasts and episodes from Apple's iTunes Search and each show's RSS feed, with direct audio URLs. |

### Research

| Skill | What it does |
|---|---|
| [`scholar-search`](skills/scholar-search) | Papers from arXiv, Crossref, Europe PMC, PubMed and bioRxiv, with citation counts, open-access links and BibTeX. |
| [`cite-check`](skills/cite-check) | Checks each reference in a `.bib` file, a reference list or a PDF: verified, mismatch, not found or unverifiable. |
| [`wiki-lookup`](skills/wiki-lookup) | Wikipedia in any language and Wikidata facts, with the revision id to cite. |

### News and everyday

| Skill | What it does |
|---|---|
| [`hackernews`](skills/hackernews) | Hacker News search, the front page and other lists, and whole comment threads. |
| [`telegram-channel`](skills/telegram-channel) | Posts of a public Telegram channel without an account: history, dates, search, links. |
| [`weather`](skills/weather) | Forecast for up to 16 days, past weather back to 1940 and air quality, from Open-Meteo (free for non-commercial use). |

### Writing and documents

| Skill | What it does |
|---|---|
| [`human-writing`](skills/human-writing) | Writes and reviews English posts, articles and docs: asks for real material, keeps a source for every fact, flags wording that reads as AI-written and runs independent reviewers. |
| [`pdf-typeset`](skills/pdf-typeset) | Markdown or JSON to a checked PDF with Typst: reports, letters, invoices, CVs, slides. |
| [`anki-deck`](skills/anki-deck) | Anki decks from Markdown, CSV or JSON. Rebuilding a deck updates the cards instead of duplicating them. |

## Requirements

- **[`uv`](https://docs.astral.sh/uv/getting-started/installation/).** Each script declares its
  dependencies inline ([PEP 723](https://peps.python.org/pep-0723/)). On a script's first run, `uv`
  fetches them within the declared version ranges, and Python 3.12+ if needed, so that run needs network.
  Without `uv` you need Python 3.12+ and `pip`; each `SKILL.md` says what to install.
- **ffmpeg** (with ffprobe) on PATH for `youtube-audio`: `brew install ffmpeg`, `sudo apt install ffmpeg`
  or `winget install ffmpeg`.
- **Playwright's Chromium** for `web-browse`, for `--render` in `web-extract`, `web-crawl` and
  `web-scrape`, and for rendering in `web-search --read`. Once per machine, about 100 MB:

  ```bash
  uv run --with playwright playwright install chromium-headless-shell
  ```

  `--headful` (a visible window) needs the full build:
  `uv run --with playwright playwright install chromium`. On Linux, if Chromium fails to start, add its
  system libraries with `uv run --with playwright playwright install-deps chromium` (asks for sudo;
  Debian and Ubuntu are supported). If the browser stops starting after an update, run the install again.
- **A Whisper model** for `transcribe-audio`, downloaded once after the agent asks you. The default
  `small` is 486 MB; `tiny` is 78 MB and `large-v3` 3.1 GB.
- **`web-extract` next to the other web skills.** `web-crawl`, `web-scrape` and `web-search --read` need
  its script. `web-browse` uses it for `read` when it is there and returns plain text otherwise.

## Other ways to install

### Claude Code plugins

The repository is also a Claude Code plugin marketplace, one plugin per skill. Plugin names are the skill
names above; install each one you want:

```bash
claude plugin marketplace add zedxter/utility-belt
claude plugin install web-search@utility-belt
```

`web-crawl`, `web-scrape`, `web-browse` and `web-search` bring `web-extract` with them. `/plugin` lists
the catalogue. Use one install method per skill: a skill installed with `npx skills add` or copied into
`~/.claude/skills/` and the same skill as a plugin both load, and the agent sees two near-identical
descriptions.

<details>
<summary>Updating plugins, and moving from the old <code>zedxter-skills</code> marketplace</summary>

Third-party marketplaces don't update on their own unless you turn on auto-update for this one in
`/plugin`. To update by hand:

```bash
claude plugin marketplace update utility-belt
claude plugin update web-search@utility-belt
```

The marketplace used to be called `zedxter-skills`, from the repository's old name `zedxter/skills`. It
keeps working because GitHub redirects that repository, but switch to `utility-belt`; adding it next to
the old one installs every skill twice. Note
which plugins you have in `/plugin`, remove the old marketplace (this uninstalls its plugins), then add
the new one and install them again:

```bash
claude plugin marketplace remove zedxter-skills
claude plugin marketplace add zedxter/utility-belt
claude plugin install web-search@utility-belt
```

</details>

<details>
<summary>OpenSkills</summary>

[OpenSkills](https://github.com/numman-ali/openskills) installs `SKILL.md` skills for any agent and lists
them in `AGENTS.md`, for agents that don't load skills on their own. It needs Node.js 20.6 or later.

```bash
npx openskills install zedxter/utility-belt
npx openskills sync
```

`sync` writes the list to `AGENTS.md`; `-o <file>` picks another file.

</details>

<details>
<summary>Copying the folders by hand</summary>

Copy a skill folder into the agent's skills directory (`~` is `%USERPROFILE%` on Windows):

| Agent | Directory |
|-------|-----------|
| Claude Code | `~/.claude/skills/` or `<project>/.claude/skills/` |
| OpenAI Codex | `~/.agents/skills/` or `<project>/.agents/skills/` |
| Cursor | `<project>/.cursor/skills/` |
| Gemini CLI | `~/.gemini/skills/` |
| OpenCode | `~/.config/opencode/skills/` |
| Generic / agentskills.io | `<project>/.agents/skills/` |

To copy all skills for Claude Code on macOS or Linux, clone the repository outside your project and copy
the folders. For another agent, put its directory from the table in place of `~/.claude/skills` in both
lines.

```bash
git clone https://github.com/zedxter/utility-belt.git /tmp/utility-belt
mkdir -p ~/.claude/skills
for skill in /tmp/utility-belt/skills/*/; do cp -R "${skill%/}" ~/.claude/skills/; done
```

One skill at a time works the same way, without a trailing slash on the source, or macOS `cp` copies the
folder's contents instead of the folder. Copy `web-extract` next to the other web skills.

```bash
cp -R /tmp/utility-belt/skills/weather ~/.claude/skills/
```

On Windows (PowerShell):

```powershell
git clone https://github.com/zedxter/utility-belt.git
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills" | Out-Null
Get-ChildItem utility-belt\skills -Directory | Copy-Item -Recurse -Destination "$env:USERPROFILE\.claude\skills\"
```

Start a new agent session to pick up the skills.

</details>

## How it's checked

- **Tests.** Every skill has an offline test suite, and CI runs all of them on each pull request. Browser
  and speech tests skip there and run locally once Chromium and the `tiny` Whisper model are installed.
- **Live sources.** A weekly workflow runs 15 skills against the public services they read and keeps an
  issue open while one is broken.
- **Trigger evals.** Prompts that should load each skill, and near misses that shouldn't, run on Claude
  Code and Codex ([`test/evals/`](test/evals)).
- **Exit codes.** Each skill documents its own. Usually 1 means nothing was found and 4 means blocked, so
  the agent stops instead of retrying. An unexpected error inside a script exits 7, never 1, so a bug
  can't pass for an empty result. A failure before the script starts, such as `uv` unable to fetch a
  dependency, returns `uv`'s own code with the reason on stderr.

## Limits and safety

- **Fetched text is untrusted.** Pages and posts can carry instructions aimed at the agent. The skills
  that read them tell the agent never to follow such instructions, which lowers the risk of prompt
  injection but doesn't remove it. Keep permission prompts on for shell commands and file writes, and
  give `web-browse` its own profile folder, not one signed in to your email or bank.
- **Fetching etiquette.** The scripts that call a service themselves send a User-Agent that names the
  skill and links here, and keep to the source's published limits. `web-extract` also makes its TLS
  handshake look like a browser's (`--impersonate none` turns that off). `web-crawl` honours robots.txt;
  `web-extract`, `web-scrape` and `web-browse` fetch the URLs they are given without checking it.
  Following each site's terms is up to you. Per-source limits and licences:
  [docs/sources.md](docs/sources.md).
- **Unofficial endpoints break.** Search engines, YouTube and Telegram's web preview are not official
  APIs. They rate-limit, block cloud and VPN addresses, and change without notice; the weekly live run
  catches it when they do.
- **Dependencies are not pinned.** `uv` resolves each script's dependencies from PyPI within the declared
  ranges on first run. There is no lock file.
- **Optional keys.** `scholar-search` and `cite-check` can also query OpenAlex and Semantic Scholar with
  your keys, `wayback` saves pages under higher limits with archive.org keys, and commercial users of
  `weather` need an Open-Meteo key.

## Contributing

Issues and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) has the rules a skill follows
and the checks to run before a pull request.

## Credits

`human-writing`'s catalogue of AI tells builds on [blader/humanizer](https://github.com/blader/humanizer),
[hardikpandya/stop-slop](https://github.com/hardikpandya/stop-slop),
[petergyang/no-ai-slop](https://github.com/petergyang/no-ai-slop),
[sergebulaev/linkedin-skills](https://github.com/sergebulaev/linkedin-skills) and
[eric-tramel/slop-guard](https://github.com/eric-tramel/slop-guard) (all MIT), and draws on Wikipedia's
[Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing) (CC BY-SA), paraphrased
rather than copied. Its test corpus of human writing comes from 18F's
[blog](https://github.com/18F/18f.gsa.gov) and [guides](https://github.com/18F/guides) (CC0),
[the Go blog](https://github.com/golang/website) (CC BY 4.0, The Go Authors) and the
[Rust blog](https://github.com/rust-lang/blog.rust-lang.org) and [RFCs](https://github.com/rust-lang/rfcs)
(MIT / Apache-2.0, The Rust Project Developers); each file links its source.

`pdf-typeset` bundles [Source Sans 3](https://github.com/adobe-fonts/source-sans) under the SIL Open Font
License 1.1, with the licence next to the fonts.

## License

[MIT](LICENSE). The bundled fonts and test texts keep their own licences, listed under Credits.
