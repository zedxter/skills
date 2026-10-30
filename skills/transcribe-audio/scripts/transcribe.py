#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["faster-whisper>=1.1", "yt-dlp[default]>=2025.10.22"]
# ///
"""Transcribe speech in an audio/video file or URL locally with faster-whisper (offline, no API key).

URLs (YouTube and other yt-dlp sites, direct podcast mp3 links) are downloaded with
yt-dlp first; audio is decoded by PyAV, which ships its own FFmpeg libraries, so no
ffmpeg install is needed. Output formats mirror youtube-transcript.

    uv run transcribe.py lecture.mp4                          # text paragraphs, language auto-detected
    uv run transcribe.py "https://www.youtube.com/watch?v=ID" -f timestamps -o t.txt
    uv run transcribe.py episode.mp3 -l ru -f srt -o episode.srt
    uv run transcribe.py talk.m4a --start 10:00 --end 25:00 --model medium
    uv run transcribe.py --models                             # sizes and what is already downloaded
    uv run transcribe.py FILE --download-model                # allow fetching the model on first use

Exit codes:
    0 ok · 2 bad input / missing dependency / GPU unusable · 3 no speech detected
    · 4 download blocked (bot check, rate limit, geo-block) · 5 media unavailable
    · 6 network error · 7 decoder error (no audio track, corrupt file) · 8 model not
    downloaded yet, model download failed, or out of memory
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

(EXIT_OK, EXIT_BAD_INPUT, EXIT_NO_SPEECH, EXIT_BLOCKED, EXIT_UNAVAILABLE, EXIT_NETWORK, EXIT_DECODE,
 EXIT_MODEL) = 0, 2, 3, 4, 5, 6, 7, 8

try:
    import av
    import numpy as np
    from faster_whisper import WhisperModel
    from faster_whisper.utils import _MODELS, download_model
except ImportError as e:
    sys.stderr.write(f"error: missing dependency ({e.name}); run with `uv run transcribe.py …` or "
                     "`pip install faster-whisper 'yt-dlp[default]'`\n")
    sys.exit(EXIT_BAD_INPUT)

SAMPLE_RATE = 16000
DEFAULT_MODEL = "small"
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Download size in MB (Hugging Face, int8/float16 CTranslate2 weights) and a note for --models.
MODEL_INFO: dict[str, tuple[int, str]] = {
    "tiny": (78, "fastest, rough; fine for finding a topic"),
    "base": (148, "fast, noticeably more errors than small"),
    "small": (486, "default: best quality per CPU minute"),
    "medium": (1531, "better names/rare words; ~3x slower than small"),
    "large-v3": (3091, "best accuracy; GPU or a lot of patience on CPU"),
    "large-v3-turbo": (1622, "near large-v3 quality, much faster; good with a GPU"),
    "distil-large-v3": (1516, "English only, fast large-class model"),
    "tiny.en": (78, "English only"), "base.en": (148, "English only"),
    "small.en": (486, "English only, slightly better than small on English"),
    "medium.en": (1530, "English only"),
}
WINDOW_SECONDS, CUT_SLACK = 600, 30  # long audio is transcribed in ~10-minute windows cut at a pause
PARAGRAPH_HARD_SPLIT = 2  # a paragraph with no sentence end is cut after this many × --paragraph-seconds
UPDATE_HINT = "sites change often; update yt-dlp (uv run picks the newest allowed release; with pip: pip install -U 'yt-dlp[default]')"

# Ordered (pattern, exit code, kind, hint) for yt-dlp errors; first match wins.
ERROR_RULES: list[tuple[str, int, str, str]] = [
    (r"confirm (?:you(?:'|’)re|you are) not a bot", EXIT_BLOCKED, "bot_check",
     "YouTube asks this IP to sign in; pass --cookies-from-browser BROWSER (the user's own logged-in browser) or --proxy, "
     "or transcribe a file the user downloaded. Do not retry in a loop."),
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
     r"this video is unavailable|does not exist|http error 404|http error 410", EXIT_UNAVAILABLE, "unavailable",
     "the media was removed or never existed"),
    (r"live event will begin|premieres? in|is upcoming|this live event", EXIT_UNAVAILABLE, "not_started",
     "the live stream/premiere has not started yet; try after it ends"),
    (r"drm|requested format is not available|no video formats found|no audio", EXIT_UNAVAILABLE, "no_audio",
     "no downloadable audio stream (DRM or no audio format); " + UPDATE_HINT),
    (r"unsupported url|is not a valid url", EXIT_BAD_INPUT, "bad_url", "not a URL yt-dlp can handle; pass a direct media link or a file"),
    (r"unable to download (?:webpage|api page|json)|timed out|name or service not known|getaddrinfo|"
     r"temporary failure in name resolution|connection (?:reset|refused|aborted)|network is unreachable|"
     r"ssl|certificate|remote end closed", EXIT_NETWORK, "network", "check the connection or --proxy; one retry later is fine"),
]


def classify_error(message: str) -> tuple[int, str, str]:
    """Map a yt-dlp error message to (exit code, kind, hint).

    >>> classify_error("ERROR: [youtube] x: Sign in to confirm you’re not a bot.")[:2]
    (4, 'bot_check')
    >>> classify_error("ERROR: [youtube] x: Private video. Sign in if you've been granted access")[:2]
    (5, 'private')
    >>> classify_error("ERROR: Unable to download webpage: <urlopen error [Errno -2] Name or service not known>")[:2]
    (6, 'network')
    >>> classify_error("ERROR: Unsupported URL: https://example.com/")[:2]
    (2, 'bad_url')
    >>> classify_error("something odd")[:2]
    (7, 'other')
    """
    text = message.lower()
    for pattern, code, kind, hint in ERROR_RULES:
        if re.search(pattern, text):
            return code, kind, hint
    return EXIT_DECODE, "other", UPDATE_HINT


def short_message(text: str) -> str:
    """First line of a yt-dlp error without the `ERROR:` prefix and the FAQ tail, ≤ 240 chars.

    >>> short_message("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. This helps protect our community. Use --cookies…")
    '[youtube] abc: Sign in to confirm you’re not a bot.'
    """
    first = (text.splitlines() or [""])[0]
    first = re.sub(r"^(?:ERROR:\s*)+", "", first).strip()
    m = re.match(r"(.+?[.!?])(?:\s+(?:This helps|Use --|See |Also see|Please )|$)", first)
    return (m.group(1) if m else first)[:240]


def as_url(arg: str) -> str | None:
    """A URL to download, or None when the argument should be a local file.

    >>> as_url("dQw4w9WgXcQ"), as_url("youtu.be/dQw4w9WgXcQ"), as_url("https://example.com/ep1.mp3")
    ('https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'https://youtu.be/dQw4w9WgXcQ', 'https://example.com/ep1.mp3')
    >>> as_url("talk.mp3"), as_url("C:\\\\audio\\\\talk.mp3"), as_url("ftp://x/y")
    (None, None, None)
    """
    s = arg.strip()
    if VIDEO_ID_RE.fullmatch(s) and not Path(s).exists():
        return f"https://www.youtube.com/watch?v={s}"
    if re.match(r"^(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/", s, re.I):
        return "https://" + s
    p = urlsplit(s)
    return s if p.scheme.lower() in ("http", "https") and p.hostname else None


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
    """Page-derived text (titles, channel names) as one safe line: control characters dropped, whitespace collapsed.

    >>> clean("Talk\\n# language: xx\\x1b[31m"), clean("  "), clean(None)
    ('Talk # language: xx[31m', None, None)
    """
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", str(value or ""))
    s = " ".join(s.split())
    return s or None


def fmt_clock(sec: float) -> str:
    """H:MM:SS or M:SS for progress lines.

    >>> fmt_clock(3723), fmt_clock(59.4)
    ('1:02:03', '0:59')
    """
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_time(seconds: float, sep: str | None = None) -> str:
    """`[mm:ss]`-style stamp (sep None) or a subtitle stamp with milliseconds (sep ',' for SRT, '.' for VTT).

    >>> fmt_time(65.5), fmt_time(3725.25), fmt_time(3725.25, ","), fmt_time(1.0005, ".")
    ('01:05', '01:02:05', '01:02:05,250', '00:00:01.000')
    """
    ms_total = int(round(seconds * 1000))
    s, ms = divmod(ms_total, 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if sep:
        return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def to_paragraphs(segments: list[dict[str, Any]], every_seconds: float) -> list[str]:
    """Join segments into paragraphs: cut at the first sentence end after `every_seconds`, or at any segment
    after PARAGRAPH_HARD_SPLIT × that.

    >>> segs = [{"start": t, "text": x} for t, x in [(0, "One."), (30, "Two"), (61, "three."), (70, "Four.")]]
    >>> to_paragraphs(segs, 60)
    ['One. Two three.', 'Four.']
    """
    paragraphs: list[str] = []
    current: list[str] = []
    para_start: float | None = None
    for seg in segments:
        text = seg["text"]
        if para_start is None:
            para_start = seg["start"]
        current.append(text)
        elapsed = seg["start"] - para_start
        if (elapsed >= every_seconds and re.search(r"[.!?…。？！]$", text)) or elapsed >= every_seconds * PARAGRAPH_HARD_SPLIT:
            paragraphs.append(" ".join(current))
            current, para_start = [], None
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def render(result: dict[str, Any], fmt: str, paragraph_seconds: float, with_header: bool) -> str:
    segments = result["segments"]
    header = ""
    if with_header and fmt in ("text", "timestamps"):
        how = "given" if result["language_given"] else f"detected, {result['language_probability']:.0%}"
        label = clean(result["title"]) or Path(result["source"]).name
        header = (f"# source: {label} ({clean(result['source'])})\n"
                  f"# language: {result['language']} ({how})\n"
                  f"# model: {result['model']}, speech-to-text: check names and numbers\n\n")
    if fmt == "text":
        return header + "\n\n".join(to_paragraphs(segments, paragraph_seconds)) + "\n"
    if fmt == "timestamps":
        return header + "\n".join(f"[{fmt_time(s['start'])}] {s['text']}" for s in segments) + "\n"
    if fmt == "srt":
        return "\n".join(f"{i}\n{fmt_time(s['start'], ',')} --> {fmt_time(s['end'], ',')}\n{s['text']}\n"
                         for i, s in enumerate(segments, 1))
    if fmt == "vtt":
        return "WEBVTT\n\n" + "\n".join(f"{fmt_time(s['start'], '.')} --> {fmt_time(s['end'], '.')}\n{s['text']}\n"
                                        for s in segments)
    if fmt == "json":
        return json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    raise ValueError(fmt)


def model_cached(name: str) -> bool:
    """True when `name` is a local CTranslate2 model folder or a size already in the Hugging Face cache."""
    if Path(name).is_dir():
        return True
    try:
        download_model(name, local_files_only=True)
        return True
    except Exception:  # LocalEntryNotFoundError, or a repo id that was never fetched
        return False


def models_table() -> str:
    rows = [f"{'model':16} {'size':>8}  {'cached':6}  note"]
    for name, (mb, note) in MODEL_INFO.items():
        size = f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb} MB"
        rows.append(f"{name:16} {size:>8}  {'yes' if model_cached(name) else 'no':6}  {note}")
    return "\n".join(rows) + "\n"


class QuietLogger:
    """yt-dlp logger that swallows output; failures surface as DownloadError and become one `error:` line."""

    def debug(self, msg: str) -> None:
        pass

    info = warning = error = debug


def ydl_opts(args: argparse.Namespace, out_dir: Path) -> dict[str, Any]:
    """yt-dlp options: one small speech-quality audio stream, saved as served (no ffmpeg post-processing)."""
    opts: dict[str, Any] = {
        # ~50-70 kbps Opus/AAC is plenty for speech recognition and 2-3x smaller than the best stream
        "format": "bestaudio[abr<=96]/worstaudio[abr>=40]/bestaudio/best",
        "outtmpl": {"default": str(out_dir / "%(title).120B [%(id)s].%(ext)s")},
        # playlistend is a backstop: a URL that only turns into a playlist while processing fetches one item at most
        "windowsfilenames": True, "noplaylist": True, "playlistend": 1, "fixup": "never", "postprocessors": [],
        "quiet": True, "no_warnings": True, "noprogress": True, "logger": QuietLogger(),
        "socket_timeout": 30, "retries": 3, "fragment_retries": 3,
    }
    if args.cookies_from_browser:
        browser, _, profile = args.cookies_from_browser.partition(":")
        opts["cookiesfrombrowser"] = (browser.lower(), profile or None, None, None)
    if args.cookies:
        opts["cookiefile"] = args.cookies
    if args.proxy:
        opts["proxy"] = args.proxy
    runtimes = {name: {} for name in ("deno", "node", "bun") if shutil.which(name)}
    if runtimes:
        opts["js_runtimes"] = runtimes
    return opts


class Failure(Exception):
    def __init__(self, code: int, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.hint = code, hint


def probe_url(url: str, args: argparse.Namespace) -> dict[str, Any]:
    """Title and duration of `url` without downloading the media."""
    import yt_dlp
    from yt_dlp.utils import DownloadError
    try:
        with yt_dlp.YoutubeDL(ydl_opts(args, Path("."))) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
    except DownloadError as e:
        code, _kind, hint = classify_error(str(e))
        raise Failure(code, short_message(str(e)) or "lookup failed", hint)
    if info.get("_type") in ("playlist", "multi_video"):
        raise Failure(EXIT_BAD_INPUT, "that URL is a playlist or channel", "pass one video/episode URL")
    return {"title": clean(info.get("title")), "duration": info.get("duration"), "url": info.get("webpage_url") or url}


# Rough CPU speed (× real time, int8, 6-core laptop) for --info estimates; GPUs are far faster.
CPU_SPEED = {"tiny": 24, "base": 12, "small": 4, "medium": 1.3, "large-v3": 0.5, "large-v3-turbo": 1.5, "distil-large-v3": 2}


def fetch(url: str, args: argparse.Namespace, out_dir: Path) -> tuple[Path, dict[str, Any]]:
    """Download the audio of `url` into out_dir; return (file, metadata)."""
    try:
        import yt_dlp
        from yt_dlp.utils import DownloadError
    except ImportError:
        raise Failure(EXIT_BAD_INPUT, "missing dependency yt-dlp", "run with uv, or pip install 'yt-dlp[default]'")
    try:
        with yt_dlp.YoutubeDL(ydl_opts(args, out_dir)) as ydl:
            # Resolve without downloading first so a playlist/channel URL is refused before anything is fetched;
            # process_ie_result then downloads from the same extraction (one request to the site, not two).
            info = ydl.extract_info(url, download=False, process=False)
            if info.get("_type") not in ("playlist", "multi_video"):
                info = ydl.process_ie_result(info, download=True)
    except DownloadError as e:
        code, _kind, hint = classify_error(str(e))
        raise Failure(code, short_message(str(e)) or "download failed", hint)
    if info.get("_type") in ("playlist", "multi_video"):
        raise Failure(EXIT_BAD_INPUT, "that URL is a playlist or channel", "pass one video/episode URL")
    files = [Path(d["filepath"]) for d in info.get("requested_downloads") or [] if d.get("filepath")]
    if not files or not files[0].exists():
        raise Failure(EXIT_UNAVAILABLE, "yt-dlp finished but produced no audio file", UPDATE_HINT)
    meta = {"title": clean(info.get("title")), "channel": clean(info.get("channel") or info.get("uploader")),
            "duration": info.get("duration"), "url": info.get("webpage_url") or url}
    return files[0], meta


def load_audio(path: Path, start: float | None, end: float | None) -> np.ndarray:
    """Decode `path` (any container/codec FFmpeg knows) to 16 kHz mono float32, keeping only [start, end)."""
    try:
        container = av.open(str(path))
    except av.error.FFmpegError as e:
        raise Failure(EXIT_DECODE, f"cannot open {path.name}: {e}", "not an audio/video file, or it is corrupt")
    with container:
        if not container.streams.audio:
            raise Failure(EXIT_DECODE, f"{path.name} has no audio track", "pass a file with sound")
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        # Timestamps need not start at 0 (MPEG-TS, HLS, broadcast recordings start at 1.4 s or anywhere);
        # `start`/`end` are positions in the audio, so shift by the stream's first timestamp.
        if stream.start_time is not None and stream.time_base is not None:
            base = float(stream.start_time * stream.time_base)
        else:
            base = container.start_time / av.time_base if container.start_time else 0.0
        if start:
            container.seek(int((start + base) * av.time_base))  # lands on or before `start`; trimmed below
        chunks: list[np.ndarray] = []
        first: float | None = None
        try:
            for frame in container.decode(stream):
                t = frame.time - base if frame.time is not None else None
                if first is None:
                    first = t if t is not None else 0.0
                if end is not None and t is not None and t >= end:
                    break
                chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
        except av.error.FFmpegError as e:
            if not chunks:
                raise Failure(EXIT_DECODE, f"cannot decode {path.name}: {e}", "the file is corrupt or uses an unsupported codec")
            sys.stderr.write(f"note: decoding stopped early ({e}); transcribing what was read\n")
        chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    audio = np.concatenate(chunks) if chunks else np.zeros(0, np.float32)
    if start:
        audio = audio[max(0, round((start - (first or 0.0)) * SAMPLE_RATE)):]
    if end is not None:
        audio = audio[: max(0, round((end - (start or 0.0)) * SAMPLE_RATE))]
    return audio


def media_duration(path: Path) -> float | None:
    try:
        with av.open(str(path)) as c:
            return c.duration / av.time_base if c.duration else None
    except av.error.FFmpegError:
        return None


def quiet_cut(audio: np.ndarray, lo: float, hi: float) -> int:
    """Sample index in the middle of the quietest 100 ms between `lo` and `hi` seconds (a pause between words).

    >>> a = np.ones(SAMPLE_RATE * 10, np.float32); a[SAMPLE_RATE * 7: SAMPLE_RATE * 7 + 1600] = 0
    >>> quiet_cut(a, 5, 9) / SAMPLE_RATE
    7.05
    """
    frame = SAMPLE_RATE // 10
    first = int(lo * SAMPLE_RATE)
    part = audio[first: int(hi * SAMPLE_RATE)]
    n = part.size // frame
    if n == 0:
        return audio.size
    energy = np.square(part[: n * frame].reshape(n, frame)).mean(axis=1)
    return first + int(np.argmin(energy)) * frame + frame // 2


def windows(path: Path, start: float | None, end: float | None):
    """Yield (offset seconds, audio) windows of about WINDOW_SECONDS, each cut at a pause and decoded on its own,
    so memory stays flat however long the recording is (faster-whisper builds the spectrogram of its whole
    input at once: gigabytes for an hour of audio)."""
    pos = start or 0.0
    while True:
        want = pos + WINDOW_SECONDS + CUT_SLACK if end is None else min(pos + WINDOW_SECONDS + CUT_SLACK, end)
        audio = load_audio(path, pos, want)
        last = audio.size < (want - pos - 1) * SAMPLE_RATE or (end is not None and want >= end)
        if not last:
            audio = audio[: quiet_cut(audio, WINDOW_SECONDS - CUT_SLACK, WINDOW_SECONDS + CUT_SLACK)]
        if audio.size:
            yield pos, audio
        if last or not audio.size:
            return
        pos += audio.size / SAMPLE_RATE


def is_oom(e: BaseException) -> bool:
    return isinstance(e, MemoryError) or bool(re.search(r"out of memory|bad_alloc|cannot allocate", str(e), re.I))


def is_compute_problem(e: BaseException) -> bool:
    return bool(re.search(r"compute type", str(e), re.I))


def is_gpu_problem(e: BaseException) -> bool:
    return bool(re.search(r"cuda|cudnn|cublas|libcu|no gpu|gpu", str(e), re.I))


def load_model(args: argparse.Namespace, device: str) -> Any:
    compute = args.compute_type
    if compute == "auto":
        compute = "int8" if device == "cpu" else "float16"
    # The model is already in the cache (checked or downloaded in main); without local_files_only faster-whisper
    # asks the Hugging Face hub again and hangs for minutes on a network that silently drops packets.
    return WhisperModel(args.model, device=device, compute_type=compute, cpu_threads=args.threads,
                        local_files_only=not Path(args.model).is_dir())


def cuda_usable() -> bool:
    """A GPU is visible and the CUDA 12 cuBLAS and cuDNN 9 libraries load. Without cuDNN, CTranslate2 aborts the
    whole process (not a Python exception), so `--device auto` must not pick the GPU on a hunch."""
    import ctypes
    import ctranslate2
    if ctranslate2.get_cuda_device_count() == 0:
        return False
    names = {"cublas": ["libcublas.so.12", "cublas64_12.dll"], "cudnn": ["libcudnn_ops.so.9", "cudnn_ops64_9.dll", "libcudnn.so.9"]}
    for candidates in names.values():
        for name in candidates:
            try:
                ctypes.CDLL(name)
                break
            except OSError:
                continue
        else:
            return False
    return True


def transcribe(model: Any, audio: np.ndarray, args: argparse.Namespace, language: str | None, offset: float,
               progress) -> tuple[Any, list[dict[str, Any]]]:
    seg_iter, info = model.transcribe(
        audio, language=language, task=args.task, beam_size=args.beam_size, initial_prompt=args.prompt,
        vad_filter=not args.no_vad, condition_on_previous_text=False)
    segments = []
    for seg in seg_iter:
        text = " ".join(seg.text.split())
        if text:
            segments.append({"start": round(seg.start + offset, 3), "end": round(seg.end + offset, 3), "text": text})
        progress(seg.end + offset)
    return info, segments


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", nargs="?", help="audio/video file, or a URL (YouTube, other yt-dlp sites, direct mp3/m4a link)")
    p.add_argument("-m", "--model", default=DEFAULT_MODEL,
                   help=f"whisper model size ({', '.join(MODEL_INFO)}) or a local CTranslate2 model folder (default {DEFAULT_MODEL})")
    p.add_argument("--download-model", action="store_true", help="allow downloading the model if it is not cached yet (ask the user first: 78 MB-3 GB)")
    p.add_argument("--info", action="store_true", help="print title, duration and an estimated transcription time; transcribe nothing")
    p.add_argument("--models", action="store_true", help="list model sizes and which ones are already downloaded, then exit")
    p.add_argument("-l", "--language", help="spoken language code (en, ru, de, …); default: detect from the first 30 s")
    p.add_argument("--task", choices=["transcribe", "translate"], default="transcribe",
                   help="translate = Whisper's own speech→English translation (default: transcribe in the spoken language)")
    p.add_argument("--prompt", metavar="TEXT", help="names and terms to spell right, e.g. 'Kubernetes, Grafana, Ilya Sutskever'")
    p.add_argument("--start", metavar="TIME", help="transcribe from TIME (seconds, MM:SS, HH:MM:SS, 1h2m3s); timestamps stay relative to the file")
    p.add_argument("--end", metavar="TIME", help="stop at TIME")
    p.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto", help="auto uses an NVIDIA GPU when CUDA 12 + cuDNN 9 work, else CPU")
    p.add_argument("--compute-type", default="auto", help="CTranslate2 compute type: auto (int8 on CPU, float16 on GPU), int8, int8_float16, float16, float32")
    p.add_argument("--threads", type=int, default=0, help="CPU threads (default 0 = CTranslate2's choice)")
    p.add_argument("--beam-size", type=int, default=5, help="beam width; 1 is a little faster, slightly less accurate (default 5)")
    p.add_argument("--no-vad", action="store_true", help="do not skip silence with the Silero VAD (default: skip; avoids hallucinated text in pauses)")
    p.add_argument("-f", "--format", choices=["text", "timestamps", "srt", "vtt", "json"], default="text", help="output format (default text paragraphs)")
    p.add_argument("--paragraph-seconds", type=float, default=60, help="target paragraph length for -f text (default 60)")
    p.add_argument("--no-header", action="store_true", help="omit the # source/language/model header")
    p.add_argument("-o", "--out", metavar="FILE", help="write the transcript to FILE instead of stdout")
    p.add_argument("--keep-audio", metavar="DIR", help="URL sources: keep the downloaded audio in DIR (default: temporary, deleted)")
    p.add_argument("--cookies-from-browser", metavar="BROWSER[:PROFILE]", help="URL sources: the user's own signed-in browser cookies, for bot checks")
    p.add_argument("--cookies", metavar="FILE", help="URL sources: Netscape cookies.txt exported by the user")
    p.add_argument("--proxy", metavar="URL", help="URL sources: http(s)/socks5 proxy for the download")
    p.add_argument("-q", "--quiet", action="store_true", help="no progress or note: lines")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = make_parser().parse_args(argv)
    note = (lambda m: None) if args.quiet else (lambda m: sys.stderr.write(f"note: {m}\n"))

    def fail(code: int, message: str, hint: str = "") -> int:
        sys.stderr.write(f"error: {message}" + (f" — {hint}" if hint else "") + "\n")
        return code

    if args.models:
        sys.stdout.write(models_table())
        return EXIT_OK
    if not args.source:
        return fail(EXIT_BAD_INPUT, "no input", "pass an audio/video file or a URL (or --models)")
    try:
        start = parse_time(args.start) if args.start else None
        end = parse_time(args.end) if args.end else None
    except ValueError as e:
        return fail(EXIT_BAD_INPUT, str(e))
    if start is not None and end is not None and end <= start:
        return fail(EXIT_BAD_INPUT, "--end must be after --start")
    if args.model not in _MODELS and not Path(args.model).is_dir() and "/" not in args.model:
        return fail(EXIT_BAD_INPUT, f"unknown model {args.model!r}", f"use one of {', '.join(MODEL_INFO)} (see --models)")
    if args.language:
        from faster_whisper.tokenizer import _LANGUAGE_CODES
        args.language = args.language.lower()
        if args.language not in _LANGUAGE_CODES:
            return fail(EXIT_BAD_INPUT, f"unknown language code {args.language!r}",
                        "use a two-letter Whisper code such as en, ru, de, uk, es, zh, ja (or omit -l to detect)")
    if args.cookies and not Path(args.cookies).is_file():
        return fail(EXIT_BAD_INPUT, f"cookies file not found: {args.cookies}")
    url = as_url(args.source)
    if url is None and not Path(args.source).expanduser().is_file():
        return fail(EXIT_BAD_INPUT, f"file not found: {args.source}", "pass an existing audio/video file or an http(s) URL")
    if args.quiet:
        from huggingface_hub.utils import disable_progress_bars
        disable_progress_bars()

    if args.info:
        try:
            meta = probe_url(url, args) if url else {"title": None, "url": None,
                                                    "duration": media_duration(Path(args.source).expanduser())}
        except Failure as e:
            return fail(e.code, str(e), e.hint)
        length = meta["duration"]
        if length is not None:
            length = max(0.0, min(end or length, length) - (start or 0.0))
        speed = CPU_SPEED.get(args.model.removesuffix(".en"))
        lines = [f"title: {meta['title'] or Path(args.source).name}", f"duration: {'unknown' if length is None else fmt_clock(length)}",
                 f"model: {args.model} ({'cached' if model_cached(args.model) else 'not downloaded: ~%s MB' % MODEL_INFO.get(args.model, ('?',))[0]})"]
        if length and speed:
            lines.append(f"estimate on CPU: ~{fmt_clock(length / speed)} (much faster on an NVIDIA GPU; varies by CPU 2-3x)")
        sys.stdout.write("\n".join(lines) + "\n")
        return EXIT_OK
    # Check the model before downloading media, so a missing model fails fast and cheaply.
    if not model_cached(args.model):
        mb = MODEL_INFO.get(args.model, (0, ""))[0]
        size = f"~{mb} MB" if mb else "size unknown"
        if not args.download_model:
            return fail(EXIT_MODEL, f"model {args.model!r} is not downloaded yet ({size})",
                        "ask the user, then re-run with --download-model (one-time, cached by Hugging Face), or pick a cached model from --models")
        note(f"downloading model {args.model} ({size}) into the Hugging Face cache; this happens once")
        try:
            download_model(args.model)
        except Exception as e:
            return fail(EXIT_MODEL, f"model download failed: {type(e).__name__}: {(str(e).splitlines() or [''])[0][:200]}",
                        "check the connection to huggingface.co (HTTPS_PROXY is honoured) and free disk space")

    meta: dict[str, Any] = {"title": None, "channel": None, "duration": None, "url": None}
    began = time.monotonic()
    last_report = [began]
    total = [0.0]

    def progress(pos: float) -> None:
        now = time.monotonic()
        if not args.quiet and now - last_report[0] >= 15:
            last_report[0] = now
            done = pos - (start or 0.0)
            share = f" ({done / total[0]:.0%})" if total[0] else ""
            sys.stderr.write(f"progress: {fmt_clock(pos)} / {fmt_clock((start or 0.0) + total[0]) if total[0] else '?'}{share}, "
                             f"{fmt_clock(now - began)} elapsed\n")

    device = args.device
    if device == "auto":
        device = "cuda" if cuda_usable() else "cpu"
    segments: list[dict[str, Any]] = []
    info = None
    duration = 0.0
    with tempfile.TemporaryDirectory(prefix="transcribe-audio-") as tmp:
        try:
            if url:
                out_dir = Path(args.keep_audio).expanduser() if args.keep_audio else Path(tmp)
                out_dir.mkdir(parents=True, exist_ok=True)
                note(f"downloading audio from {url}")
                path, meta = fetch(url, args, out_dir)
                if args.keep_audio:
                    note(f"audio kept at {path}")
            else:
                path = Path(args.source).expanduser().resolve()
            length = media_duration(path)
            if length:
                total[0] = max(0.0, min(end or length, length) - (start or 0.0))
                note(f"transcribing {fmt_clock(total[0])} of audio with model {args.model}")
            model = None
            language = args.language
            for offset, audio in windows(path, start, end):
                duration += audio.size / SAMPLE_RATE
                try:
                    if model is None:
                        model = load_model(args, device)
                    win_info, win_segments = transcribe(model, audio, args, language, offset, progress)
                except (RuntimeError, ValueError, OSError) as e:
                    if args.device != "auto" or device == "cpu" or not (is_gpu_problem(e) or is_compute_problem(e)) or is_oom(e):
                        raise
                    note(f"GPU unusable ({(str(e).splitlines() or [''])[0][:120]}); falling back to CPU")
                    device = "cpu"
                    model = load_model(args, device)
                    win_info, win_segments = transcribe(model, audio, args, language, offset, progress)
                if win_segments and info is None:
                    info = win_info
                    language = language or win_info.language  # keep later windows in the detected language
                segments.extend(win_segments)
        except Failure as e:
            return fail(e.code, str(e), e.hint)
        except KeyboardInterrupt:
            return fail(EXIT_BAD_INPUT, "interrupted")
        except Exception as e:  # CTranslate2/onnxruntime raise RuntimeError/ValueError with free-form text
            msg = f"{type(e).__name__}: {(str(e).splitlines() or [''])[0][:240]}"
            if is_oom(e):
                return fail(EXIT_MODEL, f"out of memory running model {args.model}: {msg}",
                            "use a smaller --model, --compute-type int8, or --device cpu")
            if is_compute_problem(e):
                return fail(EXIT_BAD_INPUT, msg, "use --compute-type auto (int8 on CPU, float16 on GPU) or int8")
            if is_gpu_problem(e):
                return fail(EXIT_BAD_INPUT, f"GPU unusable: {msg}", "install CUDA 12 cuBLAS + cuDNN 9, or use --device cpu")
            if isinstance(e, ImportError):
                return fail(EXIT_BAD_INPUT, f"missing dependency: {msg}", "run with uv, or pip install faster-whisper")
            if isinstance(e, OSError) and model is None:
                return fail(EXIT_BAD_INPUT, msg)
            return fail(EXIT_MODEL, msg, "the model files may be damaged; re-run with --download-model after deleting the cache entry")
    if duration < 0.1:
        return fail(EXIT_NO_SPEECH, "no audio in the selected range", "check --start/--end against the file's length")

    elapsed = time.monotonic() - began
    if not segments or info is None:
        return fail(EXIT_NO_SPEECH, "no speech detected", "the audio is silent, music only, or too quiet; try --no-vad or another --language")
    note(f"transcribed {fmt_clock(duration)} in {fmt_clock(elapsed)} ({duration / max(elapsed, 0.01):.1f}x real time, device {device})")

    result = {
        "source": meta["url"] or str(path), "title": meta["title"], "channel": meta["channel"],
        "language": info.language, "language_probability": round(info.language_probability, 3),
        "language_given": bool(args.language), "task": args.task, "model": args.model,
        "duration": round(duration, 3), "section": [start, end] if start is not None or end is not None else None,
        "segments": segments,
    }
    output = render(result, args.format, args.paragraph_seconds, not args.no_header)
    if args.out:
        out = Path(args.out).expanduser()
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(output, encoding="utf-8")
        except OSError as e:
            return fail(EXIT_BAD_INPUT, f"cannot write {out}: {e}")
        if not args.quiet:
            sys.stderr.write(f"wrote {out}\n")
    else:
        sys.stdout.write(output)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
