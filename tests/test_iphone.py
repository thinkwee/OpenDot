"""iPhone node: sync + merge, freshness, tools on cached data, events, command queue."""

from __future__ import annotations

import asyncio

from opendot import ext as ext_mod
from opendot.db import db
from opendot.tools import Ctx

ext_mod.load_all()
from opendot.ext import iphone as P  # noqa: E402

CTX = Ctx(agent={"id": "ag_x", "name": "Pip"}, thread_id="th_x", run_id="run_x")


def _clean():
    with db.lock:
        db.conn.execute("DELETE FROM iphone_data")
        db.conn.execute("DELETE FROM iphone_queue")
        db.conn.commit()


def test_sync_merges_health_and_visits(client, auth_headers):
    _clean()
    body = {"device_id": "iph1", "device_name": "iPhone", "data": {
        "health": {"days": [{"date": "2026-09-27", "steps": 8000}, {"date": "2026-09-28", "steps": 300}]},
        "location": {"current": {"place": "Home"}, "visits": [{"place": "Home", "arrived": "2026-09-28T08:00"}]},
        "calendar": {"events": [{"title": "Standup", "start": "2099-01-01T09:00", "end": "2099-01-01T09:15"}]},
    }}
    r = client.post("/api/iphone/sync", json=body, headers=auth_headers)
    assert r.status_code == 200 and r.json()["commands"] == []
    body2 = {"device_id": "iph1", "data": {
        "health": {"days": [{"date": "2026-09-28", "steps": 5200, "sleep_h": 6.5}]},
        "location": {"visits": [{"place": "Library", "arrived": "2026-09-28T10:00"}]}}}
    client.post("/api/iphone/sync", json=body2, headers=auth_headers)
    h, _ = P.get("health")
    assert [d["steps"] for d in h["days"]] == [8000, 5200] and h["days"][1]["sleep_h"] == 6.5
    loc, _ = P.get("location")
    assert loc["current"]["place"] == "Home" and len(loc["visits"]) == 2
    st = client.get("/api/iphone/status", headers=auth_headers).json()
    assert st["paired"] and set(st["kinds"]) == {"health", "location", "calendar"}
    assert asyncio.run(P._calendar(CTX, 30000))["events"][0]["title"] == "Standup"
    assert "days" in asyncio.run(P._health(CTX, 1)) and "iPhone" in P.PROMPT({})


def test_write_is_queued_when_phone_asleep_then_drained(client, auth_headers):
    _clean()
    client.post("/api/iphone/sync", json={"device_id": "iph2", "data": {"device": {"battery": .5}}},
                headers=auth_headers)
    res = asyncio.run(P._add_reminder(CTX, "Buy milk", due="2026-09-29T09:00"))
    assert res["queued"]
    cmds = client.post("/api/iphone/sync", json={"device_id": "iph2", "data": {}},
                       headers=auth_headers).json()["commands"]
    assert cmds[0]["method"] == "reminders.add" and cmds[0]["params"]["title"] == "Buy milk"
    client.post(f"/api/iphone/commands/{cmds[0]['id']}", json={"ok": True, "result": {"id": "R1"}},
                headers=auth_headers)
    assert client.get("/api/iphone/commands?device_id=iph2", headers=auth_headers).json()["commands"] == []
    # reads don't linger in the queue
    assert "error" in asyncio.run(P._contacts(CTX, "Ana"))
    assert client.get("/api/iphone/commands?device_id=iph2", headers=auth_headers).json()["commands"] == []


def test_events_and_shortcut_hook(client, auth_headers):
    from opendot.server import _hook_token
    r = client.post("/api/iphone/event", json={"type": "Arrive_Home!", "data": {"place": "Home"}},
                    headers=auth_headers)
    ev = db.one("SELECT * FROM events WHERE id=?", r.json()["event"])
    assert ev["source"] == "iphone:arrive_home"
    r = client.post(f"/hook/{_hook_token()}/iphone/alarm_stopped", content=b"good morning")
    assert r.status_code == 200
    ev = db.one("SELECT * FROM events WHERE id=?", r.json()["event"])
    assert ev["payload"]["text"] == "good morning"
    assert client.post("/hook/wrong/iphone/x", content=b"{}").status_code == 401
