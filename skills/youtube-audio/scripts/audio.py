#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["yt-dlp[default]>=2025.10.22", "mutagen>=1.47"]
# ///
"""Save the audio track of a YouTube video (or playlist) as m4a/opus, or convert it to mp3.

Uses yt-dlp (free, key-less). By default the original audio stream is kept as is
(no re-encoding, no quality loss); --mp3 / --format convert with ffmpeg. Title,
channel, date, chapters and cover art are embedded; --start/--end cut a section;
--split-chapters writes one file per chapter.

    uv run audio.py URL                       # original audio (usually .m4a), into the current directory
    uv run audio.py URL --mp3 -o music/       # mp3 (VBR ~190 kbps)
    uv run audio.py URL --mp3 --bitrate 320
    uv run audio.py URL --start 12:30 --end 45:00
    uv run audio.py URL --split-chapters
    uv run audio.py PLAYLIST_URL --max-items 10
    uv run audio.py URL --info                # metadata only, nothing downloaded

Exit codes:
    0 ok · 2 bad input / missing dependency (ffmpeg) · 3 no audio to save (live not
    started, DRM, no audio format) · 4 blocked by YouTube (bot check, rate limit,
    geo-block) · 5 video unavailable (private, removed, members-only, age-restricted)
    · 6 network error · 7 other
"""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

EXIT_OK, EXIT_BAD_INPUT, EXIT_NO_AUDIO, EXIT_BLOCKED, EXIT_UNAVAILABLE, EXIT_NETWORK, EXIT_OTHER = 0, 2, 3, 4, 5, 6, 7

try:
    import yt_dlp
    from yt_dlp.postprocessor.common import PostProcessor
    from yt_dlp.utils import DownloadError, PostProcessingError
except ImportError:
    sys.stderr.write("error: missing dependency yt-dlp; run with `uv run audio.py …` or `pip install 'yt-dlp[default]' mutagen`\n")
    sys.exit(EXIT_BAD_INPUT)

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
FORMATS = {"original", "m4a", "opus", "mp3", "flac", "wav"}
LOSSY = {"mp3", "m4a", "opus"}
DEFAULT_MAX_ITEMS = 25
FFMPEG_HINT = ("ffmpeg is required for this (conversion, cutting, chapters, cover art); install it: "
               "macOS `brew install ffmpeg`, Debian/Ubuntu `sudo apt install ffmpeg`, Windows `winget install ffmpeg`")
UPDATE_HINT = "YouTube changes often; update yt-dlp (uv run picks the newest allowed release; with pip: pip install -U 'yt-dlp[default]')"

