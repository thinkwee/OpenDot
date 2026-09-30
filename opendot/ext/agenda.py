"""Agenda — one timeline of your calendars and what your agents do.

Your side: events synced from the iPhone (every account on it: iCloud, Google,
Exchange…) and every calendar connected in Settings → Apps (Google Calendar, iCloud,
CalDAV, iCal links; ``builtin_apps.calendar_events``).

Their side: when each routine fires and each watch checks (chats aren't plans, so
replies don't show).

Links: an event gets the agents that created it (a calendar app's ``create_event`` /
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
from fastapi import APIRouter

from .. import memory
from ..db import db
from ..scheduler import next_cron

log = logging.getLogger("opendot.ext.agenda")
router = APIRouter(prefix="/api/agenda")

CALENDAR_APPS = ("google_calendar", "caldav", "ics")
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


def _range_now() -> tuple[dt.datetime, dt.datetime]:
    now = memory.now_local()
    return now, now + dt.timedelta(days=14)


# ---------------------------------------------------------------- your calendars
async def your_events(t0: float, t1: float) -> tuple[list[dict], list[dict]]:
    tz = memory.now_local().tzinfo
    start, end = dt.datetime.fromtimestamp(t0, tz), dt.datetime.fromtimestamp(t1, tz)
    out: list[dict] = []
    cals: dict[str, dict] = {}

    def cal(key: str, name: str, account: str, color: str, source: str, app: str = ""):
        cals.setdefault(key, {"key": key, "name": name, "account": account, "color": color,
                              "source": source, "app": app})
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

    # every calendar connected in Settings → Apps
    from ..connectors import load_config
    jobs = []
    for n, sp in load_config()["mcp"].items():
        if sp.get("builtin") in CALENDAR_APPS and not sp.get("disabled"):
            acct = {"icloud-calendar": "iCloud", "google-calendar": "Google"}.get(sp.get("app")) \
                or account_of(str((sp.get("settings") or {}).get("url") or ""), n)
            jobs.append((cal(f"app:{n}", sp.get("label") or n, acct, "", "app", app=n), n, sp))

    async def one(k, n, sp):
        from ..builtin_apps import calendar_events
        try:
            return k, await calendar_events(n, sp, start, end)
        except Exception as e:  # noqa: BLE001 — one calendar failing doesn't hide the others
            log.info("calendar %s failed: %s", n, e)
            cals[k]["error"] = str(e)[:160]
            return k, []

    for k, evs in await asyncio.gather(*(one(*j) for j in jobs)):
        for e in evs:
            # an account with several calendars (Google, iCloud) shows each on its own
            ck = k
            if e.get("calendar") and e["calendar"] != cals[k]["name"]:
                ck = cal(f"{k}:{e['calendar']}", e["calendar"], cals[k]["account"] or cals[k]["name"],
                         e.get("color") or "", "app", app=cals[k]["app"])
            ev = _event(e, calendar=ck)
            if ev:
                out.append(ev)

    # an account whose events all sit in its named calendars needn't show itself too
    used = {ev["calendar"] for ev in out}
    for k in [k for k, c in cals.items() if c["source"] == "app" and not c.get("error")
              and k not in used and any(o.startswith(k + ":") for o in cals)]:
        del cals[k]

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
    for s in db.q("SELECT agent_id, thread_id, args FROM steps WHERE (tool IN "
                  "('create_event','iphone_add_event') OR tool LIKE 'mcp\\_\\_%\\_\\_create\\_event' "
                  "ESCAPE '\\') AND status='done' AND created > ?", t0 - 60 * 86400):
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
