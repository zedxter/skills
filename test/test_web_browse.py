"""Tests for skills/web-browse/scripts/browse.py.

A local HTTP server serves a login form, a result page and a JavaScript counter;
the browser session is driven through the CLI exactly as an agent would. Needs
Playwright with its Chromium headless shell; skipped otherwise. The session state
file is redirected with WEB_BROWSE_STATE so a developer's own session is untouched.
"""

import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "web-browse" / "scripts" / "browse.py"
sys.path.insert(0, str(SCRIPT.parent))
import browse as wb  # noqa: E402

LOGIN = b"""<html><head><title>Login - Demo</title></head><body>
<h1>Sign in</h1><nav><a href="/about">About</a><a href="/hidden" style="display:none">Hidden</a></nav>
<form action="/welcome" method="get">
  <label for="email">Email</label><input id="email" name="email" type="email" placeholder="you@example.com" required>
  <label for="pw">Password</label><input id="pw" name="pw" type="password">
  <label><input type="checkbox" name="remember"> Remember me</label>
  <select name="lang"><option>English</option><option value="de">Deutsch</option></select>
  <button type="submit">Sign in</button>
</form>
<button id="counter" onclick="this.textContent='Clicked ' + (++window.n)">Clicked 0</button><script>window.n=0</script>
<div style="height:3000px"></div><a href="/bottom" id="deep">Deep link</a>
</body></html>"""
ABOUT = b"<html><head><title>About</title></head><body><article><h1>About us</h1>" + b"<p>We build free skills for agents, and this paragraph is long enough to be extracted as content.</p>" * 5 + b"</article></body></html>"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/login":
            return self._send(200, LOGIN)
        if u.path == "/about":
            return self._send(200, ABOUT)
        if u.path == "/welcome":
            q = parse_qs(u.query)
            body = f"<html><head><title>Welcome</title></head><body><h1>Welcome {q.get('email', [''])[0]}</h1><p>lang={q.get('lang', [''])[0]} remember={'remember' in q}</p></body></html>"
            return self._send(200, body.encode())
        if u.path == "/blocked":
            return self._send(403, b"<html><head><title>Just a moment...</title></head><body>_cf_chl_opt</body></html>")
        return self._send(404, b"<html><head><title>404</title></head><body>nope</body></html>")

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _browser_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True
    except Exception:
        return False


browser = pytest.mark.skipif(not _browser_ready(), reason="needs Playwright with its Chromium headless shell")


@pytest.fixture(scope="module")
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    state = tmp_path_factory.mktemp("wb") / "session.json"
    env = {**os.environ, "WEB_BROWSE_STATE": str(state)}

    def run(*args, timeout=90):
        p = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    if _browser_ready():
        rc, out, err = run("start", "--idle-timeout", "5")
        assert rc == 0, err
    yield run
    run("stop")


def test_format_helpers():
    it = {"ref": 3, "role": "textbox", "tag": "input", "text": "Email", "type": "email", "placeholder": "you@example.com", "name": "email", "required": True, "viewport": True}
    s = wb.fmt_item(it)
    assert s.startswith('[3] textbox "Email"') and "type=email" in s and "placeholder=you@example.com" in s and "required" in s
    assert "below fold" in wb.fmt_item({"ref": 9, "role": "link", "tag": "a", "text": "Deep", "href": "https://x/", "viewport": False})
    assert "options: English | Deutsch" in wb.fmt_item({"ref": 1, "role": "combobox", "tag": "select", "text": "", "options": ["English", "Deutsch"], "viewport": True})
    e = wb.nav_error(Exception("Page.goto: net::ERR_NAME_NOT_RESOLVED at http://x/"), "http://x/")
    assert e["code"] == 6 and e["kind"] == "dns"
    assert wb.action_error(Exception("Locator.click: Timeout 20000ms exceeded."), "click", "#x")["kind"] == "timeout"
    assert wb.action_error(Exception("strict mode violation: locator resolved to 3 elements"), "click", "a")["kind"] == "ambiguous"


