"""Watches — "tell me as soon as…", "if a slot opens up, grab it".

A watch is an automation (``kind='watch'``) that re-checks something every
``every_min`` minutes until it happens or ``expires`` passes. When it happens the
agent acts right away (or gets it one tap from done) and closes it with
``watch_done``. Quiet checks leave no message behind: the agent replies NO_REPLY.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Body, HTTPException

from ..bus import bus
from ..db import db, new_id
from ..tools import Ctx, S, fn, register_tool

log = logging.getLogger("opendot.ext.watch")
router = APIRouter(prefix="/api/watches")

I = {"type": "integer"}  # noqa: E741
MIN_EVERY = 5


def _check_prompt(w: dict) -> str:
    left = ""
    if w.get("expires"):
        days = (w["expires"] - time.time()) / 86400
        left = f" It expires in {days:.1f} days." if days > 0 else ""
    return (
        f"👀 Watch check #{(w.get('checks') or 0) + 1} — “{w['name']}” (watch id {w['id']}).{left}\n"
        f"Look for: {w['event_filter']}\nWhen it happens: {w['prompt']}\n\n"
        "Check now with your tools. If it hasn't happened, reply exactly NO_REPLY (the human "
        "hears nothing). If it has: do what was asked right away (anything that needs the "
        "human still goes through their OK), tell them in one or two lines — with "
        "`offer_choices` if they only need to agree — and call `watch_done` with what "
        "happened. If it can never happen now (sold out for good, date passed), tell them "
        "and call `watch_done` too.")


async def _watch(ctx: Ctx, name: str, look_for: str, then: str, every_minutes: int = 60,
                 for_days: int = 30) -> dict:
    every = max(MIN_EVERY, int(every_minutes or 60))
    now = time.time()
    w = db.insert("automations", id=new_id("au_"), agent_id=ctx.agent["id"], name=name[:80],
                  kind="watch", schedule="", event_filter=look_for, prompt=then, enabled=1,
                  next_run=now + 5, thread_id=ctx.thread_id, every_min=every,
                  expires=now + max(1, int(for_days or 30)) * 86400, checks=0, outcome="")
    bus.emit("automation", automation=w)
    return {"ok": True, "id": w["id"], "note": f"First check in a moment, then every "
                                               f"{every} min. Tell the human in one line."}


async def _watch_done(ctx: Ctx, id: str, outcome: str = "") -> dict:
    w = db.one("SELECT * FROM automations WHERE id=? AND kind='watch'", id)
    if not w:
        return {"error": "no such watch"}
    db.update("automations", id, enabled=0, outcome=(outcome or "done")[:400], next_run=None)
    bus.emit("automation", automation=db.one("SELECT * FROM automations WHERE id=?", id))
    return {"ok": True}


register_tool(
    "watch",
    fn("watch", "Keep an eye on something and act the moment it happens: price drops, a slot "
       "/ campsite / appointment opening, tickets going on sale, a restock, a reply arriving. "
       "look_for = the exact condition; then = what to do when it happens; every_minutes = "
       "how often to check (tickets/restocks 15-30, prices 360-1440); for_days = when to give "
       "up.", {"name": S, "look_for": S, "then": S, "every_minutes": I, "for_days": I},
       ["name", "look_for", "then"]),
    _watch, policy="allow")
register_tool(
    "watch_done",
    fn("watch_done", "Close a watch once what you were waiting for happened (or can't happen "
       "any more). outcome = one line on what happened.", {"id": S, "outcome": S}, ["id"]),
    _watch_done, policy="allow")


@router.get("")
async def list_watches(agent_id: str = ""):
    sql = "SELECT * FROM automations WHERE kind='watch'"
    args: list = []
    if agent_id:
        sql += " AND agent_id=?"
        args.append(agent_id)
    return db.q(sql + " ORDER BY enabled DESC, created DESC", *args)


@router.post("")
async def create_watch(body: dict = Body(...)):
    if not body.get("agent_id") or not body.get("look_for"):
        raise HTTPException(400, "agent_id and look_for required")
    from ..connectors import _default_thread
    ag = db.one("SELECT * FROM agents WHERE id=?", body["agent_id"])
    if not ag:
        raise HTTPException(404)
    ctx = Ctx(agent=ag, thread_id=body.get("thread_id") or _default_thread(ag["id"]),
              run_id="api")
    r = await _watch(ctx, body.get("name") or body["look_for"][:60], body["look_for"],
                     body.get("then") or "Tell me right away.", body.get("every_minutes", 60),
                     body.get("for_days", 30))
    return db.one("SELECT * FROM automations WHERE id=?", r["id"])


async def tick(now: float | None = None) -> int:
    """Run every due watch once; expire old ones. Returns how many checks started."""
    from ..connectors import _default_thread
    from ..runtime import run_agent, spawn
    now = now or time.time()
    n = 0
    for w in db.q("SELECT * FROM automations WHERE kind='watch' AND enabled=1 "
                  "AND next_run IS NOT NULL AND next_run <= ?", now):
        if w.get("expires") and w["expires"] < now:
            db.update("automations", w["id"], enabled=0, next_run=None,
                      outcome="gave up — it didn't happen in time")
            spawn(run_agent(w["agent_id"], w["thread_id"] or _default_thread(w["agent_id"]),
                            f"watch:{w['name']}",
                            f"Your watch “{w['name']}” ran out of time without it happening "
                            "("f"{w.get('checks') or 0} checks). Tell the human in one friendly "
                            "line, and offer to keep watching with `offer_choices`."))
            continue
        db.update("automations", w["id"], last_run=now,
                  next_run=now + (w.get("every_min") or 60) * 60,
                  checks=(w.get("checks") or 0) + 1)
        spawn(run_agent(w["agent_id"], w["thread_id"] or _default_thread(w["agent_id"]),
                        f"watch:{w['name']}", _check_prompt(w)))
        n += 1
    return n


async def start() -> None:
    while True:
        try:
            await tick()
        except Exception:
            log.exception("watch tick failed")
        await asyncio.sleep(30)


def PROMPT(agent: dict) -> str | None:
    rows = db.q("SELECT id, name, event_filter FROM automations WHERE kind='watch' AND "
                "enabled=1 AND agent_id=? ORDER BY created DESC LIMIT 8", agent["id"])
    if not rows:
        return None
    return "# What you're watching\n" + "\n".join(
        f"- {r['name']} (id {r['id']}): {r['event_filter']}" for r in rows)
