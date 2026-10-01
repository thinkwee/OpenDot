"""The Agent Computer: each agent gets a home directory, a real terminal, and a
persistent, watchable browser.

v2 (W7): the shell is a real PTY session (persistent cwd/env, ptyprocess) so
state survives across tool calls; the browser is one Chromium per *root*
agent (Playwright, launched with a local CDP port) and every delegated helper
(``<agent>-wN``) gets its own **tab** in that same browser instead of its own
Chromium; the live view streams over CDP ``Page.startScreencast`` to
subscribers of ``stream_subscribe()`` (wired to a dedicated WebSocket by
``opendot/ext/computer_api.py``) instead of flooding the main ``/ws``.
Sandbox backends (local / bwrap / docker / e2b) are pluggable — see
``sandbox.py``. The Gatekeeper (gatekeeper.py) still gates anything risky before
it reaches here.
"""

from __future__ import annotations

import asyncio
import base64
import glob
import hashlib
import logging
import os
import re
import shutil
import time
import urllib.parse
from pathlib import Path

import httpx

from ..bus import bus
from ..config import settings
from . import sandbox, term
from .search import search_chain

log = logging.getLogger("opendot.computer")

MAX_OUT = 12_000
CDP_PORT_BASE = 9310
CDP_PORT_RANGE = 400
SCREENCAST_FPS_CAP = 8  # frames forwarded to viewers per second, per tab


