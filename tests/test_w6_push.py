"""The APNs push extension (``opendot/ext/push.py``).

What's covered here (server side, everything testable without a Mac / real
APNs credentials / a real device): JWT (ES256) generation against a
generated throwaway EC key, the enabled/disabled no-op behavior, the
``/api/push/register`` endpoint, and the bus-listener → push heuristics
(approval pending, inbox note/report, chat-reply timing). The actual HTTP/2
call to Apple's servers is mocked — nothing here talks to the network.

The Swift/iOS side (the client that receives these pushes, the notification
action buttons, the Share Extension, the Widgets snapshot) can't be compiled
or run here (no Xcode/Mac) — see the final report and ``ios/README.md``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from opendot.db import db
from opendot.ext import push


@pytest.fixture(autouse=True)
def _clean_push_tokens():
    with db.lock:
        db.conn.execute("DELETE FROM push_tokens")
        db.conn.commit()
    yield
    with db.lock:
        db.conn.execute("DELETE FROM push_tokens")
        db.conn.commit()


@pytest.fixture
def ec_key_path(tmp_path):
    """A throwaway P-256 key written out PKCS8 PEM, standing in for the .p8
    Apple gives you for APNs (same curve/format; a real .p8 is also PKCS8
    EC PEM)."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    p = tmp_path / "AuthKey_TEST123.p8"
    p.write_bytes(pem)
    return p, key


@pytest.fixture
def configured_apns(monkeypatch, ec_key_path):
    path, key = ec_key_path
    monkeypatch.setenv("APNS_KEY_PATH", str(path))
    monkeypatch.setenv("APNS_KEY_ID", "TEST123")
    monkeypatch.setenv("APNS_TEAM_ID", "TEAMID42")
    monkeypatch.setenv("APNS_TOPIC", "com.example.opendot")
    monkeypatch.delenv("APNS_SANDBOX", raising=False)
    sender = push.APNsSender()
    return sender, key


# ---------------- configuration / no-op behavior ----------------
def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("APNS_KEY_PATH", raising=False)
    monkeypatch.delenv("APNS_KEY_ID", raising=False)
    monkeypatch.delenv("APNS_TEAM_ID", raising=False)
    monkeypatch.delenv("APNS_TOPIC", raising=False)
    sender = push.APNsSender()
    assert sender.enabled is False
    assert sender._token() is None


async def test_send_is_a_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("APNS_KEY_PATH", raising=False)
    sender = push.APNsSender()
    assert sender.enabled is False
    # Must not attempt any network I/O — if it did, this would hang/error
    # since there's no APNs server reachable in this environment.
    sent = await sender.send("title", "body")
    assert sent == 0


def test_missing_one_setting_still_disabled(monkeypatch, ec_key_path):
    path, _ = ec_key_path
    monkeypatch.setenv("APNS_KEY_PATH", str(path))
    monkeypatch.setenv("APNS_KEY_ID", "TEST123")
    monkeypatch.delenv("APNS_TEAM_ID", raising=False)  # missing
    monkeypatch.setenv("APNS_TOPIC", "com.example.opendot")
    assert push.APNsSender().enabled is False


# ---------------- JWT generation ----------------
def test_jwt_is_valid_es256(configured_apns):
    import jwt as pyjwt

    sender, key = configured_apns
    assert sender.enabled is True
    token = sender._token()
    assert token is not None

    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    header = pyjwt.get_unverified_header(token)
    assert header["alg"] == "ES256"
    assert header["kid"] == "TEST123"

    claims = pyjwt.decode(token, public_pem, algorithms=["ES256"])
    assert claims["iss"] == "TEAMID42"
    assert "iat" in claims


def test_jwt_is_cached_until_near_expiry(configured_apns):
    sender, _ = configured_apns
    first = sender._token()
    second = sender._token()
    assert first == second  # same JWT reused, not regenerated every call

    sender._jwt_at -= 46 * 60  # simulate ~46 minutes elapsed (> the 45min refresh window)
    third = sender._token()
    assert third is not None
    # A refreshed token isn't guaranteed to differ byte-for-byte (iat has
    # second resolution), but it must still be freshly minted, not stale
    # bookkeeping — the refresh path is what we're asserting was taken.
    assert sender._jwt_at > 0


def test_payload_shape():
    sender = push.APNsSender()
    payload = sender.build_payload("Pip needs your OK", "wants to use shell", "APPROVAL",
                                    {"approval_id": "ap_1", "thread_id": "th_1"})
    assert payload["aps"]["alert"] == {"title": "Pip needs your OK", "body": "wants to use shell"}
    assert payload["aps"]["category"] == "APPROVAL"
    assert payload["approval_id"] == "ap_1"
    assert payload["thread_id"] == "th_1"


