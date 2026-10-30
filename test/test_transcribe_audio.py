"""Tests for skills/transcribe-audio/scripts/transcribe.py.

Run: uv run --no-project --with pytest --with faster-whisper --with 'yt-dlp[default]' python -I -m pytest \
         test/test_transcribe_audio.py -q --doctest-modules skills/transcribe-audio/scripts/transcribe.py

Speech tests need the `tiny` model (78 MB) in the Hugging Face cache and skip otherwise; fetch it once with
`uv run skills/transcribe-audio/scripts/transcribe.py test/data/jfk.ogg -m tiny --download-model`.
test/data/jfk.ogg is 11 s of John F. Kennedy's 1961 inaugural address (a US federal government work, public
domain), the same clip whisper.cpp ships as samples/jfk.wav. URL sources are served by an in-process HTTP server;
no external site is contacted.
"""

import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "transcribe-audio" / "scripts" / "transcribe.py"
DATA = ROOT / "test" / "data"
JFK = DATA / "jfk.ogg"
sys.path.insert(0, str(SCRIPT.parent))
import transcribe as ta  # noqa: E402

HAVE_TINY = ta.model_cached("tiny")
needs_model = pytest.mark.skipif(not HAVE_TINY, reason="whisper model 'tiny' not downloaded (see module docstring)")
PHRASE = "ask not what your country can do for you"


def run(*argv: str, env: dict | None = None, timeout: int = 300):
    p = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True, encoding="utf-8",
                       timeout=timeout, env={**os.environ, **(env or {})})
    return p.returncode, p.stdout, p.stderr


def silence(path: Path, seconds: float = 4.0) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


@pytest.fixture(scope="module")
def server():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(Quiet, directory=str(DATA))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def result(segments, **extra):
    base = {"source": "/x/talk.mp3", "title": None, "channel": None, "language": "en", "language_probability": 0.97,
            "language_given": False, "task": "transcribe", "model": "small", "duration": 70.0, "section": None,
            "segments": segments}
    return {**base, **extra}


SEGS = [{"start": 0.0, "end": 4.2, "text": "Hello there."}, {"start": 62.5, "end": 3725.25, "text": "Bye."}]


# ------------------------------------------------------------------ unit level

