"""Setup wizards: the mailbox "Test" button (imaplib/smtplib mocked), the Twilio
test / search / buy / assign flow (httpx mocked — nothing real is bought), and the
MCP app catalog."""

from __future__ import annotations

import imaplib
import smtplib
import socket

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from opendot import gatekeeper
from opendot.connectors import CATALOG, CATALOG_CATEGORIES, build_spec, catalog_entry
from opendot.db import db, new_id
from opendot.ext import app_catalog
from opendot.ext import email as email_mod
from opendot.ext import phone as phone_mod


def _agent(name="Scout"):
    return db.insert("agents", id=new_id("ag_"), name=name, emoji="x", color="#fff", role="r")


def _client(*routers):
    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return TestClient(app)


# ---------------- email ----------------
class _IMAP:
    fail: BaseException | None = None
    log: list = []

    def __init__(self, host, port, timeout=None):
        _IMAP.log.append(("connect", host, port))
        if isinstance(_IMAP.fail, OSError):
            raise _IMAP.fail

    def login(self, user, pw):
        _IMAP.log.append(("login", user, pw))
        if _IMAP.fail is not None:
            raise _IMAP.fail
        return "OK", [b""]

    def _simple_command(self, name, *args):
        _IMAP.log.append(("cmd", name))
        return "OK", [b""]

    def select(self, folder):
        return "OK", [b"3"]

    def search(self, charset, crit):
        return "OK", [b"1 2 3"]

    def logout(self):
        return "BYE", [b""]


class _SMTP:
    fail: BaseException | None = None
    log: list = []

    def __init__(self, host, port, timeout=None):
        _SMTP.log.append(("connect", self.__class__.__name__, host, port))
        if isinstance(_SMTP.fail, OSError) and not isinstance(_SMTP.fail, smtplib.SMTPException):
            raise _SMTP.fail

    def starttls(self):
        _SMTP.log.append(("starttls",))

    def login(self, user, pw):
        _SMTP.log.append(("login", user, pw))
        if _SMTP.fail is not None:
            raise _SMTP.fail

    def send_message(self, msg, to_addrs=None):
        _SMTP.log.append(("send", to_addrs, msg["Subject"]))

    def close(self):
        pass

    def quit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _SMTP_SSL(_SMTP):
    pass


@pytest.fixture
def mail_mocks(monkeypatch):
    monkeypatch.setattr(email_mod.imaplib, "IMAP4_SSL", _IMAP)
    monkeypatch.setattr(email_mod.imaplib, "IMAP4", _IMAP)
    monkeypatch.setattr(email_mod.smtplib, "SMTP", _SMTP)
    monkeypatch.setattr(email_mod.smtplib, "SMTP_SSL", _SMTP_SSL)
    monkeypatch.setattr(_IMAP, "fail", None)
    monkeypatch.setattr(_IMAP, "log", [])
    monkeypatch.setattr(_SMTP, "fail", None)
    monkeypatch.setattr(_SMTP, "log", [])
    return _IMAP, _SMTP


GMAIL = {"address": "myagents@gmail.com", "password": "abcd efgh ijkl mnop",
         "smtp": {"host": "smtp.gmail.com", "port": 587, "starttls": True},
         "imap": {"host": "imap.gmail.com", "port": 993, "ssl": True, "folder": "INBOX"}}


def test_mailbox_test_ok_sends_nothing(mail_mocks):
    imap, smtp = mail_mocks
    with _client(email_mod.router) as c:
        r = c.post("/api/identity/mailbox/test", json=GMAIL).json()
    assert r["ok"] and r["imap"] == {"ok": True, "messages": 3} and r["smtp"]["ok"]
    assert ("login", "myagents@gmail.com", "abcd efgh ijkl mnop") in imap.log
    assert ("starttls",) in smtp.log
    assert not [x for x in smtp.log if x[0] == "send"]


def test_mailbox_test_sends_to_itself_when_asked(mail_mocks):
    _, smtp = mail_mocks
    with _client(email_mod.router) as c:
        r = c.post("/api/identity/mailbox/test", json={**GMAIL, "send_test": True}).json()
    assert r["ok"] and r["smtp"]["sent_to"] == "myagents@gmail.com"
    sends = [x for x in smtp.log if x[0] == "send"]
    assert sends == [("send", ["myagents@gmail.com"], "OpenDot: your agents' mailbox works")]


