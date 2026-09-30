"""FastAPI app: REST + WebSocket + the web UI, all on one port (tunnel-friendly)."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import secrets
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from . import durable, ext, gatekeeper, memory
from .bus import bus
from .computer import computer_for
from .config import ROOT, settings
from .connectors import ingest_event, load_config, mcp_hub, rss_loop, save_config
from .db import db, new_id
from .llm import llm
from .runtime import (
    ensure_default_agents,
    live_runs,
    make_agent,
    on_user_message,
    resume_jobs,
    run_agent,
    spawn,
    stop_thread,
)
from .scheduler import heartbeat_enabled, next_cron, scheduler_loop

log = logging.getLogger("opendot")
WEB = ROOT / "web" / "dist"
TOKEN = settings.access_token


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    bus.bind(asyncio.get_running_loop())
    ensure_default_agents()
    cut_off = durable.recover()  # before anything runs: clear what a restart left behind
    try:  # older calendar settings become calendar apps, before the apps start
        from .ext.calendar import move_old_calendars
        move_old_calendars()
    except Exception:
        log.exception("moving old calendars into Apps failed")
    bg = [asyncio.create_task(scheduler_loop()), asyncio.create_task(rss_loop()),
          asyncio.create_task(mcp_hub.start())]
    for m in ext.MODULES:
        if hasattr(m, "start"):
            bg.append(asyncio.create_task(_guard(m.__name__, m.start)))
    bg.append(asyncio.create_task(_resume_soon(cut_off)))
    yield
    for t in bg:
        t.cancel()
    for m in ext.MODULES:
        if hasattr(m, "stop"):
            with contextlib.suppress(Exception):
                await m.stop()
    await mcp_hub.stop()


async def _resume_soon(jobs: list[dict]) -> None:
    """Pick up work a restart cut off, once tools (MCP apps, devices) had a moment."""
    if not jobs:
        return
    await asyncio.sleep(2)
    try:
        resume_jobs(jobs)
    except Exception:
        log.exception("resuming jobs failed")


async def _guard(name: str, start) -> None:
    try:
        await start()
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("extension %s crashed", name)


app = FastAPI(title="OpenDot", lifespan=lifespan)


# ---------------- auth ----------------
def _token_ok(tok: str | None) -> bool:
    return bool(tok) and hmac.compare_digest(tok, TOKEN)


def _req_token(req: Request) -> str | None:
    h = req.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:]
    return req.cookies.get("dot_token") or req.query_params.get("token")


OPEN = ("/api/health", "/api/pair", "/hook/")


@app.middleware("http")
async def auth(req: Request, call_next):
    p = req.url.path
    needs = p.startswith("/api/") or p.startswith("/pages/")
    if needs and not p.startswith(OPEN) and not _token_ok(_req_token(req)):
        return JSONResponse({"error": "unauthorized — pair this device first"}, 401)
    return await call_next(req)


def new_setup_code() -> str:
    """Single-use pairing code valid for 10 minutes (shown as QR by `dot pair`)."""
    codes = {k: v for k, v in db.kv_get("setup_codes", {}).items() if v > time.time()}
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    codes[code] = time.time() + 600
    db.kv_set("setup_codes", codes)
    return code


@app.get("/api/health")
async def health():
    return {"ok": True, "name": "OpenDot", "model": llm.model}


@app.post("/api/pair/new")
async def pair_new(req: Request, body: dict = Body(default={})):
    if not _token_ok(_req_token(req)):  # /api/pair* is open, so check here
        raise HTTPException(401)
    code = new_setup_code()
    origin = body.get("origin") or str(req.base_url).rstrip("/")
    return {"code": code, "url": f"{origin}/#/pair?code={code}"}


@app.post("/api/pair")
async def pair(body: dict = Body(...)):
    code = (body.get("code") or "").strip().upper()
    tok = body.get("token")
    codes = db.kv_get("setup_codes", {})
    if _token_ok(tok):
        pass
    elif code in codes and codes[code] > time.time():
        codes.pop(code)
        db.kv_set("setup_codes", codes)
    else:
        raise HTTPException(401, "invalid or expired code")
    r = JSONResponse({"token": TOKEN})
    r.set_cookie("dot_token", TOKEN, httponly=True, samesite="lax", max_age=3600 * 24 * 365,
                 secure=False)
    return r


# ---------------- bootstrap ----------------
def _agents() -> list[dict]:
    out = db.q("SELECT * FROM agents ORDER BY created")
    for a in out:
        a["heartbeat"] = heartbeat_enabled(a["id"])
    return out


def _plain(md: str) -> str:
    """One line of plain text from Markdown, for list previews."""
    import re
    s = re.sub(r"```.*?```", " ", md, flags=re.S)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"[*_`#>~]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _threads() -> list[dict]:
    """Chats, newest first, each with its last line (for the home list) and whether it
    ends on an agent question the human hasn't answered yet (reply chips)."""
    out = db.q("SELECT * FROM threads ORDER BY updated DESC")
    for t in out:
        m = db.one("SELECT role, agent_id, content, meta, created FROM messages WHERE "
                   "thread_id=? ORDER BY created DESC LIMIT 1", t["id"])
        if m:
            t["last"] = {"role": m["role"], "agent_id": m["agent_id"],
                         "text": _plain(m["content"] or "")[:140], "at": m["created"]}
            t["needs_you"] = m["role"] == "agent" and bool((m["meta"] or {}).get("choices"))
    return out


