"""iPhone — your phone as the agents' window into your life (the iOS app is the node).

Two paths, because iOS never lets an app run in the background for long:

1. **Sync (cache).** Whenever iOS wakes the app (open, background refresh, HealthKit
   background delivery, a significant location change / visit, a silent push), it
   POSTs fresh snapshots to ``/api/iphone/sync``: health (daily summaries +
   workouts), calendar (-7…+14 days), reminders, location (current + visits),
   motion, device (battery, focus). Agents read these instantly, even while the phone
   sleeps, and always see how fresh they are.

2. **Live commands.** Writes and on-demand reads (add a reminder / event, search
   contacts, get the location right now, run a Shortcut) go to the phone:
     - app in the foreground → straight over its node WebSocket (see ``nodes.py``);
     - otherwise → queued, a silent push wakes the app, it drains the queue via
       ``/api/iphone/commands`` and posts results back. Writes that can't run now
       stay queued and run next time the phone wakes.

Events: the app (and any iOS Shortcuts automation — arrive home, alarm stopped,
CarPlay connected, NFC tag, Focus changed…) can POST ``/api/iphone/event`` or the
token-less-for-Shortcuts ``/hook/<hook_token>/iphone/<type>``; each becomes an
``iphone:<type>`` event that routines can trigger on.

Nothing about money or payments lives here, on purpose.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time

from fastapi import APIRouter, Body, HTTPException, Request

from ..db import db, new_id
from ..tools import Ctx, fn, register_tool

log = logging.getLogger("opendot.ext.iphone")
router = APIRouter()

db.ensure_schema("""
CREATE TABLE IF NOT EXISTS iphone_data (
  device_id TEXT, kind TEXT, data TEXT, updated REAL, PRIMARY KEY (device_id, kind)
);
CREATE TABLE IF NOT EXISTS iphone_queue (
  id TEXT PRIMARY KEY, device_id TEXT, method TEXT, params TEXT, status TEXT,
  result TEXT, created REAL, done REAL
);
""")

KEEP_HEALTH_DAYS = 90
KEEP_VISITS = 300
KEEP_WORKOUTS = 100
WAKE_WAIT = 25  # seconds to wait for a silent push to wake the phone

_done: dict[str, asyncio.Future] = {}


# ---------------------------------------------------------------- storage
def _paired() -> bool:
    return bool(_device_id())


def _device_id() -> str | None:
    """The most recently seen iPhone (single-user system: usually exactly one)."""
    row = db.one("SELECT device_id FROM iphone_data ORDER BY updated DESC LIMIT 1")
    if row:
        return row["device_id"]
    row = db.one("SELECT id FROM devices WHERE os LIKE 'iOS%' OR os LIKE 'iPadOS%' "
                 "ORDER BY last_seen DESC LIMIT 1")
    return row["id"] if row else None


def get(kind: str, device_id: str | None = None) -> tuple[dict | None, float | None]:
    device_id = device_id or _device_id()
    if not device_id:
        return None, None
    row = db.one("SELECT data, updated FROM iphone_data WHERE device_id=? AND kind=?",
                 device_id, kind)
    if not row:
        return None, None
    return json.loads(row["data"]), row["updated"]


def _put(device_id: str, kind: str, data: dict) -> None:
    with db.lock:
        db.conn.execute(
            "INSERT INTO iphone_data (device_id, kind, data, updated) VALUES (?,?,?,?) "
            "ON CONFLICT(device_id, kind) DO UPDATE SET data=excluded.data, "
            "updated=excluded.updated",
            (device_id, kind, json.dumps(data, ensure_ascii=False), time.time()))
        db.conn.commit()


def _merge(device_id: str, kind: str, new: dict) -> dict:
    """Health days and location visits accumulate; everything else is a snapshot."""
    old, _ = get(kind, device_id)
    old = old or {}
    if kind == "health":
        days = {d["date"]: d for d in old.get("days", []) if d.get("date")}
        for d in new.get("days", []):
            if d.get("date"):
                days[d["date"]] = {**days.get(d["date"], {}), **d}
        workouts = {w.get("start"): w for w in old.get("workouts", [])}
        for w in new.get("workouts", []):
            workouts[w.get("start")] = w
        return {"days": sorted(days.values(), key=lambda d: d["date"])[-KEEP_HEALTH_DAYS:],
                "workouts": sorted(workouts.values(), key=lambda w: w.get("start") or "")
                [-KEEP_WORKOUTS:],
                **{k: v for k, v in new.items() if k not in ("days", "workouts")}}
    if kind == "location":
        visits = {v.get("arrived"): v for v in old.get("visits", [])}
        for v in new.get("visits", []):
            visits[v.get("arrived")] = {**visits.get(v.get("arrived"), {}), **v}
        return {"current": new.get("current") or old.get("current"),
                "visits": sorted(visits.values(), key=lambda v: v.get("arrived") or "")
                [-KEEP_VISITS:]}
    return new


def _age(ts: float | None) -> str:
    if not ts:
        return "never synced"
    s = time.time() - ts
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return f"{int(s // 86400)} days ago"


# ---------------------------------------------------------------- live commands
def _online(device_id: str) -> bool:
    from . import nodes
    return device_id in nodes._conns


async def command(method: str, params: dict, *, write: bool, timeout: float = 30) -> dict:
    """Run ``method`` on the phone: live if it's connected, otherwise queue + wake it."""
    from . import nodes
    device_id = _device_id()
    if not device_id:
        return {"error": "No iPhone is paired yet."}
    if _online(device_id):
        return await nodes._call(device_id, method, params, timeout)
    cid = new_id("pc_")
    db.insert("iphone_queue", id=cid, device_id=device_id, method=method,
              params=json.dumps(params, ensure_ascii=False), status="pending", result=None,
              done=None)
    fut = asyncio.get_running_loop().create_future()
    _done[cid] = fut
    try:
        from .push import apns
        woke = await apns.send_silent({"dot": {"kind": "command", "id": cid}})
    except Exception as e:  # push not configured
        log.info("silent push unavailable: %s", e)
        woke = 0
    try:
        if woke:
            res = await asyncio.wait_for(fut, min(timeout, WAKE_WAIT))
            return res.get("result") if res.get("ok") else {"error": res.get("error")}
    except asyncio.TimeoutError:
        pass
    finally:
        _done.pop(cid, None)
    if write:
        return {"queued": True, "id": cid,
                "note": "The phone is asleep, so this is queued and will run the next time it "
                        "wakes (usually within minutes). Tell the human it's scheduled."}
    with db.lock:
        db.conn.execute("DELETE FROM iphone_queue WHERE id=?", (cid,))
        db.conn.commit()
    return {"error": "The iPhone is asleep/offline right now, so this can't be read live. "
                     "Use the synced data (iphone_status shows how fresh it is) or ask later."}


