"""Calendars are apps: a calendar link read over real HTTP (no mocking of the fetch or
parsing), reminders before events, and older calendar settings moved into Apps."""

from __future__ import annotations

import asyncio
import datetime as dt
import http.server
import threading

import pytest

from opendot import ext as ext_mod
from opendot import gatekeeper
from opendot.connectors import MCPHub, load_config, save_config
from opendot.db import db, new_id
from opendot.ext import agenda
from opendot.ext import calendar as calendar_mod
from opendot.tools import TOOLS

SAMPLE_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//OpenDot//test//EN
BEGIN:VEVENT
UID:standup-1@opendot.test
DTSTART:{start}
DTEND:{end}
SUMMARY:Team standup
LOCATION:Zoom
DESCRIPTION:Daily sync
END:VEVENT
BEGIN:VEVENT
UID:offsite-1@opendot.test
DTSTART:{start2}
DTEND:{end2}
SUMMARY:Offsite planning
LOCATION:HQ
END:VEVENT
END:VCALENDAR
"""


def _agent(name="Scout"):
    return db.insert("agents", id=new_id("ag_"), name=name, emoji="x", color="#fff", role="r")


def _clear_apps():
    cfg = load_config()
    cfg["mcp"] = {}
    save_config(cfg)


def test_calendar_ext_loaded_and_old_tools_gone():
    ext_mod.load_all()
    assert any(m.__name__ == "opendot.ext.calendar" for m in ext_mod.MODULES)
    # one way to use a calendar: its app's tools (no second, per-agent set)
    assert "calendar_events" not in TOOLS and "create_event" not in TOOLS


@pytest.fixture(scope="module")
def ics_server(tmp_path_factory):
    d = tmp_path_factory.mktemp("ics")
    now = dt.datetime.now(dt.timezone.utc)
    fmt = "%Y%m%dT%H%M%SZ"
    body = SAMPLE_ICS.format(
        start=(now + dt.timedelta(minutes=20)).strftime(fmt),
        end=(now + dt.timedelta(hours=1)).strftime(fmt),
        start2=(now + dt.timedelta(days=3)).strftime(fmt),
        end2=(now + dt.timedelta(days=3, hours=1)).strftime(fmt))
    (d / "basic.ics").write_text(body)

    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(d), **kw)
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}/basic.ics"
    server.shutdown()


def test_calendar_link_app_reads_real_ics(ics_server):
    from opendot.ext.apps import install
    agenda._ics_cache.clear()
    name = install("calendar-link", {"url": ics_server}, "all", label="Work")

    async def run():
        hub = MCPHub()
        srv = hub.launch(name, load_config()["mcp"][name])
        await asyncio.wait_for(srv.ready.wait(), 10)
        assert srv.status == "connected", srv.error
        week = await hub.call(f"mcp__{name}__list_events", {})
        now = calendar_mod.memory.now_local()
        soon = await hub.call(f"mcp__{name}__list_events", {
            "start": now.isoformat(), "end": (now + dt.timedelta(hours=6)).isoformat()})
        await hub.stop_all()
        return week, soon

    week, soon = asyncio.run(run())
    assert "Team standup @ Zoom" in week["result"] and "Offsite planning" in week["result"]
    assert "Offsite planning" not in soon["result"]  # 3 days out, outside the 6h window
    _clear_apps()


def test_reminder_before_an_event(ics_server):
    from opendot.ext.apps import install
    agenda._ics_cache.clear()
    install("calendar-link", {"url": ics_server}, "all", label="Work")
    db.kv_set(calendar_mod.NOTIFIED, [])
    fired = []

    async def fake_ingest(source, kind, payload):
        fired.append((source, payload["title"]))

    import opendot.connectors as conn
    orig = conn.ingest_event
    conn.ingest_event = fake_ingest
    try:
        asyncio.run(calendar_mod._tick())
        asyncio.run(calendar_mod._tick())  # once per event
    finally:
        conn.ingest_event = orig
    assert fired == [("calendar:Work", "Team standup")]
    notes = db.q("SELECT * FROM inbox WHERE kind='note' AND title LIKE '%Team standup%'")
    assert len(notes) == 1 and "Zoom" in notes[0]["body"]
    _clear_apps()


def test_old_calendar_settings_move_into_apps():
    scout = _agent("Scout")
    gatekeeper.vault_set("OLD_CALDAV_PW", "abcd-efgh")
    db.kv_set("calendars:mine", [{"name": "Family", "url": "https://p01.icloud.com/x.ics"},
                                 {"name": "Off", "url": "https://x.test/off.ics", "on": False}])
    db.kv_set(f"identity:calendar:{scout['id']}", {
        "ics_feeds": [{"name": "Team", "url": "https://calendar.google.com/y/basic.ics"}],
        "caldav": {"url": "https://caldav.icloud.com", "user": "me@icloud.com",
                   "password": "{{vault:OLD_CALDAV_PW}}"},
        "reminder_minutes": 15})
    db.kv_set(calendar_mod.MOVED, None)
    added = calendar_mod.move_old_calendars()
    apps = load_config()["mcp"]
    assert len(added) == 4
    fam = next(a for a in apps.values() if a.get("label") == "Family")
    assert fam["builtin"] == "ics" and fam["agents"] == "all"
    assert fam["settings"]["url"].startswith("{{vault:")  # a private link is a secret
    assert next(a for a in apps.values() if a.get("label") == "Off")["disabled"]
    team = next(a for a in apps.values() if a.get("label") == "Team")
    assert team["agents"] == [scout["id"]]
    dav = next(a for a in apps.values() if a.get("builtin") == "caldav")
    assert dav["app"] == "icloud-calendar" and dav["agents"] == [scout["id"]]
    assert gatekeeper.inject_secrets(dav["settings"])["password"] == "abcd-efgh"
    assert db.kv_get(calendar_mod.REMIND) == 15
    assert db.kv_get("calendars:mine") is None
    assert db.kv_get(f"identity:calendar:{scout['id']}") is None
    assert calendar_mod.move_old_calendars() == []  # once
    _clear_apps()