# Ordered (pattern, exit code, kind, hint). First match wins; matched against yt-dlp's error text.
ERROR_RULES: list[tuple[str, int, str, str]] = [
    (r"confirm (?:you(?:'|’)re|you are) not a bot", EXIT_BLOCKED, "bot_check",
     "YouTube asks this IP to sign in; pass --cookies-from-browser BROWSER (the user's own logged-in browser) or --proxy, "
     "or try again later from another network. Do not retry in a loop."),
    (r"http error 429|too many requests|rate.?limit", EXIT_BLOCKED, "rate_limited", "rate-limited; wait several minutes before retrying"),
    (r"available in your country|geo.?restrict|blocked it in your country|not available in your region", EXIT_BLOCKED, "geo_blocked",
     "geo-restricted; only a --proxy in an allowed country helps"),
    (r"confirm your age|age.?restricted|inappropriate for some users", EXIT_UNAVAILABLE, "age_restricted",
     "age-restricted; needs --cookies-from-browser with a signed-in adult account"),
    (r"private video|this video is private", EXIT_UNAVAILABLE, "private", "the video is private"),
    (r"only works when logged.?in|log ?in to (?:view|watch)|login required|requires? (?:a )?(?:log ?in|sign.?in)|"
     r"sign in to (?:view|watch)", EXIT_UNAVAILABLE, "login_required",
     "the site requires a signed-in account; pass --cookies-from-browser BROWSER with the user's own session"),
    (r"members.?only|join this channel|available to this channel's members", EXIT_UNAVAILABLE, "members_only",
     "members-only; needs --cookies-from-browser with a member account"),
    (r"has been removed|account associated with this video has been terminated|no longer available|video unavailable|"
     r"this video is unavailable|does not exist", EXIT_UNAVAILABLE, "unavailable", "the video was removed or never existed"),
    (r"live event will begin|premieres? in|is upcoming|this live event", EXIT_NO_AUDIO, "not_started",
     "the live stream/premiere has not started yet"),
    (r"drm|requested format is not available|no video formats found|no audio", EXIT_NO_AUDIO, "no_audio",
     "no downloadable audio stream (DRM or no audio format); " + UPDATE_HINT),
    (r"ffmpeg.*not found|ffprobe.*not found|ffmpeg is not installed", EXIT_BAD_INPUT, "ffmpeg_missing", FFMPEG_HINT),
    (r"unsupported url|is not a valid url", EXIT_BAD_INPUT, "bad_url", "not a URL yt-dlp can handle"),
    (r"unable to download (?:webpage|api page|json)|timed out|name or service not known|getaddrinfo|"
     r"temporary failure in name resolution|connection (?:reset|refused|aborted)|network is unreachable|"
     r"ssl|certificate|remote end closed", EXIT_NETWORK, "network", "check the connection or --proxy; one retry later is fine"),
    (r"signature|nsig|player.*(?:extract|decipher)|n challenge|js runtime", EXIT_OTHER, "extractor_outdated", UPDATE_HINT),
]


def classify_error(message: str) -> tuple[int, str, str]:
    """Map a yt-dlp error message to (exit code, kind, hint).

    >>> classify_error("ERROR: [youtube] x: Sign in to confirm you’re not a bot.")[:2]
    (4, 'bot_check')
    >>> classify_error("ERROR: [youtube] x: Private video. Sign in if you've been granted access")[:2]
    (5, 'private')
    >>> classify_error("ERROR: [youtube] x: Sign in to confirm your age")[:2]
    (5, 'age_restricted')
    >>> classify_error("ERROR: [youtube] x: This live event will begin in 3 hours.")[:2]
    (3, 'not_started')
    >>> classify_error("ERROR: Unable to download webpage: <urlopen error [Errno -2] Name or service not known>")[:2]
    (6, 'network')
    >>> classify_error("something odd")[:2]
    (7, 'other')
    """
    text = message.lower()
    for pattern, code, kind, hint in ERROR_RULES:
        if re.search(pattern, text):
            return code, kind, hint
    return EXIT_OTHER, "other", UPDATE_HINT


def normalize_target(arg: str) -> str | None:
    """Accept a URL (any site yt-dlp supports, YouTube first) or a bare 11-char video id; None if unusable.

    >>> normalize_target("dQw4w9WgXcQ")
    'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
    >>> normalize_target("-abcdefghij")  # ids may start with '-'
    'https://www.youtube.com/watch?v=-abcdefghij'
    >>> normalize_target("youtu.be/dQw4w9WgXcQ?t=42")
    'https://youtu.be/dQw4w9WgXcQ?t=42'
    >>> normalize_target("https://www.youtube.com/playlist?list=PL123")
    'https://www.youtube.com/playlist?list=PL123'
    >>> [normalize_target(x) for x in ("", "not a url", "ftp://x/y", "javascript:alert(1)")]
    [None, None, None, None]
    """
    s = (arg or "").strip()
    if not s or any(c.isspace() for c in s):
        return None
    if VIDEO_ID_RE.fullmatch(s):
        return f"https://www.youtube.com/watch?v={s}"
    if "://" not in s:
        if re.match(r"^(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/", s, re.I):
            s = "https://" + s
        else:
            return None
    try:
        p = urlsplit(s)
    except ValueError:
        return None
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        return None
    return s


