"""Tests for skills/youtube-audio/scripts/audio.py.

Run: uv run --with pytest --with 'yt-dlp[default]' --with mutagen python -I -m pytest test/test_youtube_audio.py -q

YouTube itself is not contacted: it bot-checks many IPs, so the download pipeline is
exercised with info dicts shaped like YouTube's (formats, thumbnail, chapters) whose
media is generated with ffmpeg and served by an in-process HTTP server. Pipeline tests
skip when ffmpeg/ffprobe are missing. The CLI is run as a subprocess for exit codes.
"""

import functools
import http.server
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "youtube-audio" / "scripts" / "audio.py"
sys.path.insert(0, str(SCRIPT.parent))
import audio as ya  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags=title,artist,date:stream=codec_name,codec_type",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    return {"duration": float(data["format"]["duration"]), "tags": {k.lower(): v for k, v in (data["format"].get("tags") or {}).items()},
            "codecs": [s["codec_name"] for s in data["streams"]]}


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not installed")
    d = tmp_path_factory.mktemp("media")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=30", "-c:a", "aac", "-b:a", "128k",
                    str(d / "a.m4a")], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=12", "-c:a", "libopus", "-b:a", "64k",
                    str(d / "b.webm")], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=320x180:d=1", "-frames:v", "1", str(d / "t.jpg")], check=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(d))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def video_info(base: str, vid: str = "testvid0001", title: str = "Test Song", chapters: bool = True, src: str = "a.m4a") -> dict:
    fmt = {"format_id": "140", "url": f"{base}/{src}", "ext": "m4a", "acodec": "mp4a.40.2", "vcodec": "none", "abr": 128, "protocol": "http"} \
        if src.endswith(".m4a") else {"format_id": "251", "url": f"{base}/{src}", "ext": "webm", "acodec": "opus", "vcodec": "none", "abr": 64, "protocol": "http"}
    info = {"id": vid, "title": title, "channel": "Test Channel", "uploader": "Test Channel", "upload_date": "20240315",
            "duration": 30 if src.endswith(".m4a") else 12, "webpage_url": f"{base}/watch?v={vid}", "original_url": f"{base}/watch?v={vid}",
            "extractor": "generic", "extractor_key": "Generic", "_type": "video", "formats": [fmt],
            "thumbnails": [{"url": f"{base}/t.jpg", "id": "0"}]}
    if chapters:
        info["chapters"] = [{"start_time": 0, "end_time": 10, "title": "Intro"}, {"start_time": 10, "end_time": 30, "title": "Main part"}]
    return info


def args_for(*extra: str):
    a = ya.make_parser().parse_args(["x", *extra])
    a.start = ya.parse_time(a.start) if a.start else None
    a.end = ya.parse_time(a.end) if a.end else None
    return a


def run(*argv: str, timeout: int = 120):
    p = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    return p.returncode, p.stdout, p.stderr


# ------------------------------------------------------------------ unit level

