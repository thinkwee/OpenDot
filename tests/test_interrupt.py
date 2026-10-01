"""Stop cuts a run off right away, and a message sent mid-run reaches it at its next
step (instead of waiting for the run to finish), like typing into a live session."""

from __future__ import annotations

import asyncio

from conftest import FakeReply
from opendot import runtime
from opendot.db import db, new_id


def _chat():
    ag = db.insert("agents", id=new_id("ag_"), name=new_id("Busy"), emoji="x", color="#fff",
                   role="r")
    th = db.insert("threads", id=new_id("th_"), title="t", kind="dm", members=[ag["id"]])
    return ag, th


async def _until(cond, timeout=3.0):
    for _ in range(int(timeout / 0.01)):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


def _live_in(tid):
    return [r for r in runtime._live.values() if r["thread_id"] == tid and "inbox" in r]


async def test_stop_cuts_in_mid_reply(fake_llm, monkeypatch):
    async def forever(messages, tools=None, max_tokens=None):
        await asyncio.sleep(3600)
    monkeypatch.setattr(fake_llm, "chat", forever)
    ag, th = _chat()
    run = asyncio.ensure_future(runtime.run_agent(ag["id"], th["id"], "chat", prompt="plan it"))
    await _until(lambda: _live_in(th["id"]))
    runtime.stop_thread(th["id"])
    await asyncio.wait_for(run, 2)  # stopped at once, not after the model call ends
    assert not _live_in(th["id"])
    assert not db.q("SELECT id FROM messages WHERE thread_id=? AND role='agent'", th["id"])


async def test_message_mid_run_joins_it(fake_llm, monkeypatch):
    go = asyncio.Event()
    seen = []

    async def chat(messages, tools=None, max_tokens=None):
        seen.append(messages[-1]["content"])
        if len(seen) == 1:
            await go.wait()
            return FakeReply(tool_calls=[{"id": "c1", "name": "remember",
                                          "arguments": '{"fact": "likes aisle seats"}'}])
        return FakeReply(content="Switched to Lisbon.")
    monkeypatch.setattr(fake_llm, "chat", chat)
    ag, th = _chat()
    run = asyncio.ensure_future(runtime.run_agent(ag["id"], th["id"], "chat", prompt="find Porto flights"))
    await _until(lambda: _live_in(th["id"]))
    jobs = db.one("SELECT count(*) n FROM jobs")["n"]
    await runtime.on_user_message(th["id"], "actually make it Lisbon")
    assert db.one("SELECT count(*) n FROM jobs")["n"] == jobs  # no new turn queued behind it
    go.set()
    await asyncio.wait_for(run, 3)
    assert runtime.BTW in seen[1] and "Lisbon" in seen[1]  # the very next step heard it
    replies = db.q("SELECT content FROM messages WHERE thread_id=? AND role='agent'", th["id"])
    assert [r["content"] for r in replies] == ["Switched to Lisbon."]


async def test_message_as_it_finishes_gets_its_own_turn(fake_llm, monkeypatch):
    go = asyncio.Event()
    calls = []

    async def chat(messages, tools=None, max_tokens=None):
        calls.append(messages)
        if len(calls) == 1:
            await go.wait()
            return FakeReply(content="Done with Porto.")
        return FakeReply(content="And Lisbon too.")
    monkeypatch.setattr(fake_llm, "chat", chat)
    ag, th = _chat()
    run = asyncio.ensure_future(runtime.run_agent(ag["id"], th["id"], "chat", prompt="Porto"))
    await _until(lambda: _live_in(th["id"]))
    await runtime.on_user_message(th["id"], "and Lisbon?")
    go.set()  # it answers without another step, so the message is still unread
    await asyncio.wait_for(run, 3)
    await _until(lambda: len(db.q("SELECT id FROM messages WHERE thread_id=? AND role='agent'",
                                  th["id"])) == 2)
    assert any("and Lisbon?" in str(m.get("content")) for m in calls[1])


async def test_stop_closes_an_open_question_without_a_no(fake_llm, monkeypatch):
    ag, th = _chat()
    ap = db.insert("approvals", id=new_id("ap_"), agent_id=ag["id"], thread_id=th["id"],
                   tool="send_email", args={}, reason="", status="pending")
    runtime.stop_thread(th["id"])
    assert db.one("SELECT status FROM approvals WHERE id=?", ap["id"])["status"] == "expired"
