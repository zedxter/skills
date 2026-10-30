"""Tests for skills/youtube-search/scripts/search.py.

Run: uv run --no-project --with pytest --with yt-dlp python -I -m pytest test/test_youtube_search.py -q \
         --doctest-modules skills/youtube-search/scripts/search.py

Offline only: formatting, the search filter parameter, bad input and the crash guard. Searching needs YouTube,
which the weekly liveness probe covers.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "youtube-search" / "scripts" / "search.py"
sys.path.insert(0, str(SCRIPT.parent))
import search as ys  # noqa: E402


def test_fmt_duration_and_views():
    assert ys.fmt_duration(None) == "live/?"
    assert ys.fmt_duration(65) == "1:05"
    assert ys.fmt_duration(3725) == "1:02:05"
    assert ys.fmt_views(None) == "?"
    assert ys.fmt_views(999) == "999"
    assert ys.fmt_views(1234) == "1.2K"
    assert ys.fmt_views(25_000_000) == "25M"


def test_build_sp_is_stable_base64():
    sp = ys.build_sp("date", "week", "video", "long")
    assert sp and sp == ys.build_sp("date", "week", "video", "long")
    assert sp != ys.build_sp("date", None, None, None)


def test_empty_query_exits_2(capsys):
    assert ys.main([""]) == ys.EXIT_BAD_INPUT
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
    """An unexpected exception in main() exits 7 with one error line, never 1, which means "no results"."""
    p = _run_with_parse_args_raising("RuntimeError('boom\\nsecond line')")
    assert p.returncode == 7, p.stderr
    assert p.stderr.count("error:") == 1 and "unexpected RuntimeError: boom" in p.stderr
    assert "second line" not in p.stderr and "Traceback" not in p.stderr


def test_interrupt_exits_130():
    p = _run_with_parse_args_raising("KeyboardInterrupt")
    assert p.returncode == 130 and p.stderr.strip().endswith("error: interrupted")


@pytest.mark.skipif(sys.platform == "win32", reason="a closed pipe is not EPIPE on Windows")
def test_closed_stdout_pipe_exits_0_quietly():
    """`| head` closing the pipe early is not a bug: exit 0 and no error line."""
    code = ("import argparse, runpy, sys\n"
            "def flood(*a, **k):\n"
            "    while True:\n"
            "        sys.stdout.write('x' * 65536)\n"
            "argparse.ArgumentParser.parse_args = flood\n"
            f"sys.argv = [{str(SCRIPT)!r}, 'x']\n"
            f"runpy.run_path({str(SCRIPT)!r}, run_name='__main__')\n")
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p.stdout.close()
    err = p.stderr.read()
    assert p.wait(timeout=60) == 0 and b"error" not in err, err
