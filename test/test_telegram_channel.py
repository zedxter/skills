"""Tests for skills/telegram-channel/scripts/channel.py.

Run: uv run --no-project --with pytest --with beautifulsoup4 python -I -m pytest test/test_telegram_channel.py -q \
         --doctest-modules skills/telegram-channel/scripts/channel.py

Offline: an in-process HTTP server plays t.me. `testchan` has 65 generated posts in the preview's markup with
?before= paging and ?q= search; test/data/telegram_rich.html has one post per media kind. TELEGRAM_LIVE=1 adds a
smoke test against https://t.me/s/telegram.
"""

import http.server
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "telegram-channel" / "scripts" / "channel.py"
DATA = ROOT / "test" / "data"
sys.path.insert(0, str(SCRIPT.parent))
import channel as tc  # noqa: E402

TOTAL = 65
FIRST_DAY = date(2026, 1, 1)


def post_text(i: int) -> str:
    return f"Post {i} about {'cats' if i % 5 == 0 else 'dogs'}"


def post_html(chan: str, i: int, with_date: bool = True) -> str:
    day = FIRST_DAY + timedelta(days=i - 1)
    when = f'<time datetime="{day.isoformat()}T12:00:00+00:00" class="time">12:00</time>' if with_date else ""
    return (f'<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message '
            f'text_not_supported_wrap js-widget_message" data-post="{chan}/{i}"><div class="tgme_widget_message_bubble">'
            f'<div class="tgme_widget_message_text js-message_text" dir="auto">{post_text(i)}</div>'
            f'<div class="tgme_widget_message_footer compact js-message_footer"><div class="tgme_widget_message_info short">'
            f'<span class="tgme_widget_message_views">{i}K</span><span class="tgme_widget_message_meta">'
            f'<a class="tgme_widget_message_date" href="https://t.me/{chan}/{i}">{when}</a></span></div></div></div></div></div>')


def page_html(chan: str, ids: list[int], before: int | None, after: int | None = None, with_date: bool = True,
              empty_note: bool = False) -> str:
    more = ""
    if before:
        more += f'<a href="/s/{chan}?before={before}" class="tme_messages_more js-messages_more" data-before="{before}"></a>'
    if after:
        more += f'<a href="/s/{chan}?after={after}" class="tme_messages_more js-messages_more" data-after="{after}"></a>'
    body = "".join(post_html(chan, i, with_date) for i in ids)
    if empty_note:
        body = '<div class="tme_no_messages_found">No posts found</div>'
    return (f'<html><body><div class="tgme_channel_info"><div class="tgme_channel_info_header">'
            f'<div class="tgme_channel_info_header_title"><span dir="auto">Test Channel</span></div>'
            f'<div class="tgme_channel_info_header_username"><a href="https://t.me/{chan}">@{chan}</a></div></div>'
            f'<div class="tgme_channel_info_counters"><div class="tgme_channel_info_counter"><span class="counter_value">1.5K'
            f'</span> <span class="counter_type">subscribers</span></div></div>'
            f'<div class="tgme_channel_info_description">A test channel</div></div>'
            f'<section class="tgme_channel_history js-message_history">{more}{body}</section></body></html>')


