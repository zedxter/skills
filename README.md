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
| [`youtube-audio`](skills/youtube-audio) | Saves a YouTube video's, playlist's or channel's **audio as a file** with the free, key-less `yt-dlp`: the original stream by default (m4a/opus, no re-encoding) or **mp3**/opus/flac/wav via ffmpeg at a chosen bitrate; embeds title, channel, date, chapters and cover art; cuts a section by timestamps and splits chapters into tracks; `--info` lists chapters and formats. Triggers when the user wants a YouTube link as mp3/audio, a lecture or podcast for offline listening, or audio to transcribe when there are no captions. |
| [`transcribe-audio`](skills/transcribe-audio) | Transcribes speech in an audio/video **file or URL locally and offline** with `faster-whisper` (no key, no upload, no PyTorch, no ffmpeg install): text, timestamps or SRT like `youtube-transcript`, plus VTT and JSON; downloads YouTube videos and podcast episodes with `yt-dlp`; language auto-detection, `--start/--end`, name hints, `--info` with a time estimate, flat memory on hour-long recordings. Never downloads a model without `--download-model`. Triggers when a video has no captions, or the user has a podcast, lecture, meeting or voice memo to transcribe or subtitle. |
| [`podcast-search`](skills/podcast-search) | Finds podcasts and episodes **without an API key** via Apple's iTunes Search API (shows, or episodes across all shows by topic or guest) and lists a show's episodes from its own RSS feed: title, date, duration, description, **direct audio URL** and the Podcasting 2.0 transcript link; filters by date, keyword and length; `-f urls` pipes into `transcribe-audio`. Triggers when the user wants a podcast on a topic, a specific interview, a show's latest episodes or podcast audio to transcribe. |
| [`web-extract`](skills/web-extract) | Fetches a web page or PDF from the user's own machine with a browser-grade TLS fingerprint (`curl_cffi`) and returns its **main content as Markdown, text or JSON** (title, author, date, links, JSON-LD/OpenGraph/tables) via `trafilatura` and `pypdf`; renders JavaScript pages in a **local headless Chromium** (optional Playwright) with click/type/scroll actions and screenshots, tracks changes between fetches, and falls back to the newest **Wayback Machine** snapshot when the page is blocked, gone or unreachable. A free local stand-in for paid scraping APIs. Triggers when the user shares a URL to read, summarise or quote, or when the built-in fetch tool got 403, "Just a moment" or an empty page. |
| [`web-crawl`](skills/web-crawl) | Crawls a site breadth-first from the user's machine and collects every page's **main content into one Markdown file**, a folder of pages with `index.json`, or JSON/JSONL; `--map` lists the URLs. Bounded by depth, page count and time, include/exclude patterns, same site only, robots.txt honoured, sitemap.xml as seed; each page goes through `web-extract` (PDF text, optional headless rendering). Free stand-in for Firecrawl crawl/map. Triggers when the user wants a whole documentation site, blog or section read, summarised or turned into notes, or asks which pages a site has. |
| [`web-scrape`](skills/web-scrape) | Scrapes **structured records** into JSON, JSONL, CSV or a Markdown table: one record per repeating element (CSS/XPath `--item`), fields by selector with `@attr` and `\|number`/`\|date`/`\|list` modifiers, or records from schema.org JSON-LD and HTML tables; walks next-page links, opens each record's detail page for more fields, and `--probe` shows a page's repeating blocks. Fetching via `web-extract` (bot-wall detection, optional headless rendering). Free stand-in for Apify actors and Firecrawl extract. Triggers when the user wants a list, catalogue, table or search-result page turned into rows. |
| [`web-search`](skills/web-search) | Searches the web **without an API key** via the `ddgs` metasearch library (DuckDuckGo, Bing, Brave, Google, Yahoo, Wikipedia backends) or the user's own SearXNG: title, URL, snippet, news date/source; filters by time window, region and site; `--read N` fetches the top results through `web-extract` and returns what the pages say. Free stand-in for Tavily, Serper and Firecrawl search. Triggers when the user asks to look something up, find sources, recent news or docs, or when the built-in search tool is missing or thin. |
| [`web-browse`](skills/web-browse) | Drives a **real Chromium step by step** from the CLI: a background session that persists between commands, `open`, `snapshot` of interactive elements with numbered refs, `click`, `type`, `select`, `press`, `scroll`, `wait`, tabs, `read` as Markdown (via `web-extract`), `screenshot`, `eval`; `--profile` keeps logins, `--headful` lets a human log in or pass a check. Free stand-in for Browser Use, Browserbase and Playwright MCP. Triggers when a task needs several dependent steps in a browser: log in and read, search a site and open a result, fill a form, click through an app. |
| [`prose-writer`](skills/prose-writer) | Writes, edits and reviews **English articles, posts and technical documentation** (LinkedIn, dev.to, Hashnode, Medium, how-tos, README, tutorials, reference, ADRs, changelogs) so they read as written by a person, not a chat model. Interviews the author for real material, drafts in their voice from a **voice profile**, keeps a **facts ledger** so nothing is invented, lints offline for AI tells (`scripts/lint_prose.py`, standard library only, per-genre rules and budgets), and before publishing runs a **panel of isolated reviewer sub-agents** (cold reader, hostile expert, fact checker, slop hunter, publication safety, voice keeper, platform editor, newcomer tester, source checker), then merges their findings. Triggers when the user asks to write, tighten or review a post, article or doc, to make text sound less like AI, or to check a text before publishing. |