def _pending(device_id: str) -> list[dict]:
    rows = db.q("SELECT * FROM iphone_queue WHERE device_id=? AND status='pending' "
                "ORDER BY created", device_id)
    return [{"id": r["id"], "method": r["method"], "params": json.loads(r["params"] or "{}")}
            for r in rows]


# ---------------------------------------------------------------- API
@router.post("/api/iphone/sync")
async def sync(body: dict = Body(...)):
    device_id = str(body.get("device_id") or "")[:64]
    if not device_id:
        raise HTTPException(400, "device_id required")
    data = body.get("data") or {}
    for kind, payload in data.items():
        if isinstance(payload, dict) and kind.isidentifier():
            _put(device_id, kind, _merge(device_id, kind, payload))
    if body.get("device_name") and not db.one("SELECT id FROM devices WHERE id=?", device_id):
        db.insert("devices", id=device_id, name=str(body["device_name"])[:40],
                  os=str(body.get("os") or "iOS")[:80], capabilities="[]", approved=1,
                  last_seen=time.time())
    return {"ok": True, "synced": sorted(data), "commands": _pending(device_id)}


@router.get("/api/iphone/commands")
async def commands(device_id: str):
    return {"commands": _pending(device_id)}


@router.post("/api/iphone/commands/{cid}")
async def command_result(cid: str, body: dict = Body(...)):
    ok = bool(body.get("ok"))
    db.update("iphone_queue", cid, status="done" if ok else "error",
              result=json.dumps(body.get("result") if ok else {"error": body.get("error")},
                                ensure_ascii=False), done=time.time())
    fut = _done.get(cid)
    if fut and not fut.done():
        fut.set_result(body)
    return {"ok": True}


