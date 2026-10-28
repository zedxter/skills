#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["youtube-transcript-api>=1.0"]
# ///
"""Fetch a YouTube video's transcript (subtitles) and print it as text, JSON or SRT.

Uses the free, key-less youtube-transcript-api library. Works with manual and
auto-generated captions and can translate them on the fly.

Run with uv (installs the dependency automatically):
    uv run transcript.py <url-or-id> [options]
Or with plain Python after `pip install youtube-transcript-api`:
    python3 transcript.py <url-or-id> [options]

Exit codes:
    0 ok · 2 bad input / missing dependency · 3 no transcript (disabled, missing,
    untranslatable) · 4 blocked by YouTube · 5 video unavailable · 6 network error
    · 7 other YouTube error
"""

import argparse
import json
import re
import sys
from collections.abc import Iterable
from urllib.parse import parse_qs, urlparse

try:
    from youtube_transcript_api import (
        AgeRestricted,
        CouldNotRetrieveTranscript,
        NoTranscriptFound,
        NotTranslatable,
        RequestBlocked,
        TranscriptsDisabled,
        TranslationLanguageNotAvailable,
        VideoUnavailable,
        VideoUnplayable,
        YouTubeTranscriptApi,
    )
    from youtube_transcript_api.proxies import GenericProxyConfig
