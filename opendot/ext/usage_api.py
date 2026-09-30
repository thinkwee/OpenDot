"""Settings → Usage: tokens and estimated cost, by day, agent, model and kind of work.

    GET /api/usage?range=today|7d|30d|all
"""

from __future__ import annotations

import bisect
import datetime as dt
import re

from fastapi import APIRouter

from .. import memory
from ..db import db

router = APIRouter(prefix="/api/usage")
HELPER = re.compile(r"-w\d+$")
SUM = ("COUNT(*) calls, COALESCE(SUM(input),0) input, COALESCE(SUM(output),0) output, "
       "COALESCE(SUM(cached),0) cached, COALESCE(SUM(reasoning),0) reasoning, "
       "SUM(cost) cost, SUM(cost IS NULL) unpriced")


def _window(rng: str) -> tuple[float, float, str, list[float]]:
    """(start, end, bucket unit, bucket starts) in your time zone."""
    now = memory.now_local()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if rng == "today":
        return today.timestamp(), now.timestamp(), "hour", \
            [(today + dt.timedelta(hours=h)).timestamp() for h in range(24)]
    if rng in ("7d", "30d"):
        n = 7 if rng == "7d" else 30
        first = today - dt.timedelta(days=n - 1)
        return first.timestamp(), now.timestamp(), "day", \
            [(first + dt.timedelta(days=d)).timestamp() for d in range(n)]
    row = db.one("SELECT MIN(created) t FROM usage")
    first = dt.datetime.fromtimestamp(row["t"], now.tzinfo) if row and row["t"] else today
    first = first.replace(hour=0, minute=0, second=0, microsecond=0)
    days = (today - first).days + 1
    step = 1 if days <= 60 else 7  # a long history reads better week by week
    starts = [(first + dt.timedelta(days=d)).timestamp() for d in range(0, days, step)]
    return first.timestamp(), now.timestamp(), "day" if step == 1 else "week", starts


def _totals(t0: float, t1: float) -> dict:
    r = db.one(f"SELECT {SUM} FROM usage WHERE created >= ? AND created < ?", t0, t1)
    return {k: (r[k] or 0) for k in ("calls", "input", "output", "cached", "reasoning",
                                     "unpriced")} | {"cost": r["cost"],
                                                     "avg_input": (r["input"] or 0) // max(1, r["calls"] or 0)}


def _group(col: str, t0: float, t1: float) -> list[dict]:
    rows = db.q(f"SELECT {col} key, {SUM} FROM usage WHERE created >= ? AND created < ? "
                f"GROUP BY {col}", t0, t1)
    return sorted(rows, key=lambda r: -(r["input"] + r["output"]))


@router.get("")
async def usage(range: str = "7d"):  # noqa: A002 — the query parameter's name
    rng = range if range in ("today", "7d", "30d", "all") else "7d"
    t0, t1, unit, starts = _window(rng)

    # the same length of time just before, for "vs the previous week"
    prev = _totals(t0 - (t1 - t0), t0) if rng in ("today", "7d", "30d") else None

    rows = db.q("SELECT created, input, output, cost FROM usage WHERE created >= ? AND "
                "created < ?", t0, t1 + 1)
    buckets = [{"start": s, "input": 0, "output": 0, "cost": 0.0, "calls": 0} for s in starts]
    for r in rows:
        i = bisect.bisect_right(starts, r["created"]) - 1
        if i >= 0:
            b = buckets[i]
            b["input"] += r["input"] or 0
            b["output"] += r["output"] or 0
            b["cost"] += r["cost"] or 0
            b["calls"] += 1

    # a helper's calls count for the agent that brought it in
    agents: dict[str, dict] = {}
    for r in _group("agent_id", t0, t1):
        aid = HELPER.sub("", r["key"] or "")
        a = agents.setdefault(aid, {"agent_id": aid, "calls": 0, "input": 0, "output": 0,
                                    "cached": 0, "cost": None, "unpriced": 0})
        for k in ("calls", "input", "output", "cached", "unpriced"):
            a[k] += r[k] or 0
        if r["cost"] is not None:
            a["cost"] = (a["cost"] or 0) + r["cost"]
    names = {a["id"]: a for a in db.q("SELECT id, name FROM agents")}
    for a in agents.values():
        a["gone"] = bool(a["agent_id"]) and a["agent_id"] not in names

    threads = db.q("SELECT u.thread_id, t.title, SUM(u.input + u.output) tokens, SUM(u.cost) cost "
                   "FROM usage u JOIN threads t ON t.id = u.thread_id WHERE u.created >= ? AND "
                   "u.created < ? GROUP BY u.thread_id ORDER BY tokens DESC LIMIT 5", t0, t1)
    return {
        "range": rng, "start": t0, "end": t1, "unit": unit,
        "since": (db.one("SELECT MIN(created) t FROM usage") or {}).get("t"),
        "totals": _totals(t0, t1), "prev": prev, "buckets": buckets,
        "agents": sorted(agents.values(), key=lambda a: -(a["input"] + a["output"])),
        "models": [{"model": r["key"], **{k: r[k] for k in r if k != "key"}}
                   for r in _group("model", t0, t1)],
        "kinds": [{"kind": r["key"], **{k: r[k] for k in r if k != "key"}}
                  for r in _group("kind", t0, t1)],
        "threads": threads,
    }