def _clip(s: str, n: int = MAX_OUT) -> str:
    return s if len(s) <= n else s[: n // 2] + f"\n…[{len(s) - n} chars cut]…\n" + s[-n // 2:]


# The page as a numbered list of what can be clicked or typed into (the way browser-use
# and Playwright MCP show it to a model): "click 12" works where a guessed CSS selector
# doesn't. Numbers are written onto the elements, so they hold until the next look.
ELEMENTS_JS = r"""(max) => {
  const sel = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],' +
    '[role=link],[role=checkbox],[role=radio],[role=tab],[role=menuitem],[role=option],' +
    '[role=combobox],[role=switch],[contenteditable=""],[contenteditable=true],[onclick]';
  document.querySelectorAll('[data-dot-ref]').forEach(e => e.removeAttribute('data-dot-ref'));
  const out = [];
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= max) break;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || cs.visibility === 'hidden' || el.disabled ||
        el.closest('[aria-hidden=true]')) continue;
    const tag = el.tagName.toLowerCase(), type = (el.type || '').toLowerCase();
    const role = el.getAttribute('role') || ({a: 'link', select: 'select', button: 'button',
      textarea: 'textbox'})[tag] || (tag === 'input' ? (['checkbox', 'radio', 'submit',
      'button'].includes(type) ? type : 'textbox') : tag);
    const name = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder ||
      el.title || el.name || '').replace(/\s+/g, ' ').trim().slice(0, 80);
    if (!name && role === 'link') continue;
    el.setAttribute('data-dot-ref', ++n);
    let line = `[${n}] ${role} "${name}"`;
    if (role === 'textbox' && el.value) line += ` = "${String(el.value).slice(0, 40)}"`;
    if (tag === 'select') line += ' options: ' + [...el.options].slice(0, 15).map(o => o.text.trim()).join(' | ');
    if (el.checked) line += ' (checked)';
    if (tag === 'a') line += ' -> ' + el.href.slice(0, 120);
    if (r.bottom < 0 || r.top > innerHeight) line += ' (off-screen)';
    out.push(line);
  }
  return out;
}"""

# what sites show a browser they've decided is a bot
BLOCKED = re.compile(
    r"performing security verification|verify(ing)? you are (a )?human|are you a (ro)?bot|"
    r"tell if you'?re a human|thinks you are a .?bot|unusual traffic from your|press (and|&) hold|"
    r"pardon our interruption|experiencing high demand", re.I)
BLOCKED_HEAD = re.compile(r"captcha|just a moment|access denied|attention required|"
                          r"request blocked|bot or not|/help/bots", re.I)  # only in the address or title


# a cookie banner's "no" button, in the languages people meet most
REJECT_JS = r"""() => {
  const no = /^((reject|decline|refuse)( all)?( (additional|optional|non-essential|analytics))?( cookies)?|deny( all)?|refuse( all)?|disagree|(use )?(only )?(strictly )?(necessary|essential)( cookies)?( only)?|continue without accepting|alle ablehnen|ablehnen|nur notwendige( cookies)?|tout refuser|refuser|continuer sans accepter|rechazar( todo| todas)?|rifiuta( tutto)?|weigeren|alles weigeren|rejeitar( tudo)?|odrzuć( wszystko)?|全部拒绝|拒绝|拒絕|全部拒絕|仅必要|僅必要|すべて拒否|拒否する|拒否|모두 거부|거부)$/i;
  const about = /cookie|consent|privacy|datenschutz|隐私|隱私|クッキー|쿠키/i;
  for (const el of document.querySelectorAll('button,a,[role=button],input[type=button],input[type=submit]')) {
    const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
    const r = el.getBoundingClientRect();
    if (!no.test(text) || r.width < 2 || r.height < 2) continue;
    for (let box = el.parentElement, i = 0; box && i < 8; box = box.parentElement, i++) {
      if (about.test((box.innerText || '').slice(0, 3000))) { el.click(); return text }
    }
  }
  return null
}"""


async def past_consent(page) -> None:
    """Cookie banners: answered with the "no" button on the way in, the way a browser
    extension would, so neither the agent nor the human watching has to. Google's
    full-page wall (before Flights, Maps, Hotels…) is a form of its own."""
    if "consent.google." not in page.url and "consent.youtube." not in page.url:
        for _ in range(2):  # banners are often added a moment after the page loads
            for frame in page.frames:
                try:
                    if said := await frame.evaluate(REJECT_JS):
                        log.info("cookie banner on %s: pressed %r", page.url[:80], said)
                        await page.wait_for_timeout(500)
                        return
                except Exception:  # a frame that went away or won't run scripts
                    continue
            await page.wait_for_timeout(700)
        return
    try:
        btn = page.locator('form:has(input[name="set_eom"][value="true"]):not(:has('
                           'input[name="set_sc"])) button').first
        async with page.expect_navigation(timeout=15_000):
            await btn.click(timeout=5_000)
    except Exception as e:  # leave it to the agent (it can still click it itself)
        log.info("couldn't answer the cookie wall: %s", e)


def blocked_note(url: str, title: str, text: str) -> str | None:
    if BLOCKED_HEAD.search(f"{url} {title}") or BLOCKED.search(text[:1500]):
        return ("This site is blocking automated browsers from here, and retrying won't help. "
                "Don't try its sister sites or guess other URLs on it. Use what web_search "
                "already found, try a different kind of source, or ask the human to open it "
                "themselves (they can take over your browser in the Computer panel).")
    return None


def _user_agent(exe: str | None) -> str | None:
    """The user agent of the Chrome this is, without the "Headless" giveaway."""
    try:
        import subprocess
        v = subprocess.run([exe, "--version"], capture_output=True, text=True,
                           timeout=10).stdout
        major = re.search(r"(\d+)\.\d+", v).group(1)
    except Exception:
        return None
    return (f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36")


def _proxy(url: str) -> dict:
    """DOT_PROXY=http://user:pass@host:port → Playwright's proxy settings."""
    u = urllib.parse.urlsplit(url)
    p = {"server": f"{u.scheme}://{u.hostname}:{u.port}" if u.port else f"{u.scheme}://{u.hostname}"}
    if u.username:
        p.update(username=urllib.parse.unquote(u.username),
                 password=urllib.parse.unquote(u.password or ""))
    return p


def shared_dir() -> Path:
    p = settings.DATA_DIR / "shared"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cdp_port(root_agent_id: str) -> int:
    h = int(hashlib.sha1(root_agent_id.encode()).hexdigest(), 16)
    return CDP_PORT_BASE + (h % CDP_PORT_RANGE)


def _root_id(agent_id: str) -> str:
    m = re.match(r"^(.+)-w\d+$", agent_id)
    return m.group(1) if m else agent_id


def _extract_readable(html: str, url: str) -> str | None:
    try:
        import trafilatura
        return trafilatura.extract(html, url=url, include_links=False, include_comments=False,
                                   favor_recall=True)
    except Exception as e:
        log.debug("trafilatura extract failed for %s: %s", url, e)
        return None


class Tab:
    """One browser tab, owned by an agent (root) or a helper worker."""

    def __init__(self, owner_id: str, page) -> None:
        self.owner_id = owner_id
        self.page = page
        self.cdp = None
        self.viewers: set[asyncio.Queue] = set()
        self._last_forward = 0.0
        self.refs: dict[str, object] = {}  # element number -> the frame it's in
        self.labels: dict[str, str] = {}  # element number -> what it says (for Gatekeeper)
        self._stop_handle: asyncio.TimerHandle | None = None

    def title_safe(self) -> str:
        try:
            return self.page.url
        except Exception:
            return ""


class Computer:
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.home = settings.DATA_DIR / "agents" / agent_id / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        link = self.home / "shared"
        if not link.exists():
            try:
                link.symlink_to(shared_dir(), target_is_directory=True)
            except OSError:
                pass
        self._pw = None
        self._browser = None  # playwright BrowserContext — only populated on the root Computer
        self.tabs: dict[str, Tab] = {}  # owner_id -> Tab — only populated on the root Computer
        self.cdp_port = _cdp_port(_root_id(agent_id))
        self.last_used = time.time()
        self.activity = ""

    # ---------------- paths ----------------
    def resolve(self, path: str) -> Path:
        p = Path(path.replace("~", str(self.home), 1) if path.startswith("~") else path)
        if not p.is_absolute():
            p = self.home / p
        p = Path(os.path.normpath(p))
        real = p.resolve()
        if not any(real.is_relative_to(a) for a in (self.home.resolve(), shared_dir().resolve())):
            raise PermissionError(f"path outside the agent computer: {path}")
        return p

    def root_id(self) -> str:
        return _root_id(self.agent_id)

    def _root(self) -> "Computer":
        rid = self.root_id()
        return self if rid == self.agent_id else computer_for(rid)

    def _set_activity(self, text: str) -> None:
        self.activity = text
        bus.emit("computer", agent_id=self.agent_id, view="activity", text=text)

    # ---------------- shell (persistent PTY, pluggable sandbox backend) ----------------
    def _env(self) -> dict:
        keep = {"PATH", "LANG", "LC_ALL", "TERM", "TZ"}
        env = {k: v for k, v in os.environ.items() if k in keep}
        env.update(HOME=str(self.home), TERM="xterm-256color", PYTHONUNBUFFERED="1",
                   MPLBACKEND="Agg")
        return env

    async def shell(self, command: str, timeout: int | None = None) -> dict:
        timeout = min(timeout or settings.SHELL_TIMEOUT, 600)
        backend = sandbox.backend_name()
        self._set_activity(f"running: {command[:60]}")
        bus.emit("computer", agent_id=self.agent_id, view="terminal", input=command)

        if backend == "docker":
            return await self._shell_docker(command, timeout)
        if backend == "e2b":
            return await self._shell_e2b(command, timeout)

        argv = None
        if backend == "bwrap" and shutil.which("bwrap"):
            h = str(self.home)
            argv = ["bwrap", "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc",
                    "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
                    "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
                    "--tmpfs", "/tmp", "--bind", h, h, "--bind", str(shared_dir()),
                    str(shared_dir()), "--chdir", h, *sandbox.BWRAP_ISOLATION,
                    "bash", "--noprofile", "--norc", "-i"]
        sess = term.session_for(self.agent_id, self.home, self._env())
        if not sess._alive:
            sess.start(argv)
            await asyncio.sleep(0.5)  # let the startup banner flush before the first command
        result = await sess.run(command, timeout)
        bus.emit("computer", agent_id=self.agent_id, view="terminal",
                 output=result["output"][-4000:], exit_code=result["exit_code"])
        return result

    async def _shell_docker(self, command: str, timeout: int) -> dict:
        dk = sandbox.DockerBackend(self.root_id())
        t0 = time.time()
        try:
            argv = await dk.exec_argv(command)
        except Exception as e:
            return {"exit_code": -1, "output": f"[docker backend unavailable: {e}]"}
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
            code = proc.returncode
        except asyncio.TimeoutError:
            proc.kill()
            out, code = b"[timed out]", -9
        text = _clip(out.decode(errors="replace"))
        bus.emit("computer", agent_id=self.agent_id, view="terminal", output=text[-4000:],
                 exit_code=code)
        return {"exit_code": code, "output": text, "seconds": round(time.time() - t0, 1)}

    async def _shell_e2b(self, command: str, timeout: int) -> dict:
        t0 = time.time()
        try:
            eb = sandbox.E2BBackend(self.root_id())
            r = await asyncio.wait_for(eb.run(command), timeout)
        except Exception as e:
            return {"exit_code": -1, "output": f"[e2b backend unavailable: {e}]"}
        bus.emit("computer", agent_id=self.agent_id, view="terminal",
                 output=str(r.get("output", ""))[-4000:], exit_code=r.get("exit_code"))
        return {**r, "seconds": round(time.time() - t0, 1)}

    # ---------------- files ----------------
    def read_file(self, path: str, max_chars: int = 40_000, offset: int = 0) -> dict:
        from .. import fileparse
        p = self.resolve(path)
        if not p.is_file():
            raise FileNotFoundError(f"no such file: {path}")
        parsed = False
        if p.suffix.lower() in fileparse.TEXT_EXT or not fileparse.looks_binary(p):
            data = fileparse.read_text(p)
        else:  # PDF / Office / image … → extracted text (MarkItDown, OCR)
            data, parsed = fileparse.parse(p)["text"], True
        offset = max(0, int(offset or 0))
        chunk = data[offset: offset + max_chars]
        nxt = offset + len(chunk)
        out = {"path": str(p.relative_to(self.home)) if p.is_relative_to(self.home) else str(p),
               "content": chunk, "chars": len(data), "offset": offset,
               "next_offset": nxt if nxt < len(data) else None}
        if parsed:
            out["note"] = "text extracted from a binary file; use python for exact data"
        return out

    def write_file(self, path: str, content: str, append: bool = False) -> dict:
        p = self.resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a" if append else "w") as f:
            f.write(content)
        rel = os.path.relpath(p, self.home)
        bus.emit("computer", agent_id=self.agent_id, view="files", path=rel)
        return {"path": rel, "bytes": len(content.encode())}

    def list_files(self, path: str = ".", pattern: str = "**/*") -> dict:
        base = self.resolve(path)
        items = []
        for f in sorted(glob.glob(str(base / pattern), recursive=True))[:300]:
            fp = Path(f)
            if any(part.startswith(".") for part in fp.relative_to(base).parts):
                continue
            items.append({"path": os.path.relpath(fp, self.home), "dir": fp.is_dir(),
                          "size": fp.stat().st_size if fp.is_file() else None})
        return {"files": items}

    # ---------------- browser (Playwright, one Chromium per root agent) ----------------
    async def _ensure_context(self):
        """Only meaningful on the root Computer — the shared browser lives here."""
        if self._browser is not None:
            return self._browser
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        backend = sandbox.backend_name()
        if backend in ("docker", "e2b"):
            cdp = await self._sandbox_cdp_url(backend)
            if cdp:
                try:
                    browser = await self._pw.chromium.connect_over_cdp(cdp)
                    self._browser = browser.contexts[0] if browser.contexts else \
                        await browser.new_context()
                    return self._browser
                except Exception as e:
                    log.warning("sandbox %s CDP connect failed (%s), using local chromium",
                               backend, e)
        exe = settings.CHROMIUM_PATH or _find_chromium()
        profile = settings.DATA_DIR / "agents" / self.agent_id / "browser"
        extra = {"proxy": _proxy(settings.PROXY)} if settings.PROXY else {}
        # look like the Chrome it is: no "HeadlessChrome" in the user agent, no
        # navigator.webdriver, no automation flag (the first things sites check)
        self._browser = await self._pw.chromium.launch_persistent_context(
            str(profile), headless=True, executable_path=exe or None,
            viewport={"width": 1280, "height": 800}, locale="en-GB",
            user_agent=_user_agent(exe), ignore_default_args=["--enable-automation"],
            args=[f"--remote-debugging-port={self.cdp_port}",
                  "--remote-debugging-address=127.0.0.1",
                  "--disable-blink-features=AutomationControlled"], **extra)
        return self._browser

    async def _sandbox_cdp_url(self, backend: str) -> str | None:
        if backend == "docker":
            return await sandbox.DockerBackend(self.root_id()).cdp_url()
        if backend == "e2b":
            return await sandbox.E2BBackend(self.root_id()).cdp_url()
        return None

    async def _tab(self) -> Tab:
        """The tab this agent (root or helper) uses — created lazily, one per agent id."""
        root = self._root()
        t = root.tabs.get(self.agent_id)
        if t is not None and not t.page.is_closed():
            return t
        ctx = await root._ensure_context()
        if self.agent_id == root.agent_id and ctx.pages:
            page = ctx.pages[0]
        else:
            page = await ctx.new_page()
        t = Tab(self.agent_id, page)
        root.tabs[self.agent_id] = t
        return t

    def open_tabs(self) -> list[dict]:
        root = self._root()
        return [{"owner_id": oid, "url": t.title_safe()} for oid, t in root.tabs.items()
                if not t.page.is_closed()]

    async def browser(self, action: str, url: str = "", ref: str = "", selector: str = "",
                      text: str = "", key: str = "", seconds: float = 0, **_) -> dict:
        tab = await self._tab()
        page = tab.page
        self.last_used = time.time()
        self._set_activity(f"browser: {action} {url or ref or selector or text}"[:80])
        try:
            if action == "goto":
                if not re.match(r"^https?://", url):
                    url = "https://" + url
                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                await past_consent(page)
            elif action in ("click", "type", "select"):
                el = self._element(tab, ref, selector, text if action == "click" else "")
                if action == "click":
                    await el.click(timeout=10_000)
                elif action == "type":
                    await el.fill(text, timeout=10_000)
                    if key:
                        await el.press(key)
                else:
                    try:
                        await el.select_option(label=text, timeout=10_000)
                    except Exception:
                        await el.select_option(value=text, timeout=10_000)
            elif action == "press":
                await page.keyboard.press(key or "Enter")
            elif action == "scroll":
                await page.mouse.wheel(0, 700 if text != "up" else -700)
            elif action == "back":
                await page.go_back()
            elif action == "wait":
                await page.wait_for_timeout(min(max(seconds or 2, 0.5), 15) * 1000)
            elif action not in ("read", "screenshot"):
                return {"error": f"unknown action {action}"}
            if action != "goto":  # after a goto, looking for a cookie banner was the wait
                await page.wait_for_timeout(800)
        except Exception as e:
            await self._snap(tab)
            return {"error": str(e)[:500], "url": page.url,
                    "hint": "look again (read) for fresh element numbers"}
        await self._snap(tab)
        out: dict = {"url": page.url, "title": await page.title()}
        if action != "screenshot":
            body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            out["text"] = _clip(body, 6000)
            out["elements"] = await self._elements(tab)
            note = blocked_note(page.url, out["title"], body)
            if note:
                out["blocked"] = note
        return out

    def element_label(self, ref: str) -> str:
        """What element [ref] said at the last look, e.g. 'button "Place order"'."""
        t = self._root().tabs.get(self.agent_id)
        return t.labels.get(str(ref).strip("[] "), "") if t else ""

    def _element(self, tab: Tab, ref: str, selector: str, text: str):
        """What to act on: an element number from the last look, else a selector or text."""
        ref = str(ref or "").strip().lstrip("[").rstrip("]")
        if ref:
            frame = tab.refs.get(ref)
            if frame is None:
                raise ValueError(f"no element [{ref}] on this page any more")
            return frame.locator(f'[data-dot-ref="{ref.split(".")[-1]}"]').first
        if selector:
            return tab.page.locator(selector).first
        if text:
            return tab.page.get_by_text(text, exact=False).first
        raise ValueError("say which element: ref (its number), selector or text")

    async def _elements(self, tab: Tab, limit: int = 150) -> list[str]:
        """Number what can be clicked or typed into, in the page and its frames (consent
        pop-ups often live in one): "[12]" in the page, "[2.5]" in its second frame."""
        tab.refs.clear()
        tab.labels.clear()
        lines: list[str] = []
        for i, frame in enumerate(tab.page.frames):
            if len(lines) >= limit:
                break
            try:
                got = await frame.evaluate(ELEMENTS_JS, limit - len(lines))
            except Exception:  # a frame that went away or won't run scripts
                continue
            for line in got:
                n = line[1:line.index("]")]
                key = n if i == 0 else f"{i}.{n}"
                tab.refs[key] = frame
                tab.labels[key] = line[line.index("]") + 2:].split(" -> ")[0]
                lines.append(line if i == 0 else f"[{key}]" + line[line.index("]") + 1:])
        return lines

    async def _snap(self, tab: Tab) -> None:
        try:
            png = await tab.page.screenshot(type="jpeg", quality=60)
            shots = settings.DATA_DIR / "agents" / self.agent_id / "screens"
            shots.mkdir(parents=True, exist_ok=True)
            (shots / "latest.jpg").write_bytes(png)
            bus.emit("computer", agent_id=self.agent_id, view="browser", url=tab.page.url,
                     screenshot="data:image/jpeg;base64," + base64.b64encode(png).decode())
        except Exception as e:
            log.debug("screenshot failed: %s", e)

    # ---------------- live screencast (CDP Page.startScreencast) ----------------
    async def _ensure_screencast(self, tab: Tab) -> None:
        if tab.cdp is not None:
            return
        root = self._root()
        ctx = await root._ensure_context()
        tab.cdp = await ctx.new_cdp_session(tab.page)

        def _on_frame(params: dict) -> None:
            asyncio.ensure_future(self._handle_frame(tab, params))

        tab.cdp.on("Page.screencastFrame", _on_frame)
        await tab.cdp.send("Page.startScreencast", {
            "format": "jpeg", "quality": 55, "maxWidth": 1280, "maxHeight": 800,
            "everyNthFrame": 1})

    async def _handle_frame(self, tab: Tab, params: dict) -> None:
        try:
            await tab.cdp.send("Page.screencastFrameAck",
                               {"sessionId": params.get("sessionId")})
        except Exception:
            pass
        now = time.time()
        if now - tab._last_forward < 1.0 / SCREENCAST_FPS_CAP:
            return
        tab._last_forward = now
        frame = {"kind": "frame", "screenshot": "data:image/jpeg;base64," + params["data"],
                 "url": tab.page.url, "ts": now}
        for q in list(tab.viewers):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                pass

    async def _stop_screencast_if_idle(self, tab: Tab) -> None:
        await asyncio.sleep(6)
        if tab.viewers or tab.cdp is None:
            return
        try:
            await tab.cdp.send("Page.stopScreencast")
        except Exception:
            pass
        try:
            await tab.cdp.detach()
        except Exception:
            pass
        tab.cdp = None

    async def stream_subscribe(self) -> tuple[Tab, asyncio.Queue]:
        tab = await self._tab()
        await self._ensure_screencast(tab)
        q: asyncio.Queue = asyncio.Queue(maxsize=30)
        tab.viewers.add(q)
        return tab, q

    def stream_unsubscribe(self, tab: Tab, q: asyncio.Queue) -> None:
        tab.viewers.discard(q)
        if not tab.viewers:
            asyncio.ensure_future(self._stop_screencast_if_idle(tab))

    # ---------------- web (through the visible browser by default) ----------------
    async def web_fetch(self, url: str, fast: bool = False) -> dict:
        """Read a URL through the agent's *visible* tab (renders JS, shows the work);
        falls back to a fast headless httpx+trafilatura fetch for PDFs/non-HTML pages,
        navigation failures, or when a helper asks for the fast path explicitly."""
        if not fast:
            try:
                tab = await self._tab()
                self._set_activity(f"reading: {url[:70]}")
                await tab.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await past_consent(tab.page)
                await tab.page.wait_for_timeout(400)
                html = await tab.page.content()
                await self._snap(tab)
                text = _extract_readable(html, tab.page.url)
                note = blocked_note(tab.page.url, await tab.page.title(), text or "")
                if note:
                    return {"url": tab.page.url, "blocked": note}
                if text:
                    return {"url": tab.page.url, "status": 200, "content": _clip(text, 20_000)}
            except Exception as e:
                log.debug("visible web_fetch failed for %s: %s", url, e)
        return await self._web_fetch_fast(url)

    async def _web_fetch_fast(self, url: str) -> dict:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30,
                                     headers={"User-Agent": "Mozilla/5.0 OpenDot"}) as c:
            r = await c.get(url)
        ctype = r.headers.get("content-type", "")
        if "html" in ctype:
            text = _extract_readable(r.text, str(r.url)) or r.text
        elif "pdf" in ctype:
            text = _extract_pdf(r.content)
        elif "text" in ctype or "json" in ctype or "xml" in ctype:
            text = r.text
        else:
            text = f"[binary content-type={ctype}, {len(r.content)} bytes — not extracted]"
        return {"url": str(r.url), "status": r.status_code, "content": _clip(text, 20_000)}

    async def web_search(self, query: str, max_results: int = 6) -> dict:
        self._set_activity(f"searching: {query[:60]}")
        bus.emit("computer", agent_id=self.agent_id, view="browser", url=f"search: {query}")
        cache_dir = settings.DATA_DIR / "cache" / "search"
        rows, provider = await search_chain(query, max_results, cache_dir, self._browser_search)
        if not rows:
            return {"error": "search failed across every provider "
                             "(searxng/tavily/brave/ddgs backends/in-browser)"}
        return {"results": rows, "provider": provider}

    async def _browser_search(self, query: str, n: int) -> list[dict] | None:
        """Last-resort: open a results page in the agent's visible tab and scrape it."""
        tab = await self._tab()
        q = urllib.parse.quote(query)
        try:
            await tab.page.goto(f"https://www.bing.com/search?q={q}",
                                wait_until="domcontentloaded", timeout=20_000)
            await past_consent(tab.page)
            await self._snap(tab)
            rows = await tab.page.evaluate("""
                () => Array.from(document.querySelectorAll('li.b_algo')).map(li => ({
                    title: (li.querySelector('h2') || {}).innerText || '',
                    url: (li.querySelector('h2 a') || {}).href || '',
                    snippet: (li.querySelector('.b_caption p') || {}).innerText || ''
                })).filter(r => r.url)
            """)
            if rows:
                return rows[:n]
        except Exception as e:
            log.debug("in-browser Bing search failed: %s", e)
        try:
            await tab.page.goto(f"https://duckduckgo.com/html/?q={q}",
                                wait_until="domcontentloaded", timeout=20_000)
            await past_consent(tab.page)
            await self._snap(tab)
            rows = await tab.page.evaluate("""
                () => Array.from(document.querySelectorAll('.result')).map(r => ({
                    title: (r.querySelector('.result__title') || {}).innerText || '',
                    url: (r.querySelector('.result__a') || {}).href || '',
                    snippet: (r.querySelector('.result__snippet') || {}).innerText || ''
                })).filter(r => r.url)
            """)
            return rows[:n] or None
        except Exception as e:
            log.debug("in-browser DDG search failed: %s", e)
            return None

    # ---------------- lifecycle ----------------
    async def close(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        finally:
            self._browser = self._pw = None
            self.tabs.clear()
        sess = term._sessions.pop(self.agent_id, None)
        if sess:
            sess.close()


def _extract_pdf(data: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(data))
        return "\n\n".join((p.extract_text() or "") for p in r.pages[:40])
    except Exception:
        return f"[PDF, {len(data)} bytes — install pypdf to extract text]"


def _find_chromium() -> str:
    for pat in ["~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome",
                # macOS: Chromium.app, or "Google Chrome for Testing.app" in newer Playwright
                "~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/*.app/Contents/MacOS/*"]:
        hits = sorted(glob.glob(os.path.expanduser(pat)))
        if hits:
            return hits[-1]
    return shutil.which("chromium") or shutil.which("google-chrome") or ""


_computers: dict[str, Computer] = {}


def computer_for(agent_id: str) -> Computer:
    if agent_id not in _computers:
        _computers[agent_id] = Computer(agent_id)
    return _computers[agent_id]
