---
name: youtube-transcript
description: Fetch the transcript (subtitles/captions) of a YouTube video as plain text, timestamped lines, SRT or JSON using the free, key-less youtube-transcript-api library. Use whenever the user shares a YouTube link or video id and wants the transcript, a summary, notes, key takeaways, quotes, a translation, or any analysis of what is said in the video.
license: MIT
compatibility: Python 3.12+ with uv (preferred) or pip. Needs network access to youtube.com; requests from cloud/datacenter IPs are frequently blocked by YouTube (use --proxy).
metadata:
  author: zedxter
  version: "1.0.0"
  source: https://github.com/zedxter/skills
---

# YouTube Transcript

Get the text of a YouTube video without downloading audio, without API keys and
without paid services. The script reads the video's existing captions (manual or
auto-generated) via the `youtube-transcript-api` Python library.

## When to use

Trigger this skill when the user:

- pastes a YouTube URL (`youtube.com/watch?v=…`, `youtu.be/…`, `/shorts/…`,
  `/live/…`, `/embed/…`) or a bare 11-character video id, and
- asks for the transcript, subtitles, "what does this video say", a summary,
  recap, notes/outline, key points, quotes, timestamps of a topic, a translation,
  or wants to answer questions about the video's content.

Do **not** use it for playlists, channels, comments, view counts or video
metadata (title, description) — the script only returns spoken text.

## Quick start

The script lives at `scripts/transcript.py` relative to this skill. Run it with
`uv` (installs the dependency into a cached environment automatically):

```bash
uv run scripts/transcript.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

Without `uv`:

```bash
pip install youtube-transcript-api
python3 scripts/transcript.py dQw4w9WgXcQ
```

Default behaviour: prefer Russian, then English captions (`--lang ru en`); if
neither exists, fall back to any available transcript (manual before auto) and
print a `note:` on stderr. Never pass `--translate` — fetch the original and
translate in your reply (see workflow step 4). Output is plain text joined into ~60-second
paragraphs, prefixed by a two-line header with the video id and the language used.

## Options

| Flag | Meaning |
|------|---------|
| `-l, --lang ru en …` | Preferred caption languages, in order. Falls back to anything available. |
| `-t, --translate LANG` | **Do not use.** Asks YouTube to machine-translate the captions; YouTube blocks this endpoint far more aggressively than plain caption fetches, so it fails with exit 4 where a normal fetch succeeds. Kept only for manual experiments. |
| `-f, --format text\|timestamps\|srt\|json` | `text` (paragraphs, default), `timestamps` (`[mm:ss] line`), `srt` (subtitle file), `json` (raw snippets with start/duration). |
| `--paragraph-seconds N` | Target paragraph length for `text` format (default 60). |
| `--no-header` | Omit the `# video / # language` header. |
| `--list` | Show available transcripts (language, manual/auto, translatable) and exit. |
| `-o, --out FILE` | Write to a file instead of stdout. |
| `--proxy URL` | HTTP(S) proxy, e.g. `http://user:pass@host:port`, when YouTube blocks the IP. |

## Recommended workflow

1. **Fetch once, save to a file.** Transcripts of long videos are large; keep
   them out of the conversation and read selectively:
   ```bash
   uv run scripts/transcript.py <url> -o /tmp/transcript.txt
   wc -w /tmp/transcript.txt
   ```
2. **Pick the format for the task.**
   - Summary, notes, Q&A → `text` (default).
   - "Where does he talk about X?", chapter markers, quoting with time → `timestamps`.
   - Subtitle file for the user → `srt`.
   - Programmatic processing → `json`.
3. **Mind the size.** A 10-minute talk is ~1,500 words; a 2-hour podcast is
   ~20,000 words. For long videos read the file in chunks (`sed -n`, `head`),
   summarise chunk by chunk, then merge — do not paste the whole file into the
   reply.
4. **Translate yourself, never with `--translate`.** Always fetch the
   original-language captions and do the translation in your own reply. If the
   user wants the transcript in another language (e.g. an English video, text
   in Russian), fetch the English captions to a file, then translate the file
   chunk by chunk — do not call the script with `-t`. Reasons: YouTube blocks
   the translation endpoint even when ordinary fetches work, and its machine
   translation is worse than yours. Reply in the user's language regardless of
   the caption language.
5. **Auto-captions are noisy.** No punctuation, wrong homophones, mangled
   names and numbers. The header says `auto-generated` when that is the case —
   mention it when quoting precisely, and sanity-check names/figures.
6. **Cite timestamps** when the user may want to jump to a moment:
   `https://youtu.be/<id>?t=<seconds>`.

## Errors and exit codes

The script prints a one-line `error:` on stderr and exits non-zero. Act on the
code rather than guessing:

| Exit | Meaning | What to do |
|------|---------|------------|
| 2 | Bad input (not a YouTube URL/id) or missing dependency | Ask for a correct link; or install `youtube-transcript-api` / use `uv run`. |
| 3 | No transcript: captions disabled, none in any language, or not translatable | Run `--list` to show what exists. If nothing exists, tell the user the video has no captions. Offer the Whisper fallback (below). |
| 4 | Blocked by YouTube (`RequestBlocked` / `IpBlocked`) | If you used `--translate`, drop it and fetch the original captions instead — that is the usual cause. Otherwise typical on cloud/datacenter IPs or after many rapid requests: wait a minute and retry once; then suggest `--proxy` with a residential proxy or running from the user's own machine. Do not hammer with retries — it extends the block. |
| 5 | Video unavailable: private, deleted, region-locked, age-restricted | Nothing to fetch; report it. Age-restricted videos require a signed-in session, which this tool does not support. |
| 6 | Network failure | Check connectivity and retry. |
| 7 | Other YouTube error (unparsable page, consent cookie, etc.) | Usually transient or a YouTube-side change; retry, then report with the message. Upgrading `youtube-transcript-api` often fixes parser breakage. |

Note that `--list` can succeed while the actual fetch is blocked (the two use
different YouTube endpoints), so a successful listing does not guarantee the
transcript is retrievable from the current IP.

## No captions? Whisper fallback (not included)

If a video genuinely has no captions (exit 3 and `--list` is empty), the only
way to get a transcript is speech-to-text: download the audio with `yt-dlp`
and transcribe it with Whisper (`openai-whisper`, `faster-whisper`, or the
OpenAI/Groq audio API). This is **outside this skill** — it needs a large model
download or a paid API, takes minutes for long videos and requires `ffmpeg`.
Offer it as an option and let the user decide before proceeding.

## Limitations

- Only existing captions: no captions → no transcript.
- Timestamps are caption cue starts, not word-level.
- Speaker labels are not available.
- YouTube rate-limits and blocks datacenter IPs aggressively; this is a
  YouTube policy, not a bug in the script.
