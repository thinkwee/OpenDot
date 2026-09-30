"""Keeping the model's context lean: old tool results are cleared in one go past the
budget, a run that's still too big is compacted into a note, and a long chat is its
recent messages plus a rolling summary."""

from __future__ import annotations

import asyncio
import json
import time

from opendot import context, runtime
from opendot.db import db, new_id
from opendot.llm import LLMReply


def _run_messages(n_results: int, size: int) -> list[dict]:
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "find flights"}]
    for i in range(n_results):
        msgs.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "web_fetch", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"result {i} " + "x" * size})
    return msgs


class FakeLLM:
    route = "openai/gpt-4o"

    def __init__(self):
        self.asked = []

    async def chat(self, messages, tools=None, max_tokens=None):
        self.asked.append(messages)
        return LLMReply(content="- Task: find flights\n- Found: LIS £47 on 3 Oct")


def test_old_tool_results_are_cleared_all_at_once(monkeypatch):
    msgs = _run_messages(6, 40_000)
    monkeypatch.setattr(context.settings, "CONTEXT_TRIGGER", 30_000)
    cli = FakeLLM()
    asyncio.run(context.fit(msgs, [], cli))
    results = [m["content"] for m in msgs if m["role"] == "tool"]
    assert all(r.startswith(context.CLEARED) for r in results[:3])  # older ones, in one go
    assert all(r.startswith("result") for r in results[3:])  # the last three stay whole
    assert "web_fetch returned" in results[0] and not cli.asked  # clearing was enough
    before = json.dumps(msgs)
    asyncio.run(context.fit(msgs, [], cli))
    assert json.dumps(msgs) == before  # under budget now: nothing changes (cache stays warm)


def test_a_run_still_too_big_is_compacted(monkeypatch):
    msgs = _run_messages(8, 30_000)
    monkeypatch.setattr(context.settings, "CONTEXT_TRIGGER", 10_000)
    cli = FakeLLM()
    asyncio.run(context.fit(msgs, [], cli))
    assert cli.asked and "compressing" in cli.asked[0][0]["content"]
    assert msgs[0]["content"] == "sys" and "Summary of the work so far" in msgs[1]["content"]
    assert "LIS £47" in msgs[1]["content"]
    # every tool result left still has its call right before it
    for i, m in enumerate(msgs):
        if m["role"] == "tool":
            prev = next(x for x in reversed(msgs[:i]) if x["role"] != "tool")
            assert prev["role"] == "assistant" and prev.get("tool_calls")
    roles = [m["role"] for m in msgs]
    assert not any(a == b == "assistant" for a, b in zip(roles, roles[1:]))


def test_long_chat_is_recent_messages_plus_summary():
    ag = db.insert("agents", id=new_id("ag_"), name="Scribe", emoji="x", color="#fff", role="r")
    th = db.insert("threads", id=new_id("th_"), title="Long", kind="dm", members=[ag["id"]])
    t0 = time.time() - 10_000
    for i in range(40):
        db.insert("messages", id=new_id("m_"), thread_id=th["id"], role="user" if i % 2 == 0 else "agent",
                  agent_id=None if i % 2 == 0 else ag["id"], content=f"message {i} " + "word " * 700,
                  created=t0 + i)
    h = runtime.history(th, ag)
    assert "Earlier messages, shortened" in h[0]["content"]  # nothing lost before a summary exists
    assert "message 39" in h[-1]["content"] and context.tokens(h) < 20_000

    async def fake_chat(self, messages, tools=None, max_tokens=None):
        return LLMReply(content="- The human is planning a trip; wants aisle seats.")
    from opendot.ext import profiles
    cls = type(profiles.llm_for_agent(ag["id"]))
    orig = cls.chat
    cls.chat = fake_chat
    try:
        asyncio.run(context.update_summary(th["id"], ag))
    finally:
        cls.chat = orig
    s = db.kv_get(context.summary_key(th["id"], ag["id"]))
    assert "aisle seats" in s["text"] and s["upto"] > t0
    h = runtime.history(th, ag)
    assert h[0]["content"].startswith("[Earlier in this chat — a summary]")
    assert "shortened" not in h[0]["content"]  # everything older is in the summary now
    assert "message 39" in h[-1]["content"]


def test_system_prompt_ends_with_what_changes():
    ag = db.insert("agents", id=new_id("ag_"), name="Clock", emoji="x", color="#fff", role="r")
    th = db.insert("threads", id=new_id("th_"), title="t", kind="dm", members=[ag["id"]])
    p = runtime.system_prompt(ag, th, "chat")
    assert p.rstrip().split("\n\n")[-1].startswith("# Situation")  # the clock is last


def test_claude_gets_cache_markers_others_dont():
    from opendot.llm import LLM
    kw = LLM(model="anthropic/claude-sonnet-4-5", base_url="")._kwargs([{"role": "user", "content": "x"}], None, 10)
    assert kw["cache_control_injection_points"][0] == {"location": "message", "role": "system"}
    kw = LLM(model="deepseek/deepseek-chat", base_url="")._kwargs([{"role": "user", "content": "x"}], None, 10)
    assert "cache_control_injection_points" not in kw  # caches on its own
