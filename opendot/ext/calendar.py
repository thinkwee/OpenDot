"""Agent calendar — read-only ICS subscriptions (Google/iCloud secret links) and an
optional read/write CalDAV account.

Config (per agent, kv ``identity:calendar:<agent_id>``):
    {"ics_feeds": [{"name": "personal", "url": "https://.../basic.ics"}],
     "caldav": {"url": "https://caldav.example/...", "user": "..",
                "password": "{{vault:X}}"} | null,
     "reminder_minutes": 30}

Background loop (every 5 min) fires ``calendar:<feed>`` events for events starting
within ``reminder_minutes`` and drops a short nudge card in the Inbox.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

import httpx
import icalendar
import recurring_ical_events
from fastapi import APIRouter, Body, HTTPException

from .. import memory
from ..connectors import ingest_event
from ..db import db, new_id
from ..gatekeeper import inject_secrets
from ..tools import S, fn, register_tool
from .lang import tr

log = logging.getLogger("opendot.ext.calendar")
router = APIRouter()

_running = False
POLL_SECONDS = 300


def get_config(agent_id: str) -> dict | None:
    return db.kv_get(f"identity:calendar:{agent_id}")


def set_config(agent_id: str, cfg: dict) -> None:
    db.kv_set(f"identity:calendar:{agent_id}", cfg)


def _parse_dt(s: str) -> dt.datetime:
    if not s:
        return memory.now_local()
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        d = memory.now_local()
    if d.tzinfo is None:
        d = d.replace(tzinfo=memory.now_local().tzinfo)
    return d


def _ev_dict(e) -> dict:
    start = e.get("DTSTART").dt
    end = e.get("DTEND").dt if e.get("DTEND") else start
    return {"title": str(e.get("SUMMARY", "") or ""),
           "start": start.isoformat() if hasattr(start, "isoformat") else str(start),
           "end": end.isoformat() if hasattr(end, "isoformat") else str(end),
           "location": str(e.get("LOCATION", "") or ""),
           "notes": str(e.get("DESCRIPTION", "") or "")[:800],
           "uid": str(e.get("UID", "") or "")}


async def _fetch_ics_events(url: str, start: dt.datetime, end: dt.datetime) -> list[dict]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
    cal = icalendar.Calendar.from_ical(r.content)
    events = recurring_ical_events.of(cal).between(start, end)
    return [_ev_dict(e) for e in events]


def _caldav_client(cfg: dict):
    import caldav
    return caldav.DAVClient(url=cfg["url"], username=cfg.get("user"),
                            password=cfg.get("password"))


def _caldav_events(cfg: dict, start: dt.datetime, end: dt.datetime) -> list[dict]:
    client = _caldav_client(cfg)
    out = []
    for cal in client.principal().calendars():
        for ev in cal.date_search(start=start, end=end):
            comp = ev.icalendar_component
            out.append(_ev_dict(comp))
    return out


def _caldav_create(cfg: dict, title: str, start: dt.datetime, end: dt.datetime,
                   location: str = "", notes: str = "") -> dict:
    client = _caldav_client(cfg)
    cals = client.principal().calendars()
    if not cals:
        raise RuntimeError("no calendars found on this CalDAV account")
    ev = cals[0].save_event(dtstart=start, dtend=end, summary=title, location=location,
                            description=notes)
    uid = ""
    try:
        uid = str(ev.icalendar_instance.subcomponents[0].get("UID"))
    except Exception:
        pass
    return {"uid": uid}


async def _all_events(cfg: dict, start: dt.datetime, end: dt.datetime) -> list[dict]:
    out: list[dict] = []
    for feed in cfg.get("ics_feeds", []):
        try:
            evs = await _fetch_ics_events(feed["url"], start, end)
        except Exception as e:
            log.warning("ics feed %s failed: %s", feed.get("name"), e)
            continue
        for e in evs:
            e["feed"] = feed.get("name") or "ics"
            out.append(e)
    caldav_cfg = cfg.get("caldav")
    if caldav_cfg and caldav_cfg.get("url"):
        cd = inject_secrets(caldav_cfg)
        try:
            evs = await asyncio.to_thread(_caldav_events, cd, start, end)
        except Exception as e:
            log.warning("caldav fetch failed: %s", e)
        else:
            for e in evs:
                e["feed"] = "caldav"
                out.append(e)
    out.sort(key=lambda e: e["start"])
    return out


# ---------------- tools ----------------
async def _calendar_events(ctx, start: str = "", end: str = "") -> dict:
    cfg = get_config(ctx.agent["id"])
    if not cfg or not (cfg.get("ics_feeds") or cfg.get("caldav")):
        return {"error": "no calendar configured for this agent yet — set it up in "
                         "Apps → Identity"}
    s = _parse_dt(start)
    e = _parse_dt(end) if end else s + dt.timedelta(days=7)
    return {"events": await _all_events(cfg, s, e)}


async def _create_event(ctx, title: str, start: str, end: str, location: str = "",
                        notes: str = "") -> dict:
    cfg = get_config(ctx.agent["id"])
    caldav_cfg = cfg and cfg.get("caldav")
    if not caldav_cfg or not caldav_cfg.get("url"):
        return {"error": "no CalDAV account configured — ICS feeds are read-only, add a "
                         "CalDAV account in Apps → Identity to create events"}
    cd = inject_secrets(caldav_cfg)
    s, e = _parse_dt(start), _parse_dt(end)
    try:
        res = await asyncio.to_thread(_caldav_create, cd, title, s, e, location, notes)
    except Exception as ex:
        return {"error": str(ex)}
    return {"ok": True, **res}


register_tool("calendar_events",
    fn("calendar_events", "List your calendar events between two ISO datetimes (default: "
       "next 7 days).", {"start": S, "end": S}),
    _calendar_events, policy="allow")
register_tool("create_event",
    fn("create_event", "Create a calendar event (CalDAV account only — ICS subscriptions "
       "are read-only). A human approves it first.",
       {"title": S, "start": S, "end": S, "location": S, "notes": S},
       ["title", "start", "end"]),
    _create_event, policy="ask")


# ---------------- background reminders ----------------
async def _tick() -> None:
    now = memory.now_local()
    for ag in db.q("SELECT id FROM agents"):
        cfg = get_config(ag["id"])
        if not cfg or not (cfg.get("ics_feeds") or cfg.get("caldav")):
            continue
        window = int(cfg.get("reminder_minutes", 30))
        try:
            events = await _all_events(cfg, now, now + dt.timedelta(minutes=window + 6))
        except Exception as e:
            log.warning("calendar tick for %s failed: %s", ag["id"], e)
            continue
        notified = set(db.kv_get(f"identity:calendar:notified:{ag['id']}", []))
        changed = False
        for e in events:
            key = f"{e['feed']}:{e['uid']}:{e['start']}"
            if key in notified:
                continue
            try:
                start_dt = dt.datetime.fromisoformat(e["start"])
            except ValueError:
                continue
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=now.tzinfo)
            mins = (start_dt - now).total_seconds() / 60
            if mins > window or mins < -2:
                continue
            notified.add(key)
            changed = True
            await ingest_event(f"calendar:{e['feed']}", "upcoming", e)
            when = start_dt.strftime("%H:%M")
            db.insert("inbox", id=new_id("in_"), agent_id=ag["id"], kind="note",
                      title=tr(f"📅 Starting soon: {e['title']}", f"📅 马上开始：{e['title']}"),
                      body=f"{when}" + (f" · {e['location']}" if e.get("location") else ""),
                      status="unread")
        if changed:
            db.kv_set(f"identity:calendar:notified:{ag['id']}", list(notified)[-500:])


async def start() -> None:
    global _running
    _running = True
    while _running:
        try:
            await _tick()
        except Exception:
            log.exception("calendar tick failed")
        await asyncio.sleep(POLL_SECONDS)


async def stop() -> None:
    global _running
    _running = False


# ---------------- config / test API ----------------
@router.get("/api/identity/calendar/{agent_id}")
async def api_get(agent_id: str):
    return get_config(agent_id) or {"ics_feeds": [], "reminder_minutes": 30}


@router.put("/api/identity/calendar/{agent_id}")
async def api_put(agent_id: str, body: dict = Body(...)):
    set_config(agent_id, body)
    return {"ok": True}


@router.post("/api/identity/calendar/{agent_id}/test-ics")
async def api_test_ics(agent_id: str, body: dict = Body(...)):
    url = body.get("url")
    if not url:
        raise HTTPException(400, "no url")
    now = memory.now_local()
    try:
        events = await _fetch_ics_events(url, now, now + dt.timedelta(days=14))
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "events_next_14_days": len(events),
           "sample": [e["title"] for e in events[:5]]}


@router.post("/api/identity/calendar/{agent_id}/test-caldav")
async def api_test_caldav(agent_id: str):
    cfg = get_config(agent_id)
    if not cfg or not cfg.get("caldav", {}).get("url"):
        raise HTTPException(400, "CalDAV not configured")
    cd = inject_secrets(cfg["caldav"])
    try:
        n = await asyncio.to_thread(lambda: len(_caldav_client(cd).principal().calendars()))
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "calendars": n}


def PROMPT(agent: dict) -> str | None:
    cfg = get_config(agent["id"])
    feeds = cfg.get("ics_feeds") if cfg else None
    caldav_on = bool(cfg and cfg.get("caldav", {}).get("url"))
    if not feeds and not caldav_on:
        return None
    names = ", ".join(f["name"] for f in (feeds or []) if f.get("name")) or "your feed(s)"
    return (f"# Calendar\nYou can see {names}"
           + (" and a writable CalDAV calendar" if caldav_on else "")
           + ". Use `calendar_events` for today's agenda or upcoming things"
           + (" and `create_event` to add one (a human confirms first)." if caldav_on else "."))