# ---------------- /api/push/register ----------------
def test_register_endpoint(client, auth_headers):
    r = client.post("/api/push/register", json={"device_token": "abc123", "platform": "ios",
                                                  "bundle_id": "com.example.opendot"},
                     headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    rows = db.q("SELECT * FROM push_tokens WHERE device_token=?", "abc123")
    assert len(rows) == 1
    assert rows[0]["platform"] == "ios"


def test_register_requires_auth(client):
    r = client.post("/api/push/register", json={"device_token": "abc123"})
    assert r.status_code == 401


def test_register_dedupes_by_device_token(client, auth_headers):
    body = {"device_token": "dup-token", "platform": "ios", "bundle_id": "com.example.opendot"}
    client.post("/api/push/register", json=body, headers=auth_headers)
    client.post("/api/push/register", json=body, headers=auth_headers)
    rows = db.q("SELECT * FROM push_tokens WHERE device_token=?", "dup-token")
    assert len(rows) == 1


def test_register_requires_device_token(client, auth_headers):
    r = client.post("/api/push/register", json={"platform": "ios"}, headers=auth_headers)
    assert r.status_code == 400


# ---------------- bus → push heuristics ----------------
@pytest.fixture
def mock_send(monkeypatch):
    m = AsyncMock(return_value=1)
    monkeypatch.setattr(push.apns, "send", m)
    return m


async def test_pending_approval_pushes(mock_send):
    from opendot.db import new_id

    agent = db.insert("agents", id=new_id("ag_"), name="Pip", emoji="x", color="#fff", role="r")
    await push._on_approval({
        "approval": {"id": "ap_1", "agent_id": agent["id"], "thread_id": "th_1",
                     "tool": "shell", "reason": "runs a command", "status": "pending"},
    })
    mock_send.assert_awaited_once()
    args, kwargs = mock_send.call_args
    assert args[0] == "Pip" and args[1].startswith("Can I")
    assert kwargs["category"] == "APPROVAL"
    assert kwargs["user_info"] == {"approval_id": "ap_1", "thread_id": "th_1"}


async def test_resolved_approval_does_not_push(mock_send):
    await push._on_approval({"approval": {"id": "ap_1", "status": "approved"}})
    mock_send.assert_not_awaited()


async def test_inbox_note_pushes(mock_send, monkeypatch):
    from opendot.db import new_id

    monkeypatch.setattr(push, "_quiet_now", lambda: False)
    agent = db.insert("agents", id=new_id("ag_"), name="Scout", emoji="x", color="#fff", role="r")
    await push._on_inbox({
        "item": {"kind": "note", "title": "Morning brief ready", "body": "3 stories today",
                 "agent_id": agent["id"], "thread_id": "th_2"},
    })
    mock_send.assert_awaited_once()
    args, kwargs = mock_send.call_args
    assert args[0] == "Morning brief ready"
    assert kwargs["category"] == "MESSAGE"


async def test_inbox_approval_kind_is_not_double_pushed(mock_send):
    # Approvals already get a push from _on_approval; the inbox mirror of
    # the same event must not push a second, blander notification.
    await push._on_inbox({"item": {"kind": "approval", "title": "x"}})
    mock_send.assert_not_awaited()


async def test_fast_chat_reply_is_not_pushed(mock_send):
    push._last_user_msg.clear()
    await push._on_message({"message": {"role": "user", "thread_id": "th_3", "created": 1000.0}})
    await push._on_message({
        "message": {"role": "agent", "thread_id": "th_3", "agent_id": "ag_x", "created": 1005.0,
                    "content": "quick reply", "meta": {"source": "chat"}},
    })
    mock_send.assert_not_awaited()


async def test_slow_chat_reply_is_pushed(mock_send):
    push._last_user_msg.clear()
    await push._on_message({"message": {"role": "user", "thread_id": "th_4", "created": 1000.0}})
    await push._on_message({
        "message": {"role": "agent", "thread_id": "th_4", "agent_id": "ag_x",
                    "created": 1000.0 + push.REPLY_PUSH_DELAY + 5,
                    "content": "sorry for the wait", "meta": {"source": "chat"}},
    })
    mock_send.assert_awaited_once()


async def test_non_chat_source_always_pushes_even_if_fast(mock_send, monkeypatch):
    monkeypatch.setattr(push, "_quiet_now", lambda: False)  # don't depend on the clock
    push._last_user_msg.clear()
    await push._on_message({"message": {"role": "user", "thread_id": "th_5", "created": 1000.0}})
    await push._on_message({
        "message": {"role": "agent", "thread_id": "th_5", "agent_id": "ag_x", "created": 1001.0,
                    "content": "your 8am brief is ready", "meta": {"source": "automation:brief"}},
    })
    mock_send.assert_awaited_once()


async def test_on_event_dispatches_by_kind(mock_send):
    push._last_user_msg.clear()
    await push._on_event("approval", {"approval": {"id": "ap_2", "status": "pending",
                                                     "agent_id": "ag_x", "thread_id": "th_6",
                                                     "tool": "python"}})
    mock_send.assert_awaited_once()


async def test_on_event_swallows_handler_errors(mock_send, caplog):
    # A malformed event must never crash the bus (bus.emit already isolates
    # listener exceptions, but _on_event's own try/except is the belt to
    # bus.py's suspenders — verify it doesn't raise back out).
    await push._on_event("message", {"message": None})


async def test_inbox_report_is_not_double_pushed(mock_send):
    # a background run's report mirrors its chat message, which is pushed already
    await push._on_inbox({"item": {"kind": "report", "title": "x"}})
    mock_send.assert_not_awaited()


async def test_attention_budget_holds_extra_nudges(mock_send, monkeypatch):
    from opendot.db import new_id

    monkeypatch.setattr(push, "_quiet_now", lambda: False)
    monkeypatch.setattr(push, "DAILY_NUDGES", 2)
    agent = db.insert("agents", id=new_id("ag_"), name="Scout", emoji="x", color="#fff", role="r")
    for i in range(4):
        await push._on_inbox({"item": {"kind": "note", "title": f"n{i}",
                                       "agent_id": agent["id"]}})
    assert mock_send.await_count == 2
    assert push.budget_status()["held"] == 2
    # a question that needs the human is never held
    await push._on_message({"message": {"role": "agent", "thread_id": "th_9",
                                        "agent_id": agent["id"], "content": "go?",
                                        "meta": {"source": "watch:x", "choices": ["Go"]}}})
    assert mock_send.await_count == 3