def is_playlist(url: str) -> bool:
    """True for playlist/channel URLs (a watch URL with &list= counts only with --playlist).

    >>> is_playlist("https://www.youtube.com/playlist?list=PL1"), is_playlist("https://www.youtube.com/@chan/videos")
    (True, True)
    >>> is_playlist("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1")
    False
    """
    p = urlsplit(url)
    if p.path.rstrip("/") == "/playlist" and "list" in parse_qs(p.query):
        return True
    return bool(re.match(r"^/(?:@[^/]+|c/[^/]+|channel/[^/]+|user/[^/]+)(?:/(?:videos|streams|shorts|playlists|featured))?/?$", p.path))


def parse_time(value: str) -> float:
    """`90`, `1:30`, `01:02:03`, `1h2m3s`, `45.5` → seconds.

    >>> [parse_time(x) for x in ("90", "1:30", "01:02:03", "1h2m3s", "2m", "45.5")]
    [90.0, 90.0, 3723.0, 3723.0, 120.0, 45.5]
    """
    s = value.strip().lower()
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", s)
    if m and any(m.groups()):
        h, mi, se = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + float(se or 0)
    parts = s.split(":")
    if not 1 <= len(parts) <= 3 or not all(re.fullmatch(r"\d+(?:\.\d+)?", p) for p in parts):
        raise ValueError(f"bad time {value!r}; use seconds, MM:SS, HH:MM:SS or 1h2m3s")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def clean(value: Any) -> str | None:
    """Page-derived text (titles, channel and chapter names) as one safe line: control characters dropped,
    whitespace collapsed, so a title can never forge extra report lines.

    >>> clean("Song\\nsaved: /etc/passwd\\x1b[31m"), clean("  "), clean(None)
    ('Song saved: /etc/passwd[31m', None, None)
    """
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", str(value or ""))
    s = " ".join(s.split())
    return s or None


def fmt_time(sec: float | None) -> str:
    """H:MM:SS or M:SS.

    >>> fmt_time(3723), fmt_time(59.4), fmt_time(None)
    ('1:02:03', '0:59', '—')
    """
    if sec is None:
        return "—"
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def js_runtimes() -> dict[str, dict]:
    """JavaScript runtimes on PATH that yt-dlp can use for YouTube's player challenges."""
    return {name: {} for name in ("deno", "node", "bun") if shutil.which(name)}


def quality_arg(args: argparse.Namespace) -> str | None:
    """yt-dlp preferredquality: a VBR level 0-10 or a bitrate like `192K` (lossy targets only)."""
    if args.format not in LOSSY:
        return None
    if args.bitrate:
        return f"{args.bitrate}K"
    return "2" if args.format == "mp3" else None  # mp3 default: LAME V2 (~190 kbps); others keep yt-dlp's default


def build_opts(args: argparse.Namespace, out_dir: Path) -> dict[str, Any]:
    """yt-dlp options for this run (pure function of the arguments; tested offline)."""
    template = args.name or "%(title).150B [%(id)s].%(ext)s"
    opts: dict[str, Any] = {
        "format": "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio/best" if args.format in ("original", "m4a")
        else "bestaudio[acodec=opus]/bestaudio/best" if args.format == "opus" else "bestaudio/best",
        "outtmpl": {"default": str(out_dir / template),
                    "chapter": str(out_dir / "%(title).120B [%(id)s]" / "%(section_number)02d - %(section_title).100B.%(ext)s"),
                    "thumbnail": str(out_dir / template)},
        "windowsfilenames": True,
        "noplaylist": not args.playlist,
        "playlist_items": args.items,
        "playlistend": None if args.items else args.max_items,
        "quiet": True, "no_warnings": True, "noprogress": True,
        "socket_timeout": args.timeout, "retries": 3, "fragment_retries": 3,
        "overwrites": args.overwrite, "continuedl": True,
        "writethumbnail": not args.no_cover,
        "postprocessors": [],
        "postprocessor_args": {},
        "logger": QuietLogger(),
    }
    if args.cookies_from_browser:
        browser, _, profile = args.cookies_from_browser.partition(":")
        opts["cookiesfrombrowser"] = (browser.lower(), profile or None, None, None)
    if args.cookies:
        opts["cookiefile"] = args.cookies
    if args.proxy:
        opts["proxy"] = args.proxy
    runtimes = js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes
    # Order matters: convert → (cut) → split chapters (needs the bare audio stream) → tags → cover art.
    # --start/--end are applied by TrimPP (added in make_ydl) after the conversion: the whole audio
    # track is downloaded and cut locally, which works for every format and protocol.
    pps = opts["postprocessors"]
    if args.format == "original":
        pps.append({"key": "FFmpegExtractAudio", "preferredcodec": "best", "nopostoverwrites": False})
    else:
        pp = {"key": "FFmpegExtractAudio", "preferredcodec": args.format, "nopostoverwrites": False}
        if quality_arg(args):
            pp["preferredquality"] = quality_arg(args)
        pps.append(pp)
    if args.split_chapters:
        pps.append({"key": "FFmpegSplitChapters", "force_keyframes": False})
    if not args.no_metadata:
        # chapter marks are meaningless in a cut section or in per-chapter files
        pps.append({"key": "FFmpegMetadata", "add_metadata": True,
                    "add_chapters": args.start is None and args.end is None})
    if not args.no_cover:
        pps.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
        pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
    return opts


