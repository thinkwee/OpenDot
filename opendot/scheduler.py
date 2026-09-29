"""Always-on loop: cron automations + per-agent heartbeat."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import time

from croniter import croniter

from . import memory
from .config import settings
from .db import db

log = logging.getLogger("opendot.scheduler")

HEARTBEAT_PROMPT = (
    "💓 Heartbeat. This is you checking in on your own, not the human asking. You can only "
    "look right now — read, search, check calendars and mail — not act. Look at what you "
    "actually know (USER.md, your recent journal, your active goals, your HEARTBEAT "
    "checklist) and look for one way to help: something time-sensitive, a goal that's "
    "stalled, something you said you'd follow up on, a better option for something they're "
    "planning. Propose, don't act: say it in one or two lines and call `offer_choices` "
    "(e.g. “Go ahead”, “Not now”) so they can turn it into a real job with one tap. Don't "
    "manufacture busywork or repeat what you already told them. If nothing meets that bar, "
    "reply exactly NO_REPLY — silence is the right, common outcome."
)
# the front desk checks in on its own by default, every few hours
RESEARCH_MINUTES = int(os.environ.get("DOT_RESEARCH_MINUTES", "180"))


def next_cron(expr: str, after: float | None = None) -> float:
    base = dt.datetime.fromtimestamp(after or time.time(), memory.now_local().tzinfo)
    return croniter(expr, base).get_next(dt.datetime).timestamp()


def heartbeat_enabled(agent_id: str) -> bool:
    """The per-agent toggle; untouched, it's on only for the front desk."""
    v = db.kv_get(f"heartbeat:{agent_id}", None)
    if v is None:
        return _front_desk(agent_id)
    return bool(v)


def _front_desk(agent_id: str) -> bool:
    return bool(db.one("SELECT id FROM agents WHERE id=? AND origin='default'", agent_id))


def heartbeat_every(agent_id: str) -> float:
    """Seconds between check-ins: DOT_HEARTBEAT_MINUTES if you switched it on yourself,
    every few hours for the front desk's default one."""
    if db.kv_get(f"heartbeat:{agent_id}", None) is None:
        return max(settings.HEARTBEAT_MINUTES, RESEARCH_MINUTES) * 60
    return settings.HEARTBEAT_MINUTES * 60


async def scheduler_loop() -> None:
    from .connectors import _default_thread
    from .runtime import run_agent, spawn
    while True:
        now = time.time()
        try:
            for a in db.q("SELECT * FROM automations WHERE kind='cron' AND enabled=1 "
                          "AND next_run IS NOT NULL AND next_run <= ?", now):
                db.update("automations", a["id"], last_run=now,
                          next_run=next_cron(a["schedule"], now))
                spawn(run_agent(a["agent_id"], a["thread_id"] or _default_thread(a["agent_id"]),
                                f"automation:{a['name']}",
                                f"⏰ Scheduled automation “{a['name']}”. Do this now: "
                                f"{a['prompt']}"))
            from .ext.push import _quiet_now
            if not _quiet_now():  # daytime only (DOT_QUIET_FROM / DOT_QUIET_TO)
                for ag in db.q("SELECT id FROM agents"):
                    if not heartbeat_enabled(ag["id"]):
                        continue
                    key = f"heartbeat_last:{ag['id']}"
                    last = db.kv_get(key, None)
                    if last is None and db.kv_get(f"heartbeat:{ag['id']}", None) is None:
                        db.kv_set(key, now)  # a default check-in starts its clock, no wake at boot
                        continue
                    last = last or 0
                    if now - last >= heartbeat_every(ag["id"]):
                        db.kv_set(key, now)
                        spawn(run_agent(ag["id"], _default_thread(ag["id"]), "heartbeat",
                                        HEARTBEAT_PROMPT))
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(20)
