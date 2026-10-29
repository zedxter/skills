#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.49", "curl_cffi>=0.17,<1", "trafilatura>=2.3,<3", "lxml_html_clean", "pypdf>=5,<7"]
# ///
"""Drive a real browser step by step from the command line: a free, local Browser Use.

`start` launches Chromium in a background session that survives between
commands; every other command talks to it, so the page, its state, cookies and
logins persist while the agent looks, clicks, types and reads. `snapshot` lists
the interactive elements with numbered refs; `click`/`type`/`select` take a ref
(or a CSS selector); `read` returns the current page's main content as
Markdown via the web-extract skill; `screenshot` saves a PNG; `stop` ends the
session (it also ends by itself after --idle-timeout minutes).

    uv run browse.py start [--headful] [--profile DIR]
    uv run browse.py open https://example.com/login
    uv run browse.py snapshot                       # [3] textbox "Email" · [4] textbox "Password" · [5] button "Sign in"
    uv run browse.py type 3 "me@example.com"
    uv run browse.py type 4 "secret" --enter
    uv run browse.py wait --text "Welcome"
    uv run browse.py read -o page.md
    uv run browse.py click "a.settings"             # selectors work too
    uv run browse.py screenshot shot.png
    uv run browse.py stop

Exit codes: 0 ok · 1 element/selector not found or action failed · 2 bad input, no
    session, browser missing · 3 page HTTP error · 4 bot wall · 6 navigation/network
    error or timeout · 7 other
"""

import argparse
import importlib.util
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

EXIT_OK, EXIT_FAIL, EXIT_BAD_INPUT, EXIT_HTTP, EXIT_BLOCKED, EXIT_NOT_HTML, EXIT_NETWORK, EXIT_OTHER = range(8)
DEFAULT_IDLE_MIN = 30
MAX_SNAPSHOT = 150
UA_TOKEN = "web-browse/1.0 (+https://github.com/zedxter/skills)"
SNAPSHOT_JS = r"""
([max, all]) => {
  const sel = 'a[href], button, input, select, textarea, summary, [role=button], [role=link], [role=tab], [role=menuitem], ' +
              '[role=checkbox], [role=radio], [role=switch], [role=option], [role=combobox], [role=textbox], [role=searchbox], ' +
              '[contenteditable=""], [contenteditable="true"], [onclick], [tabindex]:not([tabindex="-1"])';
  // refs are stable for the life of the document: existing numbers are kept, new elements continue the sequence
  let next = 0;
  document.querySelectorAll('[data-wb-ref]').forEach(e => { const n = +e.getAttribute('data-wb-ref'); if (n > next) next = n; });
  const els = Array.from(document.querySelectorAll(sel));
  const out = []; const seen = new Set();
  const roleOf = (el) => {
    const tag = el.tagName.toLowerCase();
    if (el.getAttribute('role')) return el.getAttribute('role');
    if (tag === 'a') return 'link'; if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'select') return 'combobox'; if (tag === 'textarea') return 'textbox';
    if (tag === 'input') return ({checkbox: 'checkbox', radio: 'radio', submit: 'button', button: 'button', reset: 'button',
                                 image: 'button', file: 'file', range: 'slider', search: 'searchbox', hidden: 'hidden'})[el.type] || 'textbox';
    if (el.isContentEditable) return 'textbox';
    return tag;
  };
  for (const el of els) {
    if (seen.has(el)) continue; seen.add(el);
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    const visible = r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0';
    const role = roleOf(el);
    if (role === 'hidden') continue;
    if (!visible && !all) continue;
    let ref = +el.getAttribute('data-wb-ref');
    if (!ref) { ref = ++next; el.setAttribute('data-wb-ref', String(ref)); }
    const tag = el.tagName.toLowerCase();
    let label = el.getAttribute('aria-label') || (el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute('title') ||
                el.getAttribute('alt') || (tag === 'input' || tag === 'textarea' || tag === 'select' ? '' : el.innerText) || el.value || '';
    if (!label && (tag === 'input')) label = el.placeholder || el.name || '';
    if (!label) { const img = el.querySelector('img[alt]'); if (img) label = img.getAttribute('alt'); }
    const item = {ref, role, tag, text: String(label || '').replace(/\s+/g, ' ').trim().slice(0, 100)};
    if (tag === 'a' && el.href) item.href = el.href;
    if (tag === 'input' || tag === 'textarea') {
      if (el.type && el.type !== 'text') item.type = el.type;
      if (el.value && el.type !== 'password') item.value = String(el.value).slice(0, 80);
      if (el.placeholder) item.placeholder = el.placeholder.slice(0, 80);
      if (el.name) item.name = el.name;
      if (el.required) item.required = true;
    }
    if (tag === 'input' && (el.type === 'checkbox' || el.type === 'radio')) item.checked = el.checked;
    if (el.getAttribute('aria-expanded') !== null) item.expanded = el.getAttribute('aria-expanded') === 'true';
    if (el.disabled) item.disabled = true;
    if (tag === 'select') item.options = Array.from(el.options).slice(0, 25).map(o => o.text.trim().slice(0, 50));
    if (!visible) item.hidden = true;
    item.viewport = r.bottom > 0 && r.top < innerHeight;
    out.push(item);
    if (out.length >= max) break;
  }
  const headings = Array.from(document.querySelectorAll('h1,h2,h3')).filter(h => h.offsetParent !== null).slice(0, 40)
    .map(h => ({level: +h.tagName[1], text: h.innerText.replace(/\s+/g, ' ').trim().slice(0, 120)}));
  const frames = document.querySelectorAll('iframe').length;
  return {items: out, total: els.length, headings, frames, title: document.title, url: location.href,
          scroll: {y: Math.round(scrollY), height: document.documentElement.scrollHeight, viewport: innerHeight}};
}
"""