class TrimPP(PostProcessor):
    """Cut the saved audio to [start, end) with ffmpeg stream copy (no re-encoding; frame-accurate, ~20 ms)."""

    def __init__(self, downloader: Any, start: float | None, end: float | None) -> None:
        super().__init__(downloader)
        self.start, self.end = start, end

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        import subprocess
        src = Path(info["filepath"])
        tmp = src.with_name(src.stem + ".cut" + src.suffix)
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src)]
        if self.start:
            cmd += ["-ss", f"{self.start:.3f}"]
        if self.end is not None:
            cmd += ["-to", f"{self.end:.3f}"]
        cmd += ["-map", "0:a", "-c", "copy", "-map_metadata", "0", "-map_chapters", "-1", str(tmp)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise PostProcessingError(f"cutting {fmt_time(self.start)}–{fmt_time(self.end)} failed: {(proc.stderr or '').strip()[:200]}")
        os.replace(tmp, src)
        info.pop("chapters", None)
        return [], info


class QuietLogger:
    """yt-dlp logger that swallows its own output; failures surface as DownloadError and get one `error:` line."""

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


def short_message(text: str) -> str:
    """First line of a yt-dlp error without the `ERROR:` prefix and the long FAQ tail, ≤ 240 chars.

    >>> short_message("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. This helps protect our community. Use --cookies…")
    '[youtube] abc: Sign in to confirm you’re not a bot.'
    """
    first = (text.splitlines() or [""])[0]
    first = re.sub(r"^(?:ERROR:\s*)+", "", first).strip()
    m = re.match(r"(.+?[.!?])(?:\s+(?:This helps|Use --|See |Also see|Please )|$)", first)
    return (m.group(1) if m else first)[:240]


def make_ydl(args: argparse.Namespace, out_dir: Path) -> Any:
    """A YoutubeDL with the run's options plus the section cutter right after the audio conversion."""
    ydl = yt_dlp.YoutubeDL(build_opts(args, out_dir))
    if args.start is not None or args.end is not None:
        # post_process hooks run in registration order; insert the cutter right after FFmpegExtractAudio
        chain = ydl._pps["post_process"]
        idx = next((i + 1 for i, pp in enumerate(chain) if type(pp).__name__ == "FFmpegExtractAudioPP"), 0)
        chain.insert(idx, TrimPP(ydl, args.start, args.end))
    return ydl


def section_length(start: float | None, end: float | None, duration: float | None) -> float | None:
    """Seconds kept by --start/--end (None without a section or when the end is unknown).

    >>> section_length(5, 12, 30), section_length(20, None, 30), section_length(None, 99, 30), section_length(None, None, 30)
    (7, 10, 30, None)
    """
    if start is None and end is None:
        return None
    stop = min(end, duration) if (end is not None and duration) else (end if end is not None else duration)
    return max(0, stop - (start or 0)) if stop is not None else None


def summarize(info: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """One result record from a processed yt-dlp info dict."""
    files: list[dict[str, Any]] = []
    for d in info.get("requested_downloads") or []:
        fp = d.get("filepath") or d.get("filename")
        if fp and Path(fp).exists():
            files.append({"path": str(Path(fp)), "bytes": Path(fp).stat().st_size})
    chapters_out = []
    if args.split_chapters:
        for c in info.get("chapters") or []:
            fp = c.get("filepath")
            if fp and Path(fp).exists():
                chapters_out.append({"title": clean(c.get("title")), "start": c.get("start_time"), "end": c.get("end_time"),
                                     "path": str(Path(fp)), "bytes": Path(fp).stat().st_size})
    date = info.get("upload_date")
    return {
        "ok": bool(files or chapters_out), "id": info.get("id"), "url": info.get("webpage_url") or info.get("original_url"),
        "title": clean(info.get("title")), "channel": clean(info.get("channel") or info.get("uploader")),
        "date": f"{date[:4]}-{date[4:6]}-{date[6:8]}" if date and len(date) == 8 else None,
        "duration": info.get("duration"), "section": [args.start, args.end] if (args.start is not None or args.end is not None) else None,
        "section_duration": section_length(args.start, args.end, info.get("duration")),
        "format": args.format, "codec": info.get("acodec"), "abr": info.get("abr"),
        "files": files, "chapters": chapters_out, "error": None,
    }


def info_record(info: dict[str, Any]) -> dict[str, Any]:
    """--info: metadata and the audio formats on offer, nothing downloaded."""
    audio = [f for f in info.get("formats") or [] if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")]
    date = info.get("upload_date")
    return {
        "id": info.get("id"), "url": info.get("webpage_url"), "title": clean(info.get("title")),
        "channel": clean(info.get("channel") or info.get("uploader")), "date": f"{date[:4]}-{date[4:6]}-{date[6:8]}" if date and len(date) == 8 else None,
        "duration": info.get("duration"), "is_live": info.get("is_live"), "live_status": info.get("live_status"),
        "chapters": [{"title": clean(c.get("title")), "start": c.get("start_time"), "end": c.get("end_time")} for c in info.get("chapters") or []],
        "audio_formats": [{"id": f.get("format_id"), "ext": f.get("ext"), "codec": f.get("acodec"), "abr": f.get("abr"),
                           "bytes": f.get("filesize") or f.get("filesize_approx"), "language": f.get("language")} for f in audio],
    }


def run_one(ydl: Any, url: str, args: argparse.Namespace) -> list[dict[str, Any]]:
    """Download (or describe) one URL; a playlist yields one record per entry."""
    if args.info:
        info = ydl.extract_info(url, download=False, process=True)
        entries = info.get("entries") if info.get("_type") == "playlist" else None
        return [info_record(e) for e in entries if e] if entries is not None else [info_record(info)]
    info = ydl.extract_info(url, download=True)
    entries = info.get("entries") if info.get("_type") == "playlist" else None
    if entries is not None:
        return [summarize(e, args) for e in entries if e]
    return [summarize(info, args)]


def process_info(info: dict[str, Any], args: argparse.Namespace, out_dir: Path) -> list[dict[str, Any]]:
    """Run the download pipeline on an already-extracted info dict (video or playlist); used by the tests."""
    with make_ydl(args, out_dir) as ydl:
        processed = ydl.process_ie_result(info, download=True)
    entries = processed.get("entries") if processed.get("_type") == "playlist" else None
    return [summarize(e, args) for e in entries if e] if entries is not None else [summarize(processed, args)]


def human(records: list[dict[str, Any]], args: argparse.Namespace) -> str:
    lines = []
    for r in records:
        if r.get("error"):  # failures look the same in --info and download mode
            lines.append(f"failed: {r.get('url') or r.get('id')}: {r['error']['kind']} — {r['error']['message']}")
            continue
        if args.info:
            lines.append(f"# {r['title'] or r['id']}\n# url: {r['url']}\n# channel: {r['channel'] or '—'} · date: {r['date'] or '—'} · "
                         f"duration: {fmt_time(r['duration'])}" + (f" · live: {r['live_status']}" if r.get("live_status") not in (None, "not_live") else ""))
            for c in r["chapters"]:
                lines.append(f"  chapter {fmt_time(c['start'])} {c['title']}")
            for f in r["audio_formats"]:
                size = f"{f['bytes'] / 1e6:.1f} MB" if f.get("bytes") else "? MB"
                lines.append(f"  audio {f['id']}: {f['ext']} {f['codec']} {f['abr'] or '?'} kbps {size}" + (f" [{f['language']}]" if f.get("language") else ""))
            lines.append("")
            continue
        head = f"saved: {r['title']} — {r['channel'] or '—'} ({fmt_time(r['duration'])}"
        if r["section"]:
            head += f", section {fmt_time(r['section'][0] or 0)}–{fmt_time(r['section'][1]) if r['section'][1] is not None else 'end'}"
        lines.append(head + ")")
        for f in r["files"]:
            lines.append(f"  {f['path']}  ({f['bytes'] / 1e6:.1f} MB)")
        for c in r["chapters"]:
            lines.append(f"  {fmt_time(c['start'])} {c['path']}  ({c['bytes'] / 1e6:.1f} MB)")
    return "\n".join(lines).rstrip() + "\n"


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("targets", nargs="+", metavar="URL", help="YouTube video/playlist URL or 11-char id (other yt-dlp sites work too)")
    p.add_argument("-o", "--out-dir", default=".", metavar="DIR", help="where files go (created; default: current directory)")
    p.add_argument("--format", choices=sorted(FORMATS), default="original",
                   help="original (default: keep the source stream, usually m4a/AAC, no re-encoding), m4a, opus, mp3, flac, wav")
    p.add_argument("--mp3", action="store_const", const="mp3", dest="format", help="shortcut for --format mp3")
    p.add_argument("--bitrate", type=int, choices=[64, 96, 128, 160, 192, 256, 320], help="kbps for mp3/m4a/opus (default mp3: VBR ~190)")
    p.add_argument("--start", metavar="TIME", help="cut from TIME (seconds, MM:SS, HH:MM:SS, 1h2m3s)")
    p.add_argument("--end", metavar="TIME", help="cut until TIME")
    p.add_argument("--split-chapters", action="store_true", help="also write one file per chapter into a folder named after the video")
    p.add_argument("--playlist", action="store_true", help="for a watch URL with &list=, take the whole playlist")
    p.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS, help=f"playlist/channel: at most N videos (default {DEFAULT_MAX_ITEMS})")
    p.add_argument("--items", metavar="SPEC", help="playlist items to take, e.g. 1-5,8 (overrides --max-items)")
    p.add_argument("--name", metavar="TEMPLATE", help="yt-dlp output template (default '%%(title)s [%%(id)s].%%(ext)s')")
    p.add_argument("--no-cover", action="store_true", help="do not embed the thumbnail as cover art")
    p.add_argument("--no-metadata", action="store_true", help="do not write title/artist/date/chapter tags")
    p.add_argument("--overwrite", action="store_true", help="replace existing files (default: skip videos already saved)")
    p.add_argument("--info", action="store_true", help="print metadata, chapters and audio formats; download nothing")
    p.add_argument("--cookies-from-browser", metavar="BROWSER[:PROFILE]",
                   help="use the user's own signed-in browser cookies (chrome, firefox, edge, safari, brave, …) for bot checks, age or members-only videos")
    p.add_argument("--cookies", metavar="FILE", help="Netscape cookies.txt exported by the user")
    p.add_argument("--proxy", metavar="URL", help="http(s)/socks5 proxy")
    p.add_argument("--timeout", type=float, default=30.0, metavar="S", help="network timeout per request (default 30)")
    p.add_argument("-f", "--output", choices=["text", "json"], default="text", help="report format on stdout (default text)")
    p.add_argument("-q", "--quiet", action="store_true", help="no note: lines")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = make_parser().parse_args(argv)
    note = (lambda m: None) if args.quiet else (lambda m: sys.stderr.write(f"note: {m}\n"))
    try:
        args.start = parse_time(args.start) if args.start else None
        args.end = parse_time(args.end) if args.end else None
    except ValueError as e:
        sys.stderr.write(f"error: {e}\n")
        return EXIT_BAD_INPUT
    if args.start is not None and args.end is not None and args.end <= args.start:
        sys.stderr.write("error: --end must be after --start\n")
        return EXIT_BAD_INPUT
    if args.bitrate and args.format not in LOSSY:
        sys.stderr.write(f"error: --bitrate applies to mp3, m4a and opus, not --format {args.format}\n")
        return EXIT_BAD_INPUT
    if args.split_chapters and (args.start is not None or args.end is not None):
        sys.stderr.write("error: --split-chapters cannot be combined with --start/--end\n")
        return EXIT_BAD_INPUT
    if args.max_items < 1:
        sys.stderr.write("error: --max-items must be ≥ 1\n")
        return EXIT_BAD_INPUT
    if args.cookies and not Path(args.cookies).is_file():
        sys.stderr.write(f"error: cookies file not found: {args.cookies}\n")
        return EXIT_BAD_INPUT
    targets = []
    for raw in args.targets:
        u = normalize_target(raw)
        if u is None:
            sys.stderr.write(f"error: not a URL or YouTube video id: {raw}\n")
            return EXIT_BAD_INPUT
        if is_playlist(u):
            args.playlist = True  # playlist and channel URLs always expand (bounded by --max-items)
        targets.append(u)
    if not args.info and not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        sys.stderr.write(f"error: {FFMPEG_HINT}\n")
        return EXIT_BAD_INPUT
    out_dir = Path(args.out_dir).expanduser()
    if not args.info:
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            probe = out_dir / ".youtube-audio-write-test"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as e:
            sys.stderr.write(f"error: cannot write to {out_dir}: {e}\n")
            return EXIT_BAD_INPUT
    if not js_runtimes():
        note("no JavaScript runtime (deno, node or bun) on PATH; yt-dlp may miss some YouTube formats — installing deno fixes it")

    records: list[dict[str, Any]] = []
    exit_code = EXIT_OK
    info_opts = {**build_opts(args, out_dir), "postprocessors": [], "writethumbnail": False}
    for url in targets:
        try:
            with (yt_dlp.YoutubeDL(info_opts) if args.info else make_ydl(args, out_dir)) as ydl:
                got = run_one(ydl, url, args)
        except DownloadError as e:
            msg = short_message(str(e)) or "download failed"
            code, kind, hint = classify_error(str(e))
            sys.stderr.write(f"error: {msg} — {hint}\n")
            records.append({"ok": False, "url": url, "error": {"code": code, "kind": kind, "message": msg, "hint": hint}})
            exit_code = exit_code or code
            continue
        except KeyboardInterrupt:
            sys.stderr.write("error: interrupted; partial downloads (.part) are resumed on the next run\n")
            return EXIT_OTHER
        except Exception as e:  # yt-dlp internals, postprocessor crashes
            msg = f"{type(e).__name__}: {(str(e).splitlines() or [''])[0][:300]}"
            code, kind, hint = classify_error(msg)
            sys.stderr.write(f"error: {msg} — {hint}\n")
            records.append({"ok": False, "url": url, "error": {"code": code, "kind": kind, "message": msg, "hint": hint}})
            exit_code = exit_code or code
            continue
        for r in got:
            if not args.info and not r["ok"]:
                r["error"] = {"code": EXIT_NO_AUDIO, "kind": "no_file", "message": "yt-dlp finished but produced no audio file", "hint": UPDATE_HINT}
                exit_code = exit_code or EXIT_NO_AUDIO
        records.extend(got)
        if not args.info and len(got) > 1:
            note(f"{sum(1 for r in got if r['ok'])}/{len(got)} playlist items saved (limit {args.items or args.max_items})")
    if args.output == "json":
        sys.stdout.write(json.dumps({"count": len(records), "results": records}, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(human(records, args))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
