"""Plain-markdown memory: remember/forget for an agent and for the shared USER.md."""

from __future__ import annotations

from opendot import memory
from opendot.db import db, new_id


def _agent():
    return db.insert("agents", id=new_id("ag_"), name="Mem", emoji="x", color="#fff",
                     role="r")["id"]


def test_remember_appends_to_memory_md():
    aid = _agent()
    memory.remember(aid, "likes tea")
    assert "likes tea" in memory.read(aid, "MEMORY.md")


def test_remember_about_user_goes_to_user_md():
    aid = _agent()
    memory.remember(aid, "the human's name is Alex", about_user=True)
    assert "Alex" in memory.read(aid, "USER.md")


def test_forget_removes_matching_lines():
    aid = _agent()
    memory.remember(aid, "likes tea")
    memory.remember(aid, "likes coffee")
    removed = memory.forget(aid, "tea")
    assert removed >= 1
    content = memory.read(aid, "MEMORY.md")
    assert "likes tea" not in content
    assert "likes coffee" in content


def test_journal_and_recent_journal():
    aid = _agent()
    memory.journal(aid, "did a thing")
    recent = memory.recent_journal(aid)
    assert "did a thing" in recent
