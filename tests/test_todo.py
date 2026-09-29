"""Agent Todo: every job an agent takes on, its checklist, and how it ended."""

from __future__ import annotations

import asyncio
import json

from opendot.db import db


def _dot(client, h):
    dot = client.get("/api/agents", headers=h).json()[0]
    tid = next(t["id"] for t in client.get("/api/threads", headers=h).json()
               if dot["id"] in t["members"])
    return dot, tid


def test_a_job_becomes_a_task_with_a_checklist(client, fake_llm, auth_headers):
    from opendot.runtime import on_user_message, run_agent
    dot, tid = _dot(client, auth_headers)
    import opendot.runtime as rt
    orig = rt.spawn
    rt.spawn = lambda coro: coro.close()
    try:
        asyncio.run(on_user_message(tid, "Plan a weekend in Budapest"))
    finally:
        rt.spawn = orig
    plan = [{"text": "Find flights", "status": "done"}, {"text": "Pick a hotel", "status": "doing"},
            {"text": "Draft the plan"}]
    fake_llm.push(tool_calls=[{"id": "c1", "name": "todo", "arguments": json.dumps(
        {"title": "Budapest weekend", "items": plan})}])
    fake_llm.push(tool_calls=[{"id": "c2", "name": "list_files", "arguments": "{}"}])
    fake_llm.push(content="Here's your weekend plan.")
    asyncio.run(run_agent(dot["id"], tid))
    tasks = [t for t in client.get("/api/todo", headers=auth_headers).json()["tasks"]
             if t["kind"] == "task"]
    assert len(tasks) == 1
    t = tasks[0]
    assert t["title"] == "Budapest weekend" and t["status"] == "done"
    assert t["progress"] == {"done": 2, "total": 3}  # "doing" finished with the run
    assert t["result"].startswith("Here's your weekend plan")


def test_quick_answers_are_not_tasks(client, fake_llm, auth_headers):
    from opendot.runtime import run_agent
    dot, tid = _dot(client, auth_headers)
    fake_llm.push(content="Hi!")
    asyncio.run(run_agent(dot["id"], tid))
    assert not db.q("SELECT * FROM tasks")


def test_chips_wait_on_you_until_you_answer(client, fake_llm, auth_headers):
    from opendot.runtime import on_user_message, run_agent
    dot, tid = _dot(client, auth_headers)
    db.insert("messages", id="m_u1", thread_id=tid, role="user", content="Find me a tennis court",
              meta={})
    fake_llm.push(tool_calls=[{"id": "c1", "name": "list_files", "arguments": "{}"}])
    fake_llm.push(tool_calls=[{"id": "c2", "name": "offer_choices",
                               "arguments": json.dumps({"options": ["Hold it", "Not now"]})}])
    fake_llm.push(content="Court 3 is free at 9.")
    asyncio.run(run_agent(dot["id"], tid))
    assert db.one("SELECT status FROM tasks")["status"] == "waiting"
    import opendot.runtime as rt
    orig = rt.spawn
    rt.spawn = lambda coro: coro.close()
    try:
        asyncio.run(on_user_message(tid, "Hold it"))
    finally:
        rt.spawn = orig
    assert db.one("SELECT status FROM tasks")["status"] == "done"


def test_watches_show_as_ongoing(client, auth_headers):
    aid = client.get("/api/agents", headers=auth_headers).json()[0]["id"]
    db.insert("automations", id="au_x", agent_id=aid, name="Cheap flight", kind="watch",
              event_filter="< £40", prompt="book", enabled=1, every_min=360, checks=7)
    w = next(t for t in client.get("/api/todo", headers=auth_headers).json()["tasks"]
             if t["id"] == "au_x")
    assert w["kind"] == "watch" and w["status"] == "doing" and w["checks"] == 7
