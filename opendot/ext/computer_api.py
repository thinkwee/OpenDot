"""Agent Computer 2.0 wiring: live browser stream, live terminal stream, and
tool-schema overrides that push agents to actually use the visible browser.

- ``GET  /api/computer/{agent_id}/tabs``      list of open tabs (root + helpers)
- ``WS   /api/computer/{agent_id}/stream``    live CDP screencast frames (JPEG),
  only running while at least one viewer is subscribed; also accepts a small
  "take over" protocol ({"type":"click"|"key"|"type", ...}) for click/type
  forwarding into the agent's own tab.
- ``WS   /api/computer/{agent_id}/term``      raw PTY bytes (xterm.js renders
  them); accepts typed input back for an owner "type into agent terminal" mode.

WebSocket routes are NOT covered by the HTTP auth middleware — the token is
checked here by hand, per PLAN_V02 rules.

Tool overrides (``register_tool``, same names, richer descriptions + a new
``browse_task``): ``web_search``/``web_fetch``/``browser`` already delegate to
``opendot.computer.Computer``, which now performs the search chain / visible
browsing / trafilatura extraction — the schemas here just describe that
honestly so the model prefers them over blind shelling-out.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import usage
from ..computer import computer_for
from ..computer.term import session_for
from ..config import settings
from ..tools import Ctx, S, fn, register_tool

log = logging.getLogger("opendot.ext.computer_api")

router = APIRouter(prefix="/api/computer")


def _token_ok(tok: str | None) -> bool:
    return bool(tok) and hmac.compare_digest(tok, settings.access_token)


# ---------------- HTTP ----------------
@router.get("/{agent_id}/tabs")
async def tabs(agent_id: str):
    return {"tabs": computer_for(agent_id).open_tabs()}


# ---------------- browser live stream ----------------
@router.websocket("/{agent_id}/stream")
async def stream_ws(sock: WebSocket, agent_id: str) -> None:
    if not _token_ok(sock.query_params.get("token")):
        await sock.close(code=4401)
        return
    await sock.accept()
    comp = computer_for(agent_id)
    tab, q = await comp.stream_subscribe()
    await sock.send_json({"kind": "hello", "url": tab.page.url})

    async def pump() -> None:
        while True:
            await sock.send_json(await q.get())

    task = asyncio.create_task(pump())
    try:
        while True:
            raw = await sock.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await _takeover(tab.page, data)
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        comp.stream_unsubscribe(tab, q)


async def _takeover(page, data: dict) -> None:
    """Optional 'take over' mode: forward the owner's clicks/typing into the tab."""
    try:
        kind = data.get("type")
        if kind == "click":
            await page.mouse.click(float(data.get("x", 0)), float(data.get("y", 0)))
        elif kind == "type":
            await page.keyboard.type(str(data.get("text", "")))
        elif kind == "key":
            await page.keyboard.press(str(data.get("key", "Enter")))
        elif kind == "scroll":
            await page.mouse.wheel(0, float(data.get("dy", 400)))
    except Exception as e:
        log.debug("takeover action failed: %s", e)


# ---------------- terminal live stream ----------------
@router.websocket("/{agent_id}/term")
async def term_ws(sock: WebSocket, agent_id: str) -> None:
    if not _token_ok(sock.query_params.get("token")):
        await sock.close(code=4401)
        return
    await sock.accept()
    comp = computer_for(agent_id)
    sess = session_for(agent_id, comp.home, comp._env())
    if not sess._alive:
        sess.start()
    q = sess.subscribe()
    history = "".join(sess._buf)[-60_000:]  # replay what happened before the viewer opened
    if history:
        await sock.send_text(history)

    async def pump() -> None:
        while True:
            await sock.send_text(await q.get())

    task = asyncio.create_task(pump())
    try:
        while True:
            msg = await sock.receive_text()
            if msg.startswith("\x00resize:"):
                try:
                    rows, cols = map(int, msg[8:].split(","))
                    sess.resize(rows, cols)
                except Exception:
                    pass
            elif msg:
                sess.write_raw(msg)
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        sess.unsubscribe(q)


# ---------------- tool schema overrides (richer descriptions, same behaviour) ----------------
I = {"type": "integer"}  # noqa: E741

register_tool(
    "web_search",
    fn("web_search", "Search the web. Tries SearXNG / Tavily / Brave / multiple ddgs backends "
       "with retry, then as a last resort opens a results page in your visible browser tab so "
       "the human can watch — this almost never fails outright, unlike a single provider.",
       {"query": S, "max_results": I}, ["query"]),
    lambda ctx, query, max_results=6: computer_for(ctx.agent["id"]).web_search(query,
                                                                               max_results),
    policy="allow", worker=True,
)

register_tool(
    "web_fetch",
    fn("web_fetch", "Read a URL through your VISIBLE browser tab (renders JS, the human "
       "watches it live), then extracts the readable article text. Falls back automatically "
       "to a headless fetch for PDFs or pages that won't load.",
       {"url": S}, ["url"]),
    # always the visible path: the model used to opt out of it on every call
    lambda ctx, url, **_: computer_for(ctx.agent["id"]).web_fetch(url),
    policy="allow", worker=True,
)