@app.get("/api/bootstrap")
async def bootstrap():
    return {
        "agents": _agents(),
        "threads": _threads(),
        # asks waiting on you are counted from "approvals", so their notices aren't counted twice
        "inbox_unread": db.one("SELECT count(*) n FROM inbox WHERE status='unread' "
                               "AND kind!='approval'")["n"],
        "approvals": db.q("SELECT * FROM approvals WHERE status='pending'"),
        "model": llm.model, "timezone": settings.TIMEZONE,
        "connectors": mcp_hub.status,
        "hook_token": _hook_token(),
        "live": live_runs(),
    }


# ---------------- threads & messages ----------------
@app.get("/api/threads")
async def threads():
    return _threads()


@app.post("/api/threads")
async def create_thread(body: dict = Body(...)):
    members = body.get("members") or []
    if not members:
        raise HTTPException(400, "pick at least one agent")
    kind = "group" if len(members) > 1 else "dm"
    t = db.insert("threads", id=new_id("th_"), title=body.get("title") or "New chat",
                  kind=kind, members=members, updated=time.time())
    bus.emit("thread", thread=t)
    return t


@app.delete("/api/threads/{tid}")
async def delete_thread(tid: str):
    """Dissolve a group chat: its work stops, its routines and watches end, and it's
    gone from every screen. A one-to-one chat stays (it's how you reach that agent)."""
    t = db.one("SELECT * FROM threads WHERE id=?", tid)
    if not t:
        return {"ok": True}
    if t["kind"] != "group":
        raise HTTPException(400, "only group chats can be dissolved")
    stop_thread(tid)
    with db.lock:
        db.conn.execute("UPDATE automations SET enabled=0 WHERE thread_id=?", (tid,))
        db.conn.execute("DELETE FROM messages WHERE thread_id=?", (tid,))
        db.conn.commit()
    db.delete("threads", tid)
    bus.emit("thread_gone", thread_id=tid)
    return {"ok": True}


@app.get("/api/threads/{tid}/messages")
async def messages(tid: str, limit: int = 200):
    msgs = db.q("SELECT * FROM messages WHERE thread_id=? ORDER BY created DESC LIMIT ?",
                tid, limit)[::-1]
    steps = db.q("SELECT * FROM steps WHERE thread_id=? ORDER BY created", tid)
    return {"messages": msgs, "steps": steps}


@app.post("/api/threads/{tid}/messages")
async def post_message(tid: str, body: dict = Body(...)):
    text = (body.get("text") or "").strip()
    from .ext.uploads import public
    atts = []
    for uid in body.get("attachments") or []:
        row = db.one("SELECT * FROM uploads WHERE id=?", uid)
        if row:
            atts.append(public(row))
    if not text and not atts:
        raise HTTPException(400, "empty")
    if not db.one("SELECT id FROM threads WHERE id=?", tid):
        raise HTTPException(404, "no such chat")
    return await on_user_message(tid, text, attachments=atts)


