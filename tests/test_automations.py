"""Automations: cron next_run computation, the REST CRUD, and event routines triggered
by an inbound webhook."""

from __future__ import annotations

import asyncio
import time

from opendot import runtime
from opendot.connectors import ingest_event
from opendot.db import db, new_id
from opendot.scheduler import next_cron


def test_next_cron_is_in_the_future():
    nxt = next_cron("0 8 * * *")
    assert nxt > time.time()


def test_next_cron_after_reference_time():
    ref = 1_700_000_000.0
    nxt = next_cron("*/5 * * * *", after=ref)
    assert nxt > ref
    assert nxt - ref <= 5 * 60


def test_create_automation_api_sets_next_run(client, auth_headers):
    aid = db.insert("agents", id=new_id("ag_"), name="Auto", emoji="x", color="#fff",
                    role="r")["id"]
    r = client.post("/api/automations", headers=auth_headers,
                    json={"agent_id": aid, "kind": "cron", "schedule": "0 9 * * *",
                          "prompt": "say hi", "name": "daily"})
    assert r.status_code == 200
    body = r.json()
    assert body["next_run"] is not None
    assert body["next_run"] > time.time()


def test_patch_automation_recomputes_next_run(client, auth_headers):
    aid = db.insert("agents", id=new_id("ag_"), name="Auto2", emoji="x", color="#fff",
                    role="r")["id"]
    made = client.post("/api/automations", headers=auth_headers,
                       json={"agent_id": aid, "kind": "cron", "schedule": "0 9 * * *",
                             "prompt": "say hi", "name": "daily"}).json()
    old_next = made["next_run"]
    updated = client.patch(f"/api/automations/{made['id']}", headers=auth_headers,
                           json={"schedule": "0 10 * * *"}).json()
    assert updated["next_run"] != old_next


async def _drain_spawned():
    await asyncio.sleep(0)
    for t in list(runtime._tasks):
        if not t.done():
            await t


async def test_webhook_event_triggers_matching_routine(fake_llm):
    aid = db.insert("agents", id=new_id("ag_"), name="Hooked", emoji="x", color="#fff",
                    role="r")["id"]
    th = db.insert("threads", id=new_id("th_"), title="Hooked", kind="dm", members=[aid])
    db.insert("automations", id=new_id("au_"), agent_id=aid, name="on-github",
              kind="event", event_filter="webhook:github", prompt="tell me about it",
              enabled=1, thread_id=th["id"])
    fake_llm.push(content="saw the github event")

    await ingest_event("webhook:github", "push", {"ref": "refs/heads/main"})
    await _drain_spawned()

    msg = db.one("SELECT * FROM messages WHERE thread_id=? AND role='agent'", th["id"])
    assert msg is not None
    assert msg["content"] == "saw the github event"


async def test_webhook_event_does_not_trigger_non_matching_routine(fake_llm):
    aid = db.insert("agents", id=new_id("ag_"), name="Hooked2", emoji="x", color="#fff",
                    role="r")["id"]
    th = db.insert("threads", id=new_id("th_"), title="Hooked2", kind="dm", members=[aid])
    db.insert("automations", id=new_id("au_"), agent_id=aid, name="on-rss",
              kind="event", event_filter="rss:*", prompt="tell me", enabled=1,
              thread_id=th["id"])

    await ingest_event("webhook:github", "push", {})
    await _drain_spawned()

    assert db.one("SELECT * FROM messages WHERE thread_id=? AND role='agent'", th["id"]) is None


def test_hook_endpoint_requires_correct_token(client, auth_headers):
    hook_token = client.get("/api/bootstrap", headers=auth_headers).json()["hook_token"]
    ok = client.post(f"/hook/{hook_token}/manual", json={"x": 1})
    assert ok.status_code == 200
    bad = client.post("/hook/wrong-token/manual", json={"x": 1})
    assert bad.status_code == 401


async def test_once_reminder_fires_one_time_then_stops(monkeypatch):
    """kind='once': a reminder at one local time; it fires, then switches itself off
    (a date written as cron would come back every year)."""
    from opendot import scheduler, tools
    aid = db.insert("agents", id=new_id("ag_"), name="Once", emoji="x", color="#fff",
                    role="r")["id"]
    ctx = tools.Ctx(agent={"id": aid}, thread_id=None, run_id="run_x")
    soon = time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + 3600))
    made = await tools._schedule(ctx, name="GP", kind="once", schedule=soon, prompt="call the GP")
    assert made["ok"] and abs(made["next_run"] - (time.time() + 3600)) < 61
    gone = await tools._schedule(ctx, name="late", kind="once", schedule="2020-01-01T09:00",
                                 prompt="x")
    assert "already passed" in gone["error"]
    db.update("automations", made["id"], next_run=time.time() - 1)
    fired = []

    async def fake_run(agent_id, thread_id, source, prompt):
        fired.append(source)
    monkeypatch.setattr(runtime, "run_agent", fake_run)
    loop = asyncio.create_task(scheduler.scheduler_loop())
    await asyncio.sleep(0.3)
    loop.cancel()
    await _drain_spawned()
    a = db.one("SELECT * FROM automations WHERE id=?", made["id"])
    assert fired == ["automation:GP"] and not a["enabled"] and a["next_run"] is None


def test_agent_sees_the_reminders_it_really_has():
    from opendot.ext import routines_api
    aid = db.insert("agents", id=new_id("ag_"), name="Remind", emoji="x", color="#fff",
                    role="r")["id"]
    assert "None are on" in routines_api.PROMPT({"id": aid})
    db.insert("automations", id="au_real", agent_id=aid, name="print letters", kind="once",
              schedule="2030-01-01T09:00", prompt="p", enabled=1, next_run=time.time() + 3600)
    db.insert("automations", id="au_off", agent_id=aid, name="old one", kind="once",
              schedule="2020-01-01T09:00", prompt="p", enabled=0, next_run=None)
    text = routines_api.PROMPT({"id": aid})
    assert "print letters (id au_real)" in text and "once" in text
    assert "old one" not in text  # what's on right now, not what's done


def test_journal_says_what_a_turn_really_did():
    run = new_id("run_")
    assert runtime._did_line(run) == "called no tools"
    for st in ("error", "done"):
        db.insert("steps", id=new_id("st_"), run_id=run, tool="schedule", args="{}",
                  result="{}", status=st, created=time.time())
    db.insert("steps", id=new_id("st_"), run_id=run, tool="mcp__icloud-calendar__list_events",
              args="{}", result="{}", status="error", created=time.time())
    assert runtime._did_line(run) == "called schedule ok, list_events error"
