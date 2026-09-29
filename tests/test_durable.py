"""A restart never loses work: jobs are resumed, approvals survive, side effects run once.

A "restart" here = cancel every running task (like the process dying), forget all
in-memory state, then call the start-up resume directly."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from opendot import durable, gatekeeper, runtime, tools
from opendot.db import db, new_id


@pytest.fixture
def fake_tools(monkeypatch):
    """post_thing: an outside-world action (counts its calls). stall: never finishes."""
    calls = {"post_thing": 0, "send_email": 0}
    hang = {"post_thing": False}

    async def post_thing(ctx, text=""):
        calls["post_thing"] += 1
        if hang["post_thing"]:
            await asyncio.Event().wait()
        return {"posted": text}

    async def send_email(ctx, **kw):
        calls["send_email"] += 1
        return {"sent": True}

    async def stall(ctx, **kw):
        await asyncio.Event().wait()

    monkeypatch.setitem(tools.EXEC, "post_thing", post_thing)
    monkeypatch.setitem(tools.EXEC, "send_email", send_email)
    monkeypatch.setitem(tools.EXEC, "stall", stall)
    monkeypatch.setitem(gatekeeper.DEFAULT_POLICY, "post_thing", "allow")
    monkeypatch.setitem(gatekeeper.DEFAULT_POLICY, "stall", "allow")
    return calls, hang


def _call(name, **args):
    return {"id": new_id("c"), "name": name, "arguments": json.dumps(args)}


def _setup():
    aid = db.insert("agents", id=new_id("ag_"), name="Keeper", emoji="x", color="#fff",
                    role="r")["id"]
    th = db.insert("threads", id=new_id("th_"), title="Keeper", kind="dm", members=[aid])
    return aid, th["id"]


async def _until(check, timeout=3.0):
    end = time.time() + timeout
    while not check():
        assert time.time() < end, "timed out"
        await asyncio.sleep(0.01)


async def _restart():
    """The process dies: tasks cancelled, in-memory state gone."""
    for t in list(runtime._tasks):
        t.cancel()
    await asyncio.sleep(0.05)
    runtime._live.clear()
    runtime._locks.clear()
    gatekeeper._waiters.clear()


async def _resume():
    for t in runtime.resume_jobs():
        await t


def _tool_results(fake_llm):
    return [m["content"] for m in fake_llm.calls[-1]["messages"] if m["role"] == "tool"]


async def test_chat_message_job_resumed_after_restart(fake_llm, fake_tools):
    aid, tid = _setup()
    fake_llm.push(tool_calls=[_call("stall")])
    await runtime.on_user_message(tid, "plan my trip")
    job = db.one("SELECT * FROM jobs WHERE thread_id=?", tid)
    assert job and job["kind"] == "reply"  # saved together with the message
    await _until(lambda: db.one("SELECT id FROM steps WHERE tool='stall' AND status='running'"))
    await _restart()
    assert db.one("SELECT status FROM jobs WHERE id=?", job["id"])["status"] == "running"

    fake_llm.push(content="here's your plan")
    await _resume()

    job = db.one("SELECT * FROM jobs WHERE id=?", job["id"])
    assert job["status"] == "done" and job["attempts"] == 2
    said = [m["content"] for m in db.q("SELECT content FROM messages WHERE thread_id=? AND "
                                       "role='agent' ORDER BY created", tid)]
    assert said == ["(picked up again after a restart)", "here's your plan"]
    # the interrupted step is no ghost, and the agent was told about it
    assert db.one("SELECT status FROM steps WHERE tool='stall'")["status"] == "error"
    prompt = fake_llm.calls[-1]["messages"][-1]["content"]
    assert "restarted" in prompt and "stall" in prompt
    assert db.one("SELECT status FROM agents WHERE id=?", aid)["status"] == "idle"


async def test_old_or_retried_jobs_are_not_resumed(fake_llm):
    aid, tid = _setup()
    old = durable.new_job(aid, tid)
    db.update("jobs", old["id"], status="running", created=time.time() - 7 * 3600)
    tried = durable.new_job(aid, tid)
    db.update("jobs", tried["id"], status="running", attempts=2)
    assert durable.recover() == []
    for j in (old, tried):
        assert db.one("SELECT status FROM jobs WHERE id=?", j["id"])["status"] == "failed"


async def test_approval_survives_restart_and_is_honoured(fake_llm, fake_tools):
    calls, _ = fake_tools
    aid, tid = _setup()
    email = dict(to="a@b.c", subject="Hi", body="yo")
    fake_llm.push(tool_calls=[_call("send_email", **email)])
    await runtime.on_user_message(tid, "email them")
    await _until(lambda: db.one("SELECT id FROM approvals WHERE status='pending'"))
    await _restart()

    stale = durable.recover()
    ap = db.one("SELECT * FROM approvals")
    assert ap["status"] == "pending"  # still on the approval card after the restart
    gatekeeper.decide(ap["id"], True)  # answered while nothing was waiting

    fake_llm.push(tool_calls=[_call("send_email", **email)])
    fake_llm.push(content="sent")
    for t in runtime.resume_jobs(stale):
        await t
    assert calls["send_email"] == 1
    assert db.one("SELECT count(*) n FROM approvals")["n"] == 1  # not asked again
    assert db.one("SELECT content FROM messages WHERE role='agent' ORDER BY created DESC "
                  "LIMIT 1")["content"] == "sent"


async def test_resumed_run_waits_on_the_same_approval(fake_llm, fake_tools):
    calls, _ = fake_tools
    aid, tid = _setup()
    fake_llm.push(tool_calls=[_call("send_email", to="x@y.z")])
    await runtime.on_user_message(tid, "email x")
    await _until(lambda: db.one("SELECT id FROM approvals WHERE status='pending'"))
    await _restart()

    fake_llm.push(tool_calls=[_call("send_email", to="x@y.z")])
    fake_llm.push(content="done")
    tasks = runtime.resume_jobs()
    await _until(lambda: gatekeeper._waiters)
    ap = db.one("SELECT * FROM approvals")
    gatekeeper.decide(ap["id"], True)
    for t in tasks:
        await t
    assert calls["send_email"] == 1
    assert db.one("SELECT count(*) n FROM approvals")["n"] == 1


async def test_side_effect_runs_once_across_a_resume(fake_llm, fake_tools):
    calls, _ = fake_tools
    aid, tid = _setup()
    fake_llm.push(tool_calls=[_call("post_thing", text="hello"), _call("stall")])
    await runtime.on_user_message(tid, "post it")
    await _until(lambda: db.one("SELECT id FROM steps WHERE tool='stall' AND status='running'"))
    await _restart()

    fake_llm.push(tool_calls=[_call("post_thing", text="hello")])
    fake_llm.push(content="posted")
    await _resume()
    assert calls["post_thing"] == 1
    out = _tool_results(fake_llm)[-1]
    assert "already done before the restart" in out and "hello" in out


async def test_crash_mid_call_is_not_repeated_blindly(fake_llm, fake_tools):
    calls, hang = fake_tools
    hang["post_thing"] = True
    aid, tid = _setup()
    fake_llm.push(tool_calls=[_call("post_thing", text="hi")])
    await runtime.on_user_message(tid, "post it")
    await _until(lambda: calls["post_thing"] == 1)
    await _restart()
    hang["post_thing"] = False

    fake_llm.push(tool_calls=[_call("post_thing", text="hi")])
    fake_llm.push(content="checking first")
    await _resume()
    assert calls["post_thing"] == 1
    assert "may already have happened" in _tool_results(fake_llm)[-1]