except ImportError:
    sys.stderr.write(
        "Missing dependency: youtube-transcript-api\n"
        "Install it with:  pip install youtube-transcript-api\n"
        "or run this script with uv:  uv run transcript.py ...\n"
    )
    sys.exit(2)

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_NO_TRANSCRIPT = 3
EXIT_BLOCKED = 4
EXIT_UNAVAILABLE = 5
EXIT_NETWORK = 6
EXIT_OTHER = 7

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extract_video_id(value: str) -> str:
    """Accept a bare 11-char id or any common YouTube URL form."""
    value = value.strip()
    if VIDEO_ID_RE.match(value):
        return value
    if "//" not in value:
        value = "https://" + value
    url = urlparse(value)
    host = (url.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host == "youtu.be":
        candidate = url.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com", "youtube-nocookie.com"):
        qs = parse_qs(url.query)
        if "v" in qs:
            candidate = qs["v"][0]
        else:
            parts = [p for p in url.path.split("/") if p]
            # /shorts/<id>, /embed/<id>, /live/<id>, /v/<id>
            candidate = (
                parts[1]
                if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v")
                else ""
            )
    else:
        candidate = ""
    if not VIDEO_ID_RE.match(candidate or ""):
        raise ValueError(f"Could not extract a YouTube video id from: {value}")
    return candidate


def fmt_time(seconds: float, srt: bool = False) -> str:
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if srt:
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def clean_text(text: str) -> str:
    text = text.replace("\n", " ")
    text = re.sub(
        r"\[(music|applause|laughter|музыка|аплодисменты|смех|[♪\s]+)\]",
        "",
        text,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", text).strip()


def to_paragraphs(snippets: Iterable, every_seconds: float) -> list[str]:
    """Join caption fragments into paragraphs of roughly `every_seconds` length.

    A paragraph ends at the first sentence boundary after `every_seconds`, or
    unconditionally after 3x that (auto-captions often have no punctuation).
    """
    paragraphs: list[str] = []
    current: list[str] = []
    para_start: float | None = None
    for sn in snippets:
        text = clean_text(sn.text)
        if not text:
            continue
        if para_start is None:
            para_start = sn.start
        current.append(text)
        elapsed = sn.start - para_start
        if (elapsed >= every_seconds and re.search(r"[.!?…]$", text)) or (
            elapsed >= every_seconds * 3
        ):
            paragraphs.append(" ".join(current))
            current, para_start = [], None
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def render(transcript, fmt: str, paragraph_seconds: float, with_header: bool) -> str:
    snippets = list(transcript.snippets)
    header = ""
    if with_header and fmt in ("text", "timestamps"):
        kind = "auto-generated" if transcript.is_generated else "manual"
        header = (
            f"# video: {transcript.video_id}\n"
            f"# language: {transcript.language} ({transcript.language_code}, {kind})\n\n"
        )
    if fmt == "text":
        return header + "\n\n".join(to_paragraphs(snippets, paragraph_seconds)) + "\n"
    if fmt == "timestamps":
        lines = [
            f"[{fmt_time(sn.start)}] {clean_text(sn.text)}"
            for sn in snippets
            if clean_text(sn.text)
        ]
        return header + "\n".join(lines) + "\n"
    if fmt == "srt":
        out = []
        for i, sn in enumerate(snippets, 1):
            out.append(
                f"{i}\n{fmt_time(sn.start, True)} --> {fmt_time(sn.start + sn.duration, True)}\n{sn.text}\n"
            )
        return "\n".join(out)
    if fmt == "json":
        return json.dumps(
            {
                "video_id": transcript.video_id,
                "language": transcript.language,
                "language_code": transcript.language_code,
                "is_generated": transcript.is_generated,
                "snippets": [
                    {"text": sn.text, "start": sn.start, "duration": sn.duration}
                    for sn in snippets
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    raise ValueError(fmt)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("video", help="YouTube URL or 11-character video id")
    p.add_argument(
        "-l",
        "--lang",
        nargs="+",
        default=["ru", "en"],
        help="preferred transcript languages, in order (default: ru en). "
        "Falls back to any available transcript.",
    )
    p.add_argument(
        "-t",
        "--translate",
        metavar="LANG",
        help="translate the transcript into this language code (e.g. ru, en)",
    )
    p.add_argument(
        "-f",
        "--format",
        choices=["text", "timestamps", "srt", "json"],
        default="text",
        help="output format (default: text, joined into paragraphs)",
    )
    p.add_argument(
        "--paragraph-seconds",
        type=float,
        default=60,
        help="target paragraph length in seconds for text format (default: 60)",
    )
    p.add_argument(
        "--no-header", action="store_true", help="omit the video/language header"
    )
    p.add_argument(
        "--list", action="store_true", help="list available transcripts and exit"
    )
    p.add_argument("-o", "--out", help="write to this file instead of stdout")
    p.add_argument(
        "--proxy", help="HTTP(S) proxy URL, useful when YouTube blocks your IP"
    )
    args = p.parse_args(argv)

    try:
        video_id = extract_video_id(args.video)
    except ValueError as e:
        sys.stderr.write(f"error: {e}\n")
        return EXIT_BAD_INPUT

    proxy = (
        GenericProxyConfig(http_url=args.proxy, https_url=args.proxy)
        if args.proxy
        else None
    )
    api = YouTubeTranscriptApi(proxy_config=proxy)

    try:
        transcripts = api.list(video_id)
        if args.list:
            for tr in transcripts:
                kind = "auto" if tr.is_generated else "manual"
                translatable = "translatable" if tr.is_translatable else ""
                print(f"{tr.language_code:8} {tr.language:30} {kind:7} {translatable}")
            return EXIT_OK
        try:
            transcript = transcripts.find_transcript(args.lang)
        except NoTranscriptFound:
            # Fall back to whatever exists, preferring manual captions.
            available = sorted(transcripts, key=lambda t: t.is_generated)
            if not available:
                raise
            transcript = available[0]
            sys.stderr.write(
                f"note: no transcript in {args.lang}; using {transcript.language_code} "
                f"({'auto' if transcript.is_generated else 'manual'})\n"
            )
        if args.translate and transcript.language_code.split("-")[0] != args.translate:
            transcript = transcript.translate(args.translate)
        fetched = transcript.fetch()
    except TranscriptsDisabled:
        sys.stderr.write(f"error: subtitles are disabled for video {video_id}\n")
        return EXIT_NO_TRANSCRIPT
    except NoTranscriptFound:
        sys.stderr.write(f"error: no transcript available for video {video_id}\n")
        return EXIT_NO_TRANSCRIPT
    except (NotTranslatable, TranslationLanguageNotAvailable):
        sys.stderr.write(
            f"error: transcript cannot be translated into '{args.translate}'. "
            "Run with --list to see available transcripts.\n"
        )
        return EXIT_NO_TRANSCRIPT
    except AgeRestricted:
        sys.stderr.write(
            f"error: video {video_id} is age-restricted; transcripts need a signed-in session, "
            "which this tool does not support\n"
        )
        return EXIT_UNAVAILABLE
    except (VideoUnavailable, VideoUnplayable):
        sys.stderr.write(
            f"error: video {video_id} is unavailable (private, removed, region-locked or unplayable)\n"
        )
        return EXIT_UNAVAILABLE
    except RequestBlocked:
        sys.stderr.write(
            "error: YouTube blocked the request (this happens on cloud/datacenter IPs or after many requests).\n"
            "Try again later, from a residential network, or pass --proxy http://user:pass@host:port\n"
        )
        return EXIT_BLOCKED
    except CouldNotRetrieveTranscript as e:
        sys.stderr.write(f"error: could not retrieve transcript: {e}\n")
        return EXIT_OTHER
    except OSError as e:  # network errors (requests exceptions subclass OSError)
        sys.stderr.write(f"error: network failure while contacting YouTube: {e}\n")
        return EXIT_NETWORK

    output = render(fetched, args.format, args.paragraph_seconds, not args.no_header)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(output)
        sys.stderr.write(f"wrote {args.out}\n")
    else:
        sys.stdout.write(output)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
