"""Tests for skills/youtube-transcript/scripts/transcript.py.

Run: uv run --no-project --with pytest --with "youtube-transcript-api>=1.0" python -I -m pytest \
         test/test_youtube_transcript.py -q

Offline only: video id parsing, time formatting, bad input and the crash guard. Fetching a transcript needs
YouTube, which the weekly liveness probe covers.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "youtube-transcript" / "scripts" / "transcript.py"
sys.path.insert(0, str(SCRIPT.parent))
import transcript as yt  # noqa: E402


@pytest.mark.parametrize("value", [
    "jNQXAC9IVRw",
    "https://www.youtube.com/watch?v=jNQXAC9IVRw&t=10s",
    "youtube.com/watch?v=jNQXAC9IVRw",
    "https://youtu.be/jNQXAC9IVRw?si=abc",
    "https://m.youtube.com/shorts/jNQXAC9IVRw",
    "https://www.youtube-nocookie.com/embed/jNQXAC9IVRw",
    "https://music.youtube.com/watch?v=jNQXAC9IVRw",
])
def test_extract_video_id(value):
    assert yt.extract_video_id(value) == "jNQXAC9IVRw"


@pytest.mark.parametrize("value", ["", "https://example.com/watch?v=jNQXAC9IVRw", "https://youtu.be/short",
                                   "https://www.youtube.com/channel/UC123"])
def test_extract_video_id_rejects(value):
    with pytest.raises(ValueError):
        yt.extract_video_id(value)


def test_fmt_time():
    assert yt.fmt_time(65) == "01:05"
    assert yt.fmt_time(3725.5) == "01:02:05"
    assert yt.fmt_time(3725.5, srt=True) == "01:02:05,500"


def test_bad_input_exits_2(capsys):
    assert yt.main(["https://example.com/not-youtube"]) == yt.EXIT_BAD_INPUT
    assert capsys.readouterr().err.startswith("error:")


def _run_with_parse_args_raising(exc: str) -> subprocess.CompletedProcess:
    code = ("import argparse, runpy, sys\n"
            f"def boom(*a, **k): raise {exc}\n"
            "argparse.ArgumentParser.parse_args = boom\n"
            f"sys.argv = [{str(SCRIPT)!r}, 'x']\n"
            f"runpy.run_path({str(SCRIPT)!r}, run_name='__main__')\n")
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def test_crash_exits_7_not_1():
    """An unexpected exception in main() exits 7 with one error line, never 1."""
    p = _run_with_parse_args_raising("RuntimeError('boom\\nsecond line')")
    assert p.returncode == 7, p.stderr
    assert p.stderr.count("error:") == 1 and "unexpected RuntimeError: boom" in p.stderr
    assert "second line" not in p.stderr and "Traceback" not in p.stderr


def test_interrupt_exits_130():
    p = _run_with_parse_args_raising("KeyboardInterrupt")
    assert p.returncode == 130 and p.stderr.strip().endswith("error: interrupted")