def test_targets_times_and_playlists():
    assert ya.normalize_target("dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert ya.normalize_target("music.youtube.com/watch?v=dQw4w9WgXcQ") == "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
    assert ya.normalize_target("https://vimeo.com/123") == "https://vimeo.com/123"  # other yt-dlp sites pass through
    assert ya.normalize_target("example.com/x") is None and ya.normalize_target("file:///etc/passwd") is None
    assert ya.is_playlist("https://www.youtube.com/@chan") and not ya.is_playlist("https://youtu.be/dQw4w9WgXcQ")
    assert ya.parse_time("1:02:03.5") == 3723.5
    with pytest.raises(ValueError):
        ya.parse_time("1:2:3:4")
    with pytest.raises(ValueError):
        ya.parse_time("soon")
    assert ya.section_length(5, None, None) is None and ya.section_length(0, 10, None) == 10


def test_error_classification_covers_youtube_messages():
    cases = {
        "ERROR: [youtube] x: Sign in to confirm you’re not a bot. This helps protect our community.": (4, "bot_check"),
        "ERROR: [youtube] x: Sign in to confirm you're not a bot": (4, "bot_check"),
        "ERROR: unable to download video data: HTTP Error 429: Too Many Requests": (4, "rate_limited"),
        "ERROR: [youtube] x: The uploader has not made this video available in your country": (4, "geo_blocked"),
        "ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate for some users.": (5, "age_restricted"),
        "ERROR: [youtube] x: Private video. Sign in if you've been granted access to this video": (5, "private"),
        "ERROR: [youtube] x: Join this channel to get access to members-only content like this video": (5, "members_only"),
        "ERROR: [youtube] x: Video unavailable. This video has been removed by the uploader": (5, "unavailable"),
        "ERROR: [youtube] x: Premieres in 2 hours": (3, "not_started"),
        "ERROR: [youtube] x: This video is DRM protected": (3, "no_audio"),
        "ERROR: Postprocessing: ffprobe and ffmpeg not found. Please install or provide the path": (2, "ffmpeg_missing"),
        "ERROR: [generic] 'x' is not a valid URL": (2, "bad_url"),
        "ERROR: Unable to download webpage: <urlopen error timed out>": (6, "network"),
        "ERROR: [youtube] x: nsig extraction failed: Some formats may be missing": (7, "extractor_outdated"),
    }
    for message, expected in cases.items():
        assert ya.classify_error(message)[:2] == expected, message
    # boilerplate tails ("This helps…", "Use --cookies…") are cut, informative sentences are kept
    assert ya.short_message("ERROR: ERROR: [youtube] x: Video unavailable. This video has been removed") == "[youtube] x: Video unavailable. This video has been removed"
    assert ya.short_message("ERROR: [youtube] x: Sign in to confirm you’re not a bot. Use --cookies-from-browser") == "[youtube] x: Sign in to confirm you’re not a bot."


def test_build_opts_shapes_the_pipeline(tmp_path):
    names = lambda o: [p["key"] for p in o["postprocessors"]]  # noqa: E731
    o = ya.build_opts(args_for(), tmp_path)
    assert o["format"].startswith("bestaudio[ext=m4a]") and names(o) == ["FFmpegExtractAudio", "FFmpegMetadata", "FFmpegThumbnailsConvertor", "EmbedThumbnail"]
    assert o["postprocessors"][0]["preferredcodec"] == "best" and o["noplaylist"] and o["playlistend"] == 25 and o["writethumbnail"]
    o = ya.build_opts(args_for("--mp3"), tmp_path)
    assert o["postprocessors"][0] == {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "nopostoverwrites": False, "preferredquality": "2"}
    o = ya.build_opts(args_for("--format", "opus", "--bitrate", "96", "--no-cover", "--no-metadata"), tmp_path)
    assert names(o) == ["FFmpegExtractAudio"] and o["postprocessors"][0]["preferredquality"] == "96K" and not o["writethumbnail"]
    assert o["format"].startswith("bestaudio[acodec=opus]")
    o = ya.build_opts(args_for("--split-chapters"), tmp_path)
    assert names(o).index("FFmpegSplitChapters") < names(o).index("EmbedThumbnail")  # split before the cover is embedded
    o = ya.build_opts(args_for("--start", "10"), tmp_path)
    assert next(p for p in o["postprocessors"] if p["key"] == "FFmpegMetadata")["add_chapters"] is False
    o = ya.build_opts(args_for("--cookies-from-browser", "Firefox:work", "--items", "1-3", "--playlist"), tmp_path)
    assert o["cookiesfrombrowser"] == ("firefox", "work", None, None) and o["playlist_items"] == "1-3" and o["playlistend"] is None and not o["noplaylist"]
    assert isinstance(o["logger"], ya.QuietLogger)
    assert ya.quality_arg(args_for("--format", "flac")) is None


def test_cli_argument_errors_exit_2(tmp_path):
    for argv in (["not a url"], ["dQw4w9WgXcQ", "--start", "50", "--end", "10"], ["dQw4w9WgXcQ", "--start", "soon"],
                 ["dQw4w9WgXcQ", "--format", "flac", "--bitrate", "320"], ["dQw4w9WgXcQ", "--split-chapters", "--start", "5"],
                 ["dQw4w9WgXcQ", "--max-items", "0"], ["dQw4w9WgXcQ", "--cookies", str(tmp_path / "missing.txt")]):
        rc, out, err = run(*argv)
        assert rc == 2 and err.startswith("error: ") and out == "", argv
    blocker = tmp_path / "file"
    blocker.write_text("x")
    rc, _, err = run("dQw4w9WgXcQ", "-o", str(blocker / "sub"))
    assert rc == 2 and "cannot write" in err


# ------------------------------------------------------------------ pipeline

@ffmpeg
def test_original_keeps_stream_and_embeds_tags_and_cover(media, tmp_path):
    [r] = ya.process_info(video_info(media), args_for(), tmp_path)
    assert r["ok"] and r["title"] == "Test Song" and r["channel"] == "Test Channel" and r["date"] == "2024-03-15" and r["section"] is None
    [f] = r["files"]
    path = Path(f["path"])
    assert path.name == "Test Song [testvid0001].m4a" and path.parent == tmp_path and f["bytes"] == path.stat().st_size
    info = probe(path)
    assert "aac" in info["codecs"] and "mjpeg" in info["codecs"] and abs(info["duration"] - 30) < 0.2
    assert info["tags"]["title"] == "Test Song" and info["tags"]["artist"] == "Test Channel"
    assert not list(tmp_path.glob("*.jpg")) and not list(tmp_path.glob("*.part"))  # thumbnail consumed, no leftovers


@ffmpeg
def test_mp3_conversion_and_bitrate(media, tmp_path):
    [r] = ya.process_info(video_info(media), args_for("--mp3", "--bitrate", "128", "--no-cover"), tmp_path)
    path = Path(r["files"][0]["path"])
    info = probe(path)
    assert path.suffix == ".mp3" and info["codecs"] == ["mp3"] and info["tags"]["title"] == "Test Song"


@ffmpeg
def test_section_is_cut_locally(media, tmp_path):
    [r] = ya.process_info(video_info(media), args_for("--mp3", "--start", "5", "--end", "0:12"), tmp_path)
    info = probe(Path(r["files"][0]["path"]))
    assert abs(info["duration"] - 7) < 0.15 and r["section"] == [5.0, 12.0] and r["section_duration"] == 7
    [r] = ya.process_info(video_info(media, vid="testvid0002"), args_for("--start", "20"), tmp_path)
    assert abs(probe(Path(r["files"][0]["path"]))["duration"] - 10) < 0.15 and r["section_duration"] == 10
    assert not list(tmp_path.glob("*.cut.*"))


@ffmpeg
@pytest.mark.parametrize("fmt", ["opus", "mp3"])
def test_split_chapters(media, tmp_path, fmt):
    [r] = ya.process_info(video_info(media), args_for("--split-chapters", "--format", fmt), tmp_path)
    assert [c["title"] for c in r["chapters"]] == ["Intro", "Main part"]
    first, second = (Path(c["path"]) for c in r["chapters"])
    assert first.parent.name == "Test Song [testvid0001]" and first.name == f"01 - Intro.{fmt}" and second.name == f"02 - Main part.{fmt}"
    assert abs(probe(first)["duration"] - 10) < 0.2 and abs(probe(second)["duration"] - 20) < 0.2
    assert Path(r["files"][0]["path"]).exists()  # the full file is kept too


@ffmpeg
def test_playlist_records_and_skip_existing(media, tmp_path):
    playlist = {"_type": "playlist", "id": "PLtest", "title": "Mix", "webpage_url": f"{media}/playlist?list=PLtest",
                "extractor": "generic", "extractor_key": "Generic",
                "entries": [video_info(media, "vid0000001", "First", False), video_info(media, "vid0000002", "Second", False, "b.webm")]}
    recs = ya.process_info(playlist, args_for(), tmp_path)
    assert [r["title"] for r in recs] == ["First", "Second"] and all(r["ok"] for r in recs)
    assert Path(recs[1]["files"][0]["path"]).suffix in (".opus", ".webm", ".ogg")  # original opus stream kept, not re-encoded to aac
    stamp = Path(recs[0]["files"][0]["path"]).stat().st_mtime_ns
    again = ya.process_info(playlist, args_for(), tmp_path)
    assert all(r["ok"] for r in again) and Path(again[0]["files"][0]["path"]).stat().st_mtime_ns == stamp  # not downloaded twice


@ffmpeg
def test_human_report_lists_files(media, tmp_path):
    a = args_for("--split-chapters")
    recs = ya.process_info(video_info(media), a, tmp_path)
    text = ya.human(recs, a)
    assert text.startswith("saved: Test Song — Test Channel (0:30)") and "01 - Intro" in text and "MB)" in text
    failed = [{"ok": False, "url": "u", "error": {"kind": "bot_check", "message": "m"}}]
    assert ya.human(failed, a) == "failed: u: bot_check — m\n"


def test_page_text_cannot_forge_report_lines():
    info = {"id": "v1", "title": "Song\nsaved: /etc/passwd  (1.0 MB)", "channel": "Ch\r\nfailed: x", "webpage_url": "u", "duration": 5,
            "chapters": [{"title": "Intro\n  /tmp/evil.mp3", "start_time": 0, "end_time": 5}], "formats": []}
    a = args_for("--info")
    text = ya.human([ya.info_record(info)], a)
    assert text.count("\n") == 4 and "Song saved: /etc/passwd" in text and "Intro /tmp/evil.mp3" in text