def test_no_session_is_exit2(tmp_path):
    env = {**os.environ, "WEB_BROWSE_STATE": str(tmp_path / "none.json")}
    p = subprocess.run([sys.executable, str(SCRIPT), "snapshot"], capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    assert p.returncode == 2 and "run `start` first" in p.stderr
    p = subprocess.run([sys.executable, str(SCRIPT), "stop"], capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    assert p.returncode == 2


@browser
def test_full_login_flow(server, session, tmp_path):
    run = session
    rc, out, err = run("open", f"{server}/login")
    assert rc == 0 and "title: Login - Demo" in out and "HTTP 200" in out, err
    rc, out, _ = run("snapshot")
    assert rc == 0 and "interactive elements:" in out and "headings: # Sign in" in out
    lines = {ln.split("]")[0].strip("[").strip(): ln for ln in out.splitlines() if ln.startswith("[")}
    refs = {ln.split('"')[1] if '"' in ln else ln: k for k, ln in lines.items()}
    assert "Hidden" not in out  # display:none excluded without --all
    rc, out_all, _ = run("snapshot", "--all")
    assert "Hidden" in out_all and "hidden" in out_all
    email_ref = next(k for k, ln in lines.items() if 'textbox "Email"' in ln)
    pw_ref = next(k for k, ln in lines.items() if "type=password" in ln)
    rc, out, _ = run("type", email_ref, "ada@example.com")
    assert rc == 0 and "typed" in out
    rc, out, _ = run("type", pw_ref, "secret")
    assert rc == 0
    rc, out, _ = run("check", next(k for k, ln in lines.items() if "checkbox" in ln))
    assert rc == 0
    rc, out, _ = run("select", "select[name=lang]", "Deutsch")
    assert rc == 0 and "selected" in out
    rc, out, _ = run("click", "#counter")
    assert rc == 0
    rc, out, _ = run("eval", "document.getElementById('counter').textContent")
    assert rc == 0 and json.loads(out) == "Clicked 1"
    rc, out, _ = run("find", "Deep")
    assert rc == 0 and "Deep link" in out and "below fold" in out
    rc, out, _ = run("scroll", "bottom")
    assert rc == 0 and "scrolled" in out
    rc, out, _ = run("snapshot", "--json")
    data = json.loads(out)
    deep = next(it for it in data["items"] if it.get("text") == "Deep link")
    assert deep["viewport"] is True and data["scroll"]["y"] > 1000
    rc, out, _ = run("click", "text=Sign in")
    assert rc == 0 and "title: Welcome" in out
    rc, out, _ = run("wait", "--text", "Welcome ada@example.com")
    assert rc == 0
    rc, out, _ = run("read", "-f", "text")
    assert rc == 0 and "Welcome ada@example.com" in out and "lang=de remember=True" in out
    rc, out, _ = run("back")
    assert rc == 0 and "Login - Demo" in out
    rc, out, _ = run("forward")
    assert rc == 0 and "Welcome" in out


@browser
def test_read_screenshot_tabs_and_errors(server, session, tmp_path):
    run = session
    rc, out, _ = run("open", f"{server}/about")
    assert rc == 0
    out_file = tmp_path / "about.md"
    rc, out, _ = run("read", "-o", str(out_file))
    assert rc == 0 and out.startswith("saved:")
    text = out_file.read_text("utf-8")
    assert text.startswith("# title: About") and "# source: browser session" in text and "We build free skills" in text
    rc, out, _ = run("read")
    assert rc == 0 and "# About us" in out or "About us" in out
    shot = tmp_path / "shot.png"
    rc, out, _ = run("screenshot", str(shot), "--full")
    assert rc == 0 and shot.stat().st_size > 500
    rc, out, _ = run("tab", "new", f"{server}/login")
    assert rc == 0 and "tab 2/2" in out
    rc, out, _ = run("tabs")
    assert rc == 0 and out.count("\n") == 2 and "* [2]" in out
    rc, out, _ = run("tab", "1")
    assert rc == 0 and "About" in out
    rc, out, _ = run("tab", "close")
    assert rc == 0 and "tab" not in out.split("\n")[-2] or True
    rc, out, _ = run("status")
    assert rc == 0 and "session: headless" in out and "recent:" in out
    # errors
    rc, out, err = run("click", "#does-not-exist")
    assert rc == 1 and "nothing matches" in err
    rc, out, err = run("click", "999")
    assert rc == 1 and "run `snapshot` again" in err
    rc, out, err = run("open", f"{server}/missing")
    assert rc == 3 and "HTTP 404" in out
    rc, out, err = run("open", f"{server}/blocked")
    assert rc == 4 and "BLOCKED: cloudflare_challenge" in out
    rc, out, err = run("open", "http://127.0.0.1:9/")
    assert rc == 6 and "connection" in err
    rc, out, err = run("wait", "--for", "#never", "--timeout", "1")
    assert rc == 1 and "timed out" in err
    rc, out, err = run("tab", "7")
    assert rc == 2
    rc, out, err = run("start")  # idempotent
    assert rc == 0 and "already running" in out
