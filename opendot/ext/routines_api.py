"""Routines calendar (upcoming cron runs, computed server-side) + run history, plus
two ready-made presets ("Morning brief" / "Evening wrap-up") the UI can one-click add.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Body, HTTPException

from ..db import db, new_id
from ..scheduler import next_cron, next_time

router = APIRouter(prefix="/api/routines")


@router.get("/upcoming")
async def upcoming(days: int = 7, limit_per: int = 20):
    """Next N days of cron runs, one row per future firing, soonest first."""
    horizon = time.time() + days * 86400
    out = []
    for a in db.q("SELECT * FROM automations WHERE kind IN ('cron','once') AND enabled=1"):
        t = a["next_run"] or time.time()
        n = 0
        while t and t <= horizon and n < limit_per:
            out.append({"automation_id": a["id"], "name": a["name"], "agent_id": a["agent_id"],
                       "at": t})
            n += 1
            try:
                t = next_time(a["kind"], a["schedule"], t + 1)
            except Exception:
                break
    out.sort(key=lambda r: r["at"])
    return out


@router.get("/history")
async def history(limit: int = 60):
    """What background work actually produced: the agent messages posted from a routine,
    a watch, a check-in or a goal (they live in the agent's chat, not the Inbox)."""
    msgs = db.q("SELECT * FROM messages WHERE role='agent' AND (meta LIKE "
                "'%\"source\": \"automation:%' OR meta LIKE '%\"source\": \"watch:%' OR meta "
                "LIKE '%\"source\": \"heartbeat\"%' OR meta LIKE '%\"source\": \"goal\"%') "
                "ORDER BY created DESC LIMIT ?", limit)
    return {"messages": msgs}


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


def PROMPT(agent: dict) -> str | None:
    """The reminders and routines this agent has on right now, so it knows what's really
    set without having to look (and a reminder it only said it set isn't here)."""
    import datetime as dt

    from .. import memory
    rows = db.q("SELECT id, name, kind, schedule, next_run FROM automations WHERE kind IN "
                "('cron','once') AND enabled=1 AND agent_id=? ORDER BY next_run LIMIT 30",
                agent["id"])
    if not rows:
        return "# Your reminders and routines\nNone are on right now."
    tz = memory.now_local().tzinfo

    def when(r):
        nxt = dt.datetime.fromtimestamp(r["next_run"], tz).strftime("%a %Y-%m-%d %H:%M") \
            if r["next_run"] else "not scheduled"
        return f"next {nxt}" + (f", repeats ({r['schedule']})" if r["kind"] == "cron" else ", once")
    return "# Your reminders and routines (what's on right now)\n" + "\n".join(
        f"- {r['name']} (id {r['id']}): {when(r)}" for r in rows)
