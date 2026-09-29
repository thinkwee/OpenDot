"""Agent email: SMTP send (real local SMTP via aiosmtpd), IMAP receive (monkeypatched
imaplib), OTP filtering, and the inbound webhook."""

from __future__ import annotations

import asyncio
import email as email_lib
import socket

import pytest

from opendot import ext as ext_mod
from opendot.db import db, new_id
from opendot.ext import email as email_mod
from opendot.gatekeeper import DEFAULT_POLICY
from opendot.tools import EXEC, TOOLS, Ctx

aiosmtpd = pytest.importorskip("aiosmtpd")


def _agent(name="Scout"):
    return db.insert("agents", id=new_id("ag_"), name=name, emoji="x", color="#fff", role="r")


def test_email_tools_registered():
    for name in ("send_email", "list_emails", "read_email"):
        assert name in TOOLS
        assert name in EXEC
    assert DEFAULT_POLICY["send_email"] == "ask"
    assert DEFAULT_POLICY["list_emails"] == "allow"
    assert DEFAULT_POLICY["read_email"] == "allow"


def test_email_ext_loaded():
    ext_mod.load_all()
    assert any(m.__name__ == "opendot.ext.email" for m in ext_mod.MODULES)


class _CollectHandler:
    """aiosmtpd message handler that stashes every accepted message."""

    def __init__(self) -> None:
        self.messages: list = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(email_lib.message_from_bytes(envelope.content))
        return "250 Message accepted"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def smtp_server():
    from aiosmtpd.controller import Controller
    handler = _CollectHandler()
    controller = Controller(handler, hostname="127.0.0.1", port=_free_port())
    controller.start()
    yield controller, handler
    controller.stop()


def test_smtp_send_real_local_server(smtp_server):
    controller, handler = smtp_server
    ag = _agent()
    cfg = {"address": "scout@opendot.test", "display_name": "Scout",
          "smtp": {"host": "127.0.0.1", "port": controller.port, "starttls": False}}
    email_mod.set_config(ag["id"], cfg)

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["send_email"](ctx, to="human@example.com", subject="hi",
                                        body="test body")

    result = asyncio.run(run())
    assert result["ok"] is True
    assert len(handler.messages) == 1
    msg = handler.messages[0]
    assert msg["Subject"] == "hi"
    assert msg["To"] == "human@example.com"
    assert msg["Date"]
    assert msg["Message-Id"].endswith("@opendot.test>")
    assert msg["Reply-To"] == "scout@opendot.test"  # replies route back even if From is rewritten
    row = db.one("SELECT * FROM emails WHERE agent_id=? AND direction='out'", ag["id"])
    assert row and row["subject"] == "hi"
    assert row["message_id"] == msg["Message-Id"]  # stored, so replies can thread


def test_smtp_reply_threads(smtp_server):
    controller, handler = smtp_server
    ag = _agent("Threader")
    email_mod.set_config(ag["id"], {"address": "me+threader@gmail.test", "display_name": "T",
                                    "smtp": {"host": "127.0.0.1", "port": controller.port,
                                             "starttls": False}})

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["send_email"](ctx, to="hotel@example.com", subject="Re: booking",
                                        body="yes", reply_to_message_id="<abc@hotel>")

    assert asyncio.run(run())["ok"] is True
    msg = handler.messages[-1]
    assert msg["In-Reply-To"] == "<abc@hotel>"
    assert msg["References"] == "<abc@hotel>"
    assert msg["Reply-To"] == "me+threader@gmail.test"


def test_smtp_send_missing_config_returns_error():
    ag = _agent("NoConfig")

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["send_email"](ctx, to="x@example.com", subject="s", body="b")

    result = asyncio.run(run())
    assert "error" in result