register_tool(
    "browser",
    fn("browser", "Drive your real, persistent Chromium browser; the human watches it live in "
       "the Computer panel. Prefer this (or web_fetch) over shell/curl for web pages. Every "
       "look returns the page text and `elements`: what can be clicked or typed into, "
       "numbered like [12] (or [2.5] inside a frame). Act by number: click(ref), "
       "type(ref, text, key='Enter' to submit), select(ref, text). Also goto(url), read, "
       "press(key), scroll(text='up'|'down'), back, wait(seconds), screenshot. Numbers "
       "change when the page does; read again if one is gone. Only goto addresses you got "
       "from search results or a page's links, never guessed ones. If a result says "
       "`blocked`, that site won't let you in from here: follow its advice, don't retry. "
       "Delegated helpers get their own tab in the same browser.",
       {"action": {"type": "string", "enum": ["goto", "read", "click", "type", "select",
                                              "press", "scroll", "back", "wait",
                                              "screenshot"]},
        "url": S, "ref": S, "text": S, "key": S, "seconds": {"type": "number"},
        "selector": {"type": "string", "description": "CSS selector, only if there's no number"}},
       ["action"]),
    lambda ctx, **kw: computer_for(ctx.agent["id"]).browser(**kw),
    policy="allow", worker=True,
)


async def _browse_task(ctx: Ctx, task: str, max_steps: int = 14) -> dict:
    """Multi-step interactive browsing (forms, logins, navigation) via Microsoft's
    Playwright MCP (`@playwright/mcp`, accessibility-snapshot based), attached over CDP
    to this agent's own visible Chromium so the human watches it happen.

    We chose Playwright MCP over the `browser-use` library: `browser-use` pulls in a
    large, fast-moving dependency tree with its own pins of `openai`/`mcp`/`websockets`
    that fight ours.
    Playwright MCP has none of that: it's a small Node process reached through the
    existing MCP hub pattern, and it drives the *same* browser via --cdp-endpoint.
    """
    root = computer_for(ctx.agent["id"])._root()
    await root._ensure_context()
    cdp_url = f"http://127.0.0.1:{root.cdp_port}"
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.get(cdp_url + "/json/version")
    except Exception as e:
        return {"error": f"browser CDP endpoint not reachable ({e})"}

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from ..llm import llm

    params = StdioServerParameters(
        command="npx", args=["-y", "@playwright/mcp@latest", "--cdp-endpoint", cdp_url])
    root._set_activity(f"browse_task: {task[:60]}")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            schemas = [{"type": "function", "function": {
                "name": t.name, "description": (t.description or "")[:500],
                "parameters": getattr(t, "inputSchema", None) or getattr(t, "input_schema", None)
                or {"type": "object", "properties": {}}}}
                for t in mcp_tools]
            messages = [
                {"role": "system", "content": (
                    "You control a real, visible web browser through tools to complete the "
                    "human's task. Take one tool call at a time; use browser_snapshot first "
                    "to see the page before clicking. When the task is done, reply with the "
                    "final result and no further tool calls.")},
                {"role": "user", "content": task},
            ]
            for _ in range(max_steps):
                with usage.tagged(kind="computer"):  # for the agent that asked
                    reply = await llm.chat(messages, tools=schemas)
                if not reply.tool_calls:
                    return {"result": reply.content or "(browse_task finished with no reply)"}
                messages.append({"role": "assistant", "content": reply.content or "",
                                 "tool_calls": [{"id": tc["id"], "type": "function",
                                                 "function": {"name": tc["name"],
                                                              "arguments": tc["arguments"]}}
                                               for tc in reply.tool_calls]})
                for tc in reply.tool_calls:
                    try:
                        args = json.loads(tc["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    root._set_activity(f"browse_task: {tc['name']}")
                    try:
                        res = await session.call_tool(tc["name"], args)
                        text = "\n".join(getattr(c, "text", "") for c in res.content)[:4000]
                    except Exception as e:
                        text = f"error: {e}"
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": text})
            return {"result": "stopped after max_steps — task may be incomplete",
                    "note": "ask for a narrower sub-task and try again"}


register_tool(
    "browse_task",
    fn("browse_task", "Give a multi-step interactive web task in plain English (fill a form, "
       "log in with a saved credential, click through a multi-page flow) and it drives your "
       "visible browser end-to-end using Playwright MCP, one accessibility-tree snapshot and "
       "action at a time, until done. Use this instead of many manual `browser` calls when the "
       "task needs real navigation/interaction, not just reading.",
       {"task": S, "max_steps": {"type": "integer"}}, ["task"]),
    _browse_task,
    policy="ask", worker=False,
)


PROMPT = ("Your browser is visible to the human in the Computer panel — prefer `browser` and "
         "`web_fetch` (which now reads pages through that same visible browser) over "
         "shell/curl for anything web-related, so they can watch you work. For multi-step "
         "interactive tasks (forms, logins, checkout-like flows) use `browse_task`.")