def test_sources_and_times():
    assert ta.as_url("dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert ta.as_url("www.youtube.com/watch?v=dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert ta.as_url("https://feeds.example.org/ep/42.mp3?x=1") == "https://feeds.example.org/ep/42.mp3?x=1"
    assert ta.as_url(str(JFK)) is None and ta.as_url("file:///etc/passwd") is None
    assert ta.parse_time("1:02:03.5") == 3723.5 and ta.parse_time("90s") == 90
    with pytest.raises(ValueError):
        ta.parse_time("later")


def test_formats_mirror_youtube_transcript():
    text = ta.render(result(SEGS, title="A talk\nwith newline"), "text", 60, True)
    assert text.startswith("# source: A talk with newline (/x/talk.mp3)\n# language: en (detected, 97%)\n# model: small")
    assert text.endswith("\n\nHello there. Bye.\n")
    three = SEGS + [{"start": 70.0, "end": 71.0, "text": "Again."}]
    assert ta.render(result(three), "text", 60, False) == "Hello there. Bye.\n\nAgain.\n"  # cut at the first sentence end after 60 s
    assert ta.render(result(SEGS, language_given=True), "timestamps", 60, True).splitlines()[1] == "# language: en (given)"
    assert ta.render(result(SEGS), "timestamps", 60, False) == "[00:00] Hello there.\n[01:02] Bye.\n"
    assert ta.render(result(SEGS), "srt", 60, True) == (
        "1\n00:00:00,000 --> 00:00:04,200\nHello there.\n\n2\n00:01:02,500 --> 01:02:05,250\nBye.\n")
    assert ta.render(result(SEGS), "vtt", 60, True) == (
        "WEBVTT\n\n00:00:00.000 --> 00:00:04.200\nHello there.\n\n00:01:02.500 --> 01:02:05.250\nBye.\n")
    data = json.loads(ta.render(result(SEGS), "json", 60, True))
    assert data["segments"] == SEGS and data["language"] == "en"


def test_paragraphs_cut_without_punctuation():
    segs = [{"start": float(t), "text": "word"} for t in range(0, 300, 10)]
    paras = ta.to_paragraphs(segs, 60)
    assert len(paras) == 3 and all(p.count("word") == 13 for p in paras[:2])  # hard split after 2 × 60 s


def test_error_classification_and_download_options(tmp_path):
    cases = {
        "ERROR: [youtube] x: Sign in to confirm you’re not a bot. This helps protect our community.": (4, "bot_check"),
        "ERROR: unable to download video data: HTTP Error 429: Too Many Requests": (4, "rate_limited"),
        "ERROR: [youtube] x: Sign in to confirm your age": (5, "age_restricted"),
        "ERROR: [youtube] x: Video unavailable. This video has been removed by the uploader": (5, "unavailable"),
        "ERROR: [generic] ep: Unable to download webpage: HTTP Error 404: Not Found": (5, "unavailable"),
        "ERROR: [youtube] x: This live event will begin in 3 hours.": (5, "not_started"),
        "ERROR: Unsupported URL: https://example.com/": (2, "bad_url"),
        "ERROR: Unable to download webpage: <urlopen error timed out>": (6, "network"),
    }
    for message, expected in cases.items():
        assert ta.classify_error(message)[:2] == expected, message
    args = ta.make_parser().parse_args(["x", "--cookies-from-browser", "Firefox:work", "--proxy", "socks5://h:1"])
    o = ta.ydl_opts(args, tmp_path)
    assert o["noplaylist"] and o["playlistend"] == 1 and o["postprocessors"] == [] and o["fixup"] == "never"
    assert o["format"].startswith("bestaudio[abr<=96]") and o["cookiesfrombrowser"] == ("firefox", "work", None, None)
    assert o["proxy"] == "socks5://h:1" and isinstance(o["logger"], ta.QuietLogger)


def test_load_audio_decodes_and_cuts_sections(tmp_path):
    full = ta.load_audio(JFK, None, None)
    assert full.dtype.name == "float32" and abs(full.size / ta.SAMPLE_RATE - 11.0) < 0.1
    part = ta.load_audio(JFK, 3.0, 8.0)
    assert abs(part.size / ta.SAMPLE_RATE - 5.0) < 0.05
    assert abs(ta.load_audio(JFK, 9.0, None).size / ta.SAMPLE_RATE - 2.0) < 0.1
    bad = tmp_path / "notes.mp3"
    bad.write_text("not audio")
    with pytest.raises(ta.Failure) as e:
        ta.load_audio(bad, None, None)
    assert e.value.code == ta.EXIT_DECODE


def test_windows_are_contiguous_and_cut_at_pauses(monkeypatch):
    monkeypatch.setattr(ta, "WINDOW_SECONDS", 4)
    monkeypatch.setattr(ta, "CUT_SLACK", 1)
    full = ta.load_audio(JFK, None, None)
    for start, end in ((None, None), (2.0, 9.5)):
        parts = list(ta.windows(JFK, start, end))
        assert len(parts) >= 2 and parts[0][0] == (start or 0.0)
        for (o1, a1), (o2, _) in zip(parts, parts[1:]):
            assert abs(o1 + a1.size / ta.SAMPLE_RATE - o2) < 1e-6  # no gap, no overlap
        joined = ta.np.concatenate([a for _, a in parts])
        first = int((start or 0) * ta.SAMPLE_RATE)
        assert abs(joined.size - (int((end or 11.0) * ta.SAMPLE_RATE) - first)) < ta.SAMPLE_RATE // 20
        # same samples as one full decode, past the first 0.1 s (resampler warm-up after a seek)
        assert ta.np.allclose(joined[1600:], full[first + 1600:first + joined.size], atol=2e-3)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_timestamps_not_starting_at_zero(tmp_path):
    # MPEG-TS (HLS downloads, broadcast recordings) starts its clock at 1.4 s or anywhere; positions must not shift
    ts = tmp_path / "jfk.ts"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(JFK), "-c:a", "aac", "-f", "mpegts", "-output_ts_offset", "100", str(ts)],
                   check=True)
    full = ta.load_audio(ts, None, None)
    assert abs(full.size / ta.SAMPLE_RATE - 11.0) < 0.2
    ref, got = ta.load_audio(JFK, 3.0, 8.0), ta.load_audio(ts, 3.0, 8.0)
    assert abs(got.size - ref.size) < ta.SAMPLE_RATE // 20
    n = min(ref.size, got.size)
    lag = max(range(-1600, 1601, 16), key=lambda k: float((ref[max(0, k):n + min(0, k)] * got[max(0, -k):n - max(0, k)]).sum()))
    assert abs(lag) <= 1600  # aligned within 0.1 s (AAC priming), not shifted by the 100 s clock offset
    [(offset, window)] = list(ta.windows(ts, None, None))
    assert offset == 0.0 and abs(window.size / ta.SAMPLE_RATE - 11.0) < 0.2