@app.post("/api/threads/{tid}/stop")
async def stop(tid: str):
    stop_thread(tid)
    return {"ok": True}


# ---------------- agents ----------------
@app.get("/api/agents")
async def agents():
    return _agents()


AGENT_FIELDS = ("name", "emoji", "color", "role", "tagline", "responsibility", "context",
                "boundary", "avatar")


@app.post("/api/agents")
async def create_agent(body: dict = Body(...)):
    """Plain create from explicit fields. (A sentence / link / QR code → /api/agents/hire.)"""
    name = (body.get("name") or "").strip()[:24]
    if not name:
        raise HTTPException(400, "name missing")
    a, t = make_agent(name=name, **{k: body[k] for k in AGENT_FIELDS
                                    if k != "name" and body.get(k)})
    return {"agent": a, "thread": t}


@app.patch("/api/agents/{aid}")
async def patch_agent(aid: str, body: dict = Body(...)):
    fields = {k: v for k, v in body.items() if k in AGENT_FIELDS}
    db.update("agents", aid, **fields)
    if "heartbeat" in body:
        db.kv_set(f"heartbeat:{aid}", bool(body["heartbeat"]))
    a = db.one("SELECT * FROM agents WHERE id=?", aid)
    if a:
        a["heartbeat"] = heartbeat_enabled(aid)
        bus.emit("agent_updated", agent=a)
    return a


@app.delete("/api/agents/{aid}")
async def delete_agent(aid: str):
    """Let an agent go: its chat, routines and watches go with it. Its files and notes
    stay on disk (data/agents/<id>) in case you want them back."""
    a = db.one("SELECT * FROM agents WHERE id=?", aid)
    if not a:
        raise HTTPException(404)
    if db.one("SELECT count(*) n FROM agents")["n"] <= 1:
        raise HTTPException(400, "keep at least one agent")
    with db.lock:
        db.conn.execute("UPDATE automations SET enabled=0 WHERE agent_id=?", (aid,))
        db.conn.execute("DELETE FROM agents WHERE id=?", (aid,))
        db.conn.commit()
    for t in db.q("SELECT * FROM threads"):
        if aid not in (t["members"] or []):
            continue
        rest = [m for m in t["members"] if m != aid]
        if t["kind"] == "dm" or not rest:
            db.delete("threads", t["id"])
        else:
            db.update("threads", t["id"], members=rest)
    bus.emit("agent_deleted", agent_id=aid)
    return {"ok": True}


@app.get("/api/agents/{aid}/memory/{name}")
async def get_memory(aid: str, name: str):
    return {"name": name, "content": memory.read(aid, name)}


@app.put("/api/agents/{aid}/memory/{name}")
async def put_memory(aid: str, name: str, body: dict = Body(...)):
    memory.write(aid, name, body.get("content", ""))
    return {"ok": True}


@app.get("/api/agents/{aid}/journal")
async def get_journal(aid: str):
    return {"content": memory.recent_journal(aid, days=7, max_chars=20000)}


@app.get("/api/agents/{aid}/policy")
async def get_policy(aid: str):
    return gatekeeper.policy_for(aid)


@app.put("/api/agents/{aid}/policy")
async def put_policy(aid: str, body: dict = Body(...)):
    for tool, decision in body.items():
        if decision in ("allow", "ask", "deny", "trusted"):
            gatekeeper.set_policy(aid, tool, decision)
    return gatekeeper.policy_for(aid)