# ---------------- IMAP (monkeypatched) ----------------
class _FakeIMAP:
    """Stands in for imaplib.IMAP4/IMAP4_SSL: one scripted mailbox with real UID
    semantics (UID SEARCH ALL / UID n:*, UID FETCH, UIDVALIDITY)."""

    inbox: list[tuple[int, bytes]] = []
    uidvalidity = b"7"
    fetched: list[str] = []
    internaldate = '"01-Jan-2020 00:00:00 +0000"'

    def __init__(self, host, port):
        self.host, self.port = host, port

    def login(self, user, password):
        return "OK", [b"logged in"]

    def select(self, folder):
        return "OK", [str(len(self.inbox)).encode()]

    def response(self, code):
        return code, [self.uidvalidity] if code == "UIDVALIDITY" else [None]

    def search(self, charset, criterion):
        return "OK", [b" ".join(str(u).encode() for u, _ in self.inbox)]

    def uid(self, cmd, *args):
        uids = [u for u, _ in self.inbox]
        if cmd == "SEARCH":
            crit = args[1:]
            if crit[0] == "UID":
                lo = int(crit[1].split(":")[0])
                hits = [u for u in uids if u >= lo] or uids[-1:]  # RFC 3501 "n:*" quirk
            else:
                hits = uids
            return "OK", [b" ".join(str(u).encode() for u in hits)]
        if cmd == "FETCH":
            want, parts = args
            if "INTERNALDATE" in parts:
                return "OK", [f"{i} (UID {u} INTERNALDATE {self.internaldate})".encode()
                              for i, u in enumerate(map(int, want.split(",")), 1)]
            _FakeIMAP.fetched.append(want)
            for u, raw in self.inbox:
                if str(u) == want:
                    return "OK", [(b"1 (UID %d BODY[] {%d}" % (u, len(raw)), raw), b")"]
            return "OK", [None]
        raise AssertionError(cmd)

    def logout(self):
        return "BYE", [b"logging out"]


def _mail(subject: str, sender="sender@example.com", body="hi") -> bytes:
    return f"From: {sender}\r\nSubject: {subject}\r\n\r\n{body}".encode()


@pytest.fixture
def fake_imap(monkeypatch):
    monkeypatch.setattr(email_mod.imaplib, "IMAP4", _FakeIMAP)
    monkeypatch.setattr(email_mod.imaplib, "IMAP4_SSL", _FakeIMAP)
    monkeypatch.setattr(_FakeIMAP, "inbox", [])
    monkeypatch.setattr(_FakeIMAP, "fetched", [])
    monkeypatch.setattr(_FakeIMAP, "uidvalidity", b"7")
    return _FakeIMAP


_IMAP = {"host": "127.0.0.1", "user": "u", "password": "p", "ssl": False, "folder": "INBOX"}


def test_imap_first_connect_does_not_backfill_old_mail(fake_imap):
    # a real mailbox with years of mail: connecting it must not wake agents for any of it
    fake_imap.inbox = [(u, _mail(f"old {u}")) for u in range(1, 501)]
    assert email_mod._imap_fetch_new("ag_old", _IMAP) == []
    assert fake_imap.fetched == []  # never downloaded a single old message
    st = db.kv_get("identity:email:imap_state:ag_old")
    assert st["last_uid"] == 500 and st["uidvalidity"] == "7"
    # nothing new → nothing (even though UID 501:* matches the newest message)
    assert email_mod._imap_fetch_new("ag_old", _IMAP) == []
    # two arrive → exactly those two, oldest first, once
    fake_imap.inbox += [(501, _mail("new A")), (502, _mail("new B"))]
    got = email_mod._imap_fetch_new("ag_old", _IMAP)
    assert [m["subject"] for m in got] == ["new A", "new B"]
    assert [m["uid"] for m in got] == ["501", "502"]
    assert email_mod._imap_fetch_new("ag_old", _IMAP) == []


def test_imap_first_connect_keeps_mail_from_the_last_minutes(fake_imap, monkeypatch):
    import time
    fake_imap.inbox = [(1, _mail("ancient")), (2, _mail("just now"))]
    now = time.strftime("%d-%b-%Y %H:%M:%S +0000", time.gmtime())

    real_uid = _FakeIMAP.uid

    def uid(self, cmd, *args):
        if cmd == "FETCH" and "INTERNALDATE" in args[1]:
            return "OK", [b'1 (UID 1 INTERNALDATE "01-Jan-2020 00:00:00 +0000")',
                          f'2 (UID 2 INTERNALDATE "{now}")'.encode()]
        return real_uid(self, cmd, *args)

    monkeypatch.setattr(fake_imap, "uid", uid)
    got = email_mod._imap_fetch_new("ag_recent", _IMAP)
    assert [m["subject"] for m in got] == ["just now"]
    assert email_mod._imap_fetch_new("ag_recent", _IMAP) == []


