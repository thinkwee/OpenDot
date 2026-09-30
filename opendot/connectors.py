"""Connectors: how OpenDot plugs into your other apps.

1. Apps (MCP servers) — local ones run as a program on this computer (stdio); online
   ones are reached over HTTPS (streamable HTTP, or the older SSE), signed in with a
   token or with the app's own "Sign in → Allow" page (OAuth, see ``mcp_oauth.py``).
   Their tools appear as ``mcp__<app>__<tool>``, only for the agents you let use that
   app, and go through the Gatekeeper like everything else.
2. Inbound events — webhooks (``POST /hook/<token>/<source>``), the Share Sheet /
   Shortcuts (``source=share``), and RSS feeds polled in the background. Events wake up
   any automation whose ``event_filter`` matches.

Config lives in data/connectors.json:
{"mcp": {"notion": {"url": "https://mcp.notion.com/mcp", "auth": "oauth",
                    "app": "notion", "agents": ["ag_…"]},
         "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/Notes"],
                   "agents": "all"}},
 "rss":  {"news": "https://example.com/rss"}}

``agents`` is who may use the app: a list of agent ids, or "all". Anything else in a
spec (label, app, disabled) is for the UI.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
import re
import logging
import time
import xml.etree.ElementTree as ET

import httpx

from .bus import bus
from .config import settings
from .db import db, new_id

log = logging.getLogger("opendot.connectors")
CONNECT_TIMEOUT = 45  # seconds for a background (re)connect; a sign-in waits for you


def load_config() -> dict:
    p = settings.DATA_DIR / "connectors.json"
    if not p.exists():
        p.write_text(json.dumps({"mcp": {}, "rss": {}}, indent=2))
    cfg = json.loads(p.read_text())
    cfg.setdefault("mcp", {})
    cfg.setdefault("rss", {})
    if _upgrade(cfg):
        save_config(cfg)
    return cfg


# Google's Gmail / Calendar / Drive MCP servers only answer projects in its Workspace
# preview programme; apps added through them now use the built-in ones (same sign-in)
_GOOGLE_MCP = {"https://gmailmcp.googleapis.com/mcp/v1": "gmail",
               "https://calendarmcp.googleapis.com/mcp/v1": "google_calendar",
               "https://drivemcp.googleapis.com/mcp/v1": "google_drive"}


def _upgrade(cfg: dict) -> bool:
    changed = False
    for spec in cfg["mcp"].values():
        kind = _GOOGLE_MCP.get(spec.get("url") or "")
        if kind:
            for k in ("url", "auth", "oauth_client", "headers", "transport", "tools"):
                spec.pop(k, None)
            spec["builtin"] = kind
            changed = True
    return changed


def save_config(cfg: dict) -> None:
    p = settings.DATA_DIR / "connectors.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    tmp.replace(p)


def can_use(spec: dict, agent_id: str) -> bool:
    """May this agent (or one of its ``<id>-wN`` helpers) use this app?"""
    who = spec.get("agents", "all")
    base = agent_id.split("-w")[0]
    return who == "all" or base in (who or [])


class NeedsSignIn(Exception):
    """An app wants you to sign in again and nobody is at the screen to do it."""


class Server:
    """One app's live connection. It runs in its own task, because the MCP transports
    must be entered and left in the same task; calls reach it through the session."""

    def __init__(self, name: str, spec: dict) -> None:
        self.name, self.spec = name, spec
        self.status = "connecting"   # connecting | sign_in | connected | error | off
        self.error = ""
        self.tools: dict[str, dict] = {}  # full name -> {tool, schema, annotations, title}
        self.session = None
        self.info: dict = {}
        self.auth_url = ""           # set while waiting for you to sign in
        self.task: asyncio.Task | None = None
        self.stop = asyncio.Event()
        self.ready = asyncio.Event()  # connected, failed, or waiting for a sign-in

    def public(self) -> dict:
        return {"status": self.status, "error": self.error, "auth_url": self.auth_url,
                "tools": [{"name": t["tool"], "title": t["title"], "description": t["description"],
                           "read_only": t["read_only"], "destructive": t["destructive"]}
                          for t in self.tools.values()],
                "server": self.info}


class MCPHub:
    def __init__(self) -> None:
        self.servers: dict[str, Server] = {}

    # ---- compatibility views used by the rest of the app
    @property
    def status(self) -> dict[str, str]:
        return {n: (s.status if s.status != "error" else f"error: {s.error}")
                for n, s in self.servers.items()}

    @property
    def tools(self) -> dict[str, dict]:
        return {k: v for s in self.servers.values() if s.status == "connected"
                for k, v in s.tools.items()}

    async def start(self) -> None:
        """Connect every app in data/connectors.json, each on its own."""
        await self.stop_all()
        for name, spec in load_config()["mcp"].items():
            if not spec.get("disabled"):
                self.launch(name, spec)

    def launch(self, name: str, spec: dict, interactive: dict | None = None) -> Server:
        """(Re)start one app's connection. ``interactive`` = {"redirect_uri": …} when
        you're at the screen and can sign in; otherwise a needed sign-in just marks it."""
        old = self.servers.get(name)
        if old and old.task and not old.task.done():
            old.stop.set()
            old.task.cancel()
        srv = Server(name, spec)
        self.servers[name] = srv
        srv.task = asyncio.create_task(self._run(srv, interactive))
        return srv

    async def remove(self, name: str) -> None:
        srv = self.servers.pop(name, None)
        if srv and srv.task:
            srv.stop.set()
            srv.task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(srv.task, 5)
        bus.emit("connectors", status=self.status)

    async def _run(self, srv: Server, interactive: dict | None) -> None:
        from .gatekeeper import inject_secrets
        spec = inject_secrets(srv.spec)
        try:
            timeout = None if interactive else CONNECT_TIMEOUT
            async with contextlib.AsyncExitStack() as stack:
                session = await asyncio.wait_for(
                    self._open(stack, srv, spec, interactive), timeout)
                srv.session = session
                await self._load_tools(srv, session)
                srv.status, srv.error, srv.auth_url = "connected", "", ""
                srv.ready.set()
                bus.emit("connectors", status=self.status, app=srv.name)
                await srv.stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001 — anything a transport throws
            err = _root(e)
            if isinstance(err, NeedsSignIn):
                srv.status, srv.error = "sign_in", ""
            else:
                srv.status, srv.error = "error", _explain(err)
                log.warning("app %s: %s", srv.name, srv.error)
            srv.ready.set()
            bus.emit("connectors", status=self.status, app=srv.name)
        finally:
            srv.session = None

    async def _open(self, stack: contextlib.AsyncExitStack, srv: Server, spec: dict,
                    interactive: dict | None):
        from mcp import ClientSession
        from mcp.types import Implementation
        if spec.get("builtin"):  # an app that lives inside OpenDot (builtin_apps.py)
            from mcp.client._memory import InMemoryTransport

            from .builtin_apps import BUILTINS, signed_in
            if not signed_in(spec["builtin"], srv.name):
                raise NeedsSignIn()
            read, write = await stack.enter_async_context(
                InMemoryTransport(BUILTINS[spec["builtin"]](srv.name, spec)))
        elif spec.get("url"):
            from mcp.shared._httpx_utils import create_mcp_http_client
            auth = None
            if spec.get("auth") == "oauth":
                from .mcp_oauth import provider_for, signed_in
                if not interactive and not signed_in(srv.name):
                    raise NeedsSignIn()  # never signed in: wait for you to press Connect
                auth = provider_for(srv, spec, interactive)
            headers = {k: v for k, v in (spec.get("headers") or {}).items() if v}
            url = spec["url"]
            if spec.get("transport") == "sse" or url.rstrip("/").endswith("/sse"):
                from mcp.client.sse import sse_client
                read, write = await stack.enter_async_context(
                    sse_client(url, headers=headers, auth=auth, timeout=30))
            else:
                from mcp.client.streamable_http import streamable_http_client
                client = await stack.enter_async_context(
                    create_mcp_http_client(headers=headers, auth=auth))
                if auth is not None:
                    from .mcp_oauth import sign_in_first
                    client._transport = sign_in_first(client._transport, srv.name, url)
                read, write = await stack.enter_async_context(
                    streamable_http_client(url, http_client=client))
        else:
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client
            import os
            cwd = spec.get("cwd") or str(settings.DATA_DIR)
            args = [os.path.expanduser(a) for a in spec.get("args", [])]
            params = StdioServerParameters(command=spec["command"], args=args,
                                           env=spec.get("env") or None, cwd=cwd)
            errlog = open(settings.DATA_DIR / "logs-apps.txt", "a")  # noqa: SIM115
            stack.callback(errlog.close)
            read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
        session = await stack.enter_async_context(ClientSession(
            read, write, client_info=Implementation(name="OpenDot", version="0.2")))
        res = await session.initialize()
        info = getattr(res, "server_info", None) or getattr(res, "serverInfo", None)
        if info is not None:
            srv.info = {"name": getattr(info, "name", ""), "version": getattr(info, "version", "")}
        return session

    async def _load_tools(self, srv: Server, session) -> None:
        tools, cursor = [], None
        for _ in range(20):  # paginated
            res = await (session.list_tools(cursor=cursor) if cursor else session.list_tools())
            tools += res.tools
            cursor = getattr(res, "next_cursor", None) or getattr(res, "nextCursor", None)
            if not cursor:
                break
        srv.tools = {}
        for t in tools:
            full = _full_name(srv.name, t.name)
            ann = t.annotations
            ro = bool(ann and (getattr(ann, "read_only_hint", None) or getattr(ann, "readOnlyHint", None)))
            destr = getattr(ann, "destructive_hint", None) if ann else None
            if destr is None and ann:
                destr = getattr(ann, "destructiveHint", None)
            title = (getattr(t, "title", None) or (ann and getattr(ann, "title", None)) or t.name)
            srv.tools[full] = {
                "server": srv.name, "tool": t.name, "title": title,
                "description": _plain(t.description or "")[:600], "read_only": ro,
                "destructive": bool(destr) if not ro else False,
                "schema": {"type": "function", "function": {
                    "name": full, "description": f"[{srv.spec.get('label') or srv.name}] "
                                                 f"{(t.description or title)[:400]}",
                    "parameters": _schema(t)}}}

    # ---- used by the agent loop
    def tool_defs(self, agent_id: str | None = None) -> list[dict]:
        return [t["schema"] for s in self.servers.values() if s.status == "connected"
                and (agent_id is None or can_use(s.spec, agent_id))
                for t in s.tools.values()]

    def tool_info(self, full: str) -> dict | None:
        return self.tools.get(full)

    def apps_for(self, agent_id: str) -> list[dict]:
        """The apps this agent may use, connected or not (for its system prompt)."""
        out = []
        for name, spec in load_config()["mcp"].items():
            if spec.get("disabled") or not can_use(spec, agent_id):
                continue
            srv = self.servers.get(name)
            out.append({"name": name, "label": spec.get("label") or name,
                        "status": srv.status if srv else "off"})
        return out

    async def call(self, full: str, args: dict, agent_id: str | None = None) -> dict:
        t = self.tools.get(full)
        if not t:
            return {"error": f"the app for {full} isn't connected right now"}
        srv = self.servers[t["server"]]
        if agent_id and not can_use(srv.spec, agent_id):
            return {"error": "this agent isn't allowed to use that app"}
        try:
            res = await asyncio.wait_for(srv.session.call_tool(t["tool"], args), 180)
        except Exception as e:  # noqa: BLE001
            err = _root(e)
            if isinstance(err, NeedsSignIn):
                srv.status = "sign_in"
                bus.emit("connectors", status=self.status, app=srv.name)
                return {"error": f"{srv.spec.get('label') or srv.name} needs you to sign in again "
                                 "(Settings → Apps)."}
            return {"error": _explain(err)}
        parts = []
        for c in res.content:
            if getattr(c, "text", None):
                parts.append(c.text)
            elif getattr(c, "type", "") == "resource" and getattr(c, "resource", None) is not None:
                parts.append(getattr(c.resource, "text", "") or str(getattr(c.resource, "uri", "")))
        structured = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
        if not parts and structured:
            parts.append(json.dumps(structured, ensure_ascii=False))
        is_err = getattr(res, "is_error", None)
        if is_err is None:
            is_err = getattr(res, "isError", False)
        text = "\n".join(parts)
        if is_err and (srv.spec.get("auth") == "oauth" or srv.spec.get("builtin")) \
                and _SIGNED_OUT.search(text):
            # the app took the connection but not the sign-in (revoked, or never finished)
            srv.status = "sign_in"
            bus.emit("connectors", status=self.status, app=srv.name)
            return {"error": f"{srv.spec.get('label') or srv.name} needs you to sign in again "
                             "(Settings → Apps)."}
        return {"result": "\n".join(x for x in parts if x)[:16000], "is_error": bool(is_err)}

    async def stop_all(self) -> None:
        for name in list(self.servers):
            srv = self.servers.pop(name)
            if srv.task:
                srv.stop.set()
                srv.task.cancel()
        await asyncio.sleep(0)

    async def stop(self) -> None:  # server shutdown
        await self.stop_all()


