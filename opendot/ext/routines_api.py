"""Routines calendar (upcoming cron runs, computed server-side) + run history, plus
two ready-made presets ("Morning brief" / "Evening wrap-up") the UI can one-click add.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Body, HTTPException

from ..db import db, new_id
from ..scheduler import next_cron

router = APIRouter(prefix="/api/routines")


@router.get("/upcoming")
async def upcoming(days: int = 7, limit_per: int = 20):
    """Next N days of cron runs, one row per future firing, soonest first."""
    horizon = time.time() + days * 86400
    out = []
    for a in db.q("SELECT * FROM automations WHERE kind='cron' AND enabled=1"):
        t = a["next_run"] or time.time()
        n = 0
        while t and t <= horizon and n < limit_per:
            out.append({"automation_id": a["id"], "name": a["name"], "agent_id": a["agent_id"],
                       "at": t})
            n += 1
            try:
                t = next_cron(a["schedule"], t + 1)
            except Exception:
                break
    out.sort(key=lambda r: r["at"])
    return out


@router.get("/history")
async def history(limit: int = 60):
    """What routines/goals actually produced: agent messages posted from an
    automation, plus the inbox reports every non-chat run leaves behind."""
    msgs = db.q("SELECT * FROM messages WHERE role='agent' AND meta LIKE "
               "'%\"source\": \"automation:%' ORDER BY created DESC LIMIT ?", limit)
    reports = db.q("SELECT * FROM inbox WHERE kind='report' ORDER BY created DESC LIMIT ?",
                   limit)
    return {"messages": msgs, "reports": reports}


PRESETS = {
    "morning_brief": dict(
        name="Morning brief", schedule="0 8 * * *",
        prompt=("Good morning brief for the human: today's agenda if you can see one (calendar "
               "connector or anything they told you), 2-3 headlines on topics they care about "
               "(see USER.md), the weather if you know their city, and a one-line nudge on any "
               "open goal that's stalled. Keep it short, skimmable, and warm — this is the "
               "first thing they read today.")),
    "evening_wrapup": dict(
        name="Evening wrap-up", schedule="0 21 * * *",
        prompt=("Evening wrap-up for the human: what got done today (yours and anything notable "
               "from teammates), what's still open or waiting on them, and one small thing to "
               "look forward to tomorrow. Keep it short.")),
}


@router.get("/presets")
async def presets():
    return [{"key": k, **v} for k, v in PRESETS.items()]


@router.post("/presets/{key}")
async def add_preset(key: str, body: dict = Body(...)):
    p = PRESETS.get(key)
    if not p:
        raise HTTPException(404, "no such preset")
    if not body.get("agent_id"):
        raise HTTPException(400, "agent_id required")
    nxt = next_cron(p["schedule"])
    a = db.insert("automations", id=new_id("au_"), agent_id=body["agent_id"], name=p["name"],
                 kind="cron", schedule=p["schedule"], event_filter="", prompt=p["prompt"],
                 enabled=1, next_run=nxt, thread_id=body.get("thread_id"))
    return a