def test_imap_uidvalidity_change_rebaselines(fake_imap):
    fake_imap.inbox = [(1, _mail("a")), (2, _mail("b"))]
    assert email_mod._imap_fetch_new("ag_uv", _IMAP) == []
    # the server renumbered the folder: old UIDs mean nothing, start over from its end
    fake_imap.uidvalidity = b"99"
    fake_imap.inbox = [(u, _mail(f"renumbered {u}")) for u in range(1, 40)]
    assert email_mod._imap_fetch_new("ag_uv", _IMAP) == []
    assert db.kv_get("identity:email:imap_state:ag_uv")["last_uid"] == 39


def test_imap_burst_is_capped_per_poll_and_resumes_in_order(fake_imap):
    fake_imap.inbox = [(1, _mail("seed"))]
    email_mod._imap_fetch_new("ag_burst", _IMAP)
    n = email_mod.IMAP_BATCH + 5
    fake_imap.inbox += [(u, _mail(f"m{u}")) for u in range(2, 2 + n)]
    first = email_mod._imap_fetch_new("ag_burst", _IMAP)
    second = email_mod._imap_fetch_new("ag_burst", _IMAP)
    assert len(first) == email_mod.IMAP_BATCH and len(second) == 5
    assert [int(m["uid"]) for m in first + second] == list(range(2, 2 + n))


def test_imap_poll_ingests_and_filters_otp(fake_imap, fake_llm):
    ag = _agent("Quill")
    cfg = {"address": "quill@opendot.test", "imap": dict(_IMAP)}
    email_mod.set_config(ag["id"], cfg)
    fake_imap.inbox = [(10, _mail("already there"))]
    assert email_mod._imap_fetch_new(ag["id"], cfg["imap"]) == []
    fake_imap.inbox += [(11, _mail("Hello there", body="Just saying hi.")),
                        (12, _mail("Your one-time code", "bank@example.com", "OTP: 123456"))]
    msgs = email_mod._imap_fetch_new(ag["id"], cfg["imap"])
    assert len(msgs) == 2
    assert any("Hello there" in m["subject"] for m in msgs)

    async def ingest_all():
        for m in msgs:
            await email_mod._ingest_email(ag["id"], **m)

    asyncio.run(ingest_all())
    rows = db.q("SELECT * FROM emails WHERE agent_id=?", ag["id"])
    assert len(rows) == 2
    otp_row = next(r for r in rows if "one-time code" in r["subject"])
    normal_row = next(r for r in rows if "Hello there" in r["subject"])
    assert otp_row["filtered"] == 1
    assert normal_row["filtered"] == 0

    # OTP emails stay hidden from list_emails unless show_otp is turned on
    async def listed():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["list_emails"](ctx)

    out = asyncio.run(listed())
    subjects = [e["subject"] for e in out["emails"]]
    assert "Hello there" in subjects
    assert "Your one-time code" not in subjects


def test_webhook_email_hook_ingests(monkeypatch, fake_llm):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    db.kv_set("hook_token", "testtoken")
    ag = _agent("Pixel")
    app = FastAPI()
    app.include_router(email_mod.router)
    with TestClient(app) as c:
        r = c.post("/hook/testtoken/email/pixel",
                  json={"from": "someone@example.com", "to": "pixel@opendot.test",
                        "subject": "Webhook test", "text": "hello via webhook"})
    assert r.status_code == 200
    row = db.one("SELECT * FROM emails WHERE agent_id=? AND subject=?", ag["id"],
                "Webhook test")
    assert row is not None
    assert row["from_addr"] == "someone@example.com"


def test_webhook_email_hook_accepts_agent_id(fake_llm):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    db.kv_set("hook_token", "testtoken")
    ag = _agent("Renamed Later")
    app = FastAPI()
    app.include_router(email_mod.router)
    with TestClient(app) as c:
        r = c.post(f"/hook/testtoken/email/{ag['id']}",
                  json={"from": "a@example.com", "subject": "By id", "text": "x"})
    assert r.status_code == 200
    assert db.one("SELECT 1 FROM emails WHERE agent_id=? AND subject='By id'", ag["id"])


def test_webhook_email_hook_bad_token_rejected():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    db.kv_set("hook_token", "testtoken")
    app = FastAPI()
    app.include_router(email_mod.router)
    with TestClient(app) as c:
        r = c.post("/hook/wrongtoken/email/pixel", json={"from": "a@b.com", "subject": "x"})
    assert r.status_code == 401


def test_chinese_agent_names_get_pinyin_addresses():
    from opendot.ext.email import slug
    assert slug("Spot Spotter") == "spot-spotter"
    assert slug("捡漏小狐") == "jian-lou-xiao-hu"
    assert slug("🦊") == "agent"
