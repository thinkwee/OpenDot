"""Your calendars: reminders, and moving older calendar settings into Apps.

Calendars are apps (Settings → Apps → Calendars): Google Calendar, iCloud, any CalDAV
account, or a calendar's private iCal link. Like any app you pick which agents may use
each one, and its own tools let them read it (and add events, if it can). The Calendar
page shows all of them next to what your agents are doing (``agenda.py``).

This module:
- a background loop (every 5 min) that drops a "starting soon" note in the Inbox and
  fires a ``calendar:<name>`` event (so a watch can react) shortly before each event;
- ``move_old_calendars``: older versions kept calendar links on the Calendar page and a
  calendar per agent; on the first start they become calendar apps.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from .. import memory
from ..db import db, new_id
from .lang import tr

log = logging.getLogger("opendot.ext.calendar")

POLL_SECONDS = 300
REMIND = "calendar:remind_minutes"  # 0 = no reminders
NOTIFIED = "calendar:notified"
MOVED = "calendar:moved_to_apps"

_running = False


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
    """An iCalendar VEVENT → the plain dict every calendar source returns."""
    start = e.get("DTSTART").dt
    end = e.get("DTEND").dt if e.get("DTEND") else start
    return {"title": str(e.get("SUMMARY", "") or ""),
            "start": start.isoformat() if hasattr(start, "isoformat") else str(start),
            "end": end.isoformat() if hasattr(end, "isoformat") else str(end),
            "location": str(e.get("LOCATION", "") or ""),
            "notes": str(e.get("DESCRIPTION", "") or "")[:800],
            "uid": str(e.get("UID", "") or "")}


# ---------------- reminders ----------------
def _front_desk() -> str | None:
    a = db.one("SELECT id FROM agents WHERE origin='default' ORDER BY created LIMIT 1") \
        or db.one("SELECT id FROM agents ORDER BY created LIMIT 1")
    return a["id"] if a else None


async def _tick() -> None:
    from ..connectors import ingest_event
    from .agenda import your_events
    window = int(db.kv_get(REMIND, 30) or 0)
    if window <= 0:
        return
    now = time.time()
    events, cals = await your_events(now, now + (window + 6) * 60)
    # the phone already reminds you of what it synced
    names = {c["key"]: c for c in cals if c.get("source") != "iphone"}
    notified = set(db.kv_get(NOTIFIED, []))
    fresh = False
    for e in events:
        cal = names.get(e.get("calendar"))
        if not cal or e.get("all_day"):
            continue
        key = f"{e['calendar']}:{e['id']}:{int(e['start'])}"
        mins = (e["start"] - now) / 60
        if key in notified or mins > window or mins < -2:
            continue
        notified.add(key)
        fresh = True
        when = dt.datetime.fromtimestamp(e["start"], memory.now_local().tzinfo).strftime("%H:%M")
        await ingest_event(f"calendar:{cal['name']}", "upcoming",
                           {"title": e["title"], "start": when, "location": e.get("location", ""),
                            "calendar": cal["name"]})
        db.insert("inbox", id=new_id("in_"), agent_id=_front_desk(), kind="note",
                  title=tr(f"📅 Starting soon: {e['title']}", f"📅 马上开始：{e['title']}"),
                  body=when + (f" · {e['location']}" if e.get("location") else ""),
                  status="unread")
    if fresh:
        db.kv_set(NOTIFIED, list(notified)[-500:])


async def start() -> None:
    global _running
    _running = True
    while _running:
        try:
            await _tick()
        except Exception:
            log.exception("calendar reminders failed")
        await asyncio.sleep(POLL_SECONDS)


async def stop() -> None:
    global _running
    _running = False


# ---------------- older settings → calendar apps ----------------
def move_old_calendars() -> list[str]:
    """Calendar links from the Calendar page (everyone could see them) and each agent's
    own calendar become calendar apps, used by the same agents as before. Runs once,
    before the apps start."""
    if db.kv_get(MOVED):
        return []
    from ..gatekeeper import inject_secrets
    from .apps import install
    added: list[str] = []
    remind = None
    for f in db.kv_get("calendars:mine") or []:
        if f.get("url"):
            added.append(install("calendar-link", {"url": f["url"]}, "all", label=f.get("name"),
                                 disabled=not f.get("on", True)))
    for row in db.q("SELECT k, v FROM kv WHERE k LIKE 'identity:calendar:%'"):
        aid = row["k"].rsplit(":", 1)[-1]
        cfg = db.kv_get(row["k"]) or {}
        if not isinstance(cfg, dict) or not db.one("SELECT id FROM agents WHERE id=?", aid):
            continue
        remind = remind if remind is not None else cfg.get("reminder_minutes")
        for f in cfg.get("ics_feeds") or []:
            if f.get("url"):
                added.append(install("calendar-link", {"url": f["url"]}, [aid], label=f.get("name")))
        dav = inject_secrets(cfg.get("caldav") or {})
        if dav.get("url") and dav.get("user"):
            app = "icloud-calendar" if "icloud" in dav["url"] else "caldav"
            added.append(install(app, {"url": dav["url"], "user": dav["user"],
                                       "password": dav.get("password", "")}, [aid]))
    if remind is not None:
        db.kv_set(REMIND, int(remind))
    with db.lock:
        db.conn.execute("DELETE FROM kv WHERE k='calendars:mine' OR k LIKE 'identity:calendar:%'")
        db.conn.commit()
    db.kv_set(MOVED, time.time())
    if added:
        log.info("moved %d calendar(s) into Apps: %s", len(added), ", ".join(added))
    return added
