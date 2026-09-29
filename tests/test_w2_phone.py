"""Agent phone/SMS: outbound Twilio REST calls (mocked via monkeypatched httpx.AsyncClient),
inbound webhook signature validation, and voicemail summarisation trigger."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac

from fastapi import FastAPI
from fastapi.testclient import TestClient

from opendot import ext as ext_mod
from opendot.db import db, new_id
from opendot.ext import phone as phone_mod
from opendot.gatekeeper import DEFAULT_POLICY
from opendot.tools import EXEC, TOOLS, Ctx


def _agent(name="Scout"):
    return db.insert("agents", id=new_id("ag_"), name=name, emoji="x", color="#fff", role="r")


def test_phone_tools_registered():
    for name in ("send_sms", "make_call"):
        assert name in TOOLS
        assert name in EXEC
    assert DEFAULT_POLICY["send_sms"] == "ask"
    assert DEFAULT_POLICY["make_call"] == "ask"


def test_phone_ext_loaded():
    ext_mod.load_all()
    assert any(m.__name__ == "opendot.ext.phone" for m in ext_mod.MODULES)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient so no real Twilio calls happen."""
    sent: list[dict] = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None, auth=None):
        _FakeAsyncClient.sent.append({"url": url, "data": data, "auth": auth})
        return _FakeResponse(201, {"sid": "SM123", "status": "queued"})

    async def get(self, url, auth=None):
        return _FakeResponse(200, {"friendly_name": "Test Account"})


def test_send_sms_uses_twilio_rest(monkeypatch):
    monkeypatch.setattr(phone_mod.httpx, "AsyncClient", _FakeAsyncClient)
    ag = _agent()
    phone_mod.set_config(ag["id"], {"account_sid": "ACxxx", "auth_token": "tok",
                                    "from_number": "+15550000000"})

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["send_sms"](ctx, to="+15551234567", body="hello")

    result = asyncio.run(run())
    assert result["ok"] is True
    assert result["sid"] == "SM123"
    assert _FakeAsyncClient.sent
    call = _FakeAsyncClient.sent[-1]
    assert call["data"]["Body"] == "hello"
    row = db.one("SELECT * FROM phone_log WHERE agent_id=? AND kind='sms'", ag["id"])
    assert row and row["to_number"] == "+15551234567"


def test_send_sms_missing_config_errors():
    ag = _agent("NoConfig")

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["send_sms"](ctx, to="+1555", body="x")

    result = asyncio.run(run())
    assert "error" in result


def test_make_call_builds_twiml(monkeypatch):
    monkeypatch.setattr(phone_mod.httpx, "AsyncClient", _FakeAsyncClient)
    ag = _agent("Quill")
    phone_mod.set_config(ag["id"], {"account_sid": "ACxxx", "auth_token": "tok",
                                    "from_number": "+15550000000"})

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["make_call"](ctx, to="+15551234567", message="Hi there")

    result = asyncio.run(run())
    assert result["ok"] is True
    call = _FakeAsyncClient.sent[-1]
    assert "<Say>Hi there</Say>" in call["data"]["Twiml"]


def _twilio_sig(auth_token: str, url: str, params: dict) -> str:
    s = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    digest = hmac.new(auth_token.encode(), s.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def test_signature_validation_roundtrip():
    params = {"From": "+15551234567", "To": "+15550000000", "Body": "hi"}
    url = "https://dot.example/hook/tok/twilio/sms/scout"
    sig = _twilio_sig("secret", url, params)
    assert phone_mod._validate_signature("secret", url, params, sig)
    assert not phone_mod._validate_signature("secret", url, params, "bad-signature")


def _app():
    app = FastAPI()
    app.include_router(phone_mod.router)
    return app


def _signed_agent(name):
    db.kv_set("hook_token", "testtoken")
    ag = _agent(name)
    phone_mod.set_config(ag["id"], {"account_sid": "ACxxx", "auth_token": "secret",
                                    "from_number": "+15550000000"})
    return ag


def _post(c, path, data, url_base="http://testserver", headers=None):
    sig = _twilio_sig("secret", url_base + path, data)
    return c.post(path, data=data, headers={"X-Twilio-Signature": sig, **(headers or {})})


def test_sms_webhook_rejected_when_no_token(fake_llm):
    db.kv_set("hook_token", "testtoken")
    ag = _agent("Scout")
    with TestClient(_app()) as c:
        r = c.post("/hook/testtoken/twilio/sms/scout",
                  data={"From": "+15551234567", "To": "+15550000000", "Body": "hello agent"})
    assert r.status_code == 403
    assert not db.one("SELECT 1 FROM phone_log WHERE agent_id=?", ag["id"])


def test_sms_webhook_rejects_missing_signature_when_token_set(fake_llm):
    ag = _signed_agent("Nosig")
    with TestClient(_app()) as c:
        r = c.post(f"/hook/testtoken/twilio/sms/{ag['id']}", data={"From": "+1", "Body": "x"})
    assert r.status_code == 403


def test_sms_webhook_rejects_bad_signature_when_token_set(fake_llm):
    _signed_agent("Pixel")
    with TestClient(_app()) as c:
        r = c.post("/hook/testtoken/twilio/sms/pixel",
                  data={"From": "+1555", "To": "+1555", "Body": "hi"},
                  headers={"X-Twilio-Signature": "totally-wrong"})
    assert r.status_code == 403


def test_sms_webhook_signed_by_name_and_by_id(fake_llm):
    ag = _signed_agent("Scout Two")
    data = {"From": "+15551234567", "To": "+15550000000", "Body": "hello agent"}
    with TestClient(_app()) as c:
        assert _post(c, "/hook/testtoken/twilio/sms/scout two", data).status_code == 200
        # id works too, and keeps working after a rename
        db.update("agents", ag["id"], name="Renamed")
        r = _post(c, f"/hook/testtoken/twilio/sms/{ag['id']}", data)
    assert r.status_code == 200 and "<Response" in r.text
    rows = db.q("SELECT * FROM phone_log WHERE agent_id=? AND kind='sms'", ag["id"])
    assert len(rows) == 2 and rows[0]["body"] == "hello agent"


def test_signature_behind_proxy_uses_forwarded_headers(fake_llm):
    ag = _signed_agent("Proxied")
    path = f"/hook/testtoken/twilio/sms/{ag['id']}"
    data = {"From": "+1", "Body": "via tunnel"}
    with TestClient(_app()) as c:
        # Twilio signed the public https URL; we see plain http on localhost
        r = _post(c, path, data, url_base="https://dot.example.com",
                  headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "dot.example.com"})
        assert r.status_code == 200
        # without the forwarded host we can't rebuild it → rejected
        r = _post(c, path, data, url_base="https://dot.example.com")
        assert r.status_code == 403


