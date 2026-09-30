"""Background check-ins only look: read-only tools, then propose."""

from __future__ import annotations

import json

import pytest

from opendot import ext, gatekeeper, runtime, scheduler
from opendot.db import db, new_id

ext.load_all()  # the calendar / email / iphone read tools register their policies


def _agent(origin="manual"):
    return db.insert("agents", id=new_id("ag_"), name=new_id("Scout"), emoji="x", color="#fff",
                     role="r", origin=origin)["id"]


@pytest.mark.parametrize("tool,args", [
    ("web_search", {"query": "flights"}), ("web_fetch", {"url": "https://x.io"}),
    ("read_email", {"id": "1"}), ("iphone_calendar", {}),
    ("notify", {"title": "found something"}), ("offer_choices", {"options": ["Go ahead"]}),
    ("browser", {"action": "goto", "url": "https://x.io"}),
])
def test_heartbeat_may_look(tool, args):
    d, _ = gatekeeper.assess(_agent(), tool, args, source="heartbeat")
    assert d == "allow"


@pytest.mark.parametrize("tool,args", [
    ("shell", {"command": "ls"}), ("python", {"code": "print(1)"}),
    ("write_file", {"path": "a", "content": "b"}), ("send_email", {"to": "a@b.c"}),
    ("browser", {"action": "click", "text": "Next"}), ("delegate", {"tasks": []}),
    ("mcp__slack__post_message", {}), ("create_event", {}), ("watch", {}),
])
def test_heartbeat_may_not_touch(tool, args):
    d, why = gatekeeper.assess(_agent(), tool, args, source="heartbeat")
    assert d == "deny" and "propose" in why.lower()


def test_trusted_does_not_unlock_research_mode():
    aid = _agent()
    gatekeeper.set_policy(aid, "shell", "trusted")
    assert gatekeeper.assess(aid, "shell", {"command": "ls"}, source="research:deals")[0] == "deny"
    assert gatekeeper.assess(aid, "shell", {"command": "ls"}, source="chat")[0] == "allow"


async def test_heartbeat_run_blocks_writes_and_proposes(fake_llm):
    aid = _agent()
    th = db.insert("threads", id=new_id("th_"), title="Scout", kind="dm", members=[aid])
    fake_llm.push(tool_calls=[{"id": "1", "name": "shell",
                               "arguments": json.dumps({"command": "touch x"})}])
    fake_llm.push(tool_calls=[{"id": "2", "name": "offer_choices",
                               "arguments": json.dumps({"options": ["Go ahead", "Not now"]})}])
    fake_llm.push(content="Prices for your Porto trip dropped. Want me to hold a room?")
    msg = await runtime.run_agent(aid, th["id"], source="heartbeat",
                                  prompt=scheduler.HEARTBEAT_PROMPT)
    step = db.one("SELECT * FROM steps WHERE thread_id=? AND tool='shell'", th["id"])
    assert step["status"] == "error" and "only reading" in step["result"]
    assert msg["meta"]["choices"] == ["Go ahead", "Not now"]
    assert db.one("SELECT * FROM inbox WHERE agent_id=? AND kind='report'", aid)


def test_heartbeat_default_on_for_front_desk_only():
    desk, other = _agent("default"), _agent()
    assert scheduler.heartbeat_enabled(desk)
    assert not scheduler.heartbeat_enabled(other)
    assert scheduler.heartbeat_every(desk) >= 180 * 60  # every few hours
    db.kv_set(f"heartbeat:{desk}", False)  # the per-agent toggle still wins
    assert not scheduler.heartbeat_enabled(desk)
    db.kv_set(f"heartbeat:{other}", True)
    assert scheduler.heartbeat_enabled(other)
    assert scheduler.heartbeat_every(other) == 60 * 60


def test_prompt_says_propose_dont_act():
    p = scheduler.HEARTBEAT_PROMPT
    assert "Propose, don't act" in p and "offer_choices" in p and "NO_REPLY" in p
