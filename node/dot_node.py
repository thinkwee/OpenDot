#!/usr/bin/env python3
"""OpenDot node client — lets your OpenDot agents use YOUR own Mac/PC.

Usage:
    python3 dot_node.py pair https://your-host CODE   # one-time, from a setup code
    python3 dot_node.py run [--ask]                    # keep this running (foreground or via launchd)

Single file, stdlib + one dependency (``websockets``). Optional extras, used only if
installed: ``mss`` + ``Pillow`` (screenshots on non-macOS), ``pyautogui`` (mouse/keyboard).

Config lives at ``~/.opendot-node/config.json`` (0600): host, token, a stable device_id,
device name, the folders file tools may touch (``allowed_dirs``), and whether risky
actions require a confirmation dialog on this machine (``ask``).

Capabilities offered depend on the OS and what's installed:
  shell, open, clipboard, notify, list_apps, files      — everywhere
  applescript                                            — macOS only
  screenshot                                              — macOS (screencapture) or mss+Pillow
  mouse, keyboard                                         — only if pyautogui is installed
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

try:
    import websockets
except ImportError:  # pragma: no cover
    sys.stderr.write("Missing dependency. Install it with:\n  python3 -m pip install --user "
                     "websockets\n")
    sys.exit(1)

CONFIG_DIR = Path.home() / ".opendot-node"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_ALLOWED_DIRS = ["~/Desktop", "~/Documents", "~/Downloads"]

# Local defense-in-depth — the server's Gatekeeper already asks/denies, but a node
# should never blindly trust a message just because it arrived over the wire.
DENY_PATTERNS = [
    (r"\bsudo\b|\bsu\s+-", "needs root"),
    (r"rm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME)(\s|$)", "would wipe a whole tree"),
    (r"\bmkfs\b|\bdd\s+.*of=/dev|:\(\)\s*\{", "destructive system command"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "power control"),
    (r">\s*/dev/sd", "writes directly to a disk device"),
]

log = logging.getLogger("dot_node")


# ---------------- config ----------------
def load_config() -> dict | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


# ---------------- capabilities ----------------
def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def capabilities() -> list[str]:
    caps = ["shell", "open", "clipboard", "notify", "list_apps", "files"]
    system = platform.system()
    if system == "Darwin":
        caps.append("applescript")
        if shutil.which("screencapture"):
            caps.append("screenshot")
    elif _has_module("mss") and _has_module("PIL"):
        caps.append("screenshot")
    if _has_module("pyautogui"):
        caps += ["mouse", "keyboard"]
    return caps


# ---------------- pair ----------------
def cmd_pair(args: argparse.Namespace) -> None:
    import urllib.error
    import urllib.request

    host = args.host.rstrip("/")
    body = json.dumps({"code": args.code}).encode()
    req = urllib.request.Request(f"{host}/api/pair", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"Pairing failed ({e.code}): the code may be wrong or expired. Ask "
                         "OpenDot for a new one from Apps > Devices.\n")
        sys.exit(1)
    token = data["token"]
    cfg = load_config() or {}
    cfg.update(
        host=host,
        token=token,
        device_id=cfg.get("device_id") or uuid.uuid4().hex,
        name=cfg.get("name") or args.name or socket.gethostname().split(".")[0],
        allowed_dirs=cfg.get("allowed_dirs") or DEFAULT_ALLOWED_DIRS,
        ask=cfg.get("ask", False),
    )
    save_config(cfg)
    print(f"Paired as \"{cfg['name']}\" (id {cfg['device_id'][:8]}…) with {host}.")
    print("Now run: python3 dot_node.py run")


# ---------------- run ----------------
async def cmd_run_async(args: argparse.Namespace) -> None:
    cfg = load_config()
    if not cfg or not cfg.get("token"):
        sys.stderr.write("Not paired yet. Run: python3 dot_node.py pair <host> <code>\n")
        sys.exit(1)
    if args.ask:
        cfg["ask"] = True

    ws_url = (cfg["host"].replace("https://", "wss://").replace("http://", "ws://")
             + f"/api/nodes/ws?token={cfg['token']}")
    backoff = 1
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20,
                                          max_size=24 * 1024 * 1024) as ws:
                log.info("connected to %s as %s", cfg["host"], cfg["name"])
                backoff = 1
                await ws.send(json.dumps({
                    "type": "hello", "device_id": cfg["device_id"], "name": cfg["name"],
                    "os": f"{platform.system()} {platform.release()}",
                    "capabilities": capabilities(),
                }))
                hb = asyncio.create_task(_heartbeat(ws))
                try:
                    async for raw in ws:
                        await _dispatch(ws, cfg, raw)
                finally:
                    hb.cancel()
        except (websockets.exceptions.ConnectionClosed, OSError, asyncio.TimeoutError) as e:
            log.warning("disconnected (%s) — retrying in %ss", e, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)


async def _heartbeat(ws) -> None:
    while True:
        await asyncio.sleep(20)
        try:
            await ws.send(json.dumps({"type": "ping"}))
        except Exception:
            return


async def _dispatch(ws, cfg: dict, raw: str) -> None:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    if msg.get("type") != "request":
        return  # pong or anything else — nothing to do
    req_id, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    try:
        result = await asyncio.to_thread(_handle, cfg, method, params)
        await ws.send(json.dumps({"type": "response", "id": req_id, "ok": True,
                                  "result": result}))
    except Exception as e:  # noqa: BLE001 — always answer the server
        log.info("handler %s failed: %s", method, e)
        await ws.send(json.dumps({"type": "response", "id": req_id, "ok": False,
                                  "error": str(e)}))


def _handle(cfg: dict, method: str, params: dict):
    handler = HANDLERS.get(method)
    if not handler:
        raise PermissionError(f"unsupported action: {method}")
    if cfg.get("ask") and method in ("shell", "applescript", "write_file"):
        if not _ask_confirm(method, params):
            raise PermissionError("denied on the device")
    return handler(cfg, params)


def _ask_confirm(method: str, params: dict) -> bool:
    summary = str(params.get("command") or params.get("script") or params.get("path")
                 or method)[:200]
    if platform.system() == "Darwin":
        script = (f'display dialog {json.dumps("OpenDot wants to: " + summary)} '
                  f'with title "OpenDot" buttons {{"Deny", "Allow"}} default button "Allow" '
                  f'cancel button "Deny"')
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True,
                              timeout=120)
        return proc.returncode == 0
    if shutil.which("zenity"):
        proc = subprocess.run(["zenity", "--question", "--title=OpenDot",
                               "--text", f"OpenDot wants to: {summary}"], timeout=120)
        return proc.returncode == 0
    log.warning("--ask is on but no dialog tool is available here; allowing %s", method)
    return True


# ---------------- action handlers ----------------
def h_shell(cfg: dict, params: dict) -> dict:
    command = params.get("command", "")
    timeout = min(int(params.get("timeout") or 60), 300)
    for pat, why in DENY_PATTERNS:
        if re.search(pat, command):
            raise PermissionError(f"refused locally: {why}")
    shell = "zsh" if shutil.which("zsh") else "bash"
    proc = subprocess.run([shell, "-lc", command], cwd=str(Path.home()), capture_output=True,
                          timeout=timeout, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    return {"exit_code": proc.returncode, "output": out[:12000]}


def h_applescript(cfg: dict, params: dict) -> dict:
    if platform.system() != "Darwin":
        raise PermissionError("applescript is macOS-only")
    proc = subprocess.run(["osascript", "-e", params.get("script", "")], capture_output=True,
                          text=True, timeout=30)
    return {"exit_code": proc.returncode, "output": (proc.stdout or "") + (proc.stderr or "")}


def h_open(cfg: dict, params: dict) -> dict:
    target = params.get("target", "")
    if not target:
        raise ValueError("target required")
    cmd = ["open", target] if platform.system() == "Darwin" else ["xdg-open", target]
    subprocess.run(cmd, timeout=15, check=False)
    return {"ok": True}


def h_screenshot(cfg: dict, params: dict) -> dict:
    if platform.system() == "Darwin" and shutil.which("screencapture"):
        tmp = CONFIG_DIR / f"shot-{os.getpid()}.jpg"
        subprocess.run(["screencapture", "-x", "-t", "jpg", str(tmp)], timeout=15, check=True)
        data = tmp.read_bytes()
        tmp.unlink(missing_ok=True)
        return {"image": base64.b64encode(data).decode()}
    if _has_module("mss") and _has_module("PIL"):
        import io

        import mss
        from PIL import Image
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0])
        img = Image.frombytes("RGB", shot.size, shot.rgb)
        img.thumbnail((1280, 800))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=60)
        return {"image": base64.b64encode(buf.getvalue()).decode()}
    raise PermissionError("no screenshot capability here (headless, or install mss + pillow)")


def h_clipboard(cfg: dict, params: dict) -> dict:
    action = params.get("action", "get")
    system = platform.system()
    if action == "set":
        text = params.get("text", "")
        if system == "Darwin":
            subprocess.run(["pbcopy"], input=text, text=True, timeout=10)
        elif shutil.which("xclip"):
            subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True,
                           timeout=10)
        elif shutil.which("xsel"):
            subprocess.run(["xsel", "--clipboard", "--input"], input=text, text=True, timeout=10)
        else:
            raise PermissionError("no clipboard tool here (install xclip)")
        return {"ok": True}
    if system == "Darwin":
        out = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=10).stdout
    elif shutil.which("xclip"):
        out = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True,
                             text=True, timeout=10).stdout
    elif shutil.which("xsel"):
        out = subprocess.run(["xsel", "--clipboard", "--output"], capture_output=True,
                             text=True, timeout=10).stdout
    else:
        raise PermissionError("no clipboard tool here (install xclip)")
    return {"text": out}


def h_notify(cfg: dict, params: dict) -> dict:
    title, body = params.get("title", "OpenDot"), params.get("body", "")
    if platform.system() == "Darwin":
        script = f'display notification {json.dumps(body)} with title {json.dumps(title)}'
        subprocess.run(["osascript", "-e", script], timeout=10, check=False)
    elif shutil.which("notify-send"):
        subprocess.run(["notify-send", title, body], timeout=10, check=False)
    else:
        log.info("notify: %s — %s", title, body)
    return {"ok": True}


def h_list_apps(cfg: dict, params: dict) -> dict:
    if platform.system() == "Darwin":
        script = ('tell application "System Events" to get name of every process whose '
                  'background only is false')
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True,
                              timeout=15)
        apps = [a.strip() for a in (proc.stdout or "").split(",") if a.strip()]
        return {"apps": apps}
    proc = subprocess.run(["ps", "-eo", "comm"], capture_output=True, text=True, timeout=15)
    apps = sorted(set(l.strip() for l in proc.stdout.splitlines()[1:] if l.strip()))
    return {"apps": apps[:200]}


def _allowed_dirs(cfg: dict) -> list[Path]:
    return [Path(os.path.expanduser(d)).resolve()
           for d in (cfg.get("allowed_dirs") or DEFAULT_ALLOWED_DIRS)]


def _resolve_in_allowed(cfg: dict, path: str) -> Path:
    p = Path(os.path.expanduser(path or ".")).resolve()
    for d in _allowed_dirs(cfg):
        if p == d or p in d.parents or str(p).startswith(str(d) + os.sep):
            return p
    dirs = ", ".join(str(d) for d in _allowed_dirs(cfg))
    raise PermissionError(f"{path!r} is outside the approved folders ({dirs})")


def h_read_file(cfg: dict, params: dict) -> dict:
    p = _resolve_in_allowed(cfg, params.get("path", ""))
    if not p.is_file():
        raise FileNotFoundError(str(p))
    data = p.read_text(errors="replace")
    return {"path": str(p), "content": data[:40000], "size": len(data)}


def h_write_file(cfg: dict, params: dict) -> dict:
    p = _resolve_in_allowed(cfg, params.get("path", ""))
    p.parent.mkdir(parents=True, exist_ok=True)
    content = params.get("content", "")
    with open(p, "a" if params.get("append") else "w") as f:
        f.write(content)
    return {"path": str(p), "bytes": len(content.encode())}


def h_list_dir(cfg: dict, params: dict) -> dict:
    dirs = _allowed_dirs(cfg)
    p = _resolve_in_allowed(cfg, params.get("path") or str(dirs[0]))
    items = []
    if p.is_dir():
        for f in sorted(p.iterdir())[:300]:
            items.append({"path": str(f), "dir": f.is_dir(),
                         "size": f.stat().st_size if f.is_file() else None})
    return {"files": items}


def h_mouse(cfg: dict, params: dict) -> dict:
    if not _has_module("pyautogui"):
        raise PermissionError("pyautogui is not installed on this device")
    import pyautogui
    action = params.get("action", "move")
    if action == "move":
        pyautogui.moveTo(params.get("x", 0), params.get("y", 0), duration=0.2)
    elif action == "click":
        pyautogui.click(params.get("x"), params.get("y"), button=params.get("button", "left"))
    else:
        raise ValueError(f"unknown mouse action {action}")
    return {"ok": True}


def h_keyboard(cfg: dict, params: dict) -> dict:
    if not _has_module("pyautogui"):
        raise PermissionError("pyautogui is not installed on this device")
    import pyautogui
    if params.get("text"):
        pyautogui.typewrite(params["text"], interval=0.02)
    if params.get("keys"):
        pyautogui.hotkey(*params["keys"])
    return {"ok": True}


HANDLERS = {
    "shell": h_shell, "applescript": h_applescript, "open": h_open, "screenshot": h_screenshot,
    "clipboard": h_clipboard, "notify": h_notify, "list_apps": h_list_apps,
    "read_file": h_read_file, "write_file": h_write_file, "list_dir": h_list_dir,
    "mouse": h_mouse, "keyboard": h_keyboard,
}


# ---------------- CLI ----------------
def main() -> None:
    ap = argparse.ArgumentParser(prog="dot_node.py", description="OpenDot node client")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_pair = sub.add_parser("pair", help="pair this machine with an OpenDot server")
    p_pair.add_argument("host", help="e.g. https://your-tunnel.example.com")
    p_pair.add_argument("code", help="the setup code shown in Apps > Devices")
    p_pair.add_argument("--name", default="", help="a name for this device (default: hostname)")

    p_run = sub.add_parser("run", help="connect and serve requests (keep this running)")
    p_run.add_argument("--ask", action="store_true",
                       help="show a confirm dialog on this machine before risky actions")

    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd == "pair":
        cmd_pair(args)
    elif args.cmd == "run":
        try:
            asyncio.run(cmd_run_async(args))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
