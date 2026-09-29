"""Devices: the device registry, the request/response bridge, and the
device_* tools. The real node client (``node/dot_node.py``) and macOS-only
paths (AppleScript, screencapture, pbcopy/pbpaste) are exercised manually
against a live server + a real (Linux) node process — see docs/ws/W4.md —
not here, since there's no real WebSocket peer in a unit test.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from opendot.db import db, new_id
from opendot.ext import nodes
from opendot.gatekeeper import DEFAULT_POLICY
from opendot.tools import EXEC, TOOLS, Ctx


@pytest.fixture(autouse=True)
def _clean_devices():
    with db.lock:
        db.conn.execute("DELETE FROM devices")
        db.conn.commit()
    nodes._conns.clear()
    nodes._waiters.clear()
    yield
    nodes._conns.clear()
    nodes._waiters.clear()


def _agent(name="Scout"):
    return db.insert("agents", id=new_id("ag_"), name=name, emoji="x", color="#fff", role="r")


def _device(id_="dev1", name="MyMac", approved=1, caps=("shell", "open")):
    return db.insert("devices", id=id_, name=name, os="Darwin 23", approved=approved,
                     capabilities=json.dumps(list(caps)), last_seen=0, created=0)


class FakeWS:
    """Stands in for the WebSocket the server holds for a connected node.
    ``responder(raw_text)`` is called synchronously from ``send_text`` and is
    expected to resolve the matching entry in ``nodes._waiters``."""

    def __init__(self, responder):
        self.sent: list[str] = []
        self._responder = responder

    async def send_text(self, text: str) -> None:
        self.sent.append(text)
        await self._responder(text)

    async def close(self) -> None:
        pass


def _ok_responder(result: dict):
    async def respond(raw: str) -> None:
        msg = json.loads(raw)
        fut = nodes._waiters.get(msg["id"])
        if fut and not fut.done():
            fut.set_result({"type": "response", "id": msg["id"], "ok": True, "result": result})
    return respond


# ---------------- registration ----------------
def test_device_tools_registered():
    for name in ("device_list", "device_shell", "device_applescript", "device_open",
                 "device_screenshot", "device_clipboard", "device_notify", "device_files"):
        assert name in TOOLS
        assert name in EXEC
    assert DEFAULT_POLICY["device_list"] == "allow"
    assert DEFAULT_POLICY["device_notify"] == "allow"
    assert DEFAULT_POLICY["device_shell"] == "ask"
    assert DEFAULT_POLICY["device_screenshot"] == "ask"


def test_ext_loaded():
    from opendot import ext as ext_mod
    ext_mod.load_all()
    assert any(m.__name__ == "opendot.ext.nodes" for m in ext_mod.MODULES)


# ---------------- device_list ----------------
def test_device_list_reports_online_and_capabilities():
    _device("dev1", "MyMac", caps=("shell", "screenshot"))
    nodes._conns["dev1"] = object()  # presence alone marks it online
    ag = _agent()

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["device_list"](ctx)

    result = asyncio.run(run())
    d = result["devices"][0]
    assert d["name"] == "MyMac"
    assert d["online"] is True
    assert set(d["capabilities"]) == {"shell", "screenshot"}


# ---------------- error paths ----------------
def test_device_shell_unknown_device():
    ag = _agent("A1")

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["device_shell"](ctx, device="ghost", command="echo hi")

    result = asyncio.run(run())
    assert "no device called" in result["error"]


def test_device_shell_offline_device():
    _device("dev2", "OfflineBox")  # not in nodes._conns
    ag = _agent("A2")

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["device_shell"](ctx, device="OfflineBox", command="echo hi")

    result = asyncio.run(run())
    assert "offline" in result["error"]


def test_device_shell_disabled_device_refused():
    _device("dev3", "PausedMac", approved=0)
    nodes._conns["dev3"] = FakeWS(_ok_responder({"exit_code": 0, "output": "should not run"}))
    ag = _agent("A3")

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["device_shell"](ctx, device="PausedMac", command="echo hi")

    result = asyncio.run(run())
    assert "disabled" in result["error"]


# ---------------- round trip over the (fake) bridge ----------------
def test_device_shell_round_trip_by_name():
    _device("dev4", "LiveMac")
    nodes._conns["dev4"] = FakeWS(_ok_responder({"exit_code": 0, "output": "hi\n"}))
    ag = _agent("A4")

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["device_shell"](ctx, device="LiveMac", command="echo hi")

    result = asyncio.run(run())
    assert result == {"exit_code": 0, "output": "hi\n"}


def test_device_shell_lookup_by_id_too():
    _device("dev5", "IdLookupMac")
    nodes._conns["dev5"] = FakeWS(_ok_responder({"exit_code": 0, "output": "ok"}))
    ag = _agent("A5")

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["device_shell"](ctx, device="dev5", command="echo ok")

    assert asyncio.run(run())["output"] == "ok"


def test_device_call_reports_remote_error():
    _device("dev6", "ErrMac")

    async def bad_responder(raw: str) -> None:
        msg = json.loads(raw)
        fut = nodes._waiters.get(msg["id"])
        fut.set_result({"type": "response", "id": msg["id"], "ok": False,
                        "error": "refused locally: needs root"})
    nodes._conns["dev6"] = FakeWS(bad_responder)
    ag = _agent("A6")

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["device_shell"](ctx, device="ErrMac", command="sudo rm -rf /")

    result = asyncio.run(run())
    assert result["error"] == "refused locally: needs root"


def test_device_call_times_out():
    _device("dev7", "SlowMac")

    async def never_responds(raw: str) -> None:
        return None  # never resolves the waiter's future
    nodes._conns["dev7"] = FakeWS(never_responds)
    ag = _agent("A7")

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await nodes._call("SlowMac", "shell", {"command": "echo hi"}, timeout=0.05)

    result = asyncio.run(run())
    assert "didn't respond" in result["error"]
    assert "SlowMac" not in nodes._waiters.values()  # the waiter was cleaned up


# ---------------- device_screenshot: emits a Computer-panel event ----------------
def test_device_screenshot_emits_computer_event(monkeypatch):
    _device("dev8", "ScreenMac", caps=("screenshot",))
    nodes._conns["dev8"] = FakeWS(_ok_responder({"image": "QUJD"}))  # base64("ABC")
    events: list[tuple] = []
    monkeypatch.setattr(nodes.bus, "emit", lambda kind, **data: events.append((kind, data)))
    ag = _agent("A8")

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["device_screenshot"](ctx, device="ScreenMac")

    result = asyncio.run(run())
    assert result["ok"] is True
    assert len(events) == 1
    kind, data = events[0]
    assert kind == "computer"
    assert data["agent_id"] == ag["id"]
    assert data["url"] == "device://ScreenMac"
    assert data["screenshot"] == "data:image/jpeg;base64,QUJD"


def test_device_screenshot_no_image_is_an_error():
    _device("dev9", "NoImageMac")
    nodes._conns["dev9"] = FakeWS(_ok_responder({}))
    ag = _agent("A9")

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["device_screenshot"](ctx, device="NoImageMac")

    assert "error" in asyncio.run(run())


# ---------------- device_files ----------------
def test_device_files_action_mapping():
    _device("dev10", "FilesMac")
    seen = {}

    async def capture(raw: str) -> None:
        msg = json.loads(raw)
        seen["method"] = msg["method"]
        seen["params"] = msg["params"]
        fut = nodes._waiters.get(msg["id"])
        fut.set_result({"type": "response", "id": msg["id"], "ok": True, "result": {"ok": True}})
    nodes._conns["dev10"] = FakeWS(capture)
    ag = _agent("A10")

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["device_files"](ctx, device="FilesMac", action="write",
                                          path="~/Desktop/x.txt", content="hi", append=False)

    asyncio.run(run())
    assert seen["method"] == "write_file"
    assert seen["params"] == {"path": "~/Desktop/x.txt", "content": "hi", "append": False}


def test_device_files_bad_action():
    _device("dev11", "BadActionMac")
    ag = _agent("A11")

    async def run():
        ctx = Ctx(agent=ag, thread_id="t1", run_id="r1")
        return await EXEC["device_files"](ctx, device="BadActionMac", action="delete",
                                          path="~/Desktop/x.txt")

    result = asyncio.run(run())
    assert "action must be" in result["error"]


# ---------------- REST ----------------
def test_rest_list_patch_delete(client, auth_headers):
    _device("dev12", "RestMac")

    r = client.get("/api/nodes", headers=auth_headers)
    assert r.status_code == 200
    row = next(d for d in r.json() if d["id"] == "dev12")
    assert row["name"] == "RestMac"
    assert row["online"] is False

    r = client.patch("/api/nodes/dev12", json={"name": "Renamed"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"

    r = client.patch("/api/nodes/dev12", json={"approved": False}, headers=auth_headers)
    assert r.json()["approved"] is False

    r = client.delete("/api/nodes/dev12", headers=auth_headers)
    assert r.json() == {"ok": True}
    assert db.one("SELECT * FROM devices WHERE id=?", "dev12") is None


def test_rest_patch_unknown_device_404(client, auth_headers):
    r = client.patch("/api/nodes/does-not-exist", json={"name": "x"}, headers=auth_headers)
    assert r.status_code == 404


def test_rest_requires_auth(client):
    r = client.get("/api/nodes")
    assert r.status_code == 401


def test_download_client_is_public_and_serves_the_real_file(client):
    r = client.get("/dl/dot_node.py")
    assert r.status_code == 200
    assert b"OpenDot node client" in r.content
    assert b"import websockets" in r.content


def test_websocket_rejects_bad_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/api/nodes/ws?token=not-the-token"):
            pass


# ---------------- PROMPT ----------------
def test_prompt_is_none_without_devices():
    assert nodes.PROMPT({"id": "any"}) is None


def test_prompt_lists_online_devices_when_paired():
    _device("dev13", "PromptMac", caps=("shell", "screenshot"))
    nodes._conns["dev13"] = object()
    text = nodes.PROMPT({"id": "any"})
    assert text is not None
    assert "PromptMac" in text
    assert "device_shell" in text


def test_prompt_mentions_offline_when_none_online():
    _device("dev14", "OfflineOnlyMac")
    text = nodes.PROMPT({"id": "any"})
    assert text is not None
    assert "offline" in text