_SIGNED_OUT = re.compile(r"needs you to sign in again|missing required authentication|invalid authentication credentials|"
                         r"UNAUTHENTICATED|invalid_grant|token (has )?(expired|been revoked)", re.I)


def _full_name(server: str, tool: str) -> str:
    full = f"mcp__{server}__{tool}"
    if len(full) <= 64:
        return full
    import hashlib
    return full[:55] + "_" + hashlib.sha1(full.encode()).hexdigest()[:8]


def _plain(text: str) -> str:
    """A tool description as one readable line (servers often write Markdown)."""
    import re
    text = re.sub(r"`{1,3}|\*\*|__|^#+\s*|\s#+\s", " ", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


def _root(e: BaseException) -> BaseException:
    """The first real error inside anyio/asyncio exception groups and timeouts."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return e


def _explain(e: BaseException) -> str:
    """One line a person can act on."""
    name, msg = type(e).__name__, str(e)
    low = msg.lower()
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return "it didn't answer in time"
    if isinstance(e, FileNotFoundError):
        return f"can't find “{e.filename or msg}” on this computer — is it installed?"
    if "401" in msg or "unauthorized" in low or "invalid_token" in low:
        return "the app didn't accept the sign-in or token"
    if "403" in msg or "forbidden" in low:
        return "the app refused access (check the token's permissions)"
    if "404" in msg:
        return "nothing answered at that address"
    if "connect" in low or name in ("ConnectError", "ConnectTimeout"):
        return "couldn't reach it — check the address and your internet connection"
    return (msg or name)[:240]


def _schema(tool) -> dict:
    """``input_schema`` in mcp>=2, ``inputSchema`` in mcp 1.x."""
    s = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
    return s or {"type": "object", "properties": {}}


mcp_hub = MCPHub()


# ---------------- inbound events ----------------
async def ingest_event(source: str, type_: str, payload: dict) -> dict:
    ev = db.insert("events", id=new_id("ev_"), source=source, type=type_, payload=payload)
    bus.emit("event", event=ev)
    from .runtime import run_agent, spawn
    for a in db.q("SELECT * FROM automations WHERE kind='event' AND enabled=1"):
        if fnmatch.fnmatch(source, a["event_filter"] or "*"):
            db.update("automations", a["id"], last_run=time.time())
            body = json.dumps(payload, ensure_ascii=False)[:6000]
            spawn(run_agent(a["agent_id"], a["thread_id"] or _default_thread(a["agent_id"]),
                            f"automation:{a['name']}",
                            f"Event from `{source}` ({type_}):\n```json\n{body}\n```\n\n"
                            f"Your standing instruction: {a['prompt']}"))
    return ev


def _default_thread(agent_id: str) -> str:
    t = db.one("SELECT id FROM threads WHERE kind='dm' AND members LIKE ?", f'%"{agent_id}"%')
    return t["id"] if t else ""


async def rss_loop() -> None:
    seen: set[str] = set(db.kv_get("rss_seen", []))
    first = not seen
    while True:
        feeds = load_config().get("rss", {})
        for name, url in feeds.items():
            try:
                async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
                    root = ET.fromstring((await c.get(url)).content)
                for item in list(root.iter("item"))[:15]:
                    link = (item.findtext("link") or "").strip()
                    if not link or link in seen:
                        continue
                    seen.add(link)
                    if not first:
                        await ingest_event(f"rss:{name}", "item", {
                            "title": item.findtext("title"), "link": link,
                            "summary": (item.findtext("description") or "")[:800]})
            except Exception as e:
                log.debug("rss %s: %s", name, e)
        first = False
        db.kv_set("rss_seen", list(seen)[-3000:])
        await asyncio.sleep(900)