# ------------------------------------------------------------------ state file

def state_path() -> Path:
    env = os.environ.get("WEB_BROWSE_STATE")
    if env:
        return Path(env)
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) if os.name == "nt" \
        else Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "web-browse" / "session.json"


def read_state() -> dict[str, Any] | None:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_web_extract() -> Any | None:
    here = Path(__file__).resolve()
    candidates = [Path(os.environ["WEB_EXTRACT_SCRIPT"])] if os.environ.get("WEB_EXTRACT_SCRIPT") else []
    candidates += [here.parent.parent.parent / "web-extract" / "scripts" / "extract.py", here.parent / "extract.py"]
    for root in (Path.home() / ".claude" / "skills", Path.cwd() / ".claude" / "skills", Path.home() / ".codex" / "skills",
                 Path.home() / ".gemini" / "skills", Path.home() / ".config" / "opencode" / "skills", Path.cwd() / ".agents" / "skills"):
        candidates.append(root / "web-extract" / "scripts" / "extract.py")
    for path in candidates:
        if path.is_file():
            try:
                spec = importlib.util.spec_from_file_location("web_extract", path)
                mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
                sys.modules["web_extract"] = mod
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
                return mod
            except SystemExit:
                return None
    return None


# --------------------------------------------------------------------- server

class Session:
    """The browser-owning side: one Playwright, one context, N pages, executed on one thread."""

    def __init__(self, headful: bool, profile: str | None, idle_min: float) -> None:
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        launch: dict[str, Any] = {"headless": not headful}
        ctx_opts: dict[str, Any] = {"viewport": {"width": 1280, "height": 900}, "locale": "en-US", "accept_downloads": True}
        self.browser = None
        if profile:
            self.ctx = self.pw.chromium.launch_persistent_context(profile, **launch, **ctx_opts)
        else:
            self.browser = self.pw.chromium.launch(**launch)
            if not headful:
                probe = self.browser.new_page()
                ua = probe.evaluate("navigator.userAgent")
                probe.close()
                ctx_opts["user_agent"] = f"{ua} {UA_TOKEN}"
            self.ctx = self.browser.new_context(**ctx_opts)
        self.ctx.set_default_timeout(20_000)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.headful, self.profile, self.idle = headful, profile, idle_min * 60
        self.last_status: int | None = None
        self.last_headers: dict[str, str] = {}
        self.wx = load_web_extract()
        self.log: list[str] = []
        self.ctx.on("page", self._on_new_page)
        self._watch(self.page)

    # bookkeeping ----------------------------------------------------------
    def _on_new_page(self, page: Any) -> None:
        self._watch(page)

    def _watch(self, page: Any) -> None:
        def on_response(resp: Any) -> None:
            try:
                req = resp.request
                if req.is_navigation_request() and req.frame == page.main_frame:
                    self.last_status = resp.status
                    self.last_headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
            except Exception:
                pass
        page.on("response", on_response)

    def pages(self) -> list[Any]:
        return [p for p in self.ctx.pages if not p.is_closed()]

    def ensure_page(self) -> Any:
        if self.page.is_closed():
            ps = self.pages()
            self.page = ps[-1] if ps else self.ctx.new_page()
        return self.page

    def settle(self, timeout_ms: int) -> None:
        page = self.ensure_page()
        for state, budget in (("load", timeout_ms), ("networkidle", min(5000, timeout_ms // 3))):
            try:
                page.wait_for_load_state(state, timeout=budget)
            except Exception:
                pass

    def state(self) -> dict[str, Any]:
        page = self.ensure_page()
        try:
            title = page.title()
        except Exception:
            title = None
        return {"url": page.url, "title": title, "status": self.last_status, "tabs": len(self.pages()),
                "tab": self.pages().index(page) + 1 if page in self.pages() else 1}

    def locator(self, target: str) -> Any:
        page = self.ensure_page()
        if re.fullmatch(r"\d+", target.strip()):
            loc = page.locator(f'[data-wb-ref="{target.strip()}"]')
            if loc.count() == 0:
                raise LookupError(f"ref {target} is not on the page any more; run `snapshot` again")
            return loc.first
        if target.startswith("text="):  # prefer something clickable/typable over a heading or paragraph with the same words
            needle = target[5:]
            inter = page.locator("a, button, input, select, textarea, summary, label, [role=button], [role=link], [role=tab], "
                                 "[role=menuitem], [role=option], [onclick]").filter(has_text=re.compile(re.escape(needle), re.I))
            if inter.count():
                return inter.first
            loc = page.get_by_text(needle, exact=False)
            if loc.count() == 0:
                raise LookupError(f"no element with text {needle!r}; run `snapshot` or `find {needle}`")
            return loc.first
        loc = page.locator(target)
        if loc.count() == 0:
            raise LookupError(f"nothing matches {target!r}; run `snapshot` for refs or check the selector")
        return loc.first

    def classify_page(self) -> Any | None:
        """web-extract's bot-wall detection on the current DOM (None when web-extract is unavailable)."""
        if self.wx is None or self.last_status is None:
            return None
        page = self.ensure_page()
        body = page.content().encode("utf-8")
        return self.wx.classify(self.last_status, self.last_headers, body[: self.wx.MARKER_SCAN_BYTES], page.url, len(body))

    # commands -------------------------------------------------------------
    def handle(self, cmd: str, a: dict[str, Any]) -> dict[str, Any]:
        page = self.ensure_page()
        t = int(float(a.get("timeout", 20)) * 1000)
        if cmd == "ping":
            return {"ok": True, **self.state()}
        if cmd == "status":
            return {"ok": True, **self.state(), "headful": self.headful, "profile": self.profile, "web_extract": self.wx is not None,
                    "pages": [{"index": i + 1, "url": p.url, "title": p.title(), "current": p == page} for i, p in enumerate(self.pages())],
                    "recent": self.log[-10:]}
        if cmd == "open":
            try:
                resp = page.goto(a["url"], wait_until="domcontentloaded", timeout=t)
            except Exception as e:
                return nav_error(e, a["url"])
            self.settle(t)
            if resp is not None:
                self.last_status = resp.status
            cls = self.classify_page()
            out = {"ok": True, **self.state()}
            if cls is not None and cls.error is not None and cls.error.code == EXIT_BLOCKED:
                out.update(blocked=cls.error.as_dict())
            elif (self.last_status or 0) >= 400:
                out.update(http_error=self.last_status)
            return out
        if cmd == "snapshot":
            data = page.evaluate(SNAPSHOT_JS, [int(a.get("max", MAX_SNAPSHOT)), bool(a.get("all"))])
            return {"ok": True, **data, "status": self.last_status}
        if cmd == "find":
            data = page.evaluate(SNAPSHOT_JS, [1000, True])
            needle = a["text"].lower()
            hits = [it for it in data["items"] if needle in json.dumps(it, ensure_ascii=False).lower()]
            return {"ok": True, "items": hits[: int(a.get("max", 50))], "url": data["url"], "title": data["title"]}
        if cmd in ("click", "hover", "check", "uncheck", "focus"):
            try:
                loc = self.locator(a["target"])
                getattr(loc, cmd)(timeout=t) if cmd != "click" else loc.click(timeout=t, button=a.get("button", "left"), click_count=int(a.get("count", 1)))
            except LookupError as e:
                return {"ok": False, "code": EXIT_FAIL, "error": str(e)}
            except Exception as e:
                return action_error(e, cmd, a["target"])
            self.settle(t)
            return {"ok": True, **self.state()}
        if cmd == "type":
            try:
                loc = self.locator(a["target"])
                if a.get("clear", True):
                    loc.fill(a["text"], timeout=t)
                else:
                    loc.type(a["text"], timeout=t)
                if a.get("enter"):
                    loc.press("Enter", timeout=t)
                    self.settle(t)
            except LookupError as e:
                return {"ok": False, "code": EXIT_FAIL, "error": str(e)}
            except Exception as e:
                return action_error(e, "type", a["target"])
            return {"ok": True, **self.state()}
        if cmd == "select":
            try:
                loc = self.locator(a["target"])
                chosen = loc.select_option(label=a["value"], timeout=t) if not a.get("by_value") else loc.select_option(value=a["value"], timeout=t)
            except LookupError as e:
                return {"ok": False, "code": EXIT_FAIL, "error": str(e)}
            except Exception as e:
                try:
                    chosen = self.locator(a["target"]).select_option(value=a["value"], timeout=t)
                except Exception:
                    return action_error(e, "select", a["target"])
            self.settle(t)
            return {"ok": True, "selected": chosen, **self.state()}
        if cmd == "press":
            try:
                page.keyboard.press(a["key"])
            except Exception as e:
                return action_error(e, "press", a["key"])
            self.settle(t)
            return {"ok": True, **self.state()}
        if cmd == "scroll":
            where = a.get("where", "down")
            if re.fullmatch(r"\d+", str(where)) or (isinstance(where, str) and not where in ("down", "up", "top", "bottom")):
                try:
                    self.locator(str(where)).scroll_into_view_if_needed(timeout=t)
                except LookupError as e:
                    return {"ok": False, "code": EXIT_FAIL, "error": str(e)}
                except Exception as e:
                    return action_error(e, "scroll", str(where))
            else:
                px = int(a.get("px") or 0)
                js = {"down": f"window.scrollBy(0, {px or 'innerHeight * 0.9'})", "up": f"window.scrollBy(0, -({px or 'innerHeight * 0.9'}))",
                      "top": "window.scrollTo(0, 0)", "bottom": "window.scrollTo(0, document.documentElement.scrollHeight)"}[where]
                page.evaluate(js)
                page.wait_for_timeout(400)
            pos = page.evaluate("({y: Math.round(scrollY), height: document.documentElement.scrollHeight, viewport: innerHeight})")
            return {"ok": True, "scroll": pos, **self.state()}
        if cmd in ("back", "forward", "reload"):
            try:
                getattr(page, "go_back" if cmd == "back" else "go_forward" if cmd == "forward" else "reload")(timeout=t, wait_until="domcontentloaded")
            except Exception as e:
                return nav_error(e, page.url)
            self.settle(t)
            return {"ok": True, **self.state()}
        if cmd == "wait":
            try:
                if a.get("selector"):
                    page.wait_for_selector(a["selector"], timeout=t, state=a.get("state", "visible"))
                elif a.get("text"):
                    page.get_by_text(a["text"], exact=False).first.wait_for(timeout=t)
                elif a.get("url"):
                    page.wait_for_url(re.compile(a["url"]), timeout=t)
                elif a.get("seconds") is not None:
                    page.wait_for_timeout(int(float(a["seconds"]) * 1000))
                else:
                    self.settle(t)
            except Exception as e:
                return action_error(e, "wait", a.get("selector") or a.get("text") or a.get("url") or "")
            return {"ok": True, **self.state()}
        if cmd == "read":
            html = page.content().encode("utf-8")
            if self.wx is None:
                text = page.evaluate("document.body ? document.body.innerText : ''")
                return {"ok": True, **self.state(), "content": text, "content_format": "text", "words": len(text.split()), "warnings": ["no_web_extract"]}
            wx = self.wx
            ns = argparse.Namespace(format="text" if a.get("format") == "text" else "markdown", links=bool(a.get("links")), raw=bool(a.get("raw")),
                                    comments=False, recall=False, precision=False, structured=bool(a.get("structured")), out=None, max_chars=0)
            f = wx.Fetched(self.last_status or 200, {**self.last_headers, "content-type": "text/html; charset=utf-8"}, page.url, html)
            result = wx.new_result(page.url)
            err = wx.handle_body(f, result, ns, "browser")
            if err is not None:
                text = page.evaluate("document.body ? document.body.innerText : ''")
                return {"ok": True, **self.state(), "content": text, "content_format": "text", "words": len(text.split()),
                        "warnings": ["extraction_failed:" + err.kind], "title": result.get("title")}
            return {"ok": True, **self.state(), **{k: result[k] for k in ("title", "author", "date", "content", "content_format", "words",
                                                                       "warnings", "links", "structured", "language")}}
        if cmd == "html":
            return {"ok": True, **self.state(), "html": page.content()}
        if cmd == "screenshot":
            path = Path(a["path"])
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                if a.get("target"):
                    self.locator(a["target"]).screenshot(path=str(path), timeout=t)
                else:
                    page.screenshot(path=str(path), full_page=bool(a.get("full")))
            except LookupError as e:
                return {"ok": False, "code": EXIT_FAIL, "error": str(e)}
            except Exception as e:
                return action_error(e, "screenshot", str(path))
            return {"ok": True, "path": str(path), **self.state()}
        if cmd == "eval":
            try:
                value = page.evaluate(a["js"])
            except Exception as e:
                return action_error(e, "eval", a["js"][:60])
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = str(value)
            return {"ok": True, "value": value, **self.state()}
        if cmd == "tabs":
            return {"ok": True, "pages": [{"index": i + 1, "url": p.url, "title": p.title(), "current": p == page} for i, p in enumerate(self.pages())]}
        if cmd == "tab":
            ps = self.pages()
            which = a.get("which")
            if which == "new":
                self.page = self.ctx.new_page()
                if a.get("url"):
                    return self.handle("open", {"url": a["url"], "timeout": a.get("timeout", 20)})
                return {"ok": True, **self.state()}
            if which == "close":
                if len(ps) > 1:
                    page.close()
                    self.page = self.pages()[-1]
                else:
                    page.goto("about:blank")
                return {"ok": True, **self.state()}
            try:
                self.page = ps[int(which) - 1]
                self.page.bring_to_front()
            except (ValueError, IndexError):
                return {"ok": False, "code": EXIT_BAD_INPUT, "error": f"no tab {which}; there are {len(ps)}"}
            return {"ok": True, **self.state()}
        if cmd == "cookies":
            cs = self.ctx.cookies()
            return {"ok": True, "count": len(cs), "cookies": [{"name": c["name"], "domain": c["domain"], "expires": c.get("expires")} for c in cs]}
        if cmd == "stop":
            return {"ok": True, "stopping": True}
        return {"ok": False, "code": EXIT_BAD_INPUT, "error": f"unknown command {cmd}"}

    def close(self) -> None:
        try:
            self.ctx.close()
        finally:
            try:
                if self.browser is not None:
                    self.browser.close()
            finally:
                self.pw.stop()


def nav_error(e: Exception, url: str) -> dict[str, Any]:
    msg = (str(e).splitlines() or [type(e).__name__])[0]
    m = re.search(r"net::(ERR_[A-Z_0-9]+)", str(e))
    code = m.group(1) if m else ""
    kind = "timeout" if "Timeout" in msg or code == "ERR_CONNECTION_TIMED_OUT" else "dns" if code == "ERR_NAME_NOT_RESOLVED" \
        else "tls" if code.startswith(("ERR_CERT", "ERR_SSL")) else "download" if code == "ERR_ABORTED" else "connection" if code else "navigation"
    return {"ok": False, "code": EXIT_NETWORK, "error": f"{kind}: {msg[:200]} ({url})", "kind": kind}


def action_error(e: Exception, cmd: str, target: str) -> dict[str, Any]:
    msg = (str(e).splitlines() or [type(e).__name__])[0]
    if "Timeout" in msg:
        hint = "element not visible/enabled in time, or the page is still loading; `snapshot` to re-check, `wait --for SELECTOR`"
        return {"ok": False, "code": EXIT_FAIL, "error": f"{cmd} {target!r} timed out — {hint}", "kind": "timeout"}
    if "strict mode" in msg or "resolved to" in msg:
        return {"ok": False, "code": EXIT_FAIL, "error": f"{cmd} {target!r}: selector matches several elements; use a ref from `snapshot`", "kind": "ambiguous"}
    return {"ok": False, "code": EXIT_FAIL, "error": f"{cmd} {target!r} failed: {msg[:200]}", "kind": "action"}


def serve(args: argparse.Namespace) -> int:
    """Daemon: own the browser, answer JSON-line requests on a loopback socket, exit when idle or told to."""
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(1.0)
    port = srv.getsockname()[1]
    try:
        session = Session(args.headful, args.profile, args.idle_timeout)
    except Exception as e:
        path.write_text(json.dumps({"error": f"browser failed to start: {(str(e).splitlines() or [type(e).__name__])[0][:300]}"}), encoding="utf-8")
        return EXIT_BAD_INPUT
    path.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid(), "headful": args.headful, "profile": args.profile,
                                "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "idle_timeout_min": args.idle_timeout}), encoding="utf-8")
    last = time.monotonic()
    stopping = False
    try:
        while not stopping:
            if time.monotonic() - last > session.idle:
                break
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            with conn:
                conn.settimeout(600)
                try:
                    raw = b""
                    while not raw.endswith(b"\n"):
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        raw += chunk
                    req = json.loads(raw.decode("utf-8"))
                    if req.get("token") != token:
                        resp = {"ok": False, "code": EXIT_BAD_INPUT, "error": "bad session token"}
                    else:
                        last = time.monotonic()
                        try:
                            resp = session.handle(req["cmd"], req.get("args") or {})
                        except Exception as e:  # never let one command kill the session
                            resp = {"ok": False, "code": EXIT_OTHER, "error": f"{type(e).__name__}: {(str(e).splitlines() or [''])[0][:300]}"}
                        session.log.append(f"{req['cmd']} {json.dumps(req.get('args') or {}, ensure_ascii=False)[:80]} → {'ok' if resp.get('ok') else 'fail'}")
                        stopping = bool(resp.get("stopping"))
                    conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
                except Exception:
                    pass
    finally:
        try:
            session.close()
        finally:
            try:
                if path.exists() and json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
                    path.unlink()
            except (OSError, ValueError):
                pass
            srv.close()
    return EXIT_OK


# --------------------------------------------------------------------- client

def send(cmd: str, args: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
    st = read_state()
    if not st or "port" not in st:
        return {"ok": False, "code": EXIT_BAD_INPUT, "error": "no browser session; run `start` first" + (f" (last start failed: {st['error']})" if st and st.get("error") else "")}
    try:
        with socket.create_connection(("127.0.0.1", st["port"]), timeout=5) as s:
            s.settimeout(timeout)
            s.sendall((json.dumps({"token": st["token"], "cmd": cmd, "args": args}, ensure_ascii=False) + "\n").encode("utf-8"))
            raw = b""
            while not raw.endswith(b"\n"):
                chunk = s.recv(1 << 20)
                if not chunk:
                    break
                raw += chunk
        return json.loads(raw.decode("utf-8"))
    except (ConnectionRefusedError, OSError):
        try:
            state_path().unlink()
        except OSError:
            pass
        return {"ok": False, "code": EXIT_BAD_INPUT, "error": "the browser session is gone (idle timeout or crash); run `start` again"}
    except ValueError:
        return {"ok": False, "code": EXIT_OTHER, "error": "garbled reply from the session"}


def start(args: argparse.Namespace) -> int:
    st = read_state()
    if st and "port" in st:
        probe = send("ping", {})
        if probe.get("ok"):
            print_state("session already running", probe, args)
            return EXIT_OK
    try:
        import playwright  # noqa: F401
    except ImportError:
        sys.stderr.write("error: playwright is not installed; run with `uv run` (it installs it) or `pip install playwright`, "
                         "then once `playwright install chromium" + ("" if args.headful else "-headless-shell") + "`\n")
        return EXIT_BAD_INPUT
    try:
        state_path().unlink()
    except OSError:
        pass
    cmd = [sys.executable, str(Path(__file__).resolve()), "_serve", "--idle-timeout", str(args.idle_timeout)]
    if args.headful:
        cmd.append("--headful")
    if args.profile:
        cmd += ["--profile", str(Path(args.profile).expanduser().resolve())]
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        st = read_state()
        if st and st.get("error"):
            sys.stderr.write(f"error: {st['error']}\n")
            if "Executable doesn't exist" in st["error"]:
                sys.stderr.write("hint: install the browser once: `uv run --with playwright playwright install chromium"
                                 + ("" if args.headful else "-headless-shell") + "`\n")
            try:
                state_path().unlink()
            except OSError:
                pass
            return EXIT_BAD_INPUT
        if st and "port" in st:
            probe = send("ping", {})
            if probe.get("ok"):
                print_state(f"session started (pid {st['pid']}, {'headful' if args.headful else 'headless'}"
                            f"{', profile ' + args.profile if args.profile else ''}, idle timeout {args.idle_timeout:g} min)", probe, args)
                return EXIT_OK
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    sys.stderr.write("error: the browser session did not come up in time\n")
    return EXIT_BAD_INPUT


def print_state(prefix: str, r: dict[str, Any], args: argparse.Namespace) -> None:
    if args.json:
        sys.stdout.write(json.dumps({"note": prefix, **{k: v for k, v in r.items() if k != "ok"}}, ensure_ascii=False, indent=2) + "\n")
        return
    status = f" · HTTP {r['status']}" if r.get("status") else ""
    tabs = f" · tab {r.get('tab')}/{r.get('tabs')}" if r.get("tabs", 1) > 1 else ""
    sys.stdout.write(f"{prefix}\nurl: {r.get('url')}\ntitle: {r.get('title') or '—'}{status}{tabs}\n")


def fmt_item(it: dict[str, Any]) -> str:
    bits = [f"[{it['ref']}]", it["role"]]
    if it.get("text"):
        bits.append(json.dumps(it["text"], ensure_ascii=False))
    extra = []
    for k in ("href", "type", "name", "placeholder", "value"):
        if it.get(k):
            extra.append(f"{k}={it[k]}" if k != "href" else f"→ {it[k]}")
    if "checked" in it:
        extra.append("checked" if it["checked"] else "unchecked")
    if "expanded" in it:
        extra.append("expanded" if it["expanded"] else "collapsed")
    if it.get("options"):
        extra.append("options: " + " | ".join(it["options"][:8]) + (" …" if len(it["options"]) > 8 else ""))
    for k in ("disabled", "required", "hidden"):
        if it.get(k):
            extra.append(k)
    if not it.get("viewport", True) and not it.get("hidden"):
        extra.append("below fold")
    return " ".join(bits) + (f"  ({'; '.join(extra)})" if extra else "")


def print_snapshot(r: dict[str, Any], args: argparse.Namespace) -> None:
    if args.json:
        sys.stdout.write(json.dumps({k: v for k, v in r.items() if k != "ok"}, ensure_ascii=False, indent=2) + "\n")
        return
    status = f" · HTTP {r['status']}" if r.get("status") else ""
    sc = r.get("scroll") or {}
    where = f" · scrolled {sc.get('y', 0)}/{max(0, sc.get('height', 0) - sc.get('viewport', 0))}" if sc else ""
    sys.stdout.write(f"url: {r['url']}\ntitle: {r.get('title') or '—'}{status}{where}\n")
    if r.get("headings"):
        sys.stdout.write("headings: " + " · ".join(("#" * h["level"]) + " " + h["text"] for h in r["headings"][:12]) + "\n")
    items = r.get("items", [])
    sys.stdout.write(f"interactive elements: {len(items)} shown of {r.get('total', len(items))}" + (f" · {r['frames']} iframe(s) not inspected" if r.get("frames") else "") + "\n")
    for it in items:
        sys.stdout.write(fmt_item(it) + "\n")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="machine-readable output for every command (before or after the command)")
    p.add_argument("--timeout", type=float, default=20.0, metavar="S", help="per action/navigation (default 20; before or after the command)")
    common = argparse.ArgumentParser(add_help=False)  # the same two flags after the command; SUPPRESS keeps the global value when absent
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--timeout", type=float, default=argparse.SUPPRESS, metavar="S")
    sp = p.add_subparsers(dest="cmd", required=True, parser_class=lambda **kw: argparse.ArgumentParser(parents=[common], **kw))
    s = sp.add_parser("start", help="launch the browser session (no-op when one is running)")
    s.add_argument("--headful", action="store_true", help="visible window (a human can log in or pass a check); needs the full Chromium build")
    s.add_argument("--profile", metavar="DIR", help="persistent profile: cookies and logins survive stop/start")
    s.add_argument("--idle-timeout", type=float, default=DEFAULT_IDLE_MIN, metavar="MIN", help=f"end the session after MIN idle minutes (default {DEFAULT_IDLE_MIN})")
    srv = sp.add_parser("_serve")
    srv.add_argument("--headful", action="store_true")
    srv.add_argument("--profile")
    srv.add_argument("--idle-timeout", type=float, default=DEFAULT_IDLE_MIN)
    sp.add_parser("stop", help="end the session")
    sp.add_parser("status", help="session, tabs, recent commands")
    o = sp.add_parser("open", help="navigate the current tab")
    o.add_argument("url")
    sn = sp.add_parser("snapshot", help="list interactive elements with refs, headings, scroll position")
    sn.add_argument("--all", action="store_true", help="include hidden/zero-size elements")
    sn.add_argument("--max", type=int, default=MAX_SNAPSHOT)
    fd = sp.add_parser("find", help="elements whose text/attributes contain TEXT")
    fd.add_argument("text")
    for name, hlp in (("click", "click a ref or CSS selector"), ("hover", "hover"), ("check", "tick a checkbox"), ("uncheck", "untick"), ("focus", "focus")):
        c = sp.add_parser(name, help=hlp)
        c.add_argument("target", help="ref number from snapshot, CSS selector, or text=…")
        if name == "click":
            c.add_argument("--right", action="store_true")
            c.add_argument("--double", action="store_true")
    ty = sp.add_parser("type", help="fill a field (clears it first unless --append)")
    ty.add_argument("target")
    ty.add_argument("text")
    ty.add_argument("--enter", action="store_true", help="press Enter afterwards")
    ty.add_argument("--append", action="store_true", help="type after the existing value instead of replacing it")
    se = sp.add_parser("select", help="choose an option of a <select> by its label (or --value)")
    se.add_argument("target")
    se.add_argument("value")
    se.add_argument("--value", dest="by_value", action="store_true")
    pr = sp.add_parser("press", help="press a key: Enter, Tab, Escape, ArrowDown, Control+a …")
    pr.add_argument("key")
    sc = sp.add_parser("scroll", help="down|up|top|bottom (default down one screen), or a ref/selector to scroll into view")
    sc.add_argument("where", nargs="?", default="down")
    sc.add_argument("--px", type=int)
    for name in ("back", "forward", "reload"):
        sp.add_parser(name)
    w = sp.add_parser("wait", help="wait for a selector, text, URL pattern or seconds (default: page settled)")
    w.add_argument("seconds", nargs="?", type=float)
    w.add_argument("--for", dest="selector", metavar="SELECTOR")
    w.add_argument("--text")
    w.add_argument("--url", metavar="REGEX")
    w.add_argument("--hidden", action="store_true", help="with --for: wait until the element is gone")
    rd = sp.add_parser("read", help="main content of the current page (web-extract), markdown by default")
    rd.add_argument("-f", "--format", choices=["markdown", "text"], default="markdown")
    rd.add_argument("-o", "--out", metavar="FILE")
    rd.add_argument("--links", action="store_true")
    rd.add_argument("--structured", action="store_true")
    rd.add_argument("--max-chars", type=int, default=60_000)
    ht = sp.add_parser("html", help="current DOM as HTML")
    ht.add_argument("-o", "--out", metavar="FILE")
    ss = sp.add_parser("screenshot", help="PNG of the viewport (or --full page, or an element)")
    ss.add_argument("path")
    ss.add_argument("--full", action="store_true")
    ss.add_argument("--of", dest="target", metavar="REF|SELECTOR")
    ev = sp.add_parser("eval", help="run JavaScript in the page and print the JSON result")
    ev.add_argument("js")
    sp.add_parser("tabs", help="list tabs")
    tb = sp.add_parser("tab", help="switch to tab N, `new [URL]`, or `close`")
    tb.add_argument("which")
    tb.add_argument("url", nargs="?")
    sp.add_parser("cookies", help="cookie names/domains in the session (no values)")
    args = p.parse_args(argv)

    if args.cmd == "_serve":
        return serve(args)
    if args.cmd == "start":
        return start(args)
    if args.cmd == "stop":
        r = send("stop", {})
        if r.get("ok"):
            for _ in range(40):  # wait for the state file to disappear
                if read_state() is None:
                    break
                time.sleep(0.1)
            sys.stdout.write("session stopped\n" if not args.json else '{"stopped": true}\n')
            return EXIT_OK
        sys.stderr.write(f"error: {r.get('error')}\n")
        return int(r.get("code", EXIT_OTHER))

    a: dict[str, Any] = {"timeout": args.timeout}
    cmd = args.cmd
    if cmd == "open":
        a["url"] = args.url if re.match(r"^[a-z][a-z0-9+.-]*:", args.url, re.I) else "https://" + args.url
    elif cmd == "snapshot":
        a.update(all=args.all, max=args.max)
    elif cmd == "find":
        a["text"] = args.text
    elif cmd in ("click", "hover", "check", "uncheck", "focus"):
        a["target"] = args.target
        if cmd == "click":
            a.update(button="right" if args.right else "left", count=2 if args.double else 1)
    elif cmd == "type":
        a.update(target=args.target, text=args.text, enter=args.enter, clear=not args.append)
    elif cmd == "select":
        a.update(target=args.target, value=args.value, by_value=args.by_value)
    elif cmd == "press":
        a["key"] = args.key
    elif cmd == "scroll":
        a.update(where=args.where, px=args.px)
    elif cmd == "wait":
        a.update(seconds=args.seconds, selector=args.selector, text=args.text, url=args.url, state="hidden" if args.hidden else "visible")
    elif cmd == "read":
        a.update(format=args.format, links=args.links, structured=args.structured)
    elif cmd == "screenshot":
        a.update(path=str(Path(args.path).resolve()), full=args.full, target=args.target)
    elif cmd == "eval":
        a["js"] = args.js
    elif cmd == "tab":
        a.update(which=args.which, url=args.url)
    r = send(cmd, a, timeout=max(args.timeout * 3, 60))
    if not r.get("ok"):
        sys.stderr.write(f"error: {r.get('error')}\n")
        if args.json:
            sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
        return int(r.get("code", EXIT_OTHER))
    if cmd in ("snapshot", "find"):
        print_snapshot(r, args) if cmd == "snapshot" else (sys.stdout.write(json.dumps(r, ensure_ascii=False, indent=2) + "\n") if args.json else
                                                            sys.stdout.write(f"{len(r['items'])} match(es) on {r['url']}\n" + "".join(fmt_item(it) + "\n" for it in r["items"])))
        return EXIT_OK
    if cmd == "read":
        content = r.get("content") or ""
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            body = ("\n".join([f"# title: {r.get('title') or '—'}", f"# url: {r['url']}",
                               f"# source: browser session · {r.get('words', 0)} words", f"# date: {r.get('date') or '—'} · author: {r.get('author') or '—'}"]
                              + [f"# warning: {w}" for w in r.get("warnings", [])]) + "\n\n" + content + "\n") if args.format != "text" or True else content
            Path(args.out).write_text(body, encoding="utf-8", newline="\n")
            sys.stdout.write(f"saved: {args.out} ({r.get('words', 0)} words)\n")
            return EXIT_OK
        if args.json:
            sys.stdout.write(json.dumps({k: v for k, v in r.items() if k != "ok"}, ensure_ascii=False, indent=2) + "\n")
            return EXIT_OK
        cut = len(content) > args.max_chars
        head = [f"# title: {r.get('title') or '—'}", f"# url: {r['url']}", f"# source: browser session · {r.get('words', 0)} words",
                f"# date: {r.get('date') or '—'} · author: {r.get('author') or '—'}"] + [f"# warning: {w}" for w in r.get("warnings", [])]
        sys.stdout.write("\n".join(head) + "\n\n" + content[: args.max_chars] + (f"\n\n[truncated: showed {args.max_chars} of {len(content)} chars — rerun with -o FILE]\n" if cut else "\n"))
        return EXIT_OK
    if cmd == "html":
        if args.out:
            Path(args.out).write_text(r["html"], encoding="utf-8", newline="\n")
            sys.stdout.write(f"saved: {args.out} ({len(r['html'])} chars)\n")
        else:
            sys.stdout.write(r["html"] + "\n")
        return EXIT_OK
    if cmd == "eval":
        sys.stdout.write(json.dumps(r["value"], ensure_ascii=False, indent=2) + "\n")
        return EXIT_OK
    if cmd in ("tabs", "status", "cookies"):
        if args.json or cmd == "cookies":
            sys.stdout.write(json.dumps({k: v for k, v in r.items() if k != "ok"}, ensure_ascii=False, indent=2) + "\n")
        else:
            if cmd == "status":
                sys.stdout.write(f"session: {'headful' if r.get('headful') else 'headless'}{' · profile ' + r['profile'] if r.get('profile') else ''}"
                                 f" · web-extract {'available' if r.get('web_extract') else 'missing (read falls back to plain text)'}\n")
            for pg in r["pages"]:
                sys.stdout.write(f"{'*' if pg['current'] else ' '} [{pg['index']}] {pg['title'] or '—'} — {pg['url']}\n")
            if cmd == "status" and r.get("recent"):
                sys.stdout.write("recent: " + " | ".join(r["recent"][-5:]) + "\n")
        return EXIT_OK
    note = {"open": "opened", "click": "clicked", "type": "typed", "select": f"selected {r.get('selected')}", "press": "pressed", "scroll": "scrolled",
            "back": "went back", "forward": "went forward", "reload": "reloaded", "wait": "ready", "hover": "hovered", "check": "checked",
            "uncheck": "unchecked", "focus": "focused", "screenshot": f"saved {r.get('path')}", "tab": "switched tab"}.get(cmd, cmd)
    if r.get("blocked"):
        note += f" — BLOCKED: {r['blocked']['kind']} ({r['blocked'].get('vendor') or 'bot check'}); use --headful so the user can pass it, do not retry"
    elif r.get("http_error"):
        note += f" — page answered HTTP {r['http_error']}"
    print_state(note, r, args)
    if r.get("blocked"):
        return EXIT_BLOCKED
    if r.get("http_error"):
        return EXIT_HTTP
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