They chain naturally: search → pick a video by date/views/length → transcript →
summary (or `transcribe-audio` when there are no captions, and `youtube-audio` for the sound itself);
`podcast-search` → episode audio URL → `transcribe-audio` does the same for podcasts; the links in a video's `--details` description go to `web-extract`;
`web-crawl --map` shows a site's structure, then `web-crawl --include` collects
the right section through `web-extract`'s engine.
`prose-writer` uses `web-search` and `web-extract` to research and read sources (every fact goes into its ledger with the URL),
and `youtube-transcript` or `transcribe-audio` to turn a talk or voice memo into material for an article.

`prose-writer`'s catalogue of AI tells is written for this repo but builds on
[blader/humanizer](https://github.com/blader/humanizer), [hardikpandya/stop-slop](https://github.com/hardikpandya/stop-slop),
[petergyang/no-ai-slop](https://github.com/petergyang/no-ai-slop), [sergebulaev/linkedin-skills](https://github.com/sergebulaev/linkedin-skills)
and [eric-tramel/slop-guard](https://github.com/eric-tramel/slop-guard) (all MIT), and paraphrases Wikipedia's
[Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing) (CC BY-SA). Its human-text test corpus is
public-domain 18F writing (CC0).

## Requirements

- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/) (recommended). Each script
  declares its dependencies inline ([PEP 723](https://peps.python.org/pep-0723/)),
  so `uv run scripts/<name>.py …` works with zero setup and `uv` downloads a
  suitable Python if none is installed.
- Without `uv`: install the dependency named in the script header with `pip`
  and run `python scripts/<name>.py …`.
- `transcribe-audio` downloads each Whisper model once from Hugging Face on
  first use (78 MB `tiny` … 486 MB default `small` … 3.1 GB `large-v3`), only
  when run with `--download-model`; it needs no ffmpeg.
- `youtube-audio` also needs [ffmpeg](https://ffmpeg.org/) on PATH
  (`brew install ffmpeg`, `sudo apt install ffmpeg`, `winget install ffmpeg`).
- Optional, for JavaScript-rendered pages (`web-extract --render`,
  `web-scrape --render`) and for `web-browse`: Playwright and its Chromium
  headless shell, installed once per machine (~100 MB, no admin rights on
  macOS/Windows; `playwright install chromium` for a visible `--headful`
  window):

  ```bash
  uv run --with playwright playwright install chromium-headless-shell
  ```

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

macOS / Linux — all skills:

```bash
git clone https://github.com/zedxter/skills.git
mkdir -p ~/.claude/skills
for skill in skills/skills/*/; do cp -R "${skill%/}" ~/.claude/skills/; done
```

or a single skill:

```bash
cp -R skills/skills/youtube-transcript ~/.claude/skills/
```

> **Warning:** the source path must **not** end with a trailing slash. On
> macOS `cp -R skills/skills/youtube-transcript/ ~/.claude/skills/` copies the
> folder's *contents* (`SKILL.md`, `scripts/`) straight into `~/.claude/skills/`
> instead of creating `~/.claude/skills/youtube-transcript/`, and the skill is
> not found. The loop above strips the slash for that reason.

Windows (PowerShell) — all skills:

```powershell
git clone https://github.com/zedxter/skills.git
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills" | Out-Null
Get-ChildItem skills\skills -Directory | Copy-Item -Recurse -Destination "$env:USERPROFILE\.claude\skills\"
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
test/                   # pytest suites for the scripts
```

Run the test suites with every script dependency available (browser tests skip
when Playwright's Chromium is not installed; network tests skip with
`WEB_EXTRACT_OFFLINE=1`; speech tests skip until the 78 MB `tiny` Whisper model
is cached: `uv run skills/transcribe-audio/scripts/transcribe.py test/data/jfk.ogg
-m tiny --download-model`):

```bash
uv run --with pytest --with curl_cffi --with trafilatura --with lxml_html_clean \
    --with pypdf --with cssselect --with 'ddgs>=9,<10' --with playwright \
    --with 'yt-dlp[default]' --with mutagen --with faster-whisper --with feedparser python -I -m pytest test -q
```

`web-crawl`, `web-scrape`, `web-search --read` and `web-browse read` import
`web-extract`'s script at run time, so install the web skills together (the
install commands above copy every skill). `web-browse` and `web-extract
--render` need Playwright's Chromium once: `uv run --with playwright playwright
install chromium-headless-shell`.

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