def test_mailbox_test_bad_app_password(mail_mocks):
    imap, smtp = mail_mocks
    imap.fail = email_mod.IMAP_ERROR("b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)'")
    smtp.fail = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    with _client(email_mod.router) as c:
        r = c.post("/api/identity/mailbox/test", json=GMAIL).json()
    assert not r["ok"]
    assert r["imap"]["code"] == "auth" and r["smtp"]["code"] == "auth"
    assert "Invalid credentials" in r["imap"]["detail"]


def test_mailbox_test_host_problems(mail_mocks):
    imap, smtp = mail_mocks
    imap.fail = socket.gaierror(-2, "Name or service not known")
    smtp.fail = ConnectionRefusedError(111, "Connection refused")
    with _client(email_mod.router) as c:
        r = c.post("/api/identity/mailbox/test", json=GMAIL).json()
    assert r["imap"]["code"] == "dns" and r["smtp"]["code"] == "unreachable"


def test_explain_error_codes():
    import ssl
    ex = email_mod.explain_error
    assert ex(ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number"))["code"] == "tls"
    assert ex(smtplib.SMTPServerDisconnected("Connection unexpectedly closed"))["code"] == "tls"
    assert ex(TimeoutError("timed out"))["code"] == "timeout"
    assert ex(smtplib.SMTPAuthenticationError(
        534, b"5.7.9 Application-specific password required"))["code"] == "app_password"
    assert ex(imaplib.IMAP4.error("Your account is not enabled for IMAP use"))["code"] == \
        "imap_off"
    assert ex(imaplib.IMAP4.error("LOGIN failed. BasicAuthBlocked"))["code"] == "basic_auth_off"


def test_mailbox_test_uses_saved_password_and_ssl_port(mail_mocks):
    imap, smtp = mail_mocks
    gatekeeper.vault_set("AGENT_MAILBOX_PASSWORD", "sekret")
    db.kv_set("identity:mailbox", {
        "address": "me@qq.com",
        "smtp": {"host": "smtp.qq.com", "port": 465, "starttls": False,
                 "password": "{{vault:AGENT_MAILBOX_PASSWORD}}"},
        "imap": {"host": "imap.qq.com", "port": 993, "ssl": True,
                 "password": "{{vault:AGENT_MAILBOX_PASSWORD}}"}})
    try:
        with _client(email_mod.router) as c:
            r = c.post("/api/identity/mailbox/test", json={}).json()
    finally:
        gatekeeper.vault_set("AGENT_MAILBOX_PASSWORD", None)
    assert r["ok"]
    assert ("connect", "_SMTP_SSL", "smtp.qq.com", 465) in smtp.log
    assert ("starttls",) not in smtp.log
    assert ("login", "me@qq.com", "sekret") in imap.log


def test_mailbox_test_no_password(mail_mocks):
    with _client(email_mod.router) as c:
        body = {k: v for k, v in GMAIL.items() if k != "password"}
        r = c.post("/api/identity/mailbox/test", json=body).json()
    assert r["imap"]["code"] == "no_password"


def test_netease_imap_sends_id(mail_mocks):
    imap, _ = mail_mocks
    email_mod._imap_test_login({"host": "imap.163.com", "user": "a@163.com", "password": "x"})
    assert ("cmd", "ID") in imap.log


# ---------------- Twilio ----------------
class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._p, self.text = status, payload, str(payload)

    def json(self):
        return self._p


class _Twilio:
    """Fake httpx.AsyncClient: routes Twilio URLs to canned answers, records posts."""
    calls: list = []
    bad_auth = False

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, auth=None):
        _Twilio.calls.append(("GET", url, params))
        if _Twilio.bad_auth:
            return _Resp(401, {"code": 20003, "message": "Authenticate", "status": 401})
        if url.endswith("/Accounts/ACtest.json"):
            return _Resp(200, {"friendly_name": "My account", "status": "active",
                               "type": "Trial"})
        if "/AvailablePhoneNumbers/US/Local.json" in url:
            return _Resp(200, {"available_phone_numbers": [
                {"phone_number": "+14155550100", "friendly_name": "(415) 555-0100",
                 "locality": "San Francisco", "region": "CA", "address_requirements": "none",
                 "capabilities": {"voice": True, "SMS": True, "MMS": True}}]})
        if "/AvailablePhoneNumbers/" in url:
            return _Resp(404, {"code": 20404, "message": "not found"})
        if "pricing.twilio.com" in url:
            return _Resp(200, {"price_unit": "USD", "phone_number_prices": [
                {"number_type": "local", "base_price": "1.15", "current_price": "1.15"},
                {"number_type": "toll free", "base_price": "2.15", "current_price": "2.15"}]})
        if url.endswith("/IncomingPhoneNumbers.json"):
            return _Resp(200, {"incoming_phone_numbers": [
                {"phone_number": "+14155550199", "sid": "PN199", "friendly_name": "x",
                 "capabilities": {"sms": True, "voice": True},
                 "sms_url": "", "voice_url": ""}]})
        return _Resp(404, {"message": "?"})

    async def post(self, url, data=None, auth=None):
        _Twilio.calls.append(("POST", url, data))
        if url.endswith("/IncomingPhoneNumbers.json"):
            return _Resp(201, {"sid": "PN100", "phone_number": data["PhoneNumber"]})
        if "/IncomingPhoneNumbers/" in url:
            return _Resp(200, {"sid": url.rsplit("/", 1)[1][:-5]})
        return _Resp(404, {"message": "?"})


