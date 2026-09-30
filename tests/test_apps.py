"""Apps: connecting MCP servers end to end — a local program, an online app with no
sign-in, and an online app with the full "Sign in → Allow" flow (a mock app that does
real OAuth: discovery, dynamic registration, PKCE, code exchange, refresh)."""

from __future__ import annotations

import asyncio
import socket
import sys
import textwrap
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest

from opendot import connectors, gatekeeper, mcp_oauth
from opendot.app_directory import APPS, CATEGORIES, build_spec, entry
from opendot.connectors import MCPHub, can_use, load_config, save_config


# ---------------- a mock app ----------------
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _mock_app(port: int, *, oauth: bool, guard: bool = True):
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations
    from starlette.requests import Request
    from starlette.responses import JSONResponse, RedirectResponse, Response
    from starlette.routing import Route

    base = f"http://127.0.0.1:{port}"
    notes: list[str] = ["buy milk"]
    state = {"codes": {}, "tokens": set(), "refresh": set(), "clients": {}, "n": 0}
    srv = MCPServer("notes-app")

    @srv.tool(annotations=ToolAnnotations(read_only_hint=True, title="List notes"))
    def list_notes() -> str:
        """All notes."""
        return "\n".join(notes)

    @srv.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    def add_note(text: str) -> str:
        """Add a note."""
        notes.append(text)
        return "added"

    app = srv.streamable_http_app(host="127.0.0.1")

    async def prm(req):
        return JSONResponse({"resource": f"{base}/mcp", "authorization_servers": [base]})

    async def asm(req):
        return JSONResponse({"issuer": base, "authorization_endpoint": f"{base}/authorize",
                             "token_endpoint": f"{base}/token",
                             "registration_endpoint": f"{base}/register",
                             "response_types_supported": ["code"],
                             "grant_types_supported": ["authorization_code", "refresh_token"],
                             "code_challenge_methods_supported": ["S256"],
                             "token_endpoint_auth_methods_supported": ["none"]})

    async def register(req: Request):
        body = await req.json()
        state["n"] += 1
        cid = f"client-{state['n']}"
        state["clients"][cid] = body
        return JSONResponse({**body, "client_id": cid, "token_endpoint_auth_method": "none"}, 201)

    async def authorize(req: Request):  # the user "presses Allow" straight away
        q = req.query_params
        code = f"code-{len(state['codes'])}"
        state["codes"][code] = q["client_id"]
        return RedirectResponse(f"{q['redirect_uri']}?{urlencode({'code': code, 'state': q['state']})}")

    async def token(req: Request):
        form = await req.form()
        if form["grant_type"] == "authorization_code":
            if form["code"] not in state["codes"]:
                return JSONResponse({"error": "invalid_grant"}, 400)
        elif form["grant_type"] == "refresh_token":
            if form["refresh_token"] not in state["refresh"]:
                return JSONResponse({"error": "invalid_grant"}, 400)
        tok, ref = f"tok-{time.time_ns()}", f"ref-{time.time_ns()}"
        state["tokens"].add(tok)
        state["refresh"].add(ref)
        return JSONResponse({"access_token": tok, "token_type": "Bearer", "expires_in": 3600,
                             "refresh_token": ref})

    if oauth:
        app.router.routes[0:0] = [
            Route("/.well-known/oauth-protected-resource/mcp", prm),
            Route("/.well-known/oauth-authorization-server", asm),
            Route("/register", register, methods=["POST"]),
            Route("/authorize", authorize),
            Route("/token", token, methods=["POST"]),
        ]

        class Guard:
            def __init__(self, inner):
                self.inner = inner

            async def __call__(self, scope, receive, send):
                if scope["type"] == "http" and scope["path"].startswith("/mcp"):
                    auth = dict(scope["headers"]).get(b"authorization", b"").decode()
                    if auth.removeprefix("Bearer ") not in state["tokens"]:
                        r = Response(status_code=401, headers={"WWW-Authenticate":
                            f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource/mcp"'})
                        return await r(scope, receive, send)
                return await self.inner(scope, receive, send)

        if guard:
            app.add_middleware(lambda inner: Guard(inner))  # noqa: PLW0108
    return app, notes, state


@pytest.fixture(scope="module")
def oauth_app():
    import uvicorn
    port = _free_port()
    app, notes, state = _mock_app(port, oauth=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", notes, state
    server.should_exit = True


@pytest.fixture(scope="module")
def open_app():
    import uvicorn
    port = _free_port()
    app, notes, _ = _mock_app(port, oauth=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", notes
    server.should_exit = True


async def _wait(srv, *states, timeout=20):
    t0 = time.time()
    while srv.status not in states:
        assert time.time() - t0 < timeout, f"stuck at {srv.status}: {srv.error}"
        await asyncio.sleep(0.05)


# ---------------- tests ----------------
def test_open_app_connects_and_calls(open_app):
    url, notes = open_app

    async def go():
        hub = MCPHub()
        srv = hub.launch("notes", {"url": f"{url}/mcp", "agents": ["ag_a"]})
        await _wait(srv, "connected", "error")
        assert srv.status == "connected", srv.error
        names = {t["tool"]: t for t in srv.tools.values()}
        assert names["list_notes"]["read_only"] and not names["add_note"]["read_only"]
        assert names["list_notes"]["title"] == "List notes"
        # only the agents it's given to see it
        assert [d["function"]["name"] for d in hub.tool_defs("ag_a")]
        assert hub.tool_defs("ag_b") == []
        r = await hub.call("mcp__notes__add_note", {"text": "call mum"}, "ag_a")
        assert r["result"] == "added" and "call mum" in notes
        denied = await hub.call("mcp__notes__add_note", {"text": "x"}, "ag_b")
        assert "isn't allowed" in denied["error"]
        await hub.stop_all()

    asyncio.run(go())


def test_oauth_sign_in_flow(oauth_app):
    url, notes, state = oauth_app
    spec = {"url": f"{url}/mcp", "auth": "oauth", "agents": "all", "label": "Notes"}
    redirect = "http://localhost:7878/oauth/callback"

    async def go():
        hub = MCPHub()
        # nobody at the screen → it just says a sign-in is needed
        quiet = hub.launch("notesapp", spec)
        await _wait(quiet, "sign_in", "error")
        assert quiet.status == "sign_in" and not quiet.auth_url
        # you press Connect → sign-in link → "Allow" → back to the callback
        srv = hub.launch("notesapp", spec, {"redirect_uri": redirect})
        await _wait(srv, "sign_in", "error")
        assert srv.auth_url.startswith(f"{url}/authorize")
        q = parse_qs(urlparse(srv.auth_url).query)
        assert q["redirect_uri"] == [redirect] and q["code_challenge_method"] == ["S256"]
        async with httpx.AsyncClient() as c:
            r = await c.get(srv.auth_url)
        back = parse_qs(urlparse(r.headers["location"]).query)
        assert mcp_oauth.complete({k: v[0] for k, v in back.items()}) == "notesapp"
        assert mcp_oauth.complete({k: v[0] for k, v in back.items()}) is None  # single use
        await _wait(srv, "connected", "error")
        assert srv.status == "connected", srv.error
        r = await hub.call("mcp__notesapp__list_notes", {})
        assert "buy milk" in r["result"]
        # the sign-in is kept in the encrypted vault, and tokens never sit in plain files
        assert mcp_oauth.signed_in("notesapp")
        assert "tok-" not in (gatekeeper.settings.DATA_DIR / "vault.enc").read_text(errors="ignore")
        await hub.stop_all()
        # after a restart it connects on its own, no sign-in
        hub2 = MCPHub()
        again = hub2.launch("notesapp", spec)
        await _wait(again, "connected", "sign_in", "error")
        assert again.status == "connected", again.error
        assert state["n"] == 1  # registered once
        await hub2.stop_all()
        mcp_oauth.forget("notesapp")

    asyncio.run(go())


def test_sign_in_even_when_the_app_answers_without_one():
    """Google's servers list their tools without a sign-in and only refuse the real work:
    Connect must still start the sign-in, not show an app that can't do anything."""
    import uvicorn
    port = _free_port()
    app, notes, state = _mock_app(port, oauth=True, guard=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    spec = {"url": f"{url}/mcp", "auth": "oauth", "agents": "all", "label": "Lenient"}

    async def go():
        hub = MCPHub()
        quiet = hub.launch("lenient", spec)
        await _wait(quiet, "sign_in", "connected", "error")
        assert quiet.status == "sign_in"
        srv = hub.launch("lenient", spec, {"redirect_uri": "http://localhost:7878/oauth/callback"})
        await _wait(srv, "sign_in", "connected", "error")
        assert srv.status == "sign_in" and srv.auth_url.startswith(f"{url}/authorize")
        async with httpx.AsyncClient() as c:
            r = await c.get(srv.auth_url)
        back = parse_qs(urlparse(r.headers["location"]).query)
        mcp_oauth.complete({k: v[0] for k, v in back.items()})
        await _wait(srv, "connected", "error")
        assert srv.status == "connected" and mcp_oauth.signed_in("lenient")
        await hub.stop_all()
        mcp_oauth.forget("lenient")

    try:
        asyncio.run(go())
    finally:
        server.should_exit = True


def test_callback_page_is_open_and_needs_a_live_state(client):
    r = client.get("/oauth/callback?code=x&state=nope", headers={"Authorization": ""})
    assert r.status_code == 200 and "didn" in r.text


def test_local_program(tmp_path):
    script = tmp_path / "srv.py"
    script.write_text(textwrap.dedent("""
        from mcp.server.mcpserver import MCPServer
        s = MCPServer("echo")
        @s.tool()
        def echo(text: str) -> str:
            return text.upper()
        s.run()
    """))

    async def go():
        hub = MCPHub()
        srv = hub.launch("echo", {"command": sys.executable, "args": [str(script)]})
        await _wait(srv, "connected", "error", timeout=40)
        assert srv.status == "connected", srv.error
        r = await hub.call("mcp__echo__echo", {"text": "hi"})
        assert r["result"] == "HI"
        await hub.stop_all()

    asyncio.run(go())


def test_missing_program_says_so():
    async def go():
        hub = MCPHub()
        srv = hub.launch("nope", {"command": "definitely-not-installed-xyz", "args": []})
        await _wait(srv, "error", "connected", timeout=20)
        assert srv.status == "error" and "installed" in srv.error
        await hub.stop_all()

    asyncio.run(go())


def test_gatekeeper_reads_app_rules(open_app, monkeypatch):
    url, _ = open_app
    cfg = load_config()
    cfg["mcp"]["notes"] = {"url": f"{url}/mcp", "agents": "all"}
    save_config(cfg)

    async def go():
        hub = MCPHub()
        monkeypatch.setattr(connectors, "mcp_hub", hub)
        srv = hub.launch("notes", cfg["mcp"]["notes"])
        await _wait(srv, "connected", "error")
        assert gatekeeper.assess("ag_x", "mcp__notes__list_notes", {})[0] == "allow"
        assert gatekeeper.assess("ag_x", "mcp__notes__add_note", {"text": "a"})[0] == "ask"
        # a check-in may read an app, never change it
        assert gatekeeper.assess("ag_x", "mcp__notes__list_notes", {}, source="heartbeat")[0] == "allow"
        assert gatekeeper.assess("ag_x", "mcp__notes__add_note", {}, source="heartbeat")[0] == "deny"
        c2 = load_config()
        c2["mcp"]["notes"]["tools"] = {"add_note": "never", "list_notes": "ask"}
        save_config(c2)
        assert gatekeeper.assess("ag_x", "mcp__notes__add_note", {})[0] == "deny"
        assert gatekeeper.assess("ag_x", "mcp__notes__list_notes", {})[0] == "ask"
        await hub.stop_all()

    asyncio.run(go())
    cfg = load_config()
    cfg["mcp"].pop("notes", None)
    save_config(cfg)


def test_directory_is_complete():
    cats = {c["id"] for c in CATEGORIES}
    ids = [a["id"] for a in APPS]
    assert len(ids) == len(set(ids))
    for a in APPS:
        assert a["category"] in cats, a["id"]
        assert a["kind"] in ("oauth", "google", "open", "key", "local", "microsoft", "account", "link"), a["id"]
        assert a["name"]["en"] and a["name"]["zh"] and a["blurb"]["en"] and a["blurb"]["zh"]
        assert a.get("url", "").startswith(("https://", "{")) or a.get("command") or a.get("builtin"), a["id"]
        if a["kind"] in ("key", "local", "account", "link"):
            assert a.get("fields"), a["id"]
    g = build_spec(entry("gmail"), {}, {})
    assert g["builtin"] == "gmail" and "gmail.readonly" in g["scopes"]
    assert "mail.google.com" not in g["scopes"]  # never full mailbox access
    assert "gmail.send" not in g["scopes"]  # drafts only
    gh = build_spec(entry("github"), {}, {"token": "APP_GITHUB_TOKEN"})
    assert gh["headers"]["Authorization"] == "Bearer {{vault:APP_GITHUB_TOKEN}}"
    f = build_spec(entry("files"), {"folder": "~/Notes"}, {})
    assert not f["args"][-1].startswith("~")
    with pytest.raises(ValueError):
        build_spec(entry("amap"), {}, {})


def test_can_use():
    assert can_use({"agents": "all"}, "ag_1")
    assert can_use({"agents": ["ag_1"]}, "ag_1-w2")  # its helpers too
    assert not can_use({"agents": ["ag_1"]}, "ag_2")
    assert not can_use({"agents": []}, "ag_1")


def test_vault_is_encrypted_and_migrates(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(gatekeeper.settings, "DATA_DIR", tmp_path)
    gatekeeper.vault_reset_cache()
    (tmp_path / "vault.json").write_text(json.dumps({"OLD": "plain-secret-123"}))
    assert gatekeeper.vault_all()["OLD"] == "plain-secret-123"
    assert not (tmp_path / "vault.json").exists()
    assert b"plain-secret-123" not in (tmp_path / "vault.enc").read_bytes()
    gatekeeper.vault_set("NEW", "another-secret-456")
    gatekeeper.vault_reset_cache()
    assert gatekeeper.vault_all() == {"OLD": "plain-secret-123", "NEW": "another-secret-456"}
    assert gatekeeper.check_paths("ag", "cat ../../vault.enc")[0] == "deny"
    assert gatekeeper.check_paths("ag", "cat ../../vault.key")[0] == "deny"
    monkeypatch.undo()
    gatekeeper.vault_reset_cache()


def test_connect_from_directory_api(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from opendot.db import db, new_id
    from opendot.ext import apps as apps_mod

    front = db.insert("agents", id=new_id("ag_"), name="Pip", emoji="x", color="#fff",
                      role="r", origin="default")
    other = db.insert("agents", id=new_id("ag_"), name="Scout", emoji="x", color="#fff", role="r")
    launched = []

    class FakeSrv:
        def __init__(self):
            self.ready = asyncio.Event()
            self.ready.set()

    monkeypatch.setattr(apps_mod.mcp_hub, "launch",
                        lambda name, spec, interactive=None: (launched.append((name, interactive)), FakeSrv())[1])
    app = FastAPI()
    app.include_router(apps_mod.router)
    with TestClient(app) as c:
        d = c.get("/api/apps").json()
        assert {"categories", "apps", "installed", "have", "google"} <= set(d)
        r1 = c.post("/api/apps/connect/files", json={"values": {"folder": "/tmp/a"}}).json()
        r2 = c.post("/api/apps/connect/files", json={"values": {"folder": "/tmp/b"},
                                                     "agents": "all"}).json()
        r3 = c.post("/api/apps/connect/amap", json={"values": {"key": "k-123"},
                                                    "agents": [other["id"], "ag_ghost"]}).json()
        assert c.post("/api/apps/connect/amap", json={"values": {}}).status_code == 400
        assert c.post("/api/apps/connect/gmail", json={}).json()["detail"] == "google_setup"
        n = c.post("/api/apps/connect/notion", json={"origin": "http://localhost:7878"}).json()
        cfg = load_config()["mcp"]
        assert r1["name"] == "files" and r2["name"] == "files-2"
        assert cfg["files"]["agents"] == [front["id"]]          # default: the front desk
        assert cfg["files-2"]["agents"] == "all"
        assert cfg["amap"]["agents"] == [other["id"]]           # unknown ids dropped
        assert cfg["amap"]["url"] == "https://mcp.amap.com/mcp?key={{vault:APP_AMAP_KEY}}"
        assert gatekeeper.vault_all()["APP_AMAP_KEY"] == "k-123"
        assert cfg["notion"]["auth"] == "oauth"
        assert launched[-1] == ("notion", {"redirect_uri": "http://localhost:7878/oauth/callback"})
        assert n["name"] == "notion"
        # per-tool rules and who can use it
        c.patch("/api/apps/installed/notion", json={"tool": "delete_page", "rule": "never"})
        c.patch("/api/apps/installed/notion", json={"agents": [other["id"]]})
        cfg = load_config()["mcp"]
        assert cfg["notion"]["tools"] == {"delete_page": "never"}
        assert cfg["notion"]["agents"] == [other["id"]]
        # disconnecting forgets its secrets
        assert c.delete("/api/apps/installed/amap").json()["ok"]
        assert "APP_AMAP_KEY" not in gatekeeper.vault_all()
        # Google: your own client, checked for shape
        assert c.put("/api/apps/google", json={"client_id": "abc", "client_secret": "s"}).status_code == 400
        assert c.put("/api/apps/google", json={"client_id": "1-x.apps.googleusercontent.com",
                                               "client_secret": "s"}).json()["ready"]
        g = c.post("/api/apps/connect/gmail", json={"origin": "http://localhost:7878"}).json()
        assert load_config()["mcp"]["gmail"]["builtin"] == "gmail"
        assert g["name"] == "gmail"
        c.delete("/api/apps/google")
        # custom: a link that answers 401 is treated as a sign-in app
        monkeypatch.setattr(apps_mod, "_wants_sign_in", lambda url: asyncio.sleep(0, True))
        cu = c.post("/api/apps/custom", json={"url": "https://mcp.example.com/mcp", "label": "My tool"}).json()
        assert load_config()["mcp"][cu["name"]]["auth"] == "oauth"
        assert c.post("/api/apps/custom", json={"url": "ftp://x"}).status_code == 400
    cfg = load_config()
    cfg["mcp"] = {}
    save_config(cfg)


def test_expired_token_is_refreshed(oauth_app):
    """An access token that has run out is swapped for a new one with the refresh token,
    with no sign-in."""
    url, notes, state = oauth_app
    spec = {"url": f"{url}/mcp", "auth": "oauth", "agents": "all"}

    async def go():
        hub = MCPHub()
        srv = hub.launch("refresher", spec, {"redirect_uri": "http://localhost:7878/oauth/callback"})
        await _wait(srv, "sign_in", "error")
        async with httpx.AsyncClient() as c:
            r = await c.get(srv.auth_url)
        back = parse_qs(urlparse(r.headers["location"]).query)
        mcp_oauth.complete({k: v[0] for k, v in back.items()})
        await _wait(srv, "connected", "error")
        await hub.stop_all()
        # the stored token is now dead and "expired"
        d = mcp_oauth._load("refresher")
        state["tokens"].discard(d["tokens"]["access_token"])
        d["expires_at"] = time.time() - 10
        mcp_oauth._save("refresher", d)
        hub2 = MCPHub()
        again = hub2.launch("refresher", spec)
        await _wait(again, "connected", "sign_in", "error")
        assert again.status == "connected", again.error
        assert mcp_oauth._load("refresher")["tokens"]["access_token"] != d["tokens"]["access_token"]
        await hub2.stop_all()
        mcp_oauth.forget("refresher")

    asyncio.run(go())


def test_microsoft_builtin_app(monkeypatch):
    """Outlook & OneDrive: device-code sign-in (Microsoft mocked), then an in-memory MCP
    server whose tools say what they are: reading allowed, drafts ask."""
    from opendot import builtin_apps

    posts = []

    class FakeResp:
        def __init__(self, status, data):
            self.status_code, self._d = status, data
            self.text = str(data)

        def json(self):
            return self._d

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, **k):
            posts.append((url, data))
            if url.endswith("/devicecode"):
                return FakeResp(200, {"user_code": "ABCD-EFGH", "device_code": "dev",
                                      "verification_uri": "https://microsoft.com/devicelogin",
                                      "expires_in": 900, "interval": 0})
            if len([p for p in posts if p[0].endswith("/token")]) < 2:
                return FakeResp(400, {"error": "authorization_pending"})
            return FakeResp(200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600})

    monkeypatch.setattr(builtin_apps.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(builtin_apps, "ms_client_id", lambda: "00000000-0000-0000-0000-000000000000")

    async def fake_graph(name, method, path, **kw):
        if "mailFolders" in path:
            return FakeResp(200, {"value": [{"id": "m1", "subject": "Hello", "receivedDateTime": "2026-10-01T09:00",
                                             "from": {"emailAddress": {"name": "Ana", "address": "ana@x.test"}},
                                             "bodyPreview": "hi there", "isRead": False}]})
        return FakeResp(200, {"webLink": "https://outlook/draft"})

    monkeypatch.setattr(builtin_apps, "_graph", fake_graph)

    async def go():
        done = asyncio.Event()
        code = await builtin_apps.ms_start_sign_in("outlook", done.set)
        assert code["user_code"] == "ABCD-EFGH" and builtin_apps.device_code("outlook")
        await asyncio.wait_for(done.wait(), 5)
        assert builtin_apps.signed_in("microsoft", "outlook")
        hub = MCPHub()
        srv = hub.launch("outlook", {"builtin": "microsoft", "agents": "all"})
        await _wait(srv, "connected", "error")
        assert srv.status == "connected", srv.error
        tools = {t["tool"]: t for t in srv.tools.values()}
        assert tools["search_mail"]["read_only"] and not tools["create_draft"]["read_only"]
        assert "send" not in " ".join(tools)  # drafts only
        r = await hub.call("mcp__outlook__search_mail", {"query": ""})
        assert "Hello" in r["result"] and "unread" in r["result"]
        await hub.stop_all()
        mcp_oauth.forget("outlook")

    asyncio.run(go())


def test_calendar_apps(monkeypatch):
    """iCloud (CalDAV, faked) and a plain iCal link: checked before saving, then ordinary
    apps: reading allowed, adding an event asks; their events also show on the Calendar page."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from opendot import builtin_apps
    from opendot.ext import agenda
    from opendot.ext import apps as apps_mod

    added = []

    class FakeCal:
        def __init__(self, name):
            self.n = name

        def get_display_name(self):
            return self.n

        def get_supported_components(self):
            return ["VEVENT"]

        def add_event(self, **kw):
            added.append((self.n, kw))

    def fake_cals(st):
        if st.get("password") != "abcd-efgh-ijkl-mnop":
            raise RuntimeError("401 Unauthorized")
        return [FakeCal("Home"), FakeCal("Work")]

    monkeypatch.setattr(builtin_apps, "_dav_calendars", fake_cals)
    monkeypatch.setattr(builtin_apps, "_dav_events", lambda st, s, e: [
        {"title": "Dentist", "start": "2026-10-02T09:00:00+01:00", "end": "2026-10-02T10:00:00+01:00",
         "location": "", "notes": "", "uid": "u1", "calendar": "Home"}])

    async def fake_ics(url, s, e):
        if "good" not in url:
            raise RuntimeError("404")
        return [{"title": "Book club", "start": "2026-10-03T19:00:00+01:00",
                 "end": "2026-10-03T21:00:00+01:00", "location": "", "notes": "", "uid": "u2"}]

    monkeypatch.setattr(agenda, "_ics", fake_ics)
    app = FastAPI()
    app.include_router(apps_mod.router)
    with TestClient(app) as c:
        bad = c.post("/api/apps/connect/icloud-calendar",
                     json={"values": {"user": "me@icloud.com", "password": "my-real-password"}})
        assert bad.status_code == 400 and "app-specific password" in bad.json()["detail"]
        assert not any(k.startswith("APP_ICLOUD") for k in gatekeeper.vault_all())  # not kept
        ok = c.post("/api/apps/connect/icloud-calendar", json={"agents": "all", "values": {
            "user": "me@icloud.com", "password": "abcd-efgh-ijkl-mnop"}}).json()
        assert ok["status"] == "connected", ok
        spec = load_config()["mcp"]["icloud-calendar"]
        assert spec["settings"]["password"].startswith("{{vault:")  # encrypted, not in the config
        assert c.post("/api/apps/connect/calendar-link",
                      json={"values": {"url": "https://x.test/bad.ics"}}).status_code == 400
        link = c.post("/api/apps/connect/calendar-link", json={"agents": "all", "values": {
            "url": "webcal://x.test/good.ics"}}).json()
        assert link["status"] == "connected"
        assert {t["name"] for t in link["tools"]} == {"list_events"}  # read only

        tools = {t["name"]: t for t in ok["tools"]}
        assert tools["list_events"]["rule"] == "allow" and tools["create_event"]["rule"] == "ask"

        async def use():
            r = await apps_mod.mcp_hub.call("mcp__icloud-calendar__list_events", {}, "ag_x")
            assert "Dentist" in r["result"] and "[Home]" in r["result"]
            r = await apps_mod.mcp_hub.call("mcp__icloud-calendar__create_event", {
                "title": "Lunch", "start": "2026-10-05T12:00", "end": "2026-10-05T13:00",
                "calendar": "work"}, "ag_x")
            assert "Work" in r["result"] and added[-1][1]["summary"] == "Lunch"
            evs, cals = await agenda.your_events(1790000000, 1792000000)
            return evs, cals

        evs, cals = c.portal.call(use)  # on the app's own loop, where the apps run
        assert {"Dentist", "Book club"} <= {e["title"] for e in evs}
        assert any(k["name"] == "Home" and k["account"] == "iCloud" for k in cals)
        d = c.get("/api/apps").json()
        assert d["google"]["local_redirect"].startswith("http://localhost:")
        c.delete("/api/apps/installed/icloud-calendar")
        c.delete("/api/apps/installed/calendar-link")
    assert not any(k.startswith("APP_ICLOUD") for k in gatekeeper.vault_all())


def test_google_builtin_apps(monkeypatch):
    """Gmail / Calendar / Drive use Google's ordinary APIs (Google mocked): sign in on
    Google's page → back to /oauth/callback → tokens in the vault → the tools work.
    Apps added through Google's preview MCP servers are moved over, sign-in kept."""
    from opendot import google_apps

    gatekeeper.vault_set("GOOGLE_CLIENT_ID", "1-x.apps.googleusercontent.com")
    gatekeeper.vault_set("GOOGLE_CLIENT_SECRET", "s3cret")
    posts = []

    class R:
        def __init__(self, status, data):
            self.status_code, self._d, self.text = status, data, str(data)

        def json(self):
            return self._d

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, **k):
            posts.append((url, data))
            return R(200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600,
                           "scope": "calendar.readonly"})

        async def request(self, method, url, **kw):
            if url.endswith("/calendarList"):
                return R(200, {"items": [{"id": "me@gmail.com", "summary": "Me", "primary": True,
                                          "selected": True, "backgroundColor": "#9fe1e7"},
                                         {"id": "zh.china#holiday@group.v.calendar.google.com",
                                          "summary": "Holidays", "selected": True},
                                         {"id": "hidden", "summary": "Hidden"}]})
            if "/events" in url and method == "GET":
                assert "hidden" not in url  # only calendars shown in Google Calendar
                assert "#" not in url  # calendar ids are escaped (holiday ids have a #)
                return R(200, {"items": [{"summary": "Dentist", "id": "e1",
                                          "start": {"dateTime": "2026-10-02T09:00:00+01:00"},
                                          "end": {"dateTime": "2026-10-02T10:00:00+01:00"}}]})
            if "/events" in url and method == "POST":
                assert kw["params"]["sendUpdates"] == "none" and kw["json"]["summary"] == "Lunch"
                return R(200, {"htmlLink": "https://calendar.google.com/e2"})
            if url.endswith("/drafts"):
                return R(200, {"id": "d1"})
            return R(403, {"error": {"message": "Gmail API has not been used in project 1 before or it is disabled."}})

    monkeypatch.setattr(google_apps.httpx, "AsyncClient", FakeClient)

    async def go():
        hub = MCPHub()
        spec = {"builtin": "google_calendar", "agents": "all", "label": "Google Calendar",
                "scopes": "https://www.googleapis.com/auth/calendar.readonly"}
        srv = hub.launch("gcal", spec)
        await _wait(srv, "sign_in", "error")
        url = await google_apps.start_sign_in("gcal", spec["scopes"],
                                              "http://localhost:7878/oauth/callback",
                                              lambda: hub.launch("gcal", spec))
        q = parse_qs(urlparse(url).query)
        assert q["access_type"] == ["offline"] and q["code_challenge_method"] == ["S256"]
        assert mcp_oauth.complete({"state": q["state"][0], "code": "c0de"}) == "gcal"
        for _ in range(100):
            if google_apps.signed_in("gcal") and hub.servers["gcal"].status == "connected":
                break
            await asyncio.sleep(0.05)
        srv = hub.servers["gcal"]
        assert srv.status == "connected", srv.error
        assert posts[0][1]["code_verifier"] and posts[0][1]["client_secret"] == "s3cret"
        tools = {t["tool"]: t for t in srv.tools.values()}
        assert tools["list_events"]["read_only"] and not tools["create_event"]["read_only"]
        r = await hub.call("mcp__gcal__list_events", {})
        assert "Dentist" in r["result"] and "[Me]" in r["result"]
        r = await hub.call("mcp__gcal__create_event", {"title": "Lunch", "start": "2026-10-05T12:00",
                                                        "end": "2026-10-05T13:00"})
        assert "calendar.google.com" in r["result"]
        # Gmail: drafts only, and a switched-off API says how to fix it
        mcp_oauth._save("gm", mcp_oauth._load("gcal"))
        gm = hub.launch("gm", {"builtin": "gmail", "agents": "all"})
        await _wait(gm, "connected", "error")
        assert "send" not in " ".join(t["tool"] for t in gm.tools.values()).replace("search", "")
        r = await hub.call("mcp__gm__search_mail", {})
        assert r["is_error"] and "console.cloud.google.com/apis/library/gmail.googleapis.com" in r["result"]
        r = await hub.call("mcp__gm__create_draft", {"to": "a@b.c", "subject": "Hi", "body": "x"})
        assert "Drafts" in r["result"]
        # a sign-in Google no longer accepts → the app shows "needs sign-in"
        d = mcp_oauth._load("gm")
        d["expires_at"] = 0
        mcp_oauth._save("gm", d)

        async def refused(*a, **k):
            return R(400, {"error": "invalid_grant", "error_description": "Token has been revoked."})
        monkeypatch.setattr(FakeClient, "post", refused)
        r = await hub.call("mcp__gm__search_mail", {})
        assert "error" in r and hub.servers["gm"].status == "sign_in"
        await hub.stop_all()
        mcp_oauth.forget("gcal")
        mcp_oauth.forget("gm")

    asyncio.run(go())
    # an app added through Google's preview MCP server moves to the built-in one
    cfg = load_config()
    cfg["mcp"]["oldgmail"] = {"url": "https://gmailmcp.googleapis.com/mcp/v1", "auth": "oauth",
                              "oauth_client": {"client_id": "x"}, "agents": "all", "scopes": "s"}
    save_config(cfg)
    spec = load_config()["mcp"]["oldgmail"]
    assert spec["builtin"] == "gmail" and "url" not in spec and spec["scopes"] == "s"
    cfg = load_config()
    cfg["mcp"].pop("oldgmail")
    save_config(cfg)
    gatekeeper.vault_set("GOOGLE_CLIENT_ID", None)
    gatekeeper.vault_set("GOOGLE_CLIENT_SECRET", None)
