"""Calendar: your events and your agents' work on one timeline, linked."""

from __future__ import annotations

import json
import time

from opendot.db import db, new_id


def _phone(events):
    db.conn.execute("INSERT OR REPLACE INTO iphone_data (device_id, kind, data, updated) "
                    "VALUES (?,?,?,?)", ("dev1", "calendar", json.dumps({"events": events}),
                                         time.time()))
    db.conn.commit()


def test_agenda_merges_phone_events_runs_and_links(client, auth_headers):
    from opendot.ext import iphone
    aid = client.get("/api/agents", headers=auth_headers).json()[0]["id"]
    th = client.get("/api/threads", headers=auth_headers).json()[0]["id"]
    now = time.time()
    start = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now + 3600))
    end = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now + 7200))
    _phone([{"id": "e1", "title": "Dentist", "start": start, "end": end,
             "calendar": "Family", "account": "iCloud"}])
    orig = iphone._device_id
    iphone._device_id = lambda: "dev1"
    try:
        db.insert("steps", id=new_id("st_"), thread_id=th, run_id="run_a", agent_id=aid,
                  tool="iphone_add_event", args={"title": "Dentist", "start": start},
                  status="done")
        db.insert("steps", id=new_id("st_"), thread_id=th, run_id="run_a",
                  agent_id=aid + "-w0", tool="web_search", args={}, status="done")
        db.insert("automations", id=new_id("au_"), agent_id=aid, name="Morning brief",
                  kind="cron", schedule="0 8 * * *", prompt="brief", enabled=1,
                  next_run=now + 600, thread_id=th, created=now)
        r = client.get(f"/api/agenda?start={now - 86400}&end={now + 86400 * 2}",
                       headers=auth_headers).json()
    finally:
        iphone._device_id = orig
    ev = next(e for e in r["events"] if e["title"] == "Dentist")
    assert ev["agents"][0] == {"agent_id": aid, "how": "created", "thread_id": th}
    cal = next(c for c in r["calendars"] if c["key"] == ev["calendar"])
    assert cal["account"] == "iCloud" and cal["name"] == "Family" and cal["source"] == "iphone"
    assert not any(i["kind"] == "run" for i in r["items"])  # chats aren't plans
    fires = [i for i in r["items"] if i["kind"] == "routine" and i["title"] == "Morning brief"]
    assert fires and all(time.localtime(i["start"]).tm_hour == 8 for i in fires)


def test_watch_checks_sit_at_their_times(client, auth_headers):
    aid = client.get("/api/agents", headers=auth_headers).json()[0]["id"]
    now = time.time()
    db.insert("automations", id="au_w6", agent_id=aid, name="Cheap flight", kind="watch",
              event_filter="< £40", prompt="book", enabled=1, every_min=360,
              next_run=now + 1800, created=now - 86400, expires=now + 5 * 86400)
    db.insert("automations", id="au_w15", agent_id=aid, name="FX", kind="watch",
              event_filter="< 8.7", prompt="tell me", enabled=1, every_min=15,
              next_run=now + 60, created=now - 86400)
    r = client.get(f"/api/agenda?start={now - 86400}&end={now + 86400}",
                   headers=auth_headers).json()
    checks = [i for i in r["items"] if i["automation_id"] == "au_w6"]
    assert all(i["kind"] == "check" for i in checks) and 7 <= len(checks) <= 9
    assert any(abs(i["start"] - (now + 1800)) < 1 for i in checks)
    fx = [i for i in r["items"] if i["automation_id"] == "au_w15"]
    assert len(fx) == 1 and fx[0]["kind"] == "watch"  # too frequent to mark each one
    db.conn.execute("DELETE FROM automations WHERE id IN ('au_w6','au_w15')")
    db.conn.commit()


def test_calendar_apps_show_on_the_calendar(client, auth_headers, monkeypatch):
    """Every calendar app is on the Calendar page; an account with several calendars
    shows each one, and one that fails says so without hiding the rest."""
    from opendot import builtin_apps
    from opendot.connectors import load_config, save_config
    now = time.time()
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))  # noqa: E731

    async def fake(name, spec, start, end):
        if name == "broken":
            raise RuntimeError("the account didn't answer")
        return [{"title": "Standup", "start": iso(now + 3600), "end": iso(now + 5400),
                 "calendar": "Work", "color": "#f00", "uid": "a"},
                {"title": "Gym", "start": iso(now + 7200), "end": iso(now + 9000),
                 "calendar": "Me", "uid": "b"}]

    monkeypatch.setattr(builtin_apps, "calendar_events", fake)
    cfg = load_config()
    cfg["mcp"] = {"google-calendar": {"builtin": "google_calendar", "app": "google-calendar",
                                      "label": "Google Calendar", "agents": "all"},
                  "broken": {"builtin": "caldav", "label": "Fastmail", "agents": "all",
                             "settings": {"url": "https://caldav.fastmail.com"}},
                  "off": {"builtin": "ics", "label": "Off", "agents": "all", "disabled": True},
                  "notion": {"url": "https://mcp.notion.com/mcp", "agents": "all"}}
    save_config(cfg)
    try:
        r = client.get(f"/api/agenda?start={now}&end={now + 86400}", headers=auth_headers).json()
    finally:
        cfg["mcp"] = {}
        save_config(cfg)
    by = {c["name"]: c for c in r["calendars"]}
    assert {"Work", "Me", "Fastmail"} <= set(by) and "Off" not in by and "notion" not in by
    assert by["Work"]["account"] == "Google" and by["Work"]["color"] == "#f00"
    assert by["Fastmail"]["error"] and by["Fastmail"]["account"] == "Fastmail"
    assert {e["title"] for e in r["events"]} >= {"Standup", "Gym"}