def test_cli_argument_errors_exit_2(tmp_path):
    for argv in ([], [str(tmp_path / "missing.mp3")], [str(JFK), "--start", "8", "--end", "3"], [str(JFK), "--start", "soon"],
                 [str(JFK), "-m", "huge"], [str(JFK), "-l", "xx"], [str(JFK), "--cookies", str(tmp_path / "c.txt")]):
        rc, out, err = run(*argv)
        assert rc == 2 and err.startswith("error: ") and out == "", argv


def test_missing_model_is_not_downloaded_silently(tmp_path):
    rc, out, err = run(str(JFK), "-m", "base", env={"HF_HUB_CACHE": str(tmp_path), "HF_HUB_OFFLINE": "1"})
    assert rc == 8 and out == "" and "not downloaded yet (~148 MB)" in err and "--download-model" in err
    rc, out, _ = run("--models", env={"HF_HUB_CACHE": str(tmp_path)})
    assert rc == 0 and out.splitlines()[0].split()[:3] == ["model", "size", "cached"]
    assert "small" in out and "486 MB" in out and " yes " not in out


def test_info_reports_duration_without_transcribing(tmp_path):
    rc, out, _ = run(str(JFK), "--info", "-m", "base", "--start", "3", env={"HF_HUB_CACHE": str(tmp_path)})
    assert rc == 0 and out.splitlines() == ["title: jfk.ogg", "duration: 0:08", "model: base (not downloaded: ~148 MB)",
                                            "estimate on CPU: ~0:01 (much faster on an NVIDIA GPU; varies by CPU 2-3x)"]


# ------------------------------------------------------------------ speech (model `tiny`)

@needs_model
def test_transcribes_speech_to_text_and_json():
    rc, out, err = run(str(JFK), "-m", "tiny")
    assert rc == 0, err
    assert out.startswith("# source: jfk.ogg (") and "# language: en (detected" in out and PHRASE in out.lower()
    assert "transcribed 0:11" in err
    rc, out, _ = run(str(JFK), "-m", "tiny", "-l", "en", "-f", "json", "-q")
    data = json.loads(out)
    assert rc == 0 and data["language"] == "en" and data["language_given"] and data["model"] == "tiny"
    assert PHRASE in " ".join(s["text"] for s in data["segments"]).lower() and data["segments"][-1]["end"] <= 11.5


@needs_model
def test_section_keeps_file_timestamps(tmp_path):
    out = tmp_path / "sub" / "jfk.srt"
    rc, stdout, err = run(str(JFK), "-m", "tiny", "-f", "srt", "--start", "3", "--end", "8", "-o", str(out))
    assert rc == 0 and stdout == "" and f"wrote {out}" in err
    first = out.read_text(encoding="utf-8").splitlines()[1]
    assert first.startswith("00:00:03,") or first.startswith("00:00:04,")  # offset by --start, not from 0


@needs_model
def test_silence_is_no_speech(tmp_path):
    rc, out, err = run(str(silence(tmp_path / "quiet.wav")), "-m", "tiny")
    assert rc == 3 and out == "" and "no speech detected" in err


@needs_model
def test_url_source_is_downloaded_and_cleaned_up(server, tmp_path):
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    rc, out, err = run(f"{server}/jfk.ogg", "-m", "tiny", "-q", env={"TMPDIR": str(scratch), "TEMP": str(scratch), "TMP": str(scratch)})
    assert rc == 0 and PHRASE in out.lower() and not list(scratch.glob("transcribe-audio-*"))  # temp download removed
    keep = tmp_path / "audio"
    rc, out, err = run(f"{server}/jfk.ogg", "-m", "tiny", "-f", "timestamps", "--keep-audio", str(keep))
    assert rc == 0, err
    assert f"({server}/jfk.ogg)" in out.splitlines()[0] and PHRASE in out.lower()
    assert [p.suffix for p in keep.iterdir()] == [".ogg"]
    rc, out, err = run(f"{server}/missing.mp3", "-m", "tiny")
    assert rc == 5 and out == "" and "404" in err
