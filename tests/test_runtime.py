"""Agent loop with a fake LLM: tool calls execute and feed back into the loop, and a
`handoff` tool call spawns a follow-up run for the target teammate."""

from __future__ import annotations

import asyncio
import json

from opendot import runtime
from opendot.db import db, new_id


def _agent(name):
    return db.insert("agents", id=new_id("ag_"), name=name, emoji="x", color="#fff",
                     role="r")["id"]


async def _drain_spawned():
    """Wait for every task started via runtime.spawn() during the test to finish."""
    await asyncio.sleep(0)
    for t in list(runtime._tasks):
        if not t.done():
            await t


async def test_agent_loop_executes_tool_call_then_replies(fake_llm):
    aid = _agent("Runner")
    th = db.insert("threads", id=new_id("th_"), title="Runner", kind="dm", members=[aid])

    fake_llm.push(tool_calls=[{"id": "1", "name": "notify",
                               "arguments": json.dumps({"title": "hello"})}])
    fake_llm.push(content="all done")

    msg = await runtime.run_agent(aid, th["id"], source="chat", prompt="do the thing")

    assert msg["content"] == "all done"
    step = db.one("SELECT * FROM steps WHERE thread_id=? AND tool='notify'", th["id"])
    assert step is not None
    assert step["status"] == "done"
    inbox_item = db.one("SELECT * FROM inbox WHERE agent_id=? AND kind='note'", aid)
    assert inbox_item is not None
    assert inbox_item["title"] == "hello"


async def test_no_reply_is_silent(fake_llm):
    aid = _agent("Quiet")
    th = db.insert("threads", id=new_id("th_"), title="Quiet", kind="dm", members=[aid])
    fake_llm.push(content="NO_REPLY")

    msg = await runtime.run_agent(aid, th["id"], source="heartbeat")
    assert msg is None
    assert db.one("SELECT * FROM messages WHERE thread_id=? AND role='agent'", th["id"]) is None


async def test_no_reply_after_a_note_is_silent(fake_llm):
    aid = _agent("Quiet")
    th = db.insert("threads", id=new_id("th_"), title="Quiet", kind="dm", members=[aid])
    fake_llm.push(content="8.9079, still above 8.7.\n\nNO_REPLY")

    assert await runtime.run_agent(aid, th["id"], source="watch:fx") is None
    assert db.one("SELECT * FROM messages WHERE thread_id=? AND role='agent'", th["id"]) is None


async def test_handoff_spawns_follow_up_run(fake_llm):
    a1 = _agent("Lead")
    a2 = _agent("Helper")
    th = db.insert("threads", id=new_id("th_"), title="Team", kind="group", members=[a1, a2])

    fake_llm.push(tool_calls=[{"id": "1", "name": "handoff",
                               "arguments": json.dumps({"agent": "Helper",
                                                        "message": "please finish this"})}])
    fake_llm.push(content="handing off to Helper")
    fake_llm.push(content="on it!")  # Helper's reply once it picks up the handoff

    await runtime.run_agent(a1, th["id"], source="chat", prompt="team, get this done")
    await _drain_spawned()

    helper_msg = db.one("SELECT * FROM messages WHERE thread_id=? AND agent_id=? "
                        "ORDER BY created DESC", th["id"], a2)
    assert helper_msg is not None
    assert helper_msg["content"] == "on it!"
    assert helper_msg["meta"]["source"] == "handoff"


async def test_handoff_to_unknown_teammate_is_reported_back(fake_llm):
    a1 = _agent("Solo")
    th = db.insert("threads", id=new_id("th_"), title="Solo", kind="group", members=[a1])
    fake_llm.push(tool_calls=[{"id": "1", "name": "handoff",
                               "arguments": json.dumps({"agent": "Ghost", "message": "hi"})}])
    fake_llm.push(content="couldn't find them")

    await runtime.run_agent(a1, th["id"], source="chat", prompt="hi")
    tool_msg = fake_llm.calls[-1]["messages"][-1]
    assert "no teammate called" in tool_msg["content"]
