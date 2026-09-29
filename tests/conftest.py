"""Shared pytest fixtures.

IMPORTANT: ``opendot.config.Settings`` reads ``DOT_DATA_DIR`` (and everything else) at
*import* time, and ``opendot.db.db`` opens its sqlite connection at import time too. So
the env vars below must be set before anything under ``opendot`` is imported — that's
why this happens at module level, before any ``import opendot...`` anywhere in the
test session (conftest.py is always collected first).
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="opendot-tests-")
os.environ["DOT_DATA_DIR"] = _tmp
os.environ["DOT_PORT"] = "7885"
os.environ["DOT_TIMEZONE"] = "Europe/London"
os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/unused"
os.environ.setdefault("DOT_HEARTBEAT_MINUTES", "60")
os.environ.setdefault("DOT_REVIEWER", "off")  # the second look has its own tests

import pytest  # noqa: E402

from opendot.config import settings  # noqa: E402
from opendot.db import db  # noqa: E402


@pytest.fixture
def data_dir() -> Path:
    return settings.DATA_DIR


@pytest.fixture
def token() -> str:
    return settings.access_token


@pytest.fixture
def auth_headers(token) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------- fake LLM ----------------
@dataclass
class FakeReply:
    content: str = ""
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)


class FakeLLM:
    """Drop-in stand-in for ``opendot.llm.LLM``.

    Scripted with ``.push(content=..., tool_calls=[...])`` calls; each ``chat()`` pops
    the next scripted reply (or, if the script is empty, returns a plain "(fake reply)").
    Also implements ``chat_stream`` (an async generator yielding a single ``("done",
    reply)`` — runtime's real streaming path calls this first and falls back to
    ``chat()`` only if it raises) so it stands in for W1's streaming client too.
    """

    def __init__(self) -> None:
        self.model = "fake-model"
        self.calls: list[dict] = []
        self._script: list[FakeReply] = []

    def push(self, content: str = "", tool_calls: list | None = None) -> None:
        self._script.append(FakeReply(content=content, tool_calls=tool_calls or []))

    async def chat(self, messages, tools=None, max_tokens=None) -> FakeReply:
        self.calls.append({"messages": messages, "tools": tools})
        if self._script:
            return self._script.pop(0)
        return FakeReply(content="(fake reply)")

    async def chat_stream(self, messages, tools=None, max_tokens=None):
        reply = await self.chat(messages, tools, max_tokens)
        if reply.content:
            yield ("delta", reply.content)
        yield ("done", reply)


@pytest.fixture
def fake_llm(monkeypatch):
    import opendot.runtime  # noqa: F401  (ensure it's imported before we patch its refs)

    fake = FakeLLM()
    monkeypatch.setattr("opendot.llm.llm", fake, raising=True)
    # runtime resolves the client per-agent via profiles.llm_for_agent — make every
    # agent (and every delegated -wN helper) resolve to the same fake, regardless of
    # its saved profile.
    monkeypatch.setattr("opendot.runtime.llm_for_agent", lambda agent_id: fake, raising=True)
    return fake


@pytest.fixture
def app(fake_llm):
    from opendot.server import app as fastapi_app
    return fastapi_app


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_db():
    """Every test starts from a clean set of tables (same sqlite file, wiped rows)."""
    with db.lock:
        for t in ("agents", "threads", "messages", "steps", "automations", "inbox",
                  "approvals", "events", "kv", "skills", "tasks", "goals", "jobs",
                  "effects"):
            try:
                db.conn.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        db.conn.commit()
    yield
