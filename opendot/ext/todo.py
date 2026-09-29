"""Agent Todo — everything your agents took on, what's moving and what's done.

A *task* is opened the moment an agent starts real work on something you asked
(its first tool call in a chat or handoff run) and closed when that run ends:
done, waiting on you (reply chips / an approval), stopped or failed. For multi-step
jobs the agent writes a checklist with the ``todo`` tool and ticks it off as it goes,
so progress is "4 of 6" rather than a spinner. Watches and goals show up alongside:
a watch is in progress until it fires, a goal until it's reached.
"""

from __future__ import annotations

import json
import logging
import re
import time

from fastapi import APIRouter

from ..bus import bus
from ..db import db, new_id
from ..tools import Ctx, S, fn, register_tool

log = logging.getLogger("opendot.ext.todo")
router = APIRouter(prefix="/api/todo")

db.ensure_schema("""
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, agent_id TEXT, thread_id TEXT, run_id TEXT, title TEXT,
  status TEXT DEFAULT 'doing', items TEXT DEFAULT '[]', result TEXT DEFAULT '',
  source TEXT DEFAULT 'chat', created REAL, updated REAL, finished REAL
);
CREATE INDEX IF NOT EXISTS tasks_run ON tasks(run_id);
""")

TRACKED = ("chat", "app", "handoff")  # background checks are tracked as watches/routines
STATUSES = {"doing", "waiting", "done", "stopped", "failed"}


def _row(t: dict | None) -> dict | None:
    if t and isinstance(t.get("items"), str):
        try:
            t["items"] = json.loads(t["items"] or "[]")
        except ValueError:
            t["items"] = []
    return t


def _save(tid: str, **fields) -> dict:
    if "items" in fields:
        fields["items"] = json.dumps(fields["items"], ensure_ascii=False)
    fields["updated"] = time.time()
    db.update("tasks", tid, **fields)
    t = _row(db.one("SELECT * FROM tasks WHERE id=?", tid))
    bus.emit("task", task=t)
    return t