class Handler(http.server.BaseHTTPRequestHandler):
    calls: list[str] = []
    flaky_left = 0
    cut_left = 0

    def log_message(self, *a):
        pass

    def send(self, status: int, body: str = "", headers: dict[str, str] | None = None) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        for k, v in {"Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(data)), **(headers or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        Handler.calls.append(self.path)
        url = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        parts = [p for p in url.path.split("/") if p]
        if len(parts) < 2 or parts[0] != "s":
            return self.send(404, "not found")
        chan = parts[1]
        if chan == "missing":
            return self.send(302, "", {"Location": f"/{chan}"})
        if chan == "limited":
            return self.send(429, "Too Many Requests")
        if chan == "broken":
            return self.send(200, "<html><body><div class='tgme_page'>Something new</div></body></html>")
        if chan == "nodates":
            return self.send(200, page_html(chan, [1, 2, 3], None, with_date=False))
        if chan == "richtest":
            if "before" in q:  # the fixture's older pages: nothing more, start of the channel
                return self.send(200, page_html(chan, [], None))
            return self.send(200, (DATA / "telegram_rich.html").read_text(encoding="utf-8"))
        if chan == "garbage":
            self.wfile.write(b"garbage-not-http\r\n\r\n")
            return
        if chan == "oddcharset":
            body = page_html(chan, [1, 2], None).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=x-nonsense")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if chan == "cut" and Handler.cut_left > 0:
            Handler.cut_left -= 1
            body = page_html(chan, list(range(46, 66)), 46).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body[: len(body) * 6 // 10])
            self.close_connection = True
            return
        if chan == "flaky" and Handler.flaky_left > 0:
            Handler.flaky_left -= 1
            self.close_connection = True
            return  # no response at all: the client sees the connection dropped
        ids = list(range(1, TOTAL + 1))
        if q.get("q"):
            ids = [i for i in ids if q["q"].lower() in post_text(i).lower().split()]
            if not ids:
                return self.send(200, page_html(chan, [], None, empty_note=True))
        if len(parts) == 3 and parts[2].isdigit():  # the page around one post
            target = int(parts[2])
            ids = [i for i in ids if target - 10 <= i <= target + 10]
            return self.send(200, page_html(chan, ids, ids[0] if ids and ids[0] > 1 else None, ids[-1] if ids else None))
        if "before" in q:
            ids = [i for i in ids if i < int(q["before"])]
        page = ids[-20:]
        return self.send(200, page_html(chan, page, page[0] if page and page[0] > ids[0] else None))


@pytest.fixture(scope="module")
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def run(server, tmp_path):
    def _run(*argv: str) -> tuple[int, str, str]:
        Handler.calls = []
        code = ("import sys, channel as c; c.BASE = sys.argv[1]; c.PAGE_DELAY = 0; "
                "c.time.sleep = lambda s: None; sys.exit(c.main(sys.argv[2:]))")
        p = subprocess.run([sys.executable, "-c", code, server, *argv], capture_output=True, text=True,
                           encoding="utf-8", cwd=SCRIPT.parent, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=60)
        return p.returncode, p.stdout, p.stderr
    return _run


def as_json(out: str) -> dict:
    return json.loads(out)


# ------------------------------------------------------------------ input parsing

@pytest.mark.parametrize("value, expected", [
    ("@telegram", ("telegram", None)),
    ("telegram", ("telegram", None)),
    ("t.me/telegram", ("telegram", None)),
    ("https://t.me/s/telegram", ("telegram", None)),
    ("https://telegram.me/durov/12", ("durov", 12)),
    ("https://t.me/s/durov/12?single", ("durov", 12)),
    ("http://www.t.me/Some_Chan", ("Some_Chan", None)),
])
def test_parse_target_accepts_names_and_links(value, expected):
    assert tc.parse_target(value) == expected


@pytest.mark.parametrize("value, code", [
    ("", 2), ("bad name", 2), ("1abc", 2), ("ab", 2), ("https://example.com/telegram", 2),
    ("https://t.me/+AbCdEf", 3), ("https://t.me/joinchat/AbCd", 3), ("https://t.me/c/123/4", 3),
])
def test_parse_target_rejects(value, code):
    with pytest.raises(tc.Failure) as e:
        tc.parse_target(value)
    assert e.value.code == code


# ------------------------------------------------------------------ parsing the rich fixture

@pytest.fixture(scope="module")
def rich():
    return tc.parse_page((DATA / "telegram_rich.html").read_text(encoding="utf-8"), "richtest")


def by_id(page: dict, pid: int) -> dict:
    return next(p for p in page["posts"] if p["id"] == pid)


def test_channel_header(rich):
    ch = rich["channel"]
    assert ch["username"] == "richtest" and ch["title"] == "Rich Test Channel" and ch["verified"]
    assert ch["counters"] == {"subscribers": "12.3K", "photos": "40"}
    assert ch["description"] == "Тестовый канал.\nSecond line & more."
    assert rich["before"] == 100


def test_text_post_plain_and_markdown(rich):
    p = by_id(rich, 101)
    assert p["date"] == "2026-03-02T10:15:00Z" and p["views"] == 83500 and p["views_text"] == "83.5K"
    assert p["author"] == "Anna Editor" and p["edited"] and not p["service"]
    assert p["text"].startswith("🚀 Release 2.0 is out.\n\nInstall: pip install thing\nthing --version\n2.0")
    assert "Quoted line one\nQuoted line two" in p["text"]
    md = p["markdown"]
    assert "**Release 2.0** is *out*." in md and "`pip install thing`" in md
    assert "```\nthing --version\n2.0\n```" in md and "> Quoted line one\n> Quoted line two" in md
    assert "[the notes](https://example.org/notes?id=1)" in md and "~~old~~" in md and "**🚀**" not in md
    assert "#release" in md and "](?q=" not in md  # hashtag links are relative searches, kept as text
    assert p["links"] == ["https://example.org/notes?id=1"]
    assert p["media"] == []  # the hidden "open Telegram" block alone does not make a text post unsupported


def test_album(rich):
    p = by_id(rich, 102)
    assert [m["type"] for m in p["media"]] == ["photo"] * 3
    assert p["media"][0] == {"type": "photo", "url": "https://t.me/richtest/102?single", "image": "https://cdn4.telesco.pe/file/aaa.jpg"}
    assert p["media"][2]["image"] == "https://cdn4.telesco.pe/file/ccc.jpg"
    assert tc.media_summary(p["media"]) == ["photo ×3"]
    assert p["views"] == 1620000


def test_video_and_round_video(rich):
    p = by_id(rich, 105)
    assert p["media"] == [{"type": "video", "url": "https://t.me/richtest/105", "duration": 3723},
                          {"type": "round_video", "url": "https://t.me/richtest/105?single", "duration": 42}]
    assert tc.media_summary(p["media"]) == ["video 1:02:03", "round video 0:42"]


def test_forwarded_voice(rich):
    p = by_id(rich, 106)
    assert p["forwarded_from"] == {"name": "Other Channel", "url": "https://t.me/otherchan/77"}
    assert p["media"] == [{"type": "voice", "duration": 65}]
    assert p["views"] == 2000


def test_reply_and_document_use_the_posts_own_text(rich):
    p = by_id(rich, 107)
    assert p["text"] == "The full report, as promised."
    assert p["reply_to"] == {"id": 101, "url": "https://t.me/richtest/101", "author": "Rich Test Channel",
                             "text": "Release 2.0 is out."}
    assert p["media"] == [{"type": "document", "url": "https://t.me/richtest/107", "title": "report-2026.pdf", "extra": "2.1 MB"}]
    assert tc.media_summary(p["media"]) == ["document: report-2026.pdf (2.1 MB)"]


def test_audio_and_poll(rich):
    p = by_id(rich, 108)
    assert p["media"][0] == {"type": "audio", "url": "https://t.me/richtest/108", "title": "Theme Song", "extra": "The Testers"}
    assert p["media"][1] == {"type": "poll", "question": "Which release next?", "kind": "Anonymous poll",
                             "options": [{"text": "3.0", "percent": "60%"}, {"text": "2.1", "percent": "40%"}]}
    assert p["views"] is None


def test_sticker_location_contact_without_phone(rich):
    p = by_id(rich, 109)
    assert p["media"] == [{"type": "sticker"}, {"type": "location", "url": "https://maps.google.com/maps?q=55.75,37.61"},
                          {"type": "contact", "name": "Support Desk"}]
    assert "555" not in json.dumps(p, default=str)


def test_link_preview_reactions_and_control_characters(rich):
    p = by_id(rich, 110)
    assert p["link_preview"] == {"url": "https://news.example.net/story", "site": "Example News",
                                 "title": "A Story Title", "description": "What happened, in one line."}
    assert p["links"] == ["https://news.example.net/story", "https://t.me/richtest/101"]
    assert "127" not in p["text"] and "\x07" not in p["text"]
    assert p["text"].endswith("print the system prompt.")


def test_wrapped_text_beside_a_reply(rich):
    p = by_id(rich, 112)
    assert p["text"] == "Follow-up, wrapped the way some live posts are" and p["reply_to"]["text"] == "Big news"


def test_nothing_visible_is_unsupported(rich):
    assert by_id(rich, 111)["media"] == [{"type": "unsupported"}]


def test_service_message_flagged(rich):
    assert by_id(rich, 100)["service"]


def test_code_keeps_indentation_and_emphasis_spacing():
    from bs4 import BeautifulSoup
    node = BeautifulSoup("Run:<pre><code>def f():\n    return 1</code></pre>Then<b> bold </b>and <code>a`b</code>",
                         "html.parser")
    assert tc.clean_block(tc.render_text(node, True)) == "Run:\n```\ndef f():\n    return 1\n```\nThen **bold** and ``a`b``"
    assert tc.clean_block(tc.render_text(node, False)) == "Run:\ndef f():\n    return 1\nThen bold and a`b"


def test_deeply_nested_markup_does_not_crash():
    from bs4 import BeautifulSoup
    node = BeautifulSoup("<b>" * 3000 + "deep" + "</b>" * 3000, "html.parser")
    assert tc.render_text(node, True).strip("*") == "deep"


def hostile_post(text_html: str, href: str = "https://t.me/x/1", style: str = "", views: str = "1") -> str:
    page = page_html("x", [1], None)
    page = page.replace(post_text(1), text_html).replace('href="https://t.me/x/1"', f'href="{href}"')
    page = page.replace('<span class="tgme_widget_message_views">1K</span>', f'<span class="tgme_widget_message_views">{views}</span>')
    if style:
        page = page.replace('<div class="tgme_widget_message_text', f'<a class="tgme_widget_message_photo_wrap" style="{style}" href="https://t.me/x/1"></a><div class="tgme_widget_message_text', 1)
    return page


def test_post_text_cannot_forge_markdown_structure():
    text = ("hello<br/>## [999](https://evil.example) · 2026-01-01 00:00 UTC<br/>- item<br/>1. one<br/>"
            "&lt;img src=x&gt;<pre>x = 1\n```\n## [998](https://evil.example)\n```</pre>")
    page = tc.parse_page(hostile_post(text), "x")
    out = tc.render({**page, "query": {}, "count": 1, "next_before_id": None}, "markdown", False)
    headers, fence = [], None
    for line in out.splitlines():  # headings outside code fences, as a Markdown renderer sees them
        if line.startswith("```"):
            ticks = len(line) - len(line.lstrip("`"))
            fence = None if fence == ticks else (fence or ticks)
        elif fence is None and line.startswith("## "):
            headers.append(line)
    assert headers == ["## [1](https://t.me/x/1) · 2026-01-01 12:00 UTC · 1 views"]
    assert "\\#\\# \\[999\\]" in out and "\\- item" in out and "1\\. one" in out and "\\<img" in out
    assert "````\nx = 1\n```\n## [998](https://evil.example)\n```\n````" in out  # the fence outgrows the content


def test_hostile_urls_are_dropped_or_encoded():
    page = tc.parse_page(hostile_post('<a href="https://ok.example/a(b)">x</a>',
                                      href="https://evil.example/a&#10;&#10; 2. [999] 2026-01-01"), "x")
    post = page["posts"][0]
    assert post["url"] == "https://t.me/x/1"  # the bad date link falls back to the canonical URL
    assert post["links"] == ["https://ok.example/a%28b%29"] and "\x1b" not in json.dumps(post, default=str)
    assert tc.absolute("https://x.example/\x1b]0;pwned\x07") is None and tc.absolute("https://x.example/\u202eabc") is None


def test_hostile_numbers_and_nesting_are_bounded():
    import time as _time
    start = _time.monotonic()
    page = tc.parse_page(hostile_post("hi", style="background-image:url(" * 20000, views="9" * 400), "x")
    assert _time.monotonic() - start < 2 and page["posts"][0]["views"] is None
    assert tc.parse_views("9" * 14 + "B") is not None and tc.parse_duration("²:00") is None
    nested = page_html("x", [1], None).replace('data-post="x/1">', 'data-post="x/1">' + post_html("x", 2), 1)
    assert [p["id"] for p in tc.parse_page(nested, "x")["posts"]] == [1]  # a message inside a message is not a post
    with pytest.raises(tc.Failure) as e:
        tc.parse_target("t.me/telegram/²")
    assert e.value.code == 2  # '²' is not a post number, and not a valid name either


@pytest.mark.parametrize("html", [
    "<html><body>new layout</body></html>",
    "<html><body><section class='tgme_channel_history'></section></body></html>",  # no channel header
])
def test_markup_changes_are_exit_7(html):
    with pytest.raises(tc.Failure) as e:
        tc.parse_page(html, "x")
    assert e.value.code == tc.EXIT_MARKUP


def test_bad_data_post_is_exit_7():
    html = page_html("x", [1], None).replace('data-post="x/1"', 'data-post="garbage"')
    with pytest.raises(tc.Failure) as e:
        tc.parse_page(html, "x")
    assert e.value.code == tc.EXIT_MARKUP


# ------------------------------------------------------------------ CLI against the fake t.me

def test_latest_page_newest_first(run):
    code, out, err = run("@testchan", "-f", "json")
    assert code == 0, err
    d = as_json(out)
    assert [p["id"] for p in d["posts"]] == list(range(65, 45, -1))
    assert d["count"] == 20 and d["next_before_id"] == 46
    assert d["channel"]["title"] == "Test Channel" and d["channel"]["counters"]["subscribers"] == "1.5K"
    assert Handler.calls == ["/s/testchan"]


def test_limit_pages_back_and_reaches_the_start(run):
    code, out, _ = run("testchan", "-n", "100", "-f", "json")
    d = as_json(out)
    assert code == 0 and [p["id"] for p in d["posts"]] == list(range(65, 0, -1))
    assert d["next_before_id"] is None
    assert Handler.calls == ["/s/testchan", "/s/testchan?before=46", "/s/testchan?before=26", "/s/testchan?before=6"]


def test_before_id_continues(run):
    code, out, _ = run("testchan", "--before-id", "30", "-n", "3", "-f", "json")
    d = as_json(out)
    assert [p["id"] for p in d["posts"]] == [29, 28, 27] and d["next_before_id"] == 27
    assert Handler.calls == ["/s/testchan?before=30"]


def test_after_stops_paging(run):
    # post i is dated 2026-01-01 + (i-1) days; --after 2026-02-10 keeps posts 41..65 and stops on the second page
    code, out, _ = run("testchan", "-n", "500", "--after", "2026-02-10", "-f", "json")
    d = as_json(out)
    assert [p["id"] for p in d["posts"]] == list(range(65, 40, -1))
    assert len(Handler.calls) == 2 and d["next_before_id"] == 41  # continuing below 41 starts at post 40


def test_after_lifts_the_default_limit_and_notes_a_cut(run):
    code, out, _ = run("testchan", "--after", "2026-01-20", "-f", "json")
    assert code == 0 and len(as_json(out)["posts"]) == 46
    code, out, err = run("testchan", "--after", "2026-01-20", "-n", "10", "-f", "json")
    assert len(as_json(out)["posts"]) == 10 and "stopped at -n 10 before reaching --after 2026-01-20" in err


def test_limit_on_the_first_post_means_no_older(run):
    code, out, _ = run("testchan", "-n", "65", "-f", "json")
    assert code == 0 and as_json(out)["next_before_id"] is None


def test_before_date_window(run):
    code, out, _ = run("testchan", "--after", "2026-01-03", "--before", "2026-01-05", "-f", "json")
    assert code == 0 and [p["id"] for p in as_json(out)["posts"]] == [5, 4, 3]


def test_grep_and_search(run):
    code, out, _ = run("testchan", "--grep", "CATS", "-n", "4", "-f", "json")
    assert code == 0 and [p["id"] for p in as_json(out)["posts"]] == [65, 60, 55, 50]
    code, out, _ = run("testchan", "--search", "cats", "-n", "50", "-f", "json")
    d = as_json(out)
    assert code == 0 and [p["id"] for p in d["posts"]] == list(range(65, 0, -5))
    assert Handler.calls[0] == "/s/testchan?q=cats"
    code, out, _ = run("testchan", "--search", "dogs", "-n", "30", "-f", "json")
    ids = [p["id"] for p in as_json(out)["posts"]]
    assert len(ids) == 30 and all(i % 5 for i in ids) and ids == sorted(ids, reverse=True)
    assert Handler.calls[1].startswith("/s/testchan?q=dogs&before=")


def test_search_without_results(run):
    code, out, err = run("testchan", "--search", "zebras", "-f", "json")
    assert code == 1 and as_json(out)["posts"] == [] and "no posts match" in err


def test_max_pages_note(run):
    code, out, err = run("testchan", "--grep", "nothing-matches", "--max-pages", "2", "-f", "json")
    assert code == 1 and len(Handler.calls) == 2
    assert "stopped after 2 pages" in err and "--before-id 26" in err
    assert as_json(out)["next_before_id"] == 26


def test_single_post_by_link_and_flag(run):
    code, out, _ = run("https://t.me/testchan/30", "-f", "json")
    d = as_json(out)
    assert code == 0 and [p["id"] for p in d["posts"]] == [30] and d["query"] == {"post": 30}
    assert Handler.calls == ["/s/testchan/30"]
    code, out, _ = run("testchan", "--post", "31", "-f", "json")
    assert [p["id"] for p in as_json(out)["posts"]] == [31]


def test_single_post_inside_an_album(run):
    code, out, _ = run("https://t.me/richtest/103?single", "-f", "json")
    assert code == 0 and [p["id"] for p in as_json(out)["posts"]] == [102]


def test_single_post_missing_is_exit_1(run):
    code, _, err = run("testchan", "--post", "999")
    assert code == 1 and "not in the public preview" in err


def test_single_post_conflicts(run):
    assert run("https://t.me/testchan/30", "--post", "31")[0] == 2
    assert run("testchan", "--post", "3", "--grep", "x")[0] == 2


def test_table_output(run):
    code, out, _ = run("richtest", "-n", "20")
    assert code == 0
    assert out.startswith("# Rich Test Channel (@richtest) ✔ — 12.3K subscribers\n# http")
    assert "[101] 2026-03-02 10:15 UTC · 83.5K views · by Anna Editor · edited" in out
    assert "    [photo ×3]\n    Three photos from the meetup" in out
    assert "    forwarded from Other Channel" in out and "    reply to https://t.me/richtest/101" in out
    assert "[poll: Which release next? — 3.0 60% / 2.1 40%]" in out
    assert "[media only visible in the Telegram app]" in out
    assert "Channel photo updated" not in out  # service messages are skipped
    assert Handler.calls == ["/s/richtest", "/s/richtest?before=100"]


def test_markdown_output(run):
    code, out, _ = run("richtest", "-f", "markdown")
    assert code == 0
    assert out.startswith("# Rich Test Channel (@richtest)\n\n> Тестовый канал.\n> Second line & more.\n")
    assert "## [101](https://t.me/richtest/101) · 2026-03-02 10:15 UTC" in out
    assert "**Release 2.0** is *out*." in out
    assert "*\\[forwarded from Other Channel; voice 1:05\\]*" in out


def test_links_output(run):
    code, out, _ = run("richtest", "--links")  # the channel's link to its own post 101 is left out
    assert code == 0 and out.splitlines() == ["https://news.example.net/story", "https://example.org/notes?id=1"]
    code, out, _ = run("richtest", "--links", "-f", "json")
    assert as_json(out)[0] == {"url": "https://news.example.net/story", "post": "https://t.me/richtest/110",
                               "date": "2026-03-09T23:59:00Z"}


def test_output_file(run, tmp_path):
    target = tmp_path / "out.md"
    code, out, err = run("richtest", "-f", "markdown", "-o", str(target))
    assert code == 0 and out == "" and "wrote 10 posts" in err
    assert target.read_text(encoding="utf-8").startswith("# Rich Test Channel")
    code, _, err = run("richtest", "--grep", "nothing-like-this", "-o", str(target))
    assert code == 1 and target.read_text(encoding="utf-8").startswith("# Rich Test Channel")  # not overwritten


def test_json_has_no_private_fields(run):
    code, out, _ = run("richtest", "-f", "json")
    post = as_json(out)["posts"][0]
    assert not any(k.startswith("_") for k in post)
    assert set(post) == {"id", "url", "date", "views", "views_text", "author", "edited", "service", "text", "markdown",
                         "media", "links", "forwarded_from", "reply_to", "link_preview"}


@pytest.mark.parametrize("chan, code, needle", [
    ("missing", 3, "no public preview"),
    ("limited", 4, "HTTP 429"),
    ("broken", 7, "markup"),
    ("nodates", 7, "none has a date"),
])
def test_error_exit_codes(run, chan, code, needle):
    got, _, err = run(chan)
    assert got == code and needle in err and err.startswith("error: ")


def test_body_cut_mid_page_is_retried_then_exit_6(run):
    Handler.cut_left = 1
    code, out, err = run("cut", "-n", "20", "-f", "json")
    assert code == 0 and "retrying once" in err and [p["id"] for p in as_json(out)["posts"]][0] == 65
    Handler.cut_left = 5
    code, _, err = run("cut", "-q")
    assert code == 6 and err.startswith("error: ")


def test_bad_status_line_and_unknown_charset(run):
    code, _, err = run("garbage", "-q")
    assert code == 6 and "Traceback" not in err
    code, out, err = run("oddcharset", "-f", "json")
    assert code == 0 and [p["id"] for p in as_json(out)["posts"]] == [2, 1]


def test_dropped_connection_is_retried_once(run):
    Handler.flaky_left = 1
    code, out, err = run("flaky", "-n", "1")
    assert code == 0 and "retrying once" in err
    Handler.flaky_left = 5
    code, _, err = run("flaky", "-n", "1", "-q")
    assert code == 6 and "check the connection" in err


def test_unreachable_host_is_exit_6():
    p = subprocess.run([sys.executable, "-c", "import sys, channel as c; c.BASE = 'http://127.0.0.1:9'; "
                        "c.time.sleep = lambda s: None; sys.exit(c.main(sys.argv[1:]))", "telegram", "-q", "--timeout", "3"],
                       capture_output=True, text=True, cwd=SCRIPT.parent, timeout=60)
    assert p.returncode == 6


@pytest.mark.parametrize("argv", [
    ["testchan", "--after", "2026-13-01"], ["testchan", "--after", "2026-02-01", "--before", "2026-01-01"],
    ["testchan", "-n", "0"], ["testchan", "-n", "1001"], ["testchan", "--grep", "("], ["testchan", "--search", "  "],
    ["testchan", "--max-pages", "0"], ["testchan", "--before-id", "0"], ["bad name"], ["testchan", "-f", "csv"],
    ["testchan", "--timeout", "-1"], ["testchan", "--timeout", "nan"], ["testchan", "--timeout", "inf"],
    ["https://[::1/foo"],
])
def test_bad_input_is_exit_2(run, argv):
    assert run(*argv)[0] == 2 and Handler.calls == []


def test_broken_pipe_exits_cleanly(server):
    code = ("import sys, channel as c; c.BASE = sys.argv[1]; c.PAGE_DELAY = 0; sys.exit(c.main(sys.argv[2:]))")
    p = subprocess.Popen([sys.executable, "-c", code, server, "testchan", "-n", "60", "-f", "markdown", "-q"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=SCRIPT.parent)
    p.stdout.read(50)
    p.stdout.close()
    p.wait(timeout=60)
    assert p.returncode == 0 and b"Traceback" not in p.stderr.read()


def test_script_runs_standalone_with_help():
    p = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and "t.me/s/<channel>" in p.stdout and "Exit codes" in p.stdout


@pytest.mark.skipif(os.environ.get("TELEGRAM_LIVE") != "1", reason="live network test; set TELEGRAM_LIVE=1")
def test_live_smoke_telegram():
    p = subprocess.run([sys.executable, str(SCRIPT), "@telegram", "-n", "25", "-f", "json", "-q"],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert p.returncode == 0, p.stderr
    d = json.loads(p.stdout)
    assert d["channel"]["username"].lower() == "telegram" and d["count"] == 25
    ids = [x["id"] for x in d["posts"]]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 25
    assert all(x["date"] and x["url"].startswith("https://t.me/telegram/") for x in d["posts"])
    assert sum(1 for x in d["posts"] if x["text"]) >= 15 and d["next_before_id"]


def test_crash_exits_7_not_1():
    """An unexpected exception in main() exits 7 with one error line, never 1, which has its own meaning."""
    code = ("import argparse, runpy, sys\n"
            "def boom(*a, **k): raise RuntimeError('boom\\nsecond line')\n"
            "argparse.ArgumentParser.parse_args = boom\n"
            f"sys.argv = [{str(SCRIPT)!r}, 'x']\n"
            f"runpy.run_path({str(SCRIPT)!r}, run_name='__main__')\n")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    assert p.returncode == 7, p.stderr
    assert p.stderr.count("error:") == 1 and "unexpected RuntimeError: boom" in p.stderr
    assert "second line" not in p.stderr and "Traceback" not in p.stderr
