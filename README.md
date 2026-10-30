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
| [`telegram-channel`](skills/telegram-channel) | Reads a **public Telegram channel without an account**, API ID or phone number through Telegram's own web preview (`t.me/s/<channel>`): text (plain or Markdown), date, views, signature, **media placeholders**, links, forwards and replies; pages back through history, `--after`/`--before` dates, Telegram's in-channel `--search`, `--grep`, one post by link, `--links` for the URLs posted; table, Markdown or JSON. A distinct exit code when Telegram changes the markup. Triggers on a channel name, @handle or t.me link, or when the user wants a channel's latest posts, a digest, or posts about a topic. |
| [`web-extract`](skills/web-extract) | Fetches a web page or PDF from the user's own machine with a browser-grade TLS fingerprint (`curl_cffi`) and returns its **main content as Markdown, text or JSON** (title, author, date, links, JSON-LD/OpenGraph/tables) via `trafilatura` and `pypdf`; renders JavaScript pages in a **local headless Chromium** (optional Playwright) with click/type/scroll actions and screenshots, tracks changes between fetches, and falls back to the newest **Wayback Machine** snapshot when the page is blocked, gone or unreachable. A free local stand-in for paid scraping APIs. Triggers when the user shares a URL to read, summarise or quote, or when the built-in fetch tool got 403, "Just a moment" or an empty page. |
| [`web-crawl`](skills/web-crawl) | Crawls a site breadth-first from the user's machine and collects every page's **main content into one Markdown file**, a folder of pages with `index.json`, or JSON/JSONL; `--map` lists the URLs. Bounded by depth, page count and time, include/exclude patterns, same site only, robots.txt honoured, sitemap.xml as seed; each page goes through `web-extract` (PDF text, optional headless rendering). Free stand-in for Firecrawl crawl/map. Triggers when the user wants a whole documentation site, blog or section read, summarised or turned into notes, or asks which pages a site has. |
| [`web-scrape`](skills/web-scrape) | Scrapes **structured records** into JSON, JSONL, CSV or a Markdown table: one record per repeating element (CSS/XPath `--item`), fields by selector with `@attr` and `\|number`/`\|date`/`\|list` modifiers, or records from schema.org JSON-LD and HTML tables; walks next-page links, opens each record's detail page for more fields, and `--probe` shows a page's repeating blocks. Fetching via `web-extract` (bot-wall detection, optional headless rendering). Free stand-in for Apify actors and Firecrawl extract. Triggers when the user wants a list, catalogue, table or search-result page turned into rows. |
| [`web-search`](skills/web-search) | Searches the web **without an API key** via the `ddgs` metasearch library (DuckDuckGo, Bing, Brave, Google, Yahoo, Wikipedia backends) or the user's own SearXNG: title, URL, snippet, news date/source; filters by time window, region and site; `--read N` fetches the top results through `web-extract` and returns what the pages say. Free stand-in for Tavily, Serper and Firecrawl search. Triggers when the user asks to look something up, find sources, recent news or docs, or when the built-in search tool is missing or thin. |
| [`web-browse`](skills/web-browse) | Drives a **real Chromium step by step** from the CLI: a background session that persists between commands, `open`, `snapshot` of interactive elements with numbered refs, `click`, `type`, `select`, `press`, `scroll`, `wait`, tabs, `read` as Markdown (via `web-extract`), `screenshot`, `eval`; `--profile` keeps logins, `--headful` lets a human log in or pass a check. Free stand-in for Browser Use, Browserbase and Playwright MCP. Triggers when a task needs several dependent steps in a browser: log in and read, search a site and open a result, fill a form, click through an app. |
| [`pdf-typeset`](skills/pdf-typeset) | Typesets **Markdown or JSON into a professional-looking PDF** with Typst, installed as a Python package (no LaTeX, browser or system libraries): report with cover and contents, document, letter, one-pager, 16:9 slides, **invoice with exact Decimal totals**, CV from JSON Resume; three themes, Cyrillic and other scripts, tagged PDF with metadata. Every render is **checked** (missing glyphs, text in the margins, overlapping text, unembedded fonts, blank, spilled or nearly empty pages, low-resolution images) and written as **PNG pages** for the agent to look at; `check` runs the same on any PDF. Triggers when the user wants a report, memo, letter, invoice, CV, one-pager or slides as a PDF, or Markdown exported to PDF. |
| [`wayback`](skills/wayback) | Checks, lists, reads and saves **Internet Archive Wayback Machine** snapshots without a key (standard library only): first and latest capture or the one **closest to a date**, every capture in a period or only those where the **content changed**, all archived URLs under a path or site, what a page said at a given time as readable text or the original bytes (PDFs too), and **Save Page Now** (anonymous, or with archive.org S3 keys). Falls back from the CDX index to the archive's own redirect for sites that hide their index. Triggers on dead or changed links, "what did this page say in …", proving how a page looked, or archiving a page. |
| [`hackernews`](skills/hackernews) | Reads **Hacker News** without a key through the official APIs (standard library only): **search** stories or comments with points, comments, date and author filters (Algolia HN Search); the **front page**, newest, best, Ask, Show and job lists in HN's order (Firebase); a story or comment with its **whole comment tree** threaded, top level in HN's ranking, bounded by `--max-comments`/`--depth`; a user's karma and recent posts; text, Markdown, JSON or bare `urls` for `web-extract`. Triggers when the user asks what HN says about something, wants the front page, a Show/Ask HN thread or an HN discussion summarised. |
| [`weather`](skills/weather) | Weather for any place **without a key** from Open-Meteo (standard library only): current conditions and a **16-day forecast** (hourly on request; temperature, feels-like, rain and its chance, wind, UV, sunrise and sunset), **past weather back to 1940** (ERA5) with summaries and monthly rows, and **air quality** (European/US AQI with levels, PM2.5, PM10, ozone, NO2, pollen). Places are resolved by name, "Name, Region", address or landmark (OpenStreetMap fallback) or lat,lon, and shown so ambiguity is visible; metric or imperial. Triggers on weather, forecast, rain, temperature, past weather on a date, air quality or pollen questions. |
| [`anki-deck`](skills/anki-deck) | Builds **Anki flashcard decks** (`.apkg`) offline with genanki from Markdown (Q:/A: blocks, headings or cloze paragraphs), CSV/TSV or JSON: basic, reversed and **cloze** notes, subdecks, tags, images and audio, MathJax math. Deck, note type and note ids are **derived from names**, so importing a rebuilt deck **updates the cards already in Anki** instead of duplicating them (checked against Anki's own library); `--dry-run` shows the parsed cards, `inspect` lists what an `.apkg` contains, and SKILL.md carries the rules for writing good cards. Triggers when the user wants flashcards, an Anki or AnkiDroid deck or cloze cards from notes, a transcript, an article or a vocabulary list. |
| [`wiki-lookup`](skills/wiki-lookup) | Reads **Wikipedia in any language and Wikidata** without a key through the official REST and Action APIs, with a User-Agent Wikimedia accepts (generic fetchers get HTTP 403; standard library only): title search, an article's lead with its **revision id and permalink** for citing, full text as Markdown or **one section**, the infobox, outgoing links, **disambiguation options**, and **Wikidata facts** (dates with their precision, quantities with units, coordinates, best-ranked statements) with the article's titles in other languages. Triggers when the user asks what Wikipedia says, wants a fact, date or number from Wikipedia or Wikidata, gives a wikipedia.org link, or needs an article in another language. |
| [`scholar-search`](skills/scholar-search) | Searches **scholarly literature without a key** across arXiv, Crossref, Europe PMC, PubMed and bioRxiv/medRxiv (OpenAlex and Semantic Scholar with the user's keys): title, authors, year, venue, DOI/arXiv id/PMID, abstract, **citation count** and **open-access link** (Unpaywall with an email), one record per work across sources, filtered by date and sorted by relevance, date or citations; full records and **BibTeX** for given DOIs, arXiv ids or PMIDs; marks papers already in the user's **Zotero**. Triggers when the user asks for papers, studies or prior work on a topic, recent preprints, the most cited papers, a free PDF, or BibTeX for a DOI. |
| [`cite-check`](skills/cite-check) | Verifies **references and citations** without a key against doi.org (Crossref, DataCite and other agencies), Crossref search, arXiv, PubMed and Europe PMC (OpenAlex and Semantic Scholar with the user's keys): for every entry of a **BibTeX file, a text or Markdown reference list, a PDF's bibliography** or a single DOI/title it says **verified, mismatch** (which of title, authors, year, venue, DOI differ), **not found** or unverifiable, flags DOIs that point to a different paper and suggests the right one, adds Unpaywall open-access links, writes an annotated `.bib`, and resumes long lists. Triggers when the user wants a bibliography checked, asks whether a paper or DOI is real, or suspects made-up references. |
| [`human-writing`](skills/human-writing) | Writes, edits and reviews **English articles, posts and technical documentation** (LinkedIn, dev.to, Hashnode, Medium, how-tos, README, tutorials, reference, ADRs, changelogs) so they read as written by a person, not a chat model. Interviews the author for real material, drafts in their voice from a **voice profile**, keeps a **facts ledger** so nothing is invented, lints offline for AI tells (`scripts/lint_prose.py`, standard library only, per-genre rules and budgets), and before publishing runs a **panel of isolated reviewer sub-agents** (cold reader, hostile expert, fact checker, slop hunter, publication safety, voice keeper, platform editor, newcomer tester, source checker), then merges their findings. Triggers when the user asks to write, tighten or review a post, article or doc, to make text sound less like AI, or to check a text before publishing. |

They chain naturally: search → pick a video by date/views/length → transcript →
summary (or `transcribe-audio` when there are no captions, and `youtube-audio` for the sound itself);
`podcast-search` → episode audio URL → `transcribe-audio` does the same for podcasts; `telegram-channel --links` → `web-extract` reads the articles a channel shares; a dead link → `wayback check` → `get` the capture closest to the date it was cited; the links in a video's `--details` description go to `web-extract`;
`web-crawl --map` shows a site's structure, then `web-crawl --include` collects
the right section through `web-extract`'s engine.
`human-writing` uses `web-search` and `web-extract` to research and read sources (every fact goes into its ledger with the URL),
and `youtube-transcript` or `transcribe-audio` to turn a talk or voice memo into material for an article.
`human-writing` writes the text and `pdf-typeset` sets it as a report, letter or one-pager PDF. `scholar-search` finds the papers to read and cite, and `cite-check` verifies the reference list before either goes out.
`anki-deck` turns a transcript, an article or papers into flashcards to remember them.

`human-writing`'s catalogue of AI tells is written for this repo but builds on
[blader/humanizer](https://github.com/blader/humanizer), [hardikpandya/stop-slop](https://github.com/hardikpandya/stop-slop),
[petergyang/no-ai-slop](https://github.com/petergyang/no-ai-slop), [sergebulaev/linkedin-skills](https://github.com/sergebulaev/linkedin-skills)
and [eric-tramel/slop-guard](https://github.com/eric-tramel/slop-guard) (all MIT), and paraphrases Wikipedia's
[Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing) (CC BY-SA). Its human-text test corpus is
public-domain 18F writing (CC0).

`pdf-typeset` bundles [Source Sans 3](https://github.com/adobe-fonts/source-sans) (SIL Open Font License 1.1,
licence in `skills/pdf-typeset/fonts/source-sans-3/LICENSE.md`) and uses the fonts built into Typst
(Libertinus Serif, New Computer Modern, DejaVu Sans Mono). Its Python dependencies are permissively licensed:
`typst` (Apache-2.0), `markdown-it-py` and `mdit-py-plugins` (MIT), PyYAML (MIT), `pypdf` (BSD-3-Clause),
`pypdfium2` (Apache-2.0 / BSD-3-Clause) and Pillow (MIT-CMU).

## Source policies

Scripts call free public endpoints from the user's own machine. Those that call an endpoint directly
(`podcast-search`, `telegram-channel`, `wiki-lookup`, `weather`, `hackernews`) identify themselves with a `zedxter-skills-<skill>` User-Agent that links
to this repository, and they stay polite on purpose:

- **Apple iTunes Search API** (`podcast-search`): about 20 requests a minute per IP; the script throttles
  itself across runs.
- **Internet Archive** (`wayback`): the Availability, CDX and Save Page Now endpoints rate-limit early and
  unannounced. The script keeps to about one request a second across runs, honours `Retry-After`, retries
  once and then stops. It saves a page only when asked, because a save publishes a public copy.
- **Scholarly APIs** (`cite-check`, `scholar-search`): Crossref (polite pool with the user's email), arXiv (one
  request every 3 s, as its terms ask; `scholar-search` keeps that spacing across runs on the machine), NCBI
  E-utilities (3 per second without a key), Europe PMC, doi.org and Unpaywall (needs a real email). Requests are
  spaced per host and carry the user's email when given.
- **Wikimedia** (`wiki-lookup`): the REST and Action APIs of Wikipedia and Wikidata, one request at a time,
  with the User-Agent the [policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy)
  asks for. The script backs off once on 429 or 503 and then stops. Text is CC BY-SA 4.0, and Wikidata is CC0.
- **Open-Meteo** (`weather`): the free API is for **non-commercial use only** (600 calls a minute, 5,000 an hour,
  10,000 a day, 300,000 a month); its data is CC BY 4.0, and the output credits it. A commercial user sets `OPEN_METEO_API_KEY`.
  Addresses and landmarks fall back to OpenStreetMap Nominatim: one request per lookup, within its 1-a-second
  policy, credited as ODbL.
- **Hacker News** (`hackernews`): the official Firebase API and Algolia HN Search, both key-less. HN Search allows
  10,000 requests an hour per IP; the script spaces them 0.4 s apart. Lists read at most 8 items in parallel
  from Firebase.
- **Telegram web preview** (`telegram-channel`): `t.me/s/<channel>` is a public HTML page, not an API.
  Telegram publishes no terms for reading it automatically, so the script reads public channels only,
  spaces its requests 1.5 s apart and never downloads media. It cannot see private channels, groups,
  comments or member lists, and must not be used to try.

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
test/                   # pytest suites for the scripts, evals, and ops.py (validation, tests, live probes)
.github/workflows/      # CI on every pull request; a weekly liveness probe of every source
```

`python3 test/ops.py test` runs every test suite with exactly the dependencies
CI installs, one suite at a time (`python3 test/ops.py test weather`). Browser
tests skip when Playwright's Chromium is not installed, and speech tests skip
until the 78 MB `tiny` Whisper model is cached
(`uv run skills/transcribe-audio/scripts/transcribe.py test/data/jfk.ogg -m tiny --download-model`).

`web-crawl`, `web-scrape`, `web-search --read` and `web-browse read` import
`web-extract`'s script at run time, so install the web skills together (the
install commands above copy every skill). `web-browse` and `web-extract
--render` need Playwright's Chromium once: `uv run --with playwright playwright
install chromium-headless-shell`.

`SKILL.md` follows the [Agent Skills specification](https://agentskills.io/specification):
`name` matches the folder name, `description` says both what the skill does and
when to use it, and the body stays short enough to be read in one go.

## Contributing

Issues and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) has the rules:
- what a skill folder holds;
- the description as a routing rule;
- exit codes;
- agent neutrality;
- permissive dependencies only;
- source etiquette;
- the checks to run before a pull request (`python3 test/ops.py validate`).

## License

[MIT](LICENSE)
