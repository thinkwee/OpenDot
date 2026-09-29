"""Agenda — one timeline of your calendars and what your agents do.

Your side: events synced from the iPhone (every account on it: iCloud, Google,
Exchange…), your own calendar links (Google / iCloud secret iCal URLs, kv
``calendars:mine``) and any calendar an agent was given (``identity:calendar:<id>``).

Their side: when each routine fires and each watch checks (chats aren't plans, so
replies don't show).

Links: an event gets the agents that created it (``create_event`` /
``iphone_add_event``), are watching for it (a watch or routine naming it) or have
talked about it in a chat — so you can see who's looking after which of your plans.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import time

import httpx
from fastapi import APIRouter, Body

from .. import memory
from ..db import db
from ..scheduler import next_cron

log = logging.getLogger("opendot.ext.agenda")
router = APIRouter(prefix="/api/agenda")

MINE = "calendars:mine"
_ics_cache: dict[str, tuple[float, list[dict]]] = {}
ICS_TTL = 300
HELPER = re.compile(r"-w\d+$")


# ---------------------------------------------------------------- helpers
def _ts(v) -> float | None:
    """ISO datetime / date → epoch seconds (dates are local midnight)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            d = dt.datetime.combine(dt.date.fromisoformat(s[:10]), dt.time())
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=memory.now_local().tzinfo)
    return d.timestamp()


def account_of(url: str = "", hint: str = "") -> str:
    s = f"{url} {hint}".lower()
    for key, name in (("google", "Google"), ("gmail", "Google"), ("icloud", "iCloud"),
                      ("me.com", "iCloud"), ("outlook", "Outlook"), ("office365", "Outlook"),
                      ("exchange", "Exchange"), ("fastmail", "Fastmail"), ("qq.com", "QQ")):
        if key in s:
            return name
    return hint or ""


def _event(e: dict, **extra) -> dict | None:
    start = _ts(e.get("start"))
    if start is None:
        return None
    end = _ts(e.get("end")) or start + 3600
    all_day = bool(e.get("all_day")) or (len(str(e.get("start") or "")) == 10)
    return {"id": str(e.get("id") or e.get("uid") or f"{e.get('title')}@{start}"),
            "title": e.get("title") or "", "start": start, "end": max(end, start + 900),
            "all_day": all_day, "location": e.get("location") or "",
            "notes": (e.get("notes") or "")[:400], "agents": [], **extra}


async def _ics(url: str, start: dt.datetime, end: dt.datetime) -> list[dict]:
    key = f"{url}|{start.date()}|{end.date()}"
    hit = _ics_cache.get(key)
    if hit and time.time() - hit[0] < ICS_TTL:
        return hit[1]
    import icalendar
    import recurring_ical_events

    from .calendar import _ev_dict
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        r = await c.get(url.replace("webcal://", "https://", 1))
        r.raise_for_status()
    cal = icalendar.Calendar.from_ical(r.content)
    evs = [_ev_dict(e) for e in recurring_ical_events.of(cal).between(start, end)]
    _ics_cache[key] = (time.time(), evs)
    return evs


def feeds() -> list[dict]:
    return db.kv_get(MINE) or []


