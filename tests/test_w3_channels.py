"""Channels: base infra (bindings, pairing, commands, chunking, outbound
mirroring) + Telegram (the one channel we can exercise end-to-end with mocked
HTTP). WeChat/Feishu/Slack/Discord protocol code is exercised at the level of
"does it import lazily and stay disabled without config" — see
test_channels_disabled_without_config — since we have no real credentials for
any of them (see docs/ws/W3.md for exactly what is/isn't covered)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from opendot.channels import base as chbase
from opendot.channels.telegram import TelegramChannel
from opendot.db import db, new_id


@pytest.fixture(autouse=True)
def _clean_channels_tables():
    with db.lock:
        db.conn.execute("DELETE FROM channel_bindings")
        db.conn.commit()
    yield


def _agent(name="Pip"):
    row = db.one("SELECT * FROM agents WHERE name=?", name)
    if row:
        return row
    return db.insert("agents", id=new_id("ag_"), name=name, emoji="✨", color="#fff", role="r")


def _dm_thread(agent):
    return db.insert("threads", id=new_id("th_"), title=agent["name"], kind="dm",
                     members=[agent["id"]], updated=0)


# ------------------------------------------------------------------ pairing
def test_pairing_allowlist_flow():
    ag = _agent()
    code = chbase.new_pairing_code("telegram")
    assert not chbase.is_allowed("telegram", "chat1")
    reply = chbase.try_pair("telegram", "chat1", "user1", f"/pair {code}")
    assert reply and "Paired" in reply
    assert chbase.is_allowed("telegram", "chat1")
    assert chbase.is_allowed("telegram", "user1")
    # code is single-use
    reply2 = chbase.try_pair("telegram", "chat2", "user2", f"/pair {code}")
    assert reply2 and "expired" in reply2.lower()


def test_pairing_rejects_expired_code():
    codes = {"DEAD00": 1.0}  # already expired (epoch ~1970)
    db.kv_set("channel_pair:telegram", codes)
    reply = chbase.try_pair("telegram", "chat1", "user1", "/pair DEAD00")
    assert "expired" in reply.lower()
    assert not chbase.is_allowed("telegram", "chat1")


# ------------------------------------------------------------------ commands
def test_command_agents_lists_agents():
    _agent("Pip")
    _agent("Scout")
    reply = chbase.handle_command("telegram", "chat1", "/agents")
    assert "Pip" in reply and "Scout" in reply


def test_command_to_switches_bound_thread():
    dot = _agent("Pip")
    scout = _agent("Scout")
    dot_thread = _dm_thread(dot)
    _dm_thread(scout)
    chbase.rebind("telegram", "chat1", dot_thread["id"], dot["id"], title="chat1")
    reply = chbase.handle_command("telegram", "chat1", "/to Scout")
    assert "Scout" in reply
    row = db.one("SELECT * FROM channel_bindings WHERE channel=? AND chat_id=?",
                "telegram", "chat1")
    thread = db.one("SELECT * FROM threads WHERE id=?", row["thread_id"])
    assert scout["id"] in thread["members"]


def test_command_to_unknown_agent():
    reply = chbase.handle_command("telegram", "chat1", "/to Nobody")
    assert "don't know anyone" in reply


def test_command_team_binds_group_thread():
    a, b = _agent("Pip"), _agent("Scout")
    group = db.insert("threads", id=new_id("th_"), title="Dream Team", kind="group",
                      members=[a["id"], b["id"]], updated=0)
    reply = chbase.handle_command("telegram", "chat1", "/team")
    assert "the group" in reply
    row = db.one("SELECT * FROM channel_bindings WHERE channel=? AND chat_id=?",
                "telegram", "chat1")
    assert row["thread_id"] == group["id"]


def test_command_new_creates_fresh_thread():
    ag = _agent("Pip")
    old = _dm_thread(ag)
    chbase.rebind("telegram", "chat1", old["id"], ag["id"])
    reply = chbase.handle_command("telegram", "chat1", "/new")
    assert "Fresh chat" in reply
    row = db.one("SELECT * FROM channel_bindings WHERE channel=? AND chat_id=?",
                "telegram", "chat1")
    assert row["thread_id"] != old["id"]


def test_non_command_returns_none():
    assert chbase.handle_command("telegram", "chat1", "just chatting") is None


# ------------------------------------------------------------------ chunking / markdown
def test_chunk_text_short_passthrough():
    assert chbase.chunk_text("hello", 100) == ["hello"]


def test_chunk_text_splits_long_text_on_boundaries():
    text = ("word " * 2000).strip()
    chunks = chbase.chunk_text(text, 500)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    assert "".join(c + " " for c in chunks).replace("  ", " ").strip().startswith("word word")


def test_to_plain_strips_markdown():
    assert chbase.to_plain("**bold** and `code`") == "bold and code"


# ------------------------------------------------------------------ bindings
def test_bound_thread_creates_dm_with_default_agent():
    ag = _agent("Pip")
    tid = chbase.bound_thread("telegram", "chat1", "Some Chat")
    thread = db.one("SELECT * FROM threads WHERE id=?", tid)
    assert thread["kind"] == "dm"
    assert ag["id"] in thread["members"]
    # second call returns the same binding, doesn't create a new thread
    tid2 = chbase.bound_thread("telegram", "chat1", "Some Chat")
    assert tid2 == tid


# ------------------------------------------------------------------ telegram inbound -> on_user_message
def test_telegram_inbound_calls_on_user_message(monkeypatch):
    ag = _agent("Pip")
    seen = {}

    async def fake_on_user_message(thread_id, text, attachments=None, source="app", sender=""):
        seen["thread_id"] = thread_id
        seen["text"] = text
        seen["source"] = source
        return {"id": "m1"}

    async def fake_ingest(source, type_, payload):
        seen["event_source"] = source
        return {"id": "ev1"}

    monkeypatch.setattr("opendot.channels.base.runtime.on_user_message", fake_on_user_message)
    monkeypatch.setattr("opendot.channels.base.ingest_event", fake_ingest)

    ch = TelegramChannel()
    chbase.REGISTRY["telegram"] = ch

    db.kv_set("channel_allow:telegram", ["chat1"])
    asyncio.run(ch.handle_inbound("chat1", "user1", "hello there", "Chat 1"))

    assert seen["source"] == "telegram"
    assert seen["text"] == "hello there"
    assert seen["event_source"] == "telegram"


def test_telegram_inbound_ignores_unpaired_chat(monkeypatch):
    called = {"n": 0}

    async def fake_on_user_message(*a, **k):
        called["n"] += 1

    monkeypatch.setattr("opendot.channels.base.runtime.on_user_message", fake_on_user_message)
    ch = TelegramChannel()
    asyncio.run(ch.handle_inbound("unknown_chat", "user1", "hello", "Chat"))
    assert called["n"] == 0


# ------------------------------------------------------------------ outbound mirroring
def test_outbound_message_mirrors_to_bound_channel(monkeypatch):
    ag = _agent("Pip")
    thread = _dm_thread(ag)
    ch = TelegramChannel()
    ch.status = "connected"
    chbase.rebind("telegram", "chat1", thread["id"], ag["id"])

    sent = []

    async def fake_send_text(chat_id, text, thread_id=None):
        sent.append((chat_id, text))

    monkeypatch.setattr(ch, "_send_text", fake_send_text)

    msg = {"role": "agent", "agent_id": ag["id"], "thread_id": thread["id"],
          "content": "hi from Pip"}

    async def run():
        await chbase._mirror_message(ch, msg)

    asyncio.run(run())
    assert sent == [("chat1", "hi from Pip")]


def test_outbound_does_not_echo_user_messages(monkeypatch):
    ag = _agent("Pip")
    thread = _dm_thread(ag)
    ch = TelegramChannel()
    chbase.rebind("telegram", "chat1", thread["id"], ag["id"])
    sent = []
    monkeypatch.setattr(ch, "_send_text", lambda *a, **k: sent.append(a))
    msg = {"role": "user", "agent_id": None, "thread_id": thread["id"], "content": "hi"}
    asyncio.run(chbase._mirror_message(ch, msg))
    assert sent == []


def test_group_thread_messages_are_prefixed_with_agent_name(monkeypatch):
    a, b = _agent("Pip"), _agent("Scout")
    group = db.insert("threads", id=new_id("th_"), title="Team", kind="group",
                      members=[a["id"], b["id"]], updated=0)
    ch = TelegramChannel()
    chbase.rebind("telegram", "chat1", group["id"], None)
    sent = []

    async def fake_send_text(chat_id, text, thread_id=None):
        sent.append(text)

    monkeypatch.setattr(ch, "_send_text", fake_send_text)
    msg = {"role": "agent", "agent_id": a["id"], "thread_id": group["id"], "content": "hi team"}
    asyncio.run(chbase._mirror_message(ch, msg))
    assert sent and "Pip" in sent[0] and "hi team" in sent[0]


# ------------------------------------------------------------------ approvals -> gatekeeper.decide
def test_telegram_text_fallback_approval_reply(monkeypatch):
    ag = _agent("Pip")
    thread = _dm_thread(ag)
    ch = TelegramChannel()
    chbase.rebind("telegram", "chat1", thread["id"], ag["id"])
    db.kv_set("channel_allow:telegram", ["chat1"])
    db.kv_set("channel_pending_appr:telegram", {"chat1": "ap_123"})

    decided = {}

    def fake_decide(ap_id, approve, always=False):
        decided.update(id=ap_id, approve=approve, always=always)

    monkeypatch.setattr("opendot.channels.base.gatekeeper.decide", fake_decide)
    sent = []
    monkeypatch.setattr(ch, "_send_text", lambda *a, **k: sent.append(a) or asyncio.sleep(0))

    asyncio.run(ch.handle_inbound("chat1", "user1", "2"))  # 2 = always allow
    assert decided == {"id": "ap_123", "approve": True, "always": True}


def test_telegram_callback_query_decides(monkeypatch):
    ch = TelegramChannel()
    ch._client = None  # answerCallbackQuery call is best-effort and guarded
    db.kv_set("channel_allow:telegram", ["chat1"])
    decided = {}

    def fake_decide(ap_id, approve, always=False):
        decided.update(id=ap_id, approve=approve, always=always)

    monkeypatch.setattr("opendot.gatekeeper.decide", fake_decide)
    cq = {"id": "cbq1", "data": "appr:ap_9:deny", "message": {"chat": {"id": "chat1"}}}
    asyncio.run(ch._handle_callback("faketoken", cq))
    assert decided == {"id": "ap_9", "approve": False, "always": False}


# ------------------------------------------------------------------ telegram HTTP (respx)
@respx.mock
def test_telegram_send_text_hits_send_message_endpoint():
    token = "123:ABC"
    db.kv_set("channel_config:telegram", {"TELEGRAM_BOT_TOKEN": token})
    route = respx.post(f"https://api.telegram.org/bot{token}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True}))

    async def run():
        ch = TelegramChannel()
        ch._client = httpx.AsyncClient()
        await ch._send_text("chat1", "**bold** hello")
        await ch._client.aclose()

    asyncio.run(run())
    assert route.called
    body = route.calls.last.request.content
    assert b"chat1" in body


@respx.mock
def test_telegram_start_checks_get_me_and_marks_connected(monkeypatch):
    token = "123:ABC"
    db.kv_set("channel_config:telegram", {"TELEGRAM_BOT_TOKEN": token})
    respx.get(f"https://api.telegram.org/bot{token}/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"id": 1}}))

    ch = TelegramChannel()
    # Don't actually spin up the background long-poll loop in a unit test — just
    # verify start() checks getMe and flips status to "connected".
    monkeypatch.setattr("asyncio.create_task", lambda coro: coro.close())

    asyncio.run(ch.start())
    assert ch.status == "connected"
    asyncio.run(ch.stop())


@respx.mock
def test_telegram_start_without_token_stays_disabled():
    db.kv_set("channel_config:telegram", {})
    ch = TelegramChannel()
    asyncio.run(ch.start())
    assert ch.status == "disabled"


# ------------------------------------------------------------------ extension loading
def test_channels_disabled_without_config():
    """Every channel module imports cleanly and reports 'disabled' with no config —
    the acceptance bar for the channels we cannot exercise for real (WeChat needs a
    live QR scan, Feishu/Slack/Discord need real app credentials)."""
    from opendot.channels import discord as discord_mod
    from opendot.channels import feishu as feishu_mod
    from opendot.channels import slack as slack_mod
    from opendot.channels import wechat as wechat_mod

    for mod, attr in ((discord_mod, "discord_channel"), (feishu_mod, "feishu"),
                     (slack_mod, "slack"), (wechat_mod, "wechat")):
        db.kv_set(f"channel_config:{getattr(mod, attr).name}", {})
        asyncio.run(getattr(mod, attr).start())
        assert getattr(mod, attr).status == "disabled"