def _title(text: str) -> str:
    t = re.sub(r"\[[^\]]*→ you\]:\s*", "", text or "")  # handoff prefix
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)  # links → their text
    t = re.sub(r"[*_`#>~|]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:80] + ("…" if len(t) > 80 else "")


def for_run(run_id: str) -> dict | None:
    return _row(db.one("SELECT * FROM tasks WHERE run_id=?", run_id))


def open_task(ctx: Ctx, title: str = "") -> dict | None:
    """The task for this run, created on first real work (helpers share the lead's)."""
    if ctx.depth:
        return None
    t = for_run(ctx.run_id)
    if t:
        return t
    source = ctx.extra.get("source", "chat")
    if source not in TRACKED:
        return None
    if not title:
        last = db.one("SELECT content FROM messages WHERE thread_id=? AND role='user' "
                      "ORDER BY created DESC LIMIT 1", ctx.thread_id)
        title = _title(ctx.extra.get("prompt") or (last or {}).get("content") or "")
    now = time.time()
    t = db.insert("tasks", id=new_id("tk_"), agent_id=ctx.agent["id"], thread_id=ctx.thread_id,
                  run_id=ctx.run_id, title=title or "…", status="doing", items="[]",
                  source=source, created=now, updated=now)
    t = _row(t)
    bus.emit("task", task=t)
    return t


def set_status(run_id: str, status: str) -> None:
    t = for_run(run_id)
    if t and t["status"] != status and t["status"] not in ("done", "failed", "stopped"):
        _save(t["id"], status=status)


def close(run_id: str, status: str, result: str = "") -> None:
    t = for_run(run_id)
    if not t:
        return
    items = t["items"]
    if status == "done":
        # finishing the run finishes whatever it was still "doing"
        items = [{**i, "status": "done"} if i.get("status") == "doing" else i for i in items]
    _save(t["id"], status=status, items=items, result=_title(result)[:160],
          finished=time.time() if status != "waiting" else None)


def answered(thread_id: str) -> None:
    """You replied in the chat: whatever was waiting on you there is settled."""
    for t in db.q("SELECT id, items FROM tasks WHERE thread_id=? AND status='waiting'",
                  thread_id):
        if not for_run_in_flight(t["id"]):
            _save(t["id"], status="done", finished=time.time())


def for_run_in_flight(task_id: str) -> bool:
    t = db.one("SELECT run_id FROM tasks WHERE id=?", task_id)
    from ..runtime import _live
    return bool(t and t["run_id"] in _live)


# ---------------------------------------------------------------- tool
async def _todo(ctx: Ctx, items: list, title: str = "") -> dict:
    t = open_task(ctx, _title(title))
    if not t:
        return {"error": "Todo lists are for work on something the human asked for."}
    clean = []
    for i in (items or [])[:20]:
        if isinstance(i, str):
            i = {"text": i}
        text = str(i.get("text") or "").strip()[:120]
        if text:
            st = i.get("status") if i.get("status") in ("todo", "doing", "done") else "todo"
            clean.append({"text": text, "status": st})
    fields = {"items": clean}
    if title:
        fields["title"] = _title(title)
    t = _save(t["id"], **fields)
    done = sum(1 for i in clean if i["status"] == "done")
    return {"ok": True, "progress": f"{done}/{len(clean)}"}


register_tool("todo", fn(
    "todo", "Your visible checklist for the job you're on (the human's Todo page shows it "
    "live). Call it at the start of any job with 3+ steps, then again whenever an item "
    "starts or finishes — always pass the whole list.",
    {"title": {**S, "description": "short name of the job, in the human's language"},
     "items": {"type": "array", "items": {"type": "object", "properties": {
         "text": S, "status": {"type": "string", "enum": ["todo", "doing", "done"]}},
         "required": ["text"]}}},
    ["items"]), _todo, policy="allow")


def PROMPT(agent: dict) -> str | None:
    return ("## Todo\nFor any job that takes 3 or more steps, start by calling `todo` with a "
            "short title and a checklist (in the human's language), and keep it current: "
            "mark an item `doing` when you start it and `done` when it's finished. The "
            "human watches their Todo page instead of reading your steps. Skip it for quick "
            "answers.")


# ---------------------------------------------------------------- API
def _progress(t: dict) -> dict:
    items = t.get("items") or []
    done = sum(1 for i in items if i.get("status") == "done")
    return {"done": done, "total": len(items)}


@router.get("")
async def board(days: int = 30):
    since = time.time() - days * 86400
    tasks = [_row(t) for t in db.q(
        "SELECT * FROM tasks WHERE status IN ('doing','waiting') OR updated > ? "
        "ORDER BY updated DESC LIMIT 400", since)]
    steps = {r["run_id"]: r for r in db.q(
        "SELECT run_id, COUNT(*) n, SUM(agent_id LIKE '%-w%') h, "
        "COUNT(DISTINCT CASE WHEN agent_id LIKE '%-w%' THEN agent_id END) helpers "
        "FROM steps WHERE run_id IN (SELECT run_id FROM tasks WHERE updated > ? OR "
        "status IN ('doing','waiting')) GROUP BY run_id", since)}
    for t in tasks:
        s = steps.get(t["run_id"]) or {}
        t["kind"] = "task"
        t["steps"] = s.get("n") or 0
        t["helpers"] = s.get("helpers") or 0
        t["progress"] = _progress(t)
    for w in db.q("SELECT * FROM automations WHERE kind='watch' AND (enabled=1 OR "
                  "COALESCE(last_run, created) > ?)", since):
        tasks.append({"id": w["id"], "kind": "watch", "agent_id": w["agent_id"],
                      "thread_id": w["thread_id"], "title": w["name"],
                      "status": "doing" if w["enabled"] else "done",
                      "looking_for": w["event_filter"], "every_min": w["every_min"],
                      "checks": w["checks"] or 0, "expires": w["expires"],
                      "result": w["outcome"] or "", "created": w["created"],
                      "updated": w["last_run"] or w["created"],
                      "finished": None if w["enabled"] else (w["last_run"] or w["created"])})
    for g in db.q("SELECT * FROM goals WHERE status='active' OR created > ?", since):
        tasks.append({"id": g["id"], "kind": "goal", "agent_id": g["agent_id"],
                      "thread_id": g["thread_id"], "title": g["title"],
                      "status": "doing" if g["status"] == "active" else "done",
                      "percent": g["progress"] or 0, "result": g["notes"] or "",
                      "created": g["created"], "updated": g["next_check"] or g["created"],
                      "finished": None})
    return {"tasks": tasks, "now": time.time()}


# ---------------------------------------------------------------- history
def backfill() -> int:
    """One-time: turn past chat runs into done tasks so the page starts with history."""
    if db.kv_get("todo:backfilled"):
        return 0
    n = 0
    for r in db.q("SELECT run_id, thread_id, MIN(created) s, MAX(COALESCE(finished, created)) "
                  "e, COUNT(*) c, MIN(agent_id) agent_id FROM steps GROUP BY run_id"):
        if not r["run_id"] or db.one("SELECT id FROM tasks WHERE run_id=?", r["run_id"]):
            continue
        reply = db.one("SELECT content, meta, created FROM messages WHERE role='agent' AND "
                       "thread_id=? AND meta LIKE ? LIMIT 1", r["thread_id"],
                       f'%"{r["run_id"]}"%')
        meta = (reply or {}).get("meta") or {}
        source = meta.get("source") if isinstance(meta, dict) else None
        if reply and source not in TRACKED:
            continue
        ask = db.one("SELECT content FROM messages WHERE thread_id=? AND role='user' AND "
                     "created <= ? ORDER BY created DESC LIMIT 1", r["thread_id"], r["s"] + 1)
        if not ask:
            continue
        aid = re.sub(r"-w\d+$", "", r["agent_id"] or "")
        db.insert("tasks", id=new_id("tk_"), agent_id=aid, thread_id=r["thread_id"],
                  run_id=r["run_id"], title=_title(ask["content"]),
                  status="done" if reply else "stopped", items="[]",
                  result=_title((reply or {}).get("content") or "")[:160],
                  source=source or "chat", created=r["s"],
                  updated=(reply or {}).get("created") or r["e"],
                  finished=(reply or {}).get("created") or r["e"])
        n += 1
    db.kv_set("todo:backfilled", True)
    return n


async def start() -> None:
    try:
        n = backfill()
        if n:
            log.info("todo: %d past jobs added", n)
    except Exception:
        log.exception("todo backfill failed")