async def _event(type_: str, data: dict) -> dict:
    from ..connectors import ingest_event
    type_ = "".join(c for c in type_.lower() if c.isalnum() or c in "_-.")[:48] or "event"
    ev = await ingest_event(f"iphone:{type_}", type_, data)
    return {"ok": True, "event": ev["id"]}


@router.post("/api/iphone/event")
async def event(body: dict = Body(...)):
    return await _event(str(body.get("type") or "event"), body.get("data") or {})


@router.post("/hook/{token}/iphone/{type_}")
async def shortcut_hook(token: str, type_: str, req: Request):
    """For iOS Shortcuts automations: POST any JSON (or text) to this URL."""
    from ..server import _hook_token
    if not hmac.compare_digest(token, _hook_token()):
        raise HTTPException(401)
    raw = (await req.body())[:20000]
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {"text": raw.decode(errors="replace")}
    if not isinstance(data, dict):
        data = {"value": data}
    return await _event(type_, data)


@router.get("/api/iphone/status")
async def status():
    device_id = _device_id()
    if not device_id:
        return {"paired": False}
    rows = db.q("SELECT kind, updated FROM iphone_data WHERE device_id=?", device_id)
    dev = db.one("SELECT * FROM devices WHERE id=?", device_id) or {}
    return {"paired": True, "device_id": device_id, "name": dev.get("name"),
            "online": _online(device_id),
            "kinds": {r["kind"]: r["updated"] for r in rows},
            "queued": len(_pending(device_id))}


# ---------------------------------------------------------------- agent tools
S = {"type": "string"}
I = {"type": "integer"}  # noqa: E741


async def _status(ctx: Ctx) -> dict:
    st = await status()
    if not st.get("paired"):
        return {"paired": False, "note": "No iPhone paired yet."}
    return {"online": st["online"], "queued_commands": st["queued"],
            "data": {k: _age(v) for k, v in st["kinds"].items()}}


async def _health(ctx: Ctx, days: int = 7) -> dict:
    data, ts = get("health")
    if not data:
        return {"error": "No health data synced — the human can enable Health in the iOS "
                         "app's settings."}
    days = max(1, min(int(days or 7), KEEP_HEALTH_DAYS))
    return {"synced": _age(ts), "days": data.get("days", [])[-days:],
            "workouts": [w for w in data.get("workouts", [])][-10:],
            "note": "Health data is private: use it to help, never lecture."}


async def _calendar(ctx: Ctx, days_ahead: int = 7) -> dict:
    data, ts = get("calendar")
    if not data:
        return {"error": "No calendar synced from the iPhone."}
    now = time.strftime("%Y-%m-%d")
    limit = time.strftime("%Y-%m-%d", time.localtime(time.time() + 86400 * int(days_ahead or 7)))
    evs = [e for e in data.get("events", []) if (e.get("end") or e.get("start") or "")[:10] >= now
           and (e.get("start") or "")[:10] <= limit]
    return {"synced": _age(ts), "events": evs[:80]}


async def _reminders(ctx: Ctx) -> dict:
    data, ts = get("reminders")
    if not data:
        return {"error": "No reminders synced from the iPhone."}
    return {"synced": _age(ts), "open": [r for r in data.get("items", [])
                                         if not r.get("completed")][:100]}


async def _location(ctx: Ctx, live: bool = False) -> dict:
    if live:
        res = await command("location.now", {}, write=False)
        if "error" not in res:
            return {"live": True, **res}
    data, ts = get("location")
    motion, _ = get("motion")
    device, _ = get("device")
    if not data:
        return {"error": "No location synced (the human may have location sharing off)."}
    return {"synced": _age(ts), "current": data.get("current"),
            "recent_visits": data.get("visits", [])[-12:], "motion": motion,
            "device": device}


async def _add_reminder(ctx: Ctx, title: str, due: str = "", notes: str = "",
                        list: str = "") -> dict:  # noqa: A002
    return await command("reminders.add", {"title": title, "due": due, "notes": notes,
                                           "list": list}, write=True)


async def _add_event(ctx: Ctx, title: str, start: str, end: str = "", location: str = "",
                     notes: str = "", all_day: bool = False) -> dict:
    return await command("calendar.add", {"title": title, "start": start, "end": end,
                                          "location": location, "notes": notes,
                                          "all_day": all_day}, write=True)


