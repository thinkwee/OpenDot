"""Agent calendar: tool registration, and calendar_events against a real ICS file
served by a local HTTP server (no mocking of the parsing/fetch path)."""

from __future__ import annotations

import asyncio
import datetime as dt
import http.server
import threading

import pytest

from opendot import ext as ext_mod
from opendot.db import db, new_id
from opendot.ext import calendar as calendar_mod
from opendot.gatekeeper import DEFAULT_POLICY
from opendot.tools import EXEC, TOOLS, Ctx

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


def test_calendar_tools_registered():
    for name in ("calendar_events", "create_event"):
        assert name in TOOLS
        assert name in EXEC
    assert DEFAULT_POLICY["calendar_events"] == "allow"
    assert DEFAULT_POLICY["create_event"] == "ask"


def test_calendar_ext_loaded():
    ext_mod.load_all()
    assert any(m.__name__ == "opendot.ext.calendar" for m in ext_mod.MODULES)


@pytest.fixture(scope="module")
def ics_server(tmp_path_factory):
    d = tmp_path_factory.mktemp("ics")
    now = dt.datetime.now(dt.timezone.utc)
    fmt = "%Y%m%dT%H%M%SZ"
    body = SAMPLE_ICS.format(
        start=(now + dt.timedelta(hours=1)).strftime(fmt),
        end=(now + dt.timedelta(hours=2)).strftime(fmt),
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


def test_calendar_events_missing_config_errors():
    ag = _agent("NoConfig")

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["calendar_events"](ctx)

    result = asyncio.run(run())
    assert "error" in result


def test_calendar_events_reads_real_ics_over_http(ics_server):
    ag = _agent("Scout")
    calendar_mod.set_config(ag["id"], {"ics_feeds": [{"name": "work", "url": ics_server}],
                                       "reminder_minutes": 30})

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["calendar_events"](ctx)

    result = asyncio.run(run())
    assert "events" in result
    titles = [e["title"] for e in result["events"]]
    assert "Team standup" in titles
    assert "Offsite planning" in titles
    standup = next(e for e in result["events"] if e["title"] == "Team standup")
    assert standup["location"] == "Zoom"
    assert standup["feed"] == "work"


def test_calendar_events_date_window_excludes_far_future(ics_server):
    ag = _agent("Pixel")
    calendar_mod.set_config(ag["id"], {"ics_feeds": [{"name": "work", "url": ics_server}]})

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        now = calendar_mod.memory.now_local()
        return await EXEC["calendar_events"](ctx, start=now.isoformat(),
                                             end=(now + dt.timedelta(hours=6)).isoformat())

    result = asyncio.run(run())
    titles = [e["title"] for e in result["events"]]
    assert "Team standup" in titles
    assert "Offsite planning" not in titles  # 3 days out, outside the 6h window


def test_create_event_without_caldav_errors():
    ag = _agent("Quill")

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["create_event"](ctx, title="x", start="2026-01-01T10:00:00",
                                          end="2026-01-01T11:00:00")

    result = asyncio.run(run())
    assert "error" in result
