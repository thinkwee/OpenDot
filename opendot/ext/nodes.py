"""Devices — lets agents use the human's own Mac/PC through a small paired
"node" client (see ``node/dot_node.py``).

Protocol: the node opens ``wss://host/api/nodes/ws?token=<access_token>`` (the
same token issued by ``/api/pair`` — this is a single-user system, so pairing
a node grants it the same access the phone/web UI already has) and sends a
``hello`` with a stable ``device_id`` it generated on first pair. The server
then sends it ``{"type":"request", "id", "method", "params"}`` and waits for a
matching ``{"type":"response", "id", "ok", "result"|"error"}``.

Tools here (``device_shell`` etc.) are thin wrappers that look the device up
by name, forward the request over its WebSocket, and await the reply.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from ..bus import bus
from ..config import ROOT, settings
from ..db import db, new_id
from ..tools import Ctx, fn, register_tool

log = logging.getLogger("opendot.ext.nodes")

db.ensure_schema("""
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY, name TEXT, os TEXT, capabilities TEXT DEFAULT '[]',
  approved INTEGER DEFAULT 1, last_seen REAL, created REAL
);
""")

router = APIRouter()

# device_id -> live WebSocket; req_id -> Future awaiting a "response" message
_conns: dict[str, WebSocket] = {}
_waiters: dict[str, asyncio.Future] = {}


def _token_ok(tok: str | None) -> bool:
    return bool(tok) and hmac.compare_digest(tok, settings.access_token)


def _row(d: dict) -> dict:
    d = dict(d)
    try:
        d["capabilities"] = json.loads(d.get("capabilities") or "[]")
    except (TypeError, ValueError):
        d["capabilities"] = []
    d["online"] = d["id"] in _conns
    d["approved"] = bool(d.get("approved"))
    return d


def _find(device: str) -> dict | None:
    row = db.one("SELECT * FROM devices WHERE id=? OR lower(name)=lower(?)", device, device)
    return _row(row) if row else None


# ---------------- REST ----------------
@router.get("/api/nodes")
async def list_devices():
    return [_row(r) for r in db.q("SELECT * FROM devices ORDER BY created")]


@router.patch("/api/nodes/{did}")
async def patch_device(did: str, body: dict = Body(...)):
    if not db.one("SELECT id FROM devices WHERE id=?", did):
        raise HTTPException(404, "no such device")
    fields = {}
    if "name" in body:
        fields["name"] = str(body["name"])[:40] or "Device"
    if "approved" in body:
        fields["approved"] = 1 if body["approved"] else 0
    if fields:
        db.update("devices", did, **fields)
    row = _row(db.one("SELECT * FROM devices WHERE id=?", did))
    bus.emit("device", device=row)
    return row


@router.delete("/api/nodes/{did}")
async def delete_device(did: str):
    db.delete("devices", did)
    ws = _conns.pop(did, None)
    if ws is not None:
        try:
            await ws.close()
        except Exception:
            pass
    bus.emit("device", device={"id": did, "removed": True})
    return {"ok": True}


@router.get("/dl/dot_node.py")
async def download_client():
    """Public, unauthenticated: the script carries no secrets by itself — it
    still needs a live setup code (single-use, 10 min) to actually pair."""
    p = ROOT / "node" / "dot_node.py"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="text/x-python", filename="dot_node.py")


# ---------------- WebSocket ----------------
@router.websocket("/api/nodes/ws")
async def nodes_ws(sock: WebSocket) -> None:
    # WebSocket routes are NOT covered by the HTTP auth middleware — check manually.
    tok = sock.query_params.get("token")
    if not _token_ok(tok):
        await sock.close(code=4401)
        return
    await sock.accept()
    device_id: str | None = None
    try:
        raw = await asyncio.wait_for(sock.receive_text(), timeout=15)
        hello = json.loads(raw)
        if hello.get("type") != "hello" or not hello.get("device_id"):
            await sock.close(code=4400)
            return
        device_id = str(hello["device_id"])[:64]
        existing = db.one("SELECT * FROM devices WHERE id=?", device_id)
        row = db.insert(
            "devices", id=device_id,
            name=(hello.get("name") or "Device")[:40],
            os=(hello.get("os") or "unknown")[:80],
            capabilities=json.dumps(hello.get("capabilities") or []),
            approved=existing["approved"] if existing else 1,
            last_seen=time.time(),
            created=existing["created"] if existing else time.time(),
        )
        _conns[device_id] = sock
        bus.emit("device", device=_row(row))
        log.info("device connected: %s (%s)", row["name"], device_id)

        while True:
            raw = await sock.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype == "ping":
                await sock.send_text(json.dumps({"type": "pong"}))
                db.update("devices", device_id, last_seen=time.time())
            elif mtype == "response":
                fut = _waiters.get(msg.get("id"))
                if fut and not fut.done():
                    fut.set_result(msg)
    except (WebSocketDisconnect, asyncio.TimeoutError, json.JSONDecodeError, KeyError) as e:
        log.info("node websocket closed: %s", e)
    finally:
        if device_id:
            _conns.pop(device_id, None)
            d = db.one("SELECT * FROM devices WHERE id=?", device_id)
            if d:
                db.update("devices", device_id, last_seen=time.time())
                bus.emit("device", device=_row(d))
            log.info("device disconnected: %s", device_id)


# ---------------- request/response bridge ----------------
async def _call(device: str, method: str, params: dict, timeout: float = 60) -> dict:
    row = _find(device)
    if not row:
        return {"error": f"no device called {device!r}. Use device_list to see paired devices."}
    if not row["approved"]:
        return {"error": f"the human has disabled {row['name']} for agent use."}
    ws = _conns.get(row["id"])
    if not ws:
        return {"error": f"{row['name']} is offline right now."}
    req_id = new_id("rq_")
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _waiters[req_id] = fut
    try:
        await ws.send_text(json.dumps({"type": "request", "id": req_id, "method": method,
                                       "params": params}))
        msg = await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        return {"error": f"{row['name']} didn't respond in time."}
    except Exception as e:
        return {"error": f"could not reach {row['name']}: {e}"}
    finally:
        _waiters.pop(req_id, None)
    if not msg.get("ok"):
        return {"error": msg.get("error") or "the device refused"}
    return msg.get("result") or {}


# ---------------- tools ----------------
S = {"type": "string"}
I = {"type": "integer"}  # noqa: E741
B = {"type": "boolean"}


async def _device_list(ctx: Ctx) -> dict:
    rows = [_row(r) for r in db.q("SELECT * FROM devices ORDER BY created") if not _is_phone(r)]
    return {"note": "The iPhone (if any) is used via the iphone_* tools.", "devices": [{"name": r["name"], "os": r["os"], "online": r["online"],
                         "approved": r["approved"], "capabilities": r["capabilities"]}
                        for r in rows]}


async def _device_shell(ctx: Ctx, device: str, command: str, timeout: int = 60) -> dict:
    timeout = min(int(timeout or 60), 300)
    return await _call(device, "shell", {"command": command, "timeout": timeout},
                       timeout=timeout + 15)


async def _device_applescript(ctx: Ctx, device: str, script: str) -> dict:
    return await _call(device, "applescript", {"script": script}, timeout=45)


async def _device_open(ctx: Ctx, device: str, target: str) -> dict:
    return await _call(device, "open", {"target": target}, timeout=20)


async def _device_screenshot(ctx: Ctx, device: str) -> dict:
    res = await _call(device, "screenshot", {}, timeout=30)
    if res.get("error"):
        return res
    img = res.get("image")
    if not img:
        return {"error": "device returned no image"}
    bus.emit("computer", agent_id=ctx.agent["id"], view="browser", url=f"device://{device}",
             screenshot="data:image/jpeg;base64," + img)
    return {"ok": True, "note": "Screenshot taken — shown to the human in their Computer panel."}


async def _device_clipboard(ctx: Ctx, device: str, action: str = "get", text: str = "") -> dict:
    if action not in ("get", "set"):
        return {"error": "action must be 'get' or 'set'"}
    return await _call(device, "clipboard", {"action": action, "text": text}, timeout=15)


async def _device_notify(ctx: Ctx, device: str, title: str, body: str = "") -> dict:
    return await _call(device, "notify", {"title": title, "body": body}, timeout=15)


async def _device_files(ctx: Ctx, device: str, action: str, path: str, content: str = "",
                        append: bool = False) -> dict:
    method = {"read": "read_file", "list": "list_dir", "write": "write_file"}.get(action)
    if not method:
        return {"error": "action must be 'read', 'list', or 'write'"}
    params: dict = {"path": path}
    if action == "write":
        params["content"] = content
        params["append"] = append
    return await _call(device, method, params, timeout=30)


def _has_computer() -> bool:
    """A paired Mac/PC (phones have their own iphone_* tools)."""
    return any(not _is_phone(r) for r in db.q("SELECT os FROM devices WHERE approved=1"))


register_tool(
    "device_list", fn("device_list", "List the human's paired devices (their own Mac/PC) and "
                      "whether each is online right now.", {}), _device_list, policy="allow", shown_when=_has_computer)
register_tool(
    "device_shell", fn("device_shell", "Run a shell command on the human's OWN computer (not "
                       "your sandbox) — e.g. to use an app only installed there. Ask first.",
                       {"device": S, "command": S, "timeout": I}, ["device", "command"]),
    _device_shell, policy="ask", shown_when=_has_computer)
register_tool(
    "device_applescript", fn("device_applescript", "Run an AppleScript on the human's Mac "
                             "(control apps, dialogs, System Events). macOS devices only.",
                             {"device": S, "script": S}, ["device", "script"]),
    _device_applescript, policy="ask", shown_when=_has_computer)
register_tool(
    "device_open", fn("device_open", "Open an app, URL, or file on the human's device.",
                      {"device": S, "target": S}, ["device", "target"]),
    _device_open, policy="ask", shown_when=_has_computer)
register_tool(
    "device_screenshot", fn("device_screenshot", "Take a screenshot of the human's device "
                            "screen. Shown to them live — use sparingly, it's their screen.",
                            {"device": S}, ["device"]),
    _device_screenshot, policy="ask", shown_when=_has_computer)
register_tool(
    "device_clipboard", fn("device_clipboard", "Read or write the human's device clipboard.",
                           {"device": S, "action": {"type": "string", "enum": ["get", "set"]},
                            "text": S}, ["device", "action"]),
    _device_clipboard, policy="ask", shown_when=_has_computer)
register_tool(
    "device_notify", fn("device_notify", "Pop a native notification on the human's device.",
                        {"device": S, "title": S, "body": S}, ["device", "title"]),
    _device_notify, policy="allow", shown_when=_has_computer)
register_tool(
    "device_files", fn("device_files", "Read, list, or write files in the folders the human "
                       "approved on their device (default: Desktop, Documents, Downloads).",
                       {"device": S, "action": {"type": "string",
                                                "enum": ["read", "list", "write"]},
                        "path": S, "content": S, "append": B}, ["device", "action", "path"]),
    _device_files, policy="ask", shown_when=_has_computer)


def _is_phone(r: dict) -> bool:
    return str(r.get("os") or "").lower().startswith(("ios", "ipados"))


def PROMPT(agent: dict) -> str | None:
    # phones are handled by the iphone_* tools (ext/iphone.py), not device_shell & co
    rows = [r for r in db.q("SELECT * FROM devices WHERE approved=1") if not _is_phone(r)]
    if not rows:
        return None
    online = [_row(r) for r in rows if r["id"] in _conns]
    if not online:
        return ("The human has paired a device (their own Mac/PC) but it's offline right now. "
                "Call device_list if you need to check again later.")
    lines = [f"- {r['name']} ({r['os']}): {', '.join(r['capabilities']) or 'basic'}"
            for r in online]
    return ("The human paired their own computer as a device you can use, with their approval "
            "for each risky action:\n" + "\n".join(lines) +
            "\nUse device_shell / device_open / device_applescript / device_screenshot / "
            "device_clipboard / device_notify / device_files. Prefer your own computer for "
            "code and files; use a device when you need the human's own apps, screen, or "
            "clipboard.")