# ---------------------------------------------------------------- your calendars
async def your_events(t0: float, t1: float) -> tuple[list[dict], list[dict]]:
    tz = memory.now_local().tzinfo
    start, end = dt.datetime.fromtimestamp(t0, tz), dt.datetime.fromtimestamp(t1, tz)
    out: list[dict] = []
    cals: dict[str, dict] = {}

    def cal(key: str, name: str, account: str, color: str, source: str, agent_id=None):
        cals.setdefault(key, {"key": key, "name": name, "account": account, "color": color,
                              "source": source, "agent_id": agent_id})
        return key

    # the iPhone: every account it has
    try:
        from . import iphone
        data, synced = iphone.get("calendar")
    except Exception:
        data, synced = None, None
    for e in (data or {}).get("events", []):
        name = e.get("calendar") or "iPhone"
        acct = account_of(hint=e.get("account") or "") or e.get("account") or "iPhone"
        k = cal(f"iphone:{acct}:{name}", name, acct, e.get("color") or "", "iphone")
        ev = _event(e, calendar=k)
        if ev and ev["end"] >= t0 and ev["start"] <= t1:
            out.append(ev)

    # your own links, then calendars an agent was given
    jobs = []
    for i, f in enumerate(feeds()):
        if f.get("url") and f.get("on", True):
            k = cal(f"mine:{i}", f.get("name") or account_of(f["url"]) or "Calendar",
                    account_of(f["url"], f.get("account") or ""), f.get("color") or "", "link")
            jobs.append((k, f["url"]))
    for row in db.q("SELECT k, v FROM kv WHERE k LIKE 'identity:calendar:%'"):
        aid = row["k"].rsplit(":", 1)[-1]
        try:
            cfg = json.loads(row["v"]) or {}
        except Exception:
            continue
        for f in cfg.get("ics_feeds") or []:
            if f.get("url"):
                k = cal(f"agent:{aid}:{f.get('name')}", f.get("name") or "Calendar",
                        account_of(f["url"]), "", "agent", aid)
                jobs.append((k, f["url"]))

    async def one(k, url):
        try:
            return k, await _ics(url, start, end)
        except Exception as e:
            log.info("calendar link failed (%s): %s", k, e)
            cals[k]["error"] = str(e)[:120]
            return k, []

    for k, evs in await asyncio.gather(*(one(k, u) for k, u in jobs)):
        for e in evs:
            ev = _event(e, calendar=k)
            if ev:
                out.append(ev)

    # the same event from two places (phone + link) shows once
    seen, uniq = set(), []
    for ev in sorted(out, key=lambda e: e["start"]):
        key = (ev["title"].strip().lower(), int(ev["start"] // 60))
        if key not in seen:
            seen.add(key)
            uniq.append(ev)
    if synced:
        for c in cals.values():
            if c["source"] == "iphone":
                c["synced"] = synced
    return uniq, list(cals.values())


# ---------------------------------------------------------------- what agents have scheduled
MAX_MARKS_PER_DAY = 12  # a watch checking more often than this shows as one all-day line


def agent_items(t0: float, t1: float) -> list[dict]:
    """When each routine fires and each watch checks — past (faded in the UI) and
    upcoming. Chats aren't plans, so replies don't show here."""
    now = time.time()
    items: list[dict] = []
    for a in db.q("SELECT * FROM automations WHERE enabled=1"):
        base = {"agent_id": a["agent_id"], "thread_id": a["thread_id"],
                "automation_id": a["id"], "title": a["name"]}
        born = a["created"] or t0
        if a["kind"] == "cron" and a["schedule"]:
            t, n = next_cron(a["schedule"], max(t0, born) - 1), 0
            while t <= t1 and n < 200:
                items.append({**base, "id": f"{a['id']}@{int(t)}", "kind": "routine",
                              "start": t, "end": t + 900, "past": t < now})
                n += 1
                t = next_cron(a["schedule"], t + 1)
        elif a["kind"] == "watch":
            every = max(int(a["every_min"] or 60), 1) * 60
            lo, hi = max(t0, born), min(t1, a["expires"] or t1)
            if hi <= lo:
                continue
            extra = {"every_min": a["every_min"], "checks": a["checks"] or 0,
                     "looking_for": a["event_filter"], "expires": a["expires"]}
            if 86400 / every > MAX_MARKS_PER_DAY:
                items.append({**base, **extra, "id": a["id"], "kind": "watch",
                              "start": lo, "end": hi})
                continue
            # checks run every `every` seconds on the grid through next_run
            anchor = a["next_run"] or a["last_run"] or born
            t = anchor - ((anchor - lo) // every) * every
            while t <= hi:
                if t >= lo:
                    items.append({**base, **extra, "id": f"{a['id']}@{int(t)}",
                                  "kind": "check", "start": t, "end": t + 900,
                                  "past": t < now})
                t += every
    return items


# ---------------------------------------------------------------- who's on which event
def link(events: list[dict], items: list[dict], t0: float, t1: float) -> None:
    by_title: dict[str, list[dict]] = {}
    for ev in events:
        k = ev["title"].strip().lower()
        if len(k) >= 3:
            by_title.setdefault(k, []).append(ev)
    if not by_title:
        return

    def add(ev, aid, how, thread_id=None):
        aid = HELPER.sub("", aid or "")
        if not aid or any(x["agent_id"] == aid and x["how"] == how for x in ev["agents"]):
            return
        ev["agents"].append({"agent_id": aid, "how": how, "thread_id": thread_id})

    # it put the event there
    for s in db.q("SELECT agent_id, thread_id, args FROM steps WHERE tool IN "
                  "('create_event','iphone_add_event') AND status='done' AND created > ?",
                  t0 - 60 * 86400):
        args = s["args"] if isinstance(s["args"], dict) else json.loads(s["args"] or "{}")
        for ev in by_title.get(str(args.get("title") or "").strip().lower(), []):
            add(ev, s["agent_id"], "created", s["thread_id"])

    # a watch or routine is about it
    autos = db.q("SELECT agent_id, thread_id, name, prompt, event_filter FROM automations "
                 "WHERE enabled=1")
    # it talked about it recently
    msgs = db.q("SELECT agent_id, thread_id, content FROM messages WHERE role='agent' AND "
                "created > ?", min(t0, time.time()) - 14 * 86400)
    for k, evs in by_title.items():
        for a in autos:
            blob = f"{a['name']} {a['prompt']} {a['event_filter']}".lower()
            if k in blob:
                for ev in evs:
                    add(ev, a["agent_id"], "watching", a["thread_id"])
        for m in msgs:
            if k in (m["content"] or "").lower():
                for ev in evs:
                    add(ev, m["agent_id"], "mentioned", m["thread_id"])


# ---------------------------------------------------------------- API
@router.get("")
async def agenda(start: float, end: float):
    end = min(end, start + 62 * 86400)
    events, calendars = await your_events(start, end)
    items = agent_items(start, end)
    link(events, items, start, end)
    return {"events": events, "calendars": calendars, "items": items, "now": time.time()}


@router.get("/feeds")
async def get_feeds():
    return feeds()


@router.put("/feeds")
async def put_feeds(body: list = Body(...)):
    clean = []
    for f in body[:20]:
        url = str(f.get("url") or "").strip()
        if not url:
            continue
        clean.append({"name": str(f.get("name") or "")[:40] or account_of(url) or "Calendar",
                      "url": url, "color": str(f.get("color") or "")[:16],
                      "account": account_of(url, str(f.get("account") or "")),
                      "on": bool(f.get("on", True))})
    db.kv_set(MINE, clean)
    _ics_cache.clear()
    return clean


@router.post("/feeds/test")
async def test_feed(body: dict = Body(...)):
    now = memory.now_local()
    try:
        evs = await _ics(str(body.get("url") or ""), now - dt.timedelta(days=7),
                         now + dt.timedelta(days=30))
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "count": len(evs), "account": account_of(str(body.get("url") or ""))}