@pytest.fixture
def twilio(monkeypatch):
    monkeypatch.setattr(phone_mod.httpx, "AsyncClient", _Twilio)
    monkeypatch.setattr(_Twilio, "calls", [])
    monkeypatch.setattr(_Twilio, "bad_auth", False)
    monkeypatch.delenv("DOT_PUBLIC_URL", raising=False)
    db.kv_set("identity:phone_account", {"account_sid": "ACtest", "auth_token": "tok"})
    db.kv_set("hook_token", "hooktok")
    return _Twilio


def test_twilio_account_test(twilio):
    db.kv_set("identity:phone_account", {})
    with _client(phone_mod.router) as c:
        r = c.post("/api/identity/phone_account/test",
                   json={"account_sid": "ACtest", "auth_token": "tok"})
        assert r.json() == {"ok": True, "name": "My account", "status": "active",
                            "type": "Trial"}
        twilio.bad_auth = True
        r = c.post("/api/identity/phone_account/test",
                   json={"account_sid": "ACtest", "auth_token": "nope"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "auth"


def test_twilio_search_with_price(twilio):
    with _client(phone_mod.router) as c:
        r = c.get("/api/identity/phone_account/available",
                  params={"country": "us", "area_code": "415", "contains": "55"}).json()
        none = c.get("/api/identity/phone_account/available",
                     params={"country": "GB", "type": "local"}).json()
    assert r["price"] == {"monthly": "1.15", "unit": "USD"}
    assert r["numbers"][0]["number"] == "+14155550100" and r["numbers"][0]["sms"]
    search = next(x for x in twilio.calls if "AvailablePhoneNumbers/US" in x[1])
    assert search[2]["AreaCode"] == "415" and search[2]["Contains"] == "55"
    assert none["numbers"] == []


def test_twilio_buy_needs_confirmation(twilio):
    with _client(phone_mod.router) as c:
        r = c.post("/api/identity/phone_account/buy", json={"phone_number": "+14155550100"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "confirm"
    assert not [x for x in twilio.calls if x[0] == "POST"]


def test_twilio_buy_for_agent_wires_webhooks(twilio, monkeypatch):
    monkeypatch.setenv("DOT_PUBLIC_URL", "https://dot.example.com/")
    ag = _agent()
    with _client(phone_mod.router) as c:
        r = c.post("/api/identity/phone_account/buy",
                   json={"phone_number": "+14155550100", "confirm": True,
                         "agent_id": ag["id"]}).json()
    assert r["ok"] and r["wired"] and r["sid"] == "PN100"
    post = next(x for x in twilio.calls if x[0] == "POST")
    assert post[2]["PhoneNumber"] == "+14155550100"
    assert post[2]["SmsUrl"] == f"https://dot.example.com/hook/hooktok/twilio/sms/{ag['id']}"
    assert post[2]["VoiceUrl"].endswith(f"/twilio/voice/{ag['id']}")
    cfg = phone_mod.get_config(ag["id"])
    assert cfg["from_number"] == "+14155550100" and cfg["number_sid"] == "PN100"
    assert cfg["account_sid"] == "ACtest"  # shared account merged in


def test_twilio_assign_uses_https_origin(twilio):
    ag = _agent()
    with _client(phone_mod.router) as c:
        r = c.post(f"/api/identity/phone/{ag['id']}/assign", json={"number": "+14155550199"},
                   headers={"Origin": "https://agents.example.org"}).json()
        nums = c.get("/api/identity/phone_account/numbers").json()
    assert r["wired"] and r["sid"] == "PN199"
    upd = next(x for x in twilio.calls if x[0] == "POST")
    assert upd[1].endswith("/IncomingPhoneNumbers/PN199.json")
    assert upd[2]["SmsUrl"] == \
        f"https://agents.example.org/hook/hooktok/twilio/sms/{ag['id']}"
    assert upd[2]["SmsMethod"] == "POST" and upd[2]["VoiceMethod"] == "POST"
    assert nums[0]["agent_id"] == ag["id"]


def test_twilio_assign_without_public_link(twilio):
    ag = _agent()
    with _client(phone_mod.router) as c:
        r = c.post(f"/api/identity/phone/{ag['id']}/assign",
                   json={"number": "+14155550199", "sid": "PN199"}).json()
        private = c.get("/api/identity/phone_account/public_url",
                        headers={"Origin": "https://box.tail1234.ts.net"}).json()
    assert r["ok"] and r["wired"] is False and r["reason"] == "no_public_url"
    assert not [x for x in twilio.calls if x[0] == "POST"]
    assert phone_mod.get_config(ag["id"])["from_number"] == "+14155550199"
    assert private == {"url": "", "reason": "private_link"}


def test_twilio_assign_refuses_someone_elses_number(twilio):
    a, b = _agent("A"), _agent("B")
    phone_mod.set_config(a["id"], {"from_number": "+14155550199"})
    with _client(phone_mod.router) as c:
        r = c.post(f"/api/identity/phone/{b['id']}/assign", json={"number": "+14155550199"})
        assert r.status_code == 409
        c.post(f"/api/identity/phone/{a['id']}/unassign")
    assert "from_number" not in phone_mod.get_config(a["id"])


# ---------------- app catalog ----------------
def test_catalog_shape():
    cats = {c["id"] for c in CATALOG_CATEGORIES}
    ids = [e["id"] for e in CATALOG]
    assert len(ids) == len(set(ids)) and 6 <= len(ids) <= 20
    for e in CATALOG:
        assert e["category"] in cats
        assert e["name"]["en"] and e["name"]["zh"] and e["blurb"]["en"] and e["blurb"]["zh"]
        assert e["needs"] in (None, "node", "uv", "docker")
        assert bool(e.get("url")) != bool(e.get("command"))
        if e.get("command"):
            assert {"npx": "node", "uvx": "uv", "docker": "docker"}[e["command"]] == e["needs"]
        for f in e["fields"]:
            assert f["label"]["en"] and f["label"]["zh"]
            assert "{" + f["key"] + "}" in repr(e)  # every field is used somewhere
            if f["link"]:
                assert f["link"].startswith("https://")


def test_build_spec_secrets_become_vault_placeholders():
    notion = build_spec(catalog_entry("notion"), {}, {"token": "MCP_NOTION_TOKEN"})
    assert notion == {"command": "npx", "args": ["-y", "@notionhq/notion-mcp-server"],
                      "env": {"NOTION_TOKEN": "{{vault:MCP_NOTION_TOKEN}}"}}
    ha = build_spec(catalog_entry("home-assistant"), {"ha_url": "http://ha.lan:8123/"},
                    {"token": "T"})
    assert ha == {"url": "http://ha.lan:8123/api/mcp",
                  "headers": {"Authorization": "Bearer {{vault:T}}"}}
    hosted = build_spec(catalog_entry("hosted"), {"url": "https://x.example/mcp"}, {})
    assert hosted == {"url": "https://x.example/mcp"}  # optional token left out
    with pytest.raises(ValueError):
        build_spec(catalog_entry("files"), {}, {})


def test_add_from_catalog(monkeypatch):
    started = []
    monkeypatch.setattr(app_catalog, "spawn", lambda coro: (started.append(1), coro.close()))
    with _client(app_catalog.router) as c:
        cat = c.get("/api/connectors/catalog").json()
        assert {"categories", "entries", "have", "installed"} <= set(cat)
        r1 = c.post("/api/connectors/catalog/files", json={"values": {"folder": "/tmp/a"}}).json()
        r2 = c.post("/api/connectors/catalog/files", json={"values": {"folder": "/tmp/b"}}).json()
        r3 = c.post("/api/connectors/catalog/todoist", json={"values": {"token": "sk"}}).json()
        bad = c.post("/api/connectors/catalog/github", json={"values": {}})
        installed = c.get("/api/connectors/catalog").json()["installed"]
    assert r1["name"] == "files" and r2["name"] == "files-2"
    assert r2["spec"]["args"][-1] == "/tmp/b"
    assert r3["spec"]["env"] == {"TODOIST_API_KEY": "{{vault:MCP_TODOIST_TOKEN}}"}
    assert gatekeeper.vault_all()["MCP_TODOIST_TOKEN"] == "sk"
    assert bad.status_code == 400
    assert {"files", "files-2", "todoist"} <= set(installed)
    assert started == [1, 1, 1]
    gatekeeper.vault_set("MCP_TODOIST_TOKEN", None)