# ---------------- agent computer ----------------
@app.get("/api/agents/{aid}/files")
async def files(aid: str, path: str = "."):
    try:
        return computer_for(aid).list_files(path, "*")
    except (PermissionError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/agents/{aid}/file")
async def file(aid: str, path: str):
    try:
        p = computer_for(aid).resolve(path)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    if not p.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(p)


@app.get("/api/agents/{aid}/screen")
async def screen(aid: str):
    p = settings.DATA_DIR / "agents" / aid / "screens" / "latest.jpg"
    if not p.exists():
        raise HTTPException(404, "no screen yet")
    return FileResponse(p, headers={"Cache-Control": "no-store"})


@app.get("/api/agents/{aid}/steps")
async def agent_steps(aid: str, limit: int = 80):
    return db.q("SELECT * FROM steps WHERE agent_id LIKE ? ORDER BY created DESC LIMIT ?",
                aid + "%", limit)


# ---------------- inbox & approvals ----------------
@app.get("/api/inbox")
async def inbox():
    return db.q("SELECT * FROM inbox ORDER BY created DESC LIMIT 200")


@app.post("/api/inbox/read-all")
async def inbox_read_all():
    with db.lock:
        db.conn.execute("UPDATE inbox SET status='read' WHERE status='unread'")
        db.conn.commit()
    return {"ok": True}


@app.post("/api/inbox/clear-read")
async def inbox_clear_read():
    with db.lock:
        n = db.conn.execute("DELETE FROM inbox WHERE status IN ('read','done') "
                            "AND kind != 'approval'").rowcount
        db.conn.commit()
    return {"ok": True, "removed": n}


# declared after the fixed paths above so they aren't captured as an item id
@app.post("/api/inbox/{iid}")
async def inbox_update(iid: str, body: dict = Body(default={})):
    db.update("inbox", iid, status=body.get("status", "read"))
    return {"ok": True}


@app.delete("/api/inbox/{iid}")
async def inbox_delete(iid: str):
    db.delete("inbox", iid)
    return {"ok": True}


@app.get("/api/approvals")
async def approvals():
    return db.q("SELECT * FROM approvals ORDER BY created DESC LIMIT 100")


@app.post("/api/approvals/{apid}")
async def approve(apid: str, body: dict = Body(...)):
    return gatekeeper.decide(apid, bool(body.get("approve")), bool(body.get("always")),
                             scope=body.get("scope"))


# ---------------- automations ----------------
@app.get("/api/automations")
async def automations():
    return db.q("SELECT * FROM automations ORDER BY created DESC")


@app.post("/api/automations")
async def create_automation(body: dict = Body(...)):
    kind = body.get("kind", "cron")
    nxt = next_cron(body["schedule"]) if kind == "cron" else None
    a = db.insert("automations", id=new_id("au_"), agent_id=body["agent_id"],
                  name=body.get("name") or "automation", kind=kind,
                  schedule=body.get("schedule", ""), event_filter=body.get("event_filter", ""),
                  prompt=body["prompt"], enabled=1, next_run=nxt,
                  thread_id=body.get("thread_id"))
    return a


@app.patch("/api/automations/{auid}")
async def patch_automation(auid: str, body: dict = Body(...)):
    fields = {k: v for k, v in body.items() if k in ("name", "schedule", "prompt", "enabled",
                                                      "event_filter")}
    a = db.one("SELECT * FROM automations WHERE id=?", auid)
    if a and a["kind"] == "cron" and ("schedule" in fields or fields.get("enabled")):
        fields["next_run"] = next_cron(fields.get("schedule") or a["schedule"])
    db.update("automations", auid, **fields)
    return db.one("SELECT * FROM automations WHERE id=?", auid)


@app.delete("/api/automations/{auid}")
async def delete_automation(auid: str):
    db.delete("automations", auid)
    return {"ok": True}


@app.post("/api/automations/{auid}/run")
async def run_automation(auid: str):
    from .connectors import _default_thread
    a = db.one("SELECT * FROM automations WHERE id=?", auid)
    if not a:
        raise HTTPException(404)
    spawn(run_agent(a["agent_id"], a["thread_id"] or _default_thread(a["agent_id"]),
                    f"automation:{a['name']}", f"Run now: {a['prompt']}"))
    return {"ok": True}


# ---------------- pages ----------------
@app.get("/api/pages")
async def pages():
    out = []
    root = settings.DATA_DIR / "pages"
    for f in sorted(root.glob("*/*.html"), key=lambda p: -p.stat().st_mtime) if root.exists() \
            else []:
        out.append({"agent_id": f.parent.name, "slug": f.stem,
                    "url": f"/pages/{f.parent.name}/{f.stem}", "updated": f.stat().st_mtime})
    return out


@app.get("/pages/{aid}/{slug}")
async def page(aid: str, slug: str):
    p = settings.DATA_DIR / "pages" / aid / f"{Path(slug).name}.html"
    if not p.exists():
        raise HTTPException(404)
    return HTMLResponse(p.read_text(), headers={
        "Content-Security-Policy": "default-src 'self' 'unsafe-inline' data: blob: "
                                   "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
                                   "img-src * data: blob:; connect-src 'self'",
    })


# ---------------- connectors & vault ----------------
@app.get("/api/connectors")
async def connectors():
    return {"config": load_config(), "status": mcp_hub.status,
            "tools": sorted(mcp_hub.tools), "hook_token": _hook_token()}


@app.put("/api/connectors")
async def put_connectors(body: dict = Body(...)):
    before = load_config().get("mcp")
    save_config(body)
    if body.get("mcp") != before:  # the hand-written apps JSON changed
        spawn(mcp_hub.start())
    return {"ok": True}


@app.get("/api/vault")
async def vault_names():
    return sorted(gatekeeper.vault_all())


@app.post("/api/vault")
async def vault_set(body: dict = Body(...)):
    gatekeeper.vault_set(body["name"], body.get("value"))
    return sorted(gatekeeper.vault_all())


def _hook_token() -> str:
    t = db.kv_get("hook_token")
    if not t:
        t = secrets.token_urlsafe(16)
        db.kv_set("hook_token", t)
    return t


@app.post("/hook/{token}/{source}")
async def hook(token: str, source: str, req: Request):
    if not hmac.compare_digest(token, _hook_token()):
        raise HTTPException(401)
    try:
        payload = await req.json()
    except Exception:
        payload = {"text": (await req.body()).decode(errors="replace")[:20000]}
    ev = await ingest_event(f"webhook:{source}" if source != "share" else "share",
                            req.headers.get("x-event-type", "post"), payload)
    return {"ok": True, "event": ev["id"]}


@app.post("/api/share")
async def share(body: dict = Body(...)):
    """iOS Share Sheet / Shortcuts: drop a link or text into Pip's chat."""
    main = db.one("SELECT id FROM threads WHERE kind='dm' ORDER BY rowid LIMIT 1")
    text = body.get("text") or ""
    if body.get("url"):
        text = f"{text}\n{body['url']}".strip()
    await ingest_event("share", "share", body)
    if main and body.get("to_chat", True):
        from .ext.lang import tr
        await on_user_message(main["id"], tr("📎 Shared with you:", "📎 分享给你：") + f"\n{text}")
    return {"ok": True}


# ---------------- live stream ----------------
@app.websocket("/ws")
async def ws(sock: WebSocket):
    tok = sock.query_params.get("token") or sock.cookies.get("dot_token")
    if not _token_ok(tok):
        await sock.close(code=4401)
        return
    await sock.accept()
    q = bus.subscribe()

    async def pump():
        while True:
            await sock.send_text(await q.get())

    task = asyncio.create_task(pump())
    try:
        while True:
            msg = await sock.receive_text()
            if msg == "ping":
                await sock.send_text('{"kind":"pong"}')
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        bus.unsubscribe(q)


# ---------------- extensions (must mount before the SPA catch-all) ----------------
@app.get("/api/extensions")
async def extensions():
    return [m.__name__ for m in ext.MODULES]


for _m in ext.load_all():
    if getattr(_m, "router", None) is not None:
        app.include_router(_m.router)


# ---------------- web UI ----------------
@app.get("/{full:path}")
async def spa(full: str):
    f = WEB / full
    if full and f.is_file() and WEB in f.resolve().parents:
        return FileResponse(f)
    idx = WEB / "index.html"
    if idx.exists():
        return FileResponse(idx, headers={"Cache-Control": "no-cache"})
    return Response("OpenDot backend is running. Build the web UI: cd web && npm i && npm run "
                    "build", media_type="text/plain")