def test_signature_uses_public_url_setting(fake_llm, monkeypatch):
    monkeypatch.setenv("DOT_PUBLIC_URL", "https://my-dot.example.org/")
    ag = _signed_agent("Public")
    with TestClient(_app()) as c:
        r = _post(c, f"/hook/testtoken/twilio/sms/{ag['id']}", {"From": "+1", "Body": "x"},
                  url_base="https://my-dot.example.org")
    assert r.status_code == 200


def test_voice_twiml_separates_recording_and_transcription(fake_llm):
    ag = _signed_agent("Caller")
    with TestClient(_app()) as c:
        r = _post(c, "/hook/testtoken/twilio/voice/caller", {"From": "+1", "CallSid": "CA1"})
    assert r.status_code == 200
    assert f'action="/hook/testtoken/twilio/recording/{ag["id"]}"' in r.text
    assert f'transcribeCallback="/hook/testtoken/twilio/transcription/{ag["id"]}"' in r.text


def test_voicemail_logged_once_and_agent_woken_once(fake_llm, monkeypatch):
    woken = []
    monkeypatch.setattr(phone_mod, "spawn", lambda coro: (woken.append(1), coro.close()))
    ag = _signed_agent("Pip")
    base = {"From": "+15551234567", "To": "+1555", "CallSid": "CA1",
            "RecordingSid": "RE1", "RecordingUrl": "https://x/rec"}
    with TestClient(_app()) as c:
        assert _post(c, f"/hook/testtoken/twilio/recording/{ag['id']}", base).status_code == 200
        assert woken == []  # recording done, but wait for the transcript
        t = {**base, "TranscriptionText": "please call me back",
             "TranscriptionStatus": "completed"}
        assert _post(c, f"/hook/testtoken/twilio/transcription/{ag['id']}", t).status_code == 200
        # Twilio retries are harmless
        assert _post(c, f"/hook/testtoken/twilio/transcription/{ag['id']}", t).status_code == 200
    rows = db.q("SELECT * FROM phone_log WHERE agent_id=? AND kind='call'", ag["id"])
    assert len(rows) == 1
    assert rows[0]["transcript"] == "please call me back"
    assert woken == [1]
    ev = db.q("SELECT * FROM events WHERE source=?", f"call:{ag['name'].lower()}")
    assert len(ev) == 1


def test_voicemail_failed_transcription_still_wakes_once(fake_llm, monkeypatch):
    woken = []
    monkeypatch.setattr(phone_mod, "spawn", lambda coro: (woken.append(1), coro.close()))
    ag = _signed_agent("Mumble")
    base = {"From": "+1", "RecordingUrl": "https://x/rec2"}
    with TestClient(_app()) as c:
        _post(c, f"/hook/testtoken/twilio/recording/{ag['id']}", base)
        _post(c, f"/hook/testtoken/twilio/transcription/{ag['id']}",
              {**base, "TranscriptionStatus": "failed"})
    assert woken == [1]


def test_voicemail_without_transcription_wakes_on_recording(fake_llm, monkeypatch):
    woken = []
    monkeypatch.setattr(phone_mod, "spawn", lambda coro: (woken.append(1), coro.close()))
    db.kv_set("hook_token", "testtoken")
    ag = _agent("Quiet")
    phone_mod.set_config(ag["id"], {"account_sid": "ACxxx", "auth_token": "secret",
                                    "from_number": "+1", "transcribe": False})
    with TestClient(_app()) as c:
        r = _post(c, f"/hook/testtoken/twilio/voice/{ag['id']}", {"From": "+1"})
        assert "transcribe" not in r.text
        _post(c, f"/hook/testtoken/twilio/recording/{ag['id']}",
              {"From": "+1", "RecordingUrl": "https://x/rec3"})
    assert woken == [1]
    assert len(db.q("SELECT 1 FROM phone_log WHERE agent_id=? AND kind='call'", ag["id"])) == 1


def test_recording_webhook_legacy_combined_callback(fake_llm):
    # calls answered before the split still post the transcript to /recording
    ag = _signed_agent("Legacy")
    with TestClient(_app()) as c:
        r = _post(c, "/hook/testtoken/twilio/recording/legacy",
                  {"From": "+15551234567", "To": "+1555", "RecordingUrl": "https://x/rec.mp3",
                   "TranscriptionText": "please call me back"})
    assert r.status_code == 200
    row = db.one("SELECT * FROM phone_log WHERE agent_id=? AND kind='call'", ag["id"])
    assert row and row["transcript"] == "please call me back"
    ev = db.one("SELECT * FROM events WHERE source=?", f"call:{ag['name'].lower()}")
    assert ev is not None
