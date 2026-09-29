"""A real, persistent PTY shell session per agent (ptyprocess).

Unlike the old one-shot ``subprocess.exec`` per command, this spawns a single
``bash`` under a real pseudo-terminal that stays alive for the agent's lifetime:
``cd`` and exported env vars persist across tool calls, exactly like a human's
terminal. The shell runs with ``stty -echo`` and an empty prompt; for each
command we draw a colored ``name@dot:~/dir$ cmd`` prompt ourselves and detect
completion with an invisible OSC 133 escape (``ESC ] 133;D;<exit>;<tag>;<cwd> BEL``,
the shell-integration sequence terminals already ignore), so the human sees a
clean terminal and no bookkeeping lines. A timeout sends Ctrl-C to the *foreground job* — the shell session
itself is never killed.

Raw bytes (with ANSI) are broadcast to any subscriber (the Computer panel's
xterm.js view over the dedicated WebSocket); the text handed back to the LLM
tool call is ANSI-stripped and clipped.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
import uuid
from collections import deque

log = logging.getLogger("opendot.computer.term")

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\].*?(\x07|\x1b\\)|\x1b[()][A-Z0-9]|\r"
                    r"|[\x00-\x08\x0b-\x1f\x7f]")
MAX_BUF = 200_000  # raw chars kept for marker-scanning / replay


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


class PtySession:
    def __init__(self, agent_id: str, home, env: dict) -> None:
        self.agent_id = agent_id
        self.home = home
        self.env = env
        self.proc = None
        self._buf = deque(maxlen=MAX_BUF)
        self._raw_all = ""  # scanning window since last command
        self._listeners: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()
        self._thread: threading.Thread | None = None
        self._alive = False
        self.cwd = str(home)
        self._ready = False

    # ---------------- lifecycle ----------------
    def start(self, argv: list[str] | None = None) -> None:
        import ptyprocess
        argv = argv or ["bash", "--noprofile", "--norc", "-i"]
        self.proc = ptyprocess.PtyProcess.spawn(
            argv, cwd=str(self.home), env=self.env, dimensions=(40, 120))
        self._alive = True
        self._loop = asyncio.get_event_loop()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        # no echo, no prompt: run() draws the prompt itself (see module docstring)
        # output is hidden until the "ready" OSC arrives, so the setup line never shows
        self._ready = False
        self.proc.write(b"stty -echo -echoctl 2>/dev/null; export PS1='' PS2='' "
                        b"PROMPT_COMMAND='' TERM=xterm-256color; set +H; "
                        b"printf '\\033]133;R;ready\\007'\n")

    def _read_loop(self) -> None:
        while self._alive:
            try:
                data = self.proc.read(4096)
            except EOFError:
                self._alive = False
                break
            except Exception:
                self._alive = False
                break
            text = data.decode(errors="replace")
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._on_data, text)

    def _on_data(self, text: str) -> None:
        self._raw_all = (self._raw_all + text)[-MAX_BUF:]
        if not self._ready:
            if "\x1b]133;R;ready\x07" in self._raw_all:
                self._ready = True
                self._raw_all = ""
            return
        self._buf.append(text)
        from ..bus import bus
        bus.emit("computer", agent_id=self.agent_id, view="terminal", raw=text)
        for q in list(self._listeners):
            try:
                q.put_nowait(text)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)

    def write_raw(self, data: str) -> None:
        """Owner typed into the terminal directly ('take over' mode)."""
        if self.proc:
            self.proc.write(data.encode())

    def resize(self, rows: int, cols: int) -> None:
        if self.proc:
            try:
                self.proc.setwinsize(rows, cols)
            except Exception:
                pass

    # ---------------- run a command, wait for it to finish ----------------
    def _prompt(self) -> str:
        from ..db import db
        base, _, worker = self.agent_id.partition("-w")
        row = db.one("SELECT name FROM agents WHERE id=?", base)
        name = (row["name"] if row else "agent").lower() + (f"-{worker}" if worker else "")
        home = str(self.home)
        cwd = "~" + self.cwd[len(home):] if self.cwd.startswith(home) else self.cwd
        return f"\x1b[1;38;5;114m{name}@dot\x1b[0m:\x1b[1;38;5;111m{cwd}\x1b[0m$ "

    async def run(self, command: str, timeout: float = 120) -> dict:
        if not self._alive or self.proc is None:
            self.start()
        for _ in range(100):  # wait for the setup line to finish (≤5s)
            if self._ready:
                break
            await asyncio.sleep(0.05)
        async with self._lock:
            tag = uuid.uuid4().hex[:8]
            done = re.compile(r"\x1b\]133;D;(-?\d+);" + tag + r";([^\x07]*)\x07")
            # what the human sees: a colored prompt + the command, like a real terminal
            shown = command.rstrip("\n").replace("\n", "\r\n\x1b[2m> \x1b[0m")
            self._on_data(self._prompt() + "\x1b[1m" + shown + "\x1b[0m\r\n")
            self._raw_all = ""  # scan only this command's output
            t0 = time.time()
            # group the command so multi-line input finishes as one unit, then report
            # exit code + cwd invisibly
            self.proc.write((f"{{ {command.rstrip()}\n}}\n"
                             f"printf '\\033]133;D;%s;{tag};%s\\007' \"$?\" \"$PWD\"\n").encode())
            code = None
            timed_out = False
            m = None
            while True:
                m = done.search(self._raw_all)
                if m:
                    code = int(m.group(1))
                    self.cwd = m.group(2) or self.cwd
                    break
                if time.time() - t0 > timeout:
                    timed_out = True
                    try:
                        self.proc.write(b"\x03")  # Ctrl-C: kill the foreground job only
                    except Exception:
                        pass
                    await asyncio.sleep(0.3)
                    break
                await asyncio.sleep(0.08)
            raw = self._raw_all[:m.start()] if m else self._raw_all
            text = strip_ansi(raw).strip()
            if timed_out:
                text += "\n[timed out — sent Ctrl-C to the foreground job; session stays open]"
                code = code if code is not None else -1
            return {"exit_code": code, "output": text, "seconds": round(time.time() - t0, 1)}

    def close(self) -> None:
        self._alive = False
        try:
            if self.proc:
                self.proc.terminate(force=True)
        except Exception:
            pass


_sessions: dict[str, PtySession] = {}


def session_for(agent_id: str, home, env: dict) -> PtySession:
    s = _sessions.get(agent_id)
    if s is None or not s._alive:
        s = PtySession(agent_id, home, env)
        _sessions[agent_id] = s
    return s
