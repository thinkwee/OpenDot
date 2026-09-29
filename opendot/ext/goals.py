"""Goals engine — durable objectives an agent keeps advancing on its own, a step at
a time, instead of only reacting to chat and cron. Complements automations (which
run a fixed prompt on a schedule): a goal is an open-ended thing the agent owns and
reports progress on.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Body, HTTPException

from ..bus import bus
from ..db import db, new_id
from ..tools import Ctx, fn, register_tool

log = logging.getLogger("opendot.ext.goals")

db.ensure_schema("""
CREATE TABLE IF NOT EXISTS goals (
  id TEXT PRIMARY KEY, agent_id TEXT, title TEXT, why TEXT DEFAULT '',
  status TEXT DEFAULT 'active', progress INTEGER DEFAULT 0, next_check REAL,
  cadence INTEGER DEFAULT 1440, notes TEXT DEFAULT '', thread_id TEXT, created REAL
);
""")

router = APIRouter(prefix="/api/goals")

S = {"type": "string"}
I = {"type": "integer"}  # noqa: E741


# ---------------- REST ----------------
@router.get("")
async def list_all(agent_id: str | None = None):
    if agent_id:
        return db.q("SELECT * FROM goals WHERE agent_id=? ORDER BY created DESC", agent_id)
    return db.q("SELECT * FROM goals ORDER BY created DESC")


@router.post("")
async def create(body: dict = Body(...)):
    if not body.get("agent_id") or not (body.get("title") or "").strip():
        raise HTTPException(400, "agent_id and title required")
    cadence = int(body.get("cadence") or 1440)
    g = db.insert("goals", id=new_id("gl_"), agent_id=body["agent_id"],
                 title=body["title"].strip(), why=body.get("why", ""), status="active",
                 progress=int(body.get("progress") or 0), cadence=cadence,
                 next_check=time.time() + cadence * 60, notes=body.get("notes", ""),
                 thread_id=body.get("thread_id"))
    bus.emit("goal", goal=g)
    return g


@router.patch("/{gid}")
async def patch(gid: str, body: dict = Body(...)):
    if not db.one("SELECT id FROM goals WHERE id=?", gid):
        raise HTTPException(404)
    fields = {k: v for k, v in body.items() if k in ("title", "why", "status", "progress",
                                                     "cadence", "notes")}
    if "progress" in fields:
        fields["progress"] = max(0, min(100, int(fields["progress"])))
    if "cadence" in fields:
        fields["next_check"] = time.time() + int(fields["cadence"]) * 60
    db.update("goals", gid, **fields)
    g = db.one("SELECT * FROM goals WHERE id=?", gid)
    bus.emit("goal", goal=g)
    return g


@router.delete("/{gid}")
async def delete(gid: str):
    db.delete("goals", gid)
    return {"ok": True}


# ---------------- tools ----------------
async def _set_goal(ctx: Ctx, title: str, why: str = "", cadence: int = 1440,
                    progress: int = 0) -> dict:
    cadence = max(15, int(cadence or 1440))
    g = db.insert("goals", id=new_id("gl_"), agent_id=ctx.agent["id"], title=title.strip(),
                 why=why, status="active", progress=max(0, min(100, int(progress))),
                 cadence=cadence, next_check=time.time() + cadence * 60, notes="",
                 thread_id=ctx.thread_id)
    bus.emit("goal", goal=g)
    return {"ok": True, "id": g["id"]}


async def _update_goal(ctx: Ctx, id: str, progress: int | None = None, status: str | None = None,
                       notes: str | None = None, cadence: int | None = None) -> dict:
    g = db.one("SELECT * FROM goals WHERE id=? AND agent_id=?", id, ctx.agent["id"])
    if not g:
        return {"error": "no such goal of yours"}
    fields: dict = {}
    if progress is not None:
        fields["progress"] = max(0, min(100, int(progress)))
    if status in ("active", "done", "paused"):
        fields["status"] = status
    if notes is not None:
        fields["notes"] = notes[:2000]
    fields["next_check"] = time.time() + int(cadence or g["cadence"]) * 60
    if cadence is not None:
        fields["cadence"] = int(cadence)
    db.update("goals", id, **fields)
    g = db.one("SELECT * FROM goals WHERE id=?", id)
    bus.emit("goal", goal=g)
    return {"ok": True, "goal": g}


async def _list_goals(ctx: Ctx) -> dict:
    return {"goals": db.q("SELECT * FROM goals WHERE agent_id=? ORDER BY created DESC",
                          ctx.agent["id"])}


register_tool(
    "set_goal", fn("set_goal", "Start tracking a durable goal you'll keep advancing yourself "
                   "over time (not a one-off task) — e.g. 'learn the human's taste in music', "
                   "'get the backlog to zero'. cadence = minutes between your own check-ins "
                   "(default one day).", {"title": S, "why": S, "cadence": I, "progress": I},
                   ["title"]),
    _set_goal, policy="allow", worker=False)
register_tool(
    "update_goal", fn("update_goal", "Update one of your goals: new progress (0-100), status "
                      "(active/done/paused), or notes — call this after you make progress on "
                      "it, including during a 'goal check-in' wake-up.",
                      {"id": S, "progress": I, "status": {"type": "string",
                       "enum": ["active", "done", "paused"]}, "notes": S, "cadence": I}, ["id"]),
    _update_goal, policy="allow", worker=False)
register_tool("list_goals", fn("list_goals", "List your own goals and their progress.", {}),
             _list_goals, policy="allow", worker=False)


def PROMPT(agent: dict) -> str | None:
    rows = db.q("SELECT title, progress FROM goals WHERE agent_id=? AND status='active' "
               "ORDER BY created DESC LIMIT 6", agent["id"])
    if not rows:
        return None
    lines = "\n".join(f"- {r['title']} ({r['progress']}%)" for r in rows)
    return ("# Your active goals\nNudge these forward when relevant, and call `update_goal` "
           f"whenever you make progress:\n{lines}")


async def start() -> None:
    from ..connectors import _default_thread
    from ..runtime import run_agent, spawn
    while True:
        try:
            now = time.time()
            for g in db.q("SELECT * FROM goals WHERE status='active' AND next_check IS NOT NULL "
                          "AND next_check <= ?", now):
                db.update("goals", g["id"], next_check=now + g["cadence"] * 60)
                thread_id = g["thread_id"] or _default_thread(g["agent_id"])
                spawn(run_agent(g["agent_id"], thread_id, "goal",
                                f"🎯 Goal check-in: “{g['title']}” — currently {g['progress']}% "
                                f"(why: {g['why'] or 'n/a'}; notes: {g['notes'] or 'none'}). "
                                "Take one concrete step forward using your tools, then call "
                                "`update_goal` with the new progress/notes. If there's genuinely "
                                "nothing useful to do right now, just call update_goal with the "
                                "same progress and a short note explaining why, and reply "
                                "NO_REPLY."))
        except Exception:
            log.exception("goals loop tick failed")
        await asyncio.sleep(60)