async def _contacts(ctx: Ctx, query: str) -> dict:
    return await command("contacts.search", {"query": query}, write=False)


async def _shortcut(ctx: Ctx, name: str, input: str = "") -> dict:  # noqa: A002
    return await command("shortcuts.run", {"name": name, "input": input}, write=True)


async def _notify(ctx: Ctx, title: str, body: str = "") -> dict:
    from .push import apns
    n = await apns.send(title[:120], body[:600], category="NOTE",
                        user_info={"dot": {"kind": "note", "agent": ctx.agent["id"],
                                           "thread": ctx.thread_id}})
    return {"sent": n} if n else {"error": "Push isn't configured (APNs key) or no phone "
                                           "registered — say it in chat instead."}


register_tool("iphone_status", fn(
    "iphone_status", "What the human's iPhone shares with you and how fresh each part is "
    "(health, calendar, reminders, location, motion, device).", {}), _status,
    policy="allow", shown_when=_paired)
register_tool("iphone_health", fn(
    "iphone_health", "Daily health summaries from Apple Health: steps, sleep, resting heart "
    "rate, HRV, active energy, exercise minutes, workouts.", {"days": I}), _health,
    policy="allow", shown_when=_paired)
register_tool("iphone_calendar", fn(
    "iphone_calendar", "Upcoming events from the iPhone's calendars (all accounts).",
    {"days_ahead": I}), _calendar, policy="allow", shown_when=_paired)
register_tool("iphone_reminders", fn(
    "iphone_reminders", "Open items in the iPhone's Reminders.", {}), _reminders,
    policy="allow", shown_when=_paired)
register_tool("iphone_location", fn(
    "iphone_location", "Where the human is / has been today (places, not a live tracker), "
    "plus motion (walking, driving…) and battery. live=true asks the phone right now.",
    {"live": {"type": "boolean"}}), _location, policy="allow", shown_when=_paired)
register_tool("iphone_add_reminder", fn(
    "iphone_add_reminder", "Add a reminder to the human's iPhone Reminders. due: ISO 8601 "
    "local time, optional.", {"title": S, "due": S, "notes": S, "list": S}, ["title"]),
    _add_reminder, policy="allow", shown_when=_paired)
register_tool("iphone_add_event", fn(
    "iphone_add_event", "Add an event to the human's iPhone calendar. start/end: ISO 8601 "
    "local time.", {"title": S, "start": S, "end": S, "location": S, "notes": S,
                    "all_day": {"type": "boolean"}}, ["title", "start"]),
    _add_event, policy="ask", shown_when=_paired)
register_tool("iphone_contacts", fn(
    "iphone_contacts", "Look someone up in the human's contacts (name → phone, email, "
    "birthday). Needs the phone to be reachable.", {"query": S}, ["query"]),
    _contacts, policy="ask", shown_when=_paired)
register_tool("iphone_run_shortcut", fn(
    "iphone_run_shortcut", "Run one of the human's Apple Shortcuts by name (e.g. smart-home "
    "scenes, 'Log water'). Only shortcuts they've set up; runs when the phone is awake.",
    {"name": S, "input": S}, ["name"]), _shortcut, policy="ask", shown_when=_paired)
register_tool("iphone_notify", fn(
    "iphone_notify", "Send a push notification to the human's iPhone — for things that "
    "truly can't wait. Normal replies already notify.", {"title": S, "body": S}, ["title"]),
    _notify, policy="allow", shown_when=_paired)


def PROMPT(agent: dict) -> str | None:
    device_id = _device_id()
    if not device_id:
        return None
    rows = db.q("SELECT kind, updated FROM iphone_data WHERE device_id=?", device_id)
    if not rows:
        return None
    have = ", ".join(f"{r['kind']} ({_age(r['updated'])})" for r in rows)
    return ("# The human's iPhone\nShared with you: " + have + ". Read with iphone_* tools "
            "when it actually helps (planning around their calendar, a gentle nudge after a "
            "short night, a reminder at the right place). Be discreet — it's personal. "
            "Routines can trigger on `iphone:*` events (arrive_home, leave_work, "
            "alarm_stopped, focus_on…).")
