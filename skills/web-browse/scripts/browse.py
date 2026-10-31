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
    uv run browse.py click "a.settings"             # selectors work too (must match one element)
    uv run browse.py screenshot shot.png
    uv run browse.py stop

Exit codes: 0 ok · 1 element/selector not found or ambiguous, action failed, page
    unresponsive · 2 bad input, no session, browser missing · 3 page HTTP error ·
    4 bot wall · 6 navigation/network error or timeout · 7 other (session busy)
"""

import argparse
import hmac
import importlib.util
import json
import os
import queue
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

EXIT_OK, EXIT_FAIL, EXIT_BAD_INPUT, EXIT_HTTP, EXIT_BLOCKED, EXIT_NOT_HTML, EXIT_NETWORK, EXIT_OTHER = range(8)
DEFAULT_IDLE_MIN = 30
MAX_SNAPSHOT = 150
MAX_REQUEST = 4 << 20          # bytes of one JSON request line
PRE_AUTH_TIMEOUT = 5.0         # seconds a client gets to send its request line
STOP_GRACE = 15.0              # seconds `stop`/idle exit waits for a clean shutdown before killing the process
UA_TOKEN = "web-browse/1.0 (+https://github.com/zedxter/skills)"
INTERACTIVE = ("a, button, input, select, textarea, summary, label, [role=button], [role=link], [role=tab], "
               "[role=menuitem], [role=option], [onclick]")
UNRESPONSIVE = "page is unresponsive (a script on it is busy)"
SNAPSHOT_JS = r"""
([max, all]) => {
  const sel = 'a[href], button, input, select, textarea, summary, [role=button], [role=link], [role=tab], [role=menuitem], ' +
              '[role=checkbox], [role=radio], [role=switch], [role=option], [role=combobox], [role=textbox], [role=searchbox], ' +
              '[contenteditable=""], [contenteditable="true"], [onclick], [tabindex]:not([tabindex="-1"])';
  // every page-controlled string is flattened to one line so it cannot forge snapshot/state lines
  const clean = (v, n) => String(v == null ? '' : v).replace(/[\s\x00-\x1f\x7f-\x9f]+/g, ' ').trim().slice(0, n);
  // refs are stable for the life of the document: existing numbers are kept, new elements continue the sequence
  // a ref already used by another element in this pass (cloned node, page-set attribute) gets a new number
  let next = 0;
  document.querySelectorAll('[data-wb-ref]').forEach(e => { const n = +e.getAttribute('data-wb-ref'); if (Number.isInteger(n) && n > next && n < 1e9) next = n; });
  const els = Array.from(document.querySelectorAll(sel));
  const out = []; const seen = new Set(); const taken = new Set();
  const roleOf = (el) => {
    const tag = el.tagName.toLowerCase();
    if (el.getAttribute('role')) return clean(el.getAttribute('role'), 30);
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
    if (!(ref > 0) || !Number.isInteger(ref) || taken.has(ref)) { ref = ++next; el.setAttribute('data-wb-ref', String(ref)); }
    taken.add(ref);
    const tag = el.tagName.toLowerCase();
    let label = el.getAttribute('aria-label') || (el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute('title') ||
                el.getAttribute('alt') || (tag === 'input' || tag === 'textarea' || tag === 'select' ? '' : el.innerText) || el.value || '';
    if (!label && (tag === 'input')) label = el.placeholder || el.name || '';
    if (!label) { const img = el.querySelector('img[alt]'); if (img) label = img.getAttribute('alt'); }
    const item = {ref, role, tag, text: clean(label, 100)};
    if (tag === 'a' && el.href) item.href = clean(el.href, 300);
    if (tag === 'input' || tag === 'textarea') {
      const choice = el.type === 'checkbox' || el.type === 'radio';
      if (el.type && el.type !== 'text') item.type = clean(el.type, 30);
      if (el.value && el.type !== 'password' && !choice) item.value = clean(el.value, 80);
      if (el.placeholder) item.placeholder = clean(el.placeholder, 80);
      if (el.name) item.name = clean(el.name, 80);
      if (el.required) item.required = true;
    }
    if (tag === 'input' && (el.type === 'checkbox' || el.type === 'radio')) item.checked = el.checked;
    if (el.getAttribute('aria-expanded') !== null) item.expanded = el.getAttribute('aria-expanded') === 'true';
    if (el.disabled) item.disabled = true;
    if (tag === 'select') item.options = Array.from(el.options).slice(0, 25).map(o => clean(o.text, 50));
    if (!visible) item.hidden = true;
    item.viewport = r.bottom > 0 && r.top < innerHeight;
    out.push(item);
    if (out.length >= max) break;
  }
  const headings = Array.from(document.querySelectorAll('h1,h2,h3')).filter(h => h.offsetParent !== null).slice(0, 40)
    .map(h => ({level: +h.tagName[1], text: clean(h.innerText, 120)}));
  const frames = document.querySelectorAll('iframe').length;
  return {items: out, total: els.length, headings, frames, title: clean(document.title, 300), url: location.href,
          scroll: {y: Math.round(scrollY), height: document.documentElement.scrollHeight, viewport: innerHeight}};
}
"""
_CLEAN_RE = re.compile(r"[\s\x00-\x1f\x7f-\x9f]+")


def clean(v: Any) -> str:
    """One line of page-controlled text (no newlines/control characters that could forge output lines)."""
    return _CLEAN_RE.sub(" ", str(v)).strip()


def budget(cmd: str, a: dict[str, Any]) -> float:
    """Seconds the daemon lets one command run before declaring the page unresponsive (the client waits a bit longer)."""
    try:
        t = max(0.1, float(a.get("timeout") or 20))
    except (TypeError, ValueError):
        t = 20.0
    b = 3 * t + 10  # open = goto (t) + settle (t + 5 s) + checks
    if cmd == "wait" and a.get("seconds"):
        try:
            b += max(0.0, float(a["seconds"]))
        except (TypeError, ValueError):
            pass
    if cmd == "read":
        b += 60  # web-extract runs in Python after the DOM is taken
    return b


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
        st = json.loads(state_path().read_text(encoding="utf-8"))
        return st if isinstance(st, dict) else None
    except (OSError, ValueError):
        return None


def write_state(data: dict[str, Any]) -> None:
    """Owner-only (0600) state file in an owner-only directory, replaced atomically: it holds the session token."""
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data))
    os.replace(tmp, path)


def remove_own_state() -> None:
    try:
        path = state_path()
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":  # os.kill(pid, 0) would send CTRL_C_EVENT on Windows
        import ctypes
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True

def plugin_versions(plugin_dir: Path) -> list[Path]:
    """web-extract's extract.py in each installed version of a cached plugin, newest first.

    Versions the agent has marked for removal are skipped, and unreadable or vanishing entries are ignored."""
    found = []
    try:
        versions = list(plugin_dir.iterdir())
    except OSError:
        return []
    for ver in versions:
        script = ver / "scripts" / "extract.py"
        try:
            if (ver / ".orphaned_at").exists() or not script.is_file():
                continue
            found.append((script.stat().st_mtime, str(script)))
        except OSError:
            continue
    return [Path(p) for _, p in sorted(found, reverse=True)]


def load_web_extract() -> Any | None:
    if os.environ.get("WEB_BROWSE_NO_EXTRACT", "").strip() not in ("", "0"):
        return None
    here = Path(__file__).resolve()
    candidates = [Path(os.environ["WEB_EXTRACT_SCRIPT"])] if os.environ.get("WEB_EXTRACT_SCRIPT") else []
    candidates += [here.parent.parent.parent / "web-extract" / "scripts" / "extract.py", here.parent / "extract.py"]
    # Installed as a plugin (<cache>/<marketplace>/<plugin>/<version>/scripts/): the same marketplace's web-extract.
    if len(here.parents) > 5 and here.parents[4].name == "cache" and here.parents[5].name == "plugins":
        candidates += plugin_versions(here.parents[3] / "web-extract")
    # The skill folders agents install into: the agentskills.io layout first, then agents' own folders.
    for root in (Path.cwd() / ".agents" / "skills", Path.home() / ".agents" / "skills",
                 Path.home() / ".claude" / "skills", Path.cwd() / ".claude" / "skills",  # agent-dirs
                 Path.home() / ".codex" / "skills", Path.home() / ".gemini" / "skills",  # agent-dirs
                 Path.home() / ".config" / "opencode" / "skills"):  # agent-dirs
        candidates.append(root / "web-extract" / "scripts" / "extract.py")
    # Any marketplace in an agent's plugin cache (<cache>/<marketplace>/web-extract/<version>/), newest first.
    plugin_cache = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "plugins" / "cache"  # agent-dirs
    try:
        markets = sorted(plugin_cache.iterdir())
    except OSError:
        markets = []
    for market in markets:
        candidates += plugin_versions(market / "web-extract")
    for path in candidates:
        try:
            found = path.is_file()
        except OSError:  # an unreadable folder on the way is just another place it isn't
            found = False
        if found:
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
    """The browser-owning side: one Playwright, one context, N pages. Every Playwright call happens on the main thread."""

    def __init__(self, headful: bool, profile: str | None, idle_min: float) -> None:
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        launch: dict[str, Any] = {"headless": not headful}
        ctx_opts: dict[str, Any] = {"viewport": {"width": 1280, "height": 900}, "locale": "en-US", "accept_downloads": True}
        self.browser = None
        try:
            if profile:
                if not headful:  # identify the headless browser in persistent sessions too
                    probe = self.pw.chromium.launch(headless=True)
                    try:
                        ctx_opts["user_agent"] = f"{probe.new_page().evaluate('navigator.userAgent')} {UA_TOKEN}"
                    finally:
                        probe.close()
                Path(profile).mkdir(parents=True, exist_ok=True, mode=0o700)
                self.ctx = self.pw.chromium.launch_persistent_context(profile, **launch, **ctx_opts)
                if os.name != "nt":
                    try:
                        os.chmod(profile, 0o700)  # cookies and logins live here
                    except OSError:
                        pass
            else:
                self.browser = self.pw.chromium.launch(**launch)
                if not headful:
                    probe = self.browser.new_page()
                    ua = probe.evaluate("navigator.userAgent")
                    probe.close()
                    ctx_opts["user_agent"] = f"{ua} {UA_TOKEN}"
                self.ctx = self.browser.new_context(**ctx_opts)
        except BaseException:
            self.pw.stop()
            raise
        self.ctx.set_default_timeout(20_000)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.headful, self.profile, self.idle = headful, profile, idle_min * 60
        self.nav: dict[Any, tuple[int, dict[str, str]]] = {}  # page → status and headers of its last main-frame navigation
        self.wx = load_web_extract()
        self.log: list[str] = []
        self.cached: dict[str, Any] = {}  # last state(), served to `ping` without touching the browser
        self.ctx.on("page", self._watch)
        for p in self.ctx.pages:
            self._watch(p)
        self.state()

    # bookkeeping ----------------------------------------------------------
    def _watch(self, page: Any) -> None:
        def on_response(resp: Any) -> None:
            try:
                req = resp.request
                if req.is_navigation_request() and req.frame == page.main_frame:
                    self.nav[page] = (resp.status, {str(k).lower(): str(v) for k, v in resp.headers.items()})
            except Exception:
                pass
        page.on("response", on_response)
        page.on("close", lambda *_: self.nav.pop(page, None))

    def pages(self) -> list[Any]:
        return [p for p in self.ctx.pages if not p.is_closed()]

    def ensure_page(self) -> Any:
        if self.page.is_closed():
            ps = self.pages()
            self.page = ps[-1] if ps else self.ctx.new_page()
        return self.page

    def status_of(self, page: Any) -> tuple[int | None, dict[str, str]]:
        return self.nav.get(page, (None, {}))

    def responsive(self, page: Any, ms: int) -> bool:
        """True when the page's main thread runs a trivial script within `ms` (a busy loop would hang page.title()/content() forever)."""
        try:
            page.wait_for_function("() => true", timeout=max(100, ms), polling=100)
            return True
        except Exception:
            return False

    def title_of(self, page: Any, ms: int = 1500) -> str | None:
        if not self.responsive(page, ms):
            return None
        try:
            return clean(page.title())
        except Exception:
            return None

    def settle(self, timeout_ms: int) -> None:
        page = self.ensure_page()
        for state, budget_ms in (("load", timeout_ms), ("networkidle", min(5000, timeout_ms // 3))):
            try:
                page.wait_for_load_state(state, timeout=budget_ms)
            except Exception:
                pass

    def state(self) -> dict[str, Any]:
        page = self.ensure_page()
        ps = self.pages()
        title = self.title_of(page)
        out = {"url": page.url, "title": title, "status": self.status_of(page)[0], "tabs": len(ps),
               "tab": ps.index(page) + 1 if page in ps else 1}
        if title is None and not self.responsive(page, 100):
            out["unresponsive"] = True
        self.cached = dict(out)
        return out

    def tab_list(self) -> list[dict[str, Any]]:
        cur = self.ensure_page()
        return [{"index": i + 1, "url": p.url, "title": self.title_of(p, 1000) or ("(unresponsive)" if not self.responsive(p, 100) else ""),
                 "current": p == cur} for i, p in enumerate(self.pages())]

    def locator(self, target: str) -> Any:
        """A locator for exactly one element: a ref, `text=…`, or a CSS selector. Several matches are an error (use a ref)."""
        page = self.ensure_page()
        if re.fullmatch(r"\d+", target.strip()):
            loc = page.locator(f'[data-wb-ref="{target.strip()}"]')
            n = loc.count()
            if n == 0:
                raise LookupError(f"ref {target} is not on the page any more; run `snapshot` again")
            if n > 1:  # cloned nodes or the page itself set the attribute; `snapshot` renumbers duplicates
                raise LookupError(f"ref {target} is on {n} elements now; run `snapshot` again")
            return loc
        if target.startswith("text="):  # prefer something clickable/typable over a heading or paragraph with the same words
            needle = target[5:]
            exact_re = re.compile(r"^\s*" + re.escape(needle) + r"\s*$", re.I)
            text_re = re.compile(re.escape(needle), re.I)
            inter = page.locator(INTERACTIVE).filter(has_text=text_re)
            n = inter.count()
            if n == 1:
                return inter
            if n > 1:
                # nested clickables (<a><button>Sign in</button></a>) are one target: keep the innermost match
                inner = inter.filter(has_not=page.locator(INTERACTIVE).filter(has_text=text_re))
                m = inner.count()
                if m == 1:
                    return inner
                exact = (inner if m else inter).filter(has_text=exact_re)
                if exact.count() == 1:
                    return exact
                raise LookupError(f"text={needle!r} matches {m or n} clickable elements; use a ref from `snapshot`")
            loc = page.get_by_text(needle, exact=False)
            n = loc.count()
            if n == 0:
                raise LookupError(f"no element with text {needle!r}; run `snapshot` or `find {needle}`")
            if n > 1:
                exact = page.get_by_text(needle, exact=True)
                if exact.count() == 1:
                    return exact
                raise LookupError(f"text={needle!r} matches {n} elements; use a ref from `snapshot`")
            return loc
        loc = page.locator(target)
        n = loc.count()
        if n == 0:
            raise LookupError(f"nothing matches {target!r}; run `snapshot` for refs or check the selector")
        if n > 1:
            raise LookupError(f"{target!r} matches {n} elements; use a ref from `snapshot` or a narrower selector")
        return loc

    def classify_page(self) -> Any | None:
        """web-extract's bot-wall detection on the current DOM (None when web-extract is unavailable)."""
        page = self.ensure_page()
        status, headers = self.status_of(page)
        if self.wx is None or status is None or not self.responsive(page, 2000):
            return None
        body = page.content().encode("utf-8")
        return self.wx.classify(status, headers, body[: self.wx.MARKER_SCAN_BYTES], page.url, len(body))

    def replace_if_stuck(self, page: Any) -> tuple[Any, bool]:
        """A tab whose script never yields cannot navigate away; close it and continue in a fresh tab."""
        if self.responsive(page, 1000):
            return page, False
        try:
            page.close(run_before_unload=False)
        except Exception:
            pass
        self.page = self.ctx.new_page()
        return self.page, True

    def request_close(self, page: Any) -> None:
        """Called from another thread when a command is wedged in a busy page: close that tab on Playwright's own
        event loop (the main thread is blocked inside it), which makes the pending call fail and frees the session."""
        try:
            loop, impl = page._loop, page._impl_obj
            loop.call_soon_threadsafe(lambda: loop.create_task(impl.close(runBeforeUnload=False)))
        except Exception:
            pass

    # commands -------------------------------------------------------------
    def handle(self, cmd: str, a: dict[str, Any]) -> dict[str, Any]:
        page = self.ensure_page()
        t = int(float(a.get("timeout", 20)) * 1000)
        fail = lambda e: {"ok": False, "code": EXIT_FAIL, "error": str(e)}  # noqa: E731
        if cmd == "ping":
            return {"ok": True, **self.state()}
        if cmd == "status":
            return {"ok": True, **self.state(), "headful": self.headful, "profile": self.profile, "web_extract": self.wx is not None,
                    "pages": self.tab_list(), "recent": self.log[-10:]}
        if cmd in ("snapshot", "find", "read", "html", "eval", "scroll", "press") and not self.responsive(page, min(t, 5000)):
            return {"ok": False, "code": EXIT_FAIL, "kind": "unresponsive",
                    "error": f"{UNRESPONSIVE}; `reload` or `open` replaces the tab, `tab close` drops it"}
        if cmd == "open" or (cmd == "reload" and not self.responsive(page, 1000)):
            url = a["url"] if cmd == "open" else page.url
            page, replaced = self.replace_if_stuck(page)
            self.nav.pop(page, None)
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=t)
            except Exception as e:
                return nav_error(e, url)
            self.settle(t)
            if resp is not None and page not in self.nav:
                self.nav[page] = (resp.status, {str(k).lower(): str(v) for k, v in resp.headers.items()})
            cls = self.classify_page()
            out = {"ok": True, **self.state(), **({"replaced_tab": True} if replaced else {})}
            status = out.get("status")
            if cls is not None and cls.error is not None and cls.error.code == EXIT_BLOCKED:
                out.update(blocked=cls.error.as_dict())
            elif (status or 0) >= 400:
                out.update(http_error=status)
            return out
        if cmd == "snapshot":
            data = page.evaluate(SNAPSHOT_JS, [int(a.get("max", MAX_SNAPSHOT)), bool(a.get("all"))])
            return {"ok": True, **data, "status": self.status_of(page)[0]}
        if cmd == "find":
            data = page.evaluate(SNAPSHOT_JS, [1000, True])
            needle = str(a["text"]).lower()
            fields = ("text", "href", "name", "placeholder", "value", "type", "role")
            hits = [it for it in data["items"] if needle in " ".join(str(it.get(k, "")) for k in fields).lower()]
            return {"ok": True, "items": hits[: int(a.get("max", 50))], "url": data["url"], "title": data["title"]}
        if cmd in ("click", "hover", "check", "uncheck", "focus"):
            try:
                loc = self.locator(a["target"])
                getattr(loc, cmd)(timeout=t) if cmd != "click" else loc.click(timeout=t, button=a.get("button", "left"), click_count=int(a.get("count", 1)))
            except LookupError as e:
                return fail(e)
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
                    loc.evaluate("e => { e.focus(); const n = (e.value || '').length;"
                                 " if (e.setSelectionRange) { try { e.setSelectionRange(n, n); } catch (_) {} } }", timeout=t)
                    loc.press_sequentially(a["text"], timeout=t)
                if a.get("enter"):
                    loc.press("Enter", timeout=t)
                    self.settle(t)
            except LookupError as e:
                return fail(e)
            except Exception as e:
                return action_error(e, "type", a["target"])
            return {"ok": True, **self.state()}
        if cmd == "select":
            want = str(a["value"])
            try:
                loc = self.locator(a["target"])
                opts = loc.evaluate("e => e.options ? Array.from(e.options).map(o => [o.text.trim(), o.value]) : null", timeout=t)
                if opts is None:
                    return fail(f"{a['target']!r} is not a <select>; use `click` for custom drop-downs")
                if a.get("by_value"):
                    match = [v for _, v in opts if v == want]
                else:
                    match = ([v for lbl, v in opts if lbl == want] or [v for lbl, v in opts if lbl.lower() == want.lower()]
                             or [v for _, v in opts if v == want])
                if not match:
                    shown = " | ".join(clean(lbl)[:50] for lbl, _ in opts[:30]) + (" …" if len(opts) > 30 else "")
                    return fail(f"no option {want!r} ({'value' if a.get('by_value') else 'label'}); options: {shown}")
                chosen = loc.select_option(value=match[0], timeout=t)
            except LookupError as e:
                return fail(e)
            except Exception as e:
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
            if re.fullmatch(r"\d+", str(where)) or (isinstance(where, str) and where not in ("down", "up", "top", "bottom")):
                try:
                    self.locator(str(where)).scroll_into_view_if_needed(timeout=t)
                except LookupError as e:
                    return fail(e)
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
            self.nav.pop(page, None)
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
            except re.error as e:
                return {"ok": False, "code": EXIT_BAD_INPUT, "error": f"bad --url pattern: {e}"}
            except Exception as e:
                return action_error(e, "wait", a.get("selector") or a.get("text") or a.get("url") or "")
            return {"ok": True, **self.state()}
        if cmd == "read":
            html = page.content().encode("utf-8")
            if self.wx is None:
                text = page.evaluate("document.body ? document.body.innerText : ''")
                return {"ok": True, **self.state(), "content": text, "content_format": "text", "words": len(text.split()), "warnings": ["no_web_extract"]}
            wx = self.wx
            status, headers = self.status_of(page)
            ns = argparse.Namespace(format="text" if a.get("format") == "text" else "markdown", links=bool(a.get("links")), raw=bool(a.get("raw")),
                                    comments=False, recall=False, precision=False, structured=bool(a.get("structured")), out=None, max_chars=0)
            f = wx.Fetched(status or 200, {**headers, "content-type": "text/html; charset=utf-8"}, page.url, html)
            result = wx.new_result(page.url)
            err = wx.handle_body(f, result, ns, "browser")
            if err is not None:
                text = page.evaluate("document.body ? document.body.innerText : ''")
                return {"ok": True, **self.state(), "content": text, "content_format": "text", "words": len(text.split()),
                        "warnings": ["extraction_failed:" + err.kind], "title": result.get("title")}
            return {"ok": True, **self.state(), **{k: result.get(k) for k in ("title", "author", "date", "content", "content_format", "words",
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
                    page.screenshot(path=str(path), full_page=bool(a.get("full")), timeout=t)
            except LookupError as e:
                return fail(e)
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
            return {"ok": True, "pages": self.tab_list()}
        if cmd == "tab":
            ps = self.pages()
            which = str(a.get("which"))
            if which == "new":
                self.page = self.ctx.new_page()
                if a.get("url"):
                    return self.handle("open", {"url": a["url"], "timeout": a.get("timeout", 20)})
                return {"ok": True, **self.state()}
            if which == "close":
                if len(ps) <= 1:  # keep one tab open: a fresh blank one (works even when the old one is stuck)
                    self.ctx.new_page()
                page.close(run_before_unload=False)
                self.page = self.pages()[-1]
                return {"ok": True, **self.state()}
            try:
                n = int(which)
            except ValueError:
                n = 0
            if not 1 <= n <= len(ps):
                return {"ok": False, "code": EXIT_BAD_INPUT, "error": f"no tab {which}; there are {len(ps)}"}
            self.page = ps[n - 1]
            self.page.bring_to_front()
            return {"ok": True, **self.state()}
        if cmd == "cookies":
            cs = self.ctx.cookies()
            return {"ok": True, "count": len(cs), "cookies": [{"name": c["name"], "domain": c["domain"], "expires": c.get("expires")} for c in cs]}
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
    if "closed" in msg and ("Target" in msg or "page" in msg):
        return {"ok": False, "code": EXIT_FAIL, "error": f"{cmd} {target!r}: the tab was closed ({UNRESPONSIVE}?)", "kind": "closed"}
    return {"ok": False, "code": EXIT_FAIL, "error": f"{cmd} {target!r} failed: {msg[:200]}", "kind": "action"}


def log_line(cmd: str, args: dict[str, Any], resp: dict[str, Any]) -> str:
    """One `recent` entry: typed text and eval source are redacted, URLs lose query, fragment and credentials."""
    shown: dict[str, Any] = {k: ("***" if k in ("text", "js") and v else v) for k, v in args.items() if k != "timeout" and v not in (None, False, "")}
    if isinstance(shown.get("url"), str) and cmd != "wait":
        try:
            u = urlsplit(shown["url"])
            shown["url"] = u._replace(netloc=u.netloc.rsplit("@", 1)[-1], query="", fragment="").geturl()
        except ValueError:
            shown["url"] = "***"
    return f"{cmd} {json.dumps(shown, ensure_ascii=False)[:100]} → {'ok' if resp.get('ok') else 'fail'}"


class Job:
    """One authenticated request, handed from a connection thread to the main (browser) thread."""

    def __init__(self, cmd: str, args: dict[str, Any]) -> None:
        self.cmd, self.args = cmd, args
        self.done = threading.Event()
        self.result: dict[str, Any] | None = None
        self.state = "queued"  # queued → running → done, or abandoned (client gave up / session stopping)


class Daemon:
    """Sockets on background threads, Playwright on the main thread.

    The accept thread owns the idle timer and `stop`, and each connection thread waits for its job only up to
    the command's budget, so a page stuck in a busy script can neither hang a client nor keep the session alive:
    the client gets `page is unresponsive`, the stuck tab is closed, and `stop` still ends the process."""

    def __init__(self, session: Session, srv: socket.socket, token: str) -> None:
        self.session, self.srv, self.token = session, srv, token
        self.jobs: queue.Queue[Job | None] = queue.Queue()
        self.lock = threading.Lock()
        self.current: Job | None = None
        self.last = time.monotonic()
        self.stopping = threading.Event()

    # main thread ----------------------------------------------------------
    def run(self) -> None:
        threading.Thread(target=self.accept_loop, name="wb-accept", daemon=True).start()
        while True:
            try:
                job = self.jobs.get(timeout=1.0)
            except queue.Empty:
                continue
            if job is None:
                break
            with self.lock:
                if job.state != "queued":
                    continue
                job.state, self.current = "running", job
            try:
                resp = self.session.handle(job.cmd, job.args)
            except Exception as e:  # never let one command kill the session
                resp = {"ok": False, "code": EXIT_OTHER, "error": f"{type(e).__name__}: {(str(e).splitlines() or [''])[0][:300]}"}
            self.session.log.append(log_line(job.cmd, job.args, resp))
            with self.lock:
                if job.state == "running":
                    job.result, job.state = resp, "done"
                self.current, self.last = None, time.monotonic()
            job.done.set()

    # background threads ---------------------------------------------------
    def accept_loop(self) -> None:
        self.srv.settimeout(1.0)
        while not self.stopping.is_set():
            with self.lock:
                idle = self.current is None and time.monotonic() - self.last > self.session.idle
            if idle:
                self.shutdown()
                return
            try:
                conn, _ = self.srv.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            threading.Thread(target=self.serve_conn, args=(conn,), name="wb-conn", daemon=True).start()

    def serve_conn(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(PRE_AUTH_TIMEOUT)  # an unauthenticated client cannot hold the line open
                raw = b""
                while not raw.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    raw += chunk
                    if len(raw) > MAX_REQUEST:
                        return
                req = json.loads(raw.decode("utf-8"))
                tok = req.get("token") if isinstance(req, dict) else None
                if not isinstance(tok, str) or not hmac.compare_digest(tok.encode("utf-8"), self.token.encode("utf-8")):
                    return self.reply(conn, {"ok": False, "code": EXIT_BAD_INPUT, "error": "bad session token"})
                cmd = str(req.get("cmd"))
                args = req.get("args") if isinstance(req.get("args"), dict) else {}
                with self.lock:
                    self.last = time.monotonic()
                if cmd == "stop":
                    self.reply(conn, {"ok": True, "stopping": True})
                    self.shutdown()
                    return
                if cmd == "ping":  # answered here so `start` sees a live session even while a long command runs
                    with self.lock:
                        busy = self.current.cmd if self.current else None
                    return self.reply(conn, {"ok": True, **self.session.cached, "pid": os.getpid(), "busy": busy})
                if self.stopping.is_set():
                    return self.reply(conn, {"ok": False, "code": EXIT_BAD_INPUT, "error": "the session is stopping; run `start` again"})
                job = Job(cmd, args)
                self.jobs.put(job)
                limit = budget(cmd, args)
                if job.done.wait(limit):
                    return self.reply(conn, job.result or {"ok": False, "code": EXIT_OTHER, "error": "no result"})
                stuck = False
                with self.lock:
                    if job.state == "queued":
                        job.state = "abandoned"
                        resp = {"ok": False, "code": EXIT_OTHER, "error": "the session is still busy with an earlier command; retry in a moment"}
                    elif job.state == "running":
                        job.state, stuck = "abandoned", True
                        resp = {"ok": False, "code": EXIT_FAIL, "kind": "unresponsive",
                                "error": f"{UNRESPONSIVE}: `{cmd}` got no answer in {limit:g} s; closing that tab"}
                    else:
                        resp = job.result or {"ok": False, "code": EXIT_OTHER, "error": "no result"}
                self.reply(conn, resp)
                if stuck:
                    self.unwedge(job)
            except (OSError, ValueError, UnicodeDecodeError):
                pass

    @staticmethod
    def reply(conn: socket.socket, resp: dict[str, Any]) -> None:
        try:
            conn.settimeout(30)
            conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass

    def unwedge(self, job: Job) -> None:
        """Close the tab the main thread is stuck in; if that does not free it, end the process rather than hang forever."""
        self.session.request_close(self.session.page)
        if not self.wait_idle(job, 20):
            self.hard_exit()

    def wait_idle(self, job: Job, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            with self.lock:
                if self.current is not job:
                    return True
            time.sleep(0.1)
        return False

    def shutdown(self) -> None:
        """`stop` or idle timeout: refuse queued work, let the main thread close the browser; force it if it is stuck."""
        if self.stopping.is_set():
            return
        self.stopping.set()
        gone = {"ok": False, "code": EXIT_OTHER, "error": "the session was stopped while this command waited"}
        with self.lock:
            while True:
                try:
                    j = self.jobs.get_nowait()
                except queue.Empty:
                    break
                if j is not None:
                    j.state, j.result = "abandoned", gone
                    j.done.set()
            running = self.current
        self.jobs.put(None)
        if running is not None:
            with self.lock:
                running.state, running.result = "abandoned", gone
            running.done.set()
            self.session.request_close(self.session.page)
        timer = threading.Timer(STOP_GRACE, self.hard_exit)
        timer.daemon = True
        timer.start()

    def hard_exit(self) -> None:
        remove_own_state()
        try:
            self.srv.close()
        finally:
            time.sleep(0.3)  # let connection threads flush their replies
            os._exit(EXIT_OK)  # the Playwright driver sees its pipe close and takes Chromium down with it


def serve(args: argparse.Namespace) -> int:
    """Daemon: own the browser, answer JSON-line requests on a loopback socket, exit when idle or told to."""
    token = secrets.token_hex(16)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    port = srv.getsockname()[1]
    try:
        session = Session(args.headful, args.profile, args.idle_timeout)
    except Exception as e:
        write_state({"error": f"browser failed to start: {(str(e).splitlines() or [type(e).__name__])[0][:300]}", "pid": os.getpid()})
        srv.close()
        return EXIT_BAD_INPUT
    write_state({"port": port, "token": token, "pid": os.getpid(), "headful": args.headful, "profile": args.profile,
                 "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "idle_timeout_min": args.idle_timeout})
    daemon = Daemon(session, srv, token)
    try:
        daemon.run()
    finally:
        daemon.stopping.set()
        try:
            session.close()
        except Exception:
            pass
        finally:
            remove_own_state()
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
            s.sendall((json.dumps({"token": st.get("token"), "cmd": cmd, "args": args}, ensure_ascii=False) + "\n").encode("utf-8"))
            raw = b""
            while not raw.endswith(b"\n"):
                chunk = s.recv(1 << 20)
                if not chunk:
                    break
                raw += chunk
        if not raw.strip():
            return {"ok": False, "code": EXIT_OTHER, "error": "the session closed the connection without answering (it may be stopping); `status` to check"}
        return json.loads(raw.decode("utf-8"))
    except (socket.timeout, TimeoutError):
        return {"ok": False, "code": EXIT_OTHER, "error": "session still busy (the command exceeded the client wait); retry `status` in a moment"}
    except ConnectionRefusedError:
        if not pid_alive(st.get("pid")):
            try:
                state_path().unlink()
            except OSError:
                pass
            return {"ok": False, "code": EXIT_BAD_INPUT, "error": "the browser session is gone (idle timeout or crash); run `start` again"}
        return {"ok": False, "code": EXIT_BAD_INPUT, "error": f"the browser session (pid {st.get('pid')}) does not accept connections; run `start` again"}
    except OSError as e:
        return {"ok": False, "code": EXIT_OTHER, "error": f"cannot reach the browser session: {e}"}
    except ValueError:
        return {"ok": False, "code": EXIT_OTHER, "error": "garbled reply from the session"}


def start(args: argparse.Namespace) -> int:
    st = read_state()
    if st and "port" in st and pid_alive(st.get("pid")):
        probe = send("ping", {}, timeout=3)
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
            probe = send("ping", {}, timeout=3)
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
    tabs = f" · tab {r.get('tab')}/{r.get('tabs')}" if (r.get("tabs") or 1) > 1 else ""
    busy = f" · {UNRESPONSIVE}" if r.get("unresponsive") else ""
    sys.stdout.write(f"{prefix}\nurl: {clean(r.get('url'))}\ntitle: {clean(r.get('title') or '—')}{status}{tabs}{busy}\n")


def fmt_item(it: dict[str, Any]) -> str:
    bits = [f"[{it['ref']}]", clean(it["role"])]
    if it.get("text"):
        bits.append(json.dumps(it["text"], ensure_ascii=False))
    extra = []
    for k in ("href", "type", "name", "placeholder", "value"):
        if it.get(k):  # page-controlled: quoted (escapes newlines and quotes) so it cannot pose as another line or field
            extra.append(f"{k}={json.dumps(str(it[k]), ensure_ascii=False)}" if k != "href" else f"→ {clean(it[k])}")
    if "checked" in it:
        extra.append("checked" if it["checked"] else "unchecked")
    if "expanded" in it:
        extra.append("expanded" if it["expanded"] else "collapsed")
    if it.get("options"):
        extra.append("options: " + " | ".join(clean(o) for o in it["options"][:8]) + (" …" if len(it["options"]) > 8 else ""))
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
    sys.stdout.write(f"url: {clean(r['url'])}\ntitle: {clean(r.get('title') or '—')}{status}{where}\n")
    if r.get("headings"):
        sys.stdout.write("headings: " + " · ".join(("#" * h["level"]) + " " + clean(h["text"]) for h in r["headings"][:12]) + "\n")
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
    p.add_argument("--json", action="store_true", help="machine-readable JSON output for every command (before or after the command)")
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
        r = send("stop", {}, timeout=10)
        if r.get("ok"):
            for _ in range(int(STOP_GRACE * 10) + 20):  # wait for the state file to disappear (a stuck page is killed after STOP_GRACE)
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
    r = send(cmd, a, timeout=budget(cmd, a) + 15)  # the daemon answers "unresponsive" at budget(); never give up before it
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
            body = "\n".join([f"# title: {clean(r.get('title') or '—')}", f"# url: {clean(r['url'])}",
                              f"# source: browser session · {r.get('words', 0)} words",
                              f"# date: {clean(r.get('date') or '—')} · author: {clean(r.get('author') or '—')}"]
                             + [f"# warning: {w}" for w in r.get("warnings") or []]) + "\n\n" + content + "\n"
            Path(args.out).write_text(body, encoding="utf-8", newline="\n")
            if args.json:
                sys.stdout.write(json.dumps({"saved": args.out, "words": r.get("words", 0), "url": r.get("url"), "title": r.get("title"),
                                             "warnings": r.get("warnings") or []}, ensure_ascii=False, indent=2) + "\n")
            else:
                sys.stdout.write(f"saved: {args.out} ({r.get('words', 0)} words)\n")
            return EXIT_OK
        if args.json:
            sys.stdout.write(json.dumps({k: v for k, v in r.items() if k != "ok"}, ensure_ascii=False, indent=2) + "\n")
            return EXIT_OK
        cut = len(content) > args.max_chars
        head = [f"# title: {clean(r.get('title') or '—')}", f"# url: {clean(r['url'])}", f"# source: browser session · {r.get('words', 0)} words",
                f"# date: {clean(r.get('date') or '—')} · author: {clean(r.get('author') or '—')}"] + [f"# warning: {w}" for w in r.get("warnings") or []]
        sys.stdout.write("\n".join(head) + "\n\n" + content[: args.max_chars] + (f"\n\n[truncated: showed {args.max_chars} of {len(content)} chars — rerun with -o FILE]\n" if cut else "\n"))
        return EXIT_OK
    if cmd == "html":
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(r["html"], encoding="utf-8", newline="\n")
            sys.stdout.write(json.dumps({"saved": args.out, "chars": len(r["html"]), "url": r.get("url")}) + "\n" if args.json
                             else f"saved: {args.out} ({len(r['html'])} chars)\n")
        elif args.json:
            sys.stdout.write(json.dumps({k: v for k, v in r.items() if k != "ok"}, ensure_ascii=False) + "\n")
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
                sys.stdout.write(f"{'*' if pg['current'] else ' '} [{pg['index']}] {clean(pg['title'] or '—')} — {clean(pg['url'])}\n")
            if cmd == "status" and r.get("recent"):
                sys.stdout.write("recent: " + " | ".join(r["recent"][-5:]) + "\n")
        return EXIT_OK
    note = {"open": "opened", "click": "clicked", "type": "typed", "select": f"selected {r.get('selected')}", "press": "pressed", "scroll": "scrolled",
            "back": "went back", "forward": "went forward", "reload": "reloaded", "wait": "ready", "hover": "hovered", "check": "checked",
            "uncheck": "unchecked", "focus": "focused", "screenshot": f"saved {r.get('path')}",
            "tab": {"close": "closed tab", "new": "opened tab"}.get(str(getattr(args, "which", "")), "switched tab")}.get(cmd, cmd)
    if r.get("replaced_tab"):
        note += " (the unresponsive tab was closed and replaced by a new one)"
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
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("error: interrupted\n")
        sys.exit(130)
    except BrokenPipeError as e:
        try:
            sys.stdout.flush()
        except BrokenPipeError:  # the reader closed stdout (`| head`): stop quietly, as a pipe expects
            try:
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            except OSError:
                pass
            sys.exit(0)
        sys.stderr.write(f"error: unexpected BrokenPipeError: {e} — a bug; report it with the command line\n")
        sys.exit(EXIT_OTHER)
    except Exception as e:  # noqa: BLE001 - a crash exits 7, never 1 or another code that has a meaning
        sys.stderr.write(f"error: unexpected {type(e).__name__}: {(str(e).splitlines() or [''])[0][:300]} "
                         "— a bug; report it with the command line\n")
        sys.exit(EXIT_OTHER)
