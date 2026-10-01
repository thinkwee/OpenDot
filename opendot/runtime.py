"""Agent runtime: the loop, multi-agent group chat, handoffs and parallel workers.

Concurrency model: each agent is single-threaded (one lock per agent, so one agent
works sequentially) but different agents run in parallel, and
``delegate`` fans work out to short-lived parallel workers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time

from . import context, durable, memory, usage
from .bus import bus
from .config import settings
from .connectors import mcp_hub
from .db import db, new_id
from .ext.profiles import llm_for_agent
from .gatekeeper import gate, inject_secrets, redact, request_approval
from .tools import EXEC, Ctx, parse_args, tool_list

log = logging.getLogger("opendot.runtime")

# A new install starts with one front-desk agent. Everything else is made per
# responsibility — by the human ("＋ New agent", a link, a QR code) or by Pip itself
# when a request turns into an ongoing job (see docs/SOUL.md).
DEFAULT_AGENTS = [
    dict(name="Pip", emoji="✨", color="#FFB38A", role="your front desk",
         tagline="Tell me anything. Ongoing jobs get their own agent.",
         responsibility=("Anything you ask. When something turns into an ongoing job, I set "
                         "someone up just for it."),
         origin="default", avatar="fox"),
]

# the front desk's own blurb, per language (ext/lang.py swaps it when you switch)
FRONT_DESK_TEXT = {
    "role": {"en": "your front desk", "zh": "你的前台"},
    "tagline": {"en": "Tell me anything. Ongoing jobs get their own agent.",
                "zh": "有事就跟我说，长期的事我给它配专人。"},
    "responsibility": {
        "en": "Anything you ask. When something turns into an ongoing job, I set someone up "
              "just for it.",
        "zh": "你交代的任何事。一件事要长期跟进时，我会给它单独配一个助理。"},
}

PALETTE = ["#FFB38A", "#8FD6B8", "#B9A6F2", "#8EC5FF", "#FFD37A", "#FF9CB8", "#7ED6E0",
           "#C8E07E"]

# tools that only decorate the reply; text the model writes next to them is kept
REPLY_TOOLS = {"offer_choices", "suggest_routine", "suggest_app"}

_locks: dict[str, asyncio.Lock] = {}
_cancel: set[str] = set()
_loops: dict[str, asyncio.Task] = {}  # run_id -> its agent loop, so Stop can cut in
# runs in flight, so a page opened (or refreshed) mid-run can pick up where the
# live stream is: run_id -> {agent_id, thread_id, run_id, text, thought, workers,
# inbox: what the human said since the run began, read at its next step}
_live: dict[str, dict] = {}
BTW = ("[The human sent this while you were working. Take it into account now: it may add "
       "to, change or cancel what you're doing.]\n")


def live_runs() -> list[dict]:
    return [{**r, "workers": list(r["workers"].values())} for r in _live.values()]
_tasks: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return t


def _front_desk() -> dict:
    from .ext.lang import lang
    d = dict(DEFAULT_AGENTS[0])
    for key, texts in FRONT_DESK_TEXT.items():
        d[key] = texts[lang()]
    return d


def ensure_default_agents() -> None:
    if db.one("SELECT id FROM agents LIMIT 1"):
        # installs from before v0.3: the first agent becomes the front desk
        if not db.one("SELECT id FROM agents WHERE origin='default'"):
            first = db.one("SELECT * FROM agents ORDER BY created LIMIT 1")
            d = _front_desk()
            db.update("agents", first["id"], origin="default",
                      responsibility=first.get("responsibility") or d["responsibility"],
                      avatar=first.get("avatar") or d["avatar"])
        return
    make_agent(**_front_desk())


ANIMALS = ["fox", "cat", "bunny", "bear", "frog", "owl", "panda", "raccoon", "duck", "shiba",
           "axolotl", "penguin", "koala", "lion", "tiger", "pig", "mouse", "sheep", "hedgehog",
           "monkey", "cow", "deer", "seal", "chick"]  # web/src/components/animals/shapes.js


def pick_animal(wish: str = "") -> str:
    """The animal a new agent gets: the one asked for if nobody on the team has it yet,
    otherwise one of the least used, so a team doesn't turn into five owls."""
    used: dict[str, int] = {a: 0 for a in ANIMALS}
    for r in db.q("SELECT avatar FROM agents"):
        if r["avatar"] in used:
            used[r["avatar"]] += 1
    if used.get(wish) == 0:
        return wish
    fewest = min(used.values())
    return random.choice([a for a, n in used.items() if n == fewest])


def make_agent(name: str, role: str = "", emoji: str = "🌟", color: str = "",
               tagline: str = "", responsibility: str = "", context: str = "",
               boundary: str = "", origin: str = "manual", origin_url: str = "",
               greeting: str = "", avatar: str = "") -> tuple[dict, dict]:
    """Create an agent, its soul file and its own chat. Returns (agent, thread).

    A taken name gets a number (“Trip Buddy 2”) rather than failing — agents are
    meant to be cheap to make."""
    name = (name or "").strip()[:24] or "Helper"
    base, n = name, 2
    while db.one("SELECT id FROM agents WHERE lower(name)=lower(?)", name):
        name = f"{base[:21]} {n}"
        n += 1
    count = db.one("SELECT count(*) n FROM agents")["n"]
    role = (role or "").strip() or (responsibility[:80] if responsibility else "a helper")
    a = db.insert("agents", id=new_id("ag_"), name=name, emoji=emoji or "🌟",
                  color=color or PALETTE[count % len(PALETTE)], role=role,
                  tagline=(tagline or "").strip()[:140], responsibility=responsibility.strip(),
                  context=context.strip(), boundary=boundary.strip(), origin=origin,
                  origin_url=origin_url, avatar=pick_animal(avatar))
    memory.write(a["id"], "SOUL.md", memory.read(a["id"], "SOUL.md").format(name=name,
                                                                             role=role))
    t = db.insert("threads", id=new_id("th_"), title=name, kind="dm", members=[a["id"]],
                  updated=time.time())
    if greeting:
        m = db.insert("messages", id=new_id("m_"), thread_id=t["id"], role="agent",
                      agent_id=a["id"], content=greeting.strip(),
                      meta={"source": "hello"})
        bus.emit("message", message=m)
    bus.emit("agent_created", agent=a, thread=t)
    return a, t


def set_status(agent_id: str, status: str, text: str = "") -> None:
    db.update("agents", agent_id, status=status, status_text=text[:120])
    bus.emit("agent", agent_id=agent_id, status=status, status_text=text[:120])


def _tr(en: str, zh: str) -> str:
    from .ext.lang import tr
    return tr(en, zh)


def _status_text(tool: str, args: dict) -> str:
    from .ext.lang import tr
    if tool == "browser":
        where = args.get('url') or args.get('text') or ''
        return tr(f"browsing {where}", f"在浏览 {where}").strip()
    if tool == "web_search":
        return tr(f"searching “{args.get('query', '')}”", f"在搜“{args.get('query', '')}”")
    if tool == "web_fetch":
        return tr(f"reading {args.get('url', '')}", f"在看 {args.get('url', '')}")
    if tool in ("shell", "python"):
        first = (args.get("command") or args.get("code") or "").strip().splitlines()
        what = first[0][:60] if first else tr("code", "代码")
        return tr(f"running {what}", f"在跑 {what}")
    if tool == "write_file":
        return tr(f"writing {args.get('path', '')}", f"在写 {args.get('path', '')}")
    if tool == "delegate":
        n = len(args.get('tasks', []))
        return tr(f"leading {n} helpers", f"带着 {n} 个帮手")
    return tool.replace("_", " ")


# ---------------- prompt ----------------
def system_prompt(agent: dict, thread: dict, source: str) -> str:
    aid = agent["id"]
    now = memory.now_local()
    others = db.q("SELECT name, role, responsibility FROM agents WHERE id != ?", aid)
    parts = [
        f"You are {agent['name']} ({agent['role']}). This is your own identity — you are "
        f"never any other agent, and never say another agent's name when speaking about "
        f"yourself. Other agents' messages in this chat are prefixed like “[Name]: …”.",
        memory.read(aid, "SOUL.md"),
        _job_section(agent),
        "# About the human\n" + memory.read(aid, "USER.md"),
        memory.read(aid, "MEMORY.md"),
    ]
    j = memory.recent_journal(aid)
    if j:
        parts.append("# My recent journal\n" + j)
    situation = (
        "# Situation\n"
        f"- Now: {now:%A %Y-%m-%d %H:%M} ({settings.TIMEZONE}).\n"
        f"- Channel: {'group chat “' + thread['title'] + '”' if thread['kind'] == 'group' else 'direct chat'}"
        f" · trigger: {source}.\n"
        + ("- The human's other agents: " + "; ".join(
            f"{t['name']} ({(t['responsibility'] or t['role'])[:70]})" for t in others[:20])
           + ".\n" if others else "")
        + "- Your computer home has a `shared/` folder every agent can read and write.\n"
    )
    rules = [
        "# Rules",
        "- Act, don't just talk: use tools to actually do the work, then answer briefly.",
        "- Bring work back finished, or one tap from finished. When the human only needs to "
        "say yes/no or pick one, call `offer_choices` with 2-3 short replies (e.g. “Yes, go "
        "ahead”, “Not now”) instead of asking an open question. They just tap one.",
        "- Talk like a friend texting: short, warm, plain words, in the human's language. "
        "Never sound like a dev tool (no “approve/confirm execution/tool call/run”).",
        "- For wide/parallel work (many items to research or produce) use `delegate`.",
        "- Hand results over as files (make_document / make_spreadsheet / make_slides / "
        "attach); an interactive mini-app is a web-page file via `publish_page`.",
        "- 'Every day / every Monday' → `schedule`. 'Tell me when / as soon as / if a slot "
        "opens / keep an eye on' → `watch` (it re-checks until it happens, then you act).",
        "- If you learn a lasting preference or fact about the human, `remember` it.",
        "- Never invent results. If a tool fails, say so and try another way.",
        "- Money: you never pay. Get everything ready and hand over the link so the human "
        "pays themselves.",
        "- Mail about the human's OpenDot setup (a test email, a provider's “new app password "
        "/ new sign-in” alert right after they connected a mailbox) is expected: don't report it.",
    ]
    if thread["kind"] == "group":
        rules.append("- Group chat: messages from teammates appear as “[Name]: …”. Only speak "
                     "when you add value. Use `handoff` to pass a sub-task to the right "
                     "teammate by name instead of doing everything yourself. Don't @ yourself.")
        rules.append("- If a [system] note says you're the coordinator for several @mentioned "
                     "teammates: don't do their research yourself — split it with `handoff`, "
                     "one distinct sub-task each (or, for pure small talk, a quick 'just say hi "
                     "briefly'), then synthesise once they've replied.")
    if source.startswith("heartbeat"):
        rules.append("- This is a heartbeat wake-up. Check your HEARTBEAT checklist. If nothing "
                     "needs the human, reply exactly NO_REPLY.\n" + memory.read(aid, "HEARTBEAT.md"))
    parts.append("\n".join(rules))
    from . import ext
    parts.extend(ext.prompt_sections(agent))
    # what changes every minute goes last: everything before it stays in the prompt cache
    parts.append(situation)
    return "\n\n".join(p.strip() for p in parts if p.strip())


def _source_label(source: str) -> str:
    from .ext.lang import tr
    kind, _, name = source.partition(":")
    label = {"automation": tr("routine", "例行"), "watch": tr("watching", "盯着"),
             "heartbeat": tr("check-in", "例行查看"), "goal": tr("goal", "目标"),
             "email": tr("email", "邮件"), "sms": tr("text", "短信"),
             "call": tr("call", "电话")}.get(kind, kind)
    return f"{label} · {name}" if name and kind in ("automation", "watch") else label


def _job_section(agent: dict) -> str:
    lines = []
    if agent.get("responsibility"):
        lines.append(f"Your responsibility: {agent['responsibility']}")
    if agent.get("context"):
        lines.append(f"What you know for it:\n{agent['context']}")
    lines.append("Come back to the human when: " + (
        agent.get("boundary") or "a decision needs them — anything that spends money, "
        "speaks for them in a new way, or can't be undone. Otherwise just get it done."))
    if agent.get("origin_url"):
        lines.append(f"You were made from: {agent['origin_url']}")
    return "# Your job\n" + "\n".join(lines)


def _tools_used_here(thread_id: str, days: float = 3) -> set[str]:
    """App tools that ran in this chat recently."""
    rows = db.q("SELECT DISTINCT tool FROM steps WHERE thread_id=? AND tool LIKE 'mcp\\_\\_%' "
                "ESCAPE '\\' AND created > ?", thread_id, time.time() - days * 86400)
    return {r["tool"] for r in rows}


def history(thread: dict, agent: dict, limit: int = 400) -> list[dict]:
    """The chat as this agent sees it: a summary of the older part (``context``), then the
    recent messages word for word, within a token budget."""
    rows = db.q("SELECT * FROM messages WHERE thread_id=? ORDER BY created DESC LIMIT ?",
                thread["id"], limit)[::-1]
    summary = db.kv_get(context.summary_key(thread["id"], agent["id"])) or {"upto": 0, "text": ""}
    older, rows = context.split_history(rows, summary["upto"])
    names = {a["id"]: a["name"] for a in db.q("SELECT id, name FROM agents")}
    earlier = []
    if summary["text"]:
        earlier.append("[Earlier in this chat — a summary]\n" + summary["text"])
    if older:  # not summarised yet (it happens after a run): the gist of each
        earlier.append("[Earlier messages, shortened]\n" + "\n".join(
            f"- {'Human' if m['role'] == 'user' else names.get(m['agent_id'], 'Agent')}: "
            f"{re.sub(r'\s+', ' ', m['content'] or '')[:240]}" for m in older[-20:]))
    from .ext.uploads import context_block
    with_files = [m["id"] for m in rows if m["role"] == "user" and
                  any(a.get("upload") for a in (m["meta"] or {}).get("attachments") or [])]
    recent_files = set(with_files[-2:])  # older uploads: card only, to keep context lean
    out: list[dict] = [{"role": "user", "content": "\n\n".join(earlier)}] if earlier else []
    for m in rows:
        if m["role"] == "user":
            content = m["content"] or ""
            atts = (m["meta"] or {}).get("attachments") or []
            if atts:
                block = context_block(atts, full=m["id"] in recent_files)
                content = f"{content}\n\n{block}".strip() if block else content
            out.append({"role": "user", "content": content})
        elif m["role"] == "agent" and m["agent_id"] == agent["id"]:
            out.append({"role": "assistant", "content": m["content"] or "(done)"})
        elif m["role"] == "agent":
            out.append({"role": "user", "content": f"[{names.get(m['agent_id'], '?')}]: "
                                                   f"{m['content']}"})
        elif m["role"] == "system":
            out.append({"role": "user", "content": f"[system note] {m['content']}"})
    # merge consecutive same-role messages (some backends dislike them)
    merged: list[dict] = []
    for m in out:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"] += "\n\n" + m["content"]
        else:
            merged.append(dict(m))
    _attach_images(rows, merged)
    return merged


def _attach_images(rows: list[dict], merged: list[dict]) -> None:
    """Vision models (LLM_VISION=1): show the latest user message's images for real."""
    if not settings.LLM_VISION or not merged or merged[-1]["role"] != "user":
        return
    last_user = next((m for m in reversed(rows) if m["role"] == "user"), None)
    if not last_user:
        return
    from .ext.uploads import image_paths
    parts = []
    for p in image_paths((last_user["meta"] or {}).get("attachments") or [])[:4]:
        try:
            import base64
            import io

            from PIL import Image
            with Image.open(p) as im:
                im = im.convert("RGB")
                im.thumbnail((1568, 1568))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=85)
            parts.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}})
        except Exception as e:
            log.info("could not attach image %s: %s", p, e)
    if parts:
        merged[-1]["content"] = [{"type": "text", "text": merged[-1]["content"]}, *parts]


async def _stream_or_fallback(client, ctx: Ctx, messages: list[dict], tools: list[dict]):
    """Stream the reply, emitting ``delta`` events as text arrives; fall back to a
    plain (non-streaming) call if the stream errors out partway (some OpenAI-compatible
    backends flake on ``stream=True``)."""
    main = ctx.depth == 0  # helpers share the run id; only the lead's words stream
    live = _live.get(ctx.run_id) if main else None
    streamed = False
    shown = 0.0
    try:
        reply = None
        async for kind, payload in client.chat_stream(messages, tools):
            if kind == "delta" and main:
                streamed = True
                if live is not None:
                    live["text"] += payload
                bus.emit("delta", agent_id=ctx.agent["id"], thread_id=ctx.thread_id,
                         run_id=ctx.run_id, text=payload)
            elif kind == "tool_delta" and main:
                # a long tool call (e.g. a whole report in write_file) streams no text;
                # show it growing so the chat doesn't look frozen
                if time.time() - shown > 2:
                    shown = time.time()
                    _, n = payload
                    from .ext.lang import tr
                    set_status(ctx.agent["id"], "working",
                               tr(f"writing it up… {n:,} characters so far",
                                  f"正在写…已写 {n:,} 字"))
            elif kind == "done":
                reply = payload
        if reply is None:
            raise RuntimeError("stream ended without a final reply")
        return reply
    except Exception as e:
        log.warning("stream failed for run %s (%s), falling back to non-stream", ctx.run_id, e)
        if streamed:  # the retry answers from scratch; drop the half-streamed text
            if live is not None:
                live["text"] = ""
            bus.emit("delta", agent_id=ctx.agent["id"], thread_id=ctx.thread_id,
                     run_id=ctx.run_id, text="", reset=True)
        return await client.chat(messages, tools)


# ---------------- core loop ----------------
async def agent_loop(ctx: Ctx, messages: list[dict], tools: list[dict],
                     max_steps: int | None = None) -> str:
    # the model calls in this run count for this agent (a helper's for its own id) and
    # for what started it; a helper inherits the kind of work from its lead
    src = ctx.extra.get("source")
    with usage.tagged(agent_id=ctx.agent["id"], thread_id=ctx.thread_id,
                      kind=usage.kind_of(src) if src else None):
        return await _agent_loop(ctx, messages, tools, max_steps)


async def _agent_loop(ctx: Ctx, messages: list[dict], tools: list[dict],
                      max_steps: int | None = None) -> str:
    aid = ctx.agent["id"]
    client = llm_for_agent(aid)
    said: list[str] = []  # text written alongside reply-only tools (offer_choices) is the answer
    for _ in range(max_steps or settings.MAX_STEPS):
        if ctx.run_id in _cancel:
            return "(stopped)"
        heard = _live.get(ctx.run_id, {}).get("inbox") if ctx.depth == 0 else None
        if heard:  # said mid-run: the next step sees it, like a message typed into a live session
            messages.append({"role": "user", "content": BTW + "\n\n".join(heard)})
            heard.clear()
        set_status(aid, "thinking", _tr("thinking…", "在想…"))
        await context.fit(messages, tools, client)
        reply = await _stream_or_fallback(client, ctx, messages, tools)
        if not reply.tool_calls:
            final = (reply.content or "").strip()
            head = "\n\n".join(t for t in said if t and t not in final)
            return f"{head}\n\n{final}".strip() if head else final
        if all(c["name"] in REPLY_TOOLS for c in reply.tool_calls):
            said.append((reply.content or "").strip())
        else:
            said.clear()  # narration before real work isn't the answer
        messages.append({"role": "assistant", "content": reply.content or None, "tool_calls": [
            {"id": c["id"], "type": "function",
             "function": {"name": c["name"], "arguments": c["arguments"]}}
            for c in reply.tool_calls]})
        if ctx.depth == 0 and ctx.run_id in _live:
            # narration before tool calls isn't the answer: clear the streamed bubble
            _live[ctx.run_id]["text"] = ""
            if reply.content:
                _live[ctx.run_id]["thought"] = reply.content
        if reply.content and ctx.depth == 0:
            bus.emit("thought", agent_id=aid, thread_id=ctx.thread_id, run_id=ctx.run_id,
                     text=reply.content)
        for call in reply.tool_calls:
            result = await run_tool(ctx, call["name"], parse_args(call["arguments"]))
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": redact(json.dumps(result, ensure_ascii=False,
                                                          default=str))[:16000]})
    messages.append({"role": "user", "content": "Step limit reached. Summarise what you did "
                                                "and what remains, without calling tools."})
    return (await client.chat(messages)).content


async def run_tool(ctx: Ctx, name: str, args: dict) -> dict:
    aid = ctx.agent["id"]
    if name not in REPLY_TOOLS and name != "todo":
        try:
            from .ext import todo
            todo.open_task(ctx)  # real work on something asked → it's on the Todo page
        except Exception:
            log.exception("todo open failed")
    if name == "browser" and args.get("ref"):
        # Gatekeeper judges a click by what the button says, not its number
        from .computer import computer_for
        args = {**args, "element": computer_for(aid).element_label(args["ref"])}
    step = db.insert("steps", id=new_id("st_"), thread_id=ctx.thread_id, run_id=ctx.run_id,
                     agent_id=aid, tool=name, args=args, status="running")
    bus.emit("step", step=step)
    decision, reason = await gate(ctx, name, args)  # rules + second look
    job_id, attempt = ctx.extra.get("job_id"), ctx.extra.get("attempt", 1)
    result: dict
    done = durable.replay(job_id, attempt, name, args) if decision != "deny" else None
    if done is not None:
        result = done  # a resumed job repeating a call it already made: don't do it twice
    elif decision == "deny":
        result = {"error": f"Gatekeeper blocked this: {reason}"}
    else:
        ok = True
        if decision == "ask":
            set_status(aid, "waiting", _tr("waiting for you", "等你点头"))
            db.update("steps", step["id"], status="waiting")
            bus.emit("step", step={**step, "status": "waiting"})
            from .ext import todo
            todo.set_status(ctx.run_id, "waiting")
            ok = await request_approval(aid, ctx.thread_id, name, args, reason,
                                        job_id=job_id, attempt=attempt)
            todo.set_status(ctx.run_id, "doing")
        if not ok:
            result = {"error": "The human declined this action. Do not retry it; offer an "
                               "alternative or ask what they prefer."}
        else:
            set_status(aid, "working", _status_text(name, args))
            effect = durable.effect_begin(job_id, attempt, ctx.thread_id, name, args)
            try:
                if name.startswith("mcp__"):
                    result = await mcp_hub.call(name, inject_secrets(args), aid)
                elif name in EXEC:
                    result = await EXEC[name](ctx, **inject_secrets(args))
                else:
                    result = {"error": f"unknown tool {name}"}
            except TypeError as e:
                result = {"error": f"bad arguments: {e}"}
            except Exception as e:  # tool failures are reported back, never retried here
                log.exception("tool %s failed", name)
                result = {"error": f"{type(e).__name__}: {e}"}
            if not isinstance(result, dict):
                result = {"result": result}
            durable.effect_done(effect, result)
    if not isinstance(result, dict):
        result = {"result": result}
    status = "error" if "error" in result else "done"
    preview = redact(json.dumps(result, ensure_ascii=False, default=str))[:1500]
    db.update("steps", step["id"], status=status, result=preview, finished=time.time())
    bus.emit("step", step={**step, "status": status, "result": preview})
    return result


async def run_agent(agent_id: str, thread_id: str, source: str = "chat",
                    prompt: str | None = None, hops: int = 0,
                    from_id: str | None = None, job_id: str | None = None,
                    resumed: bool = False) -> dict | None:
    """Run one agent turn as a durable job (a saved one via ``job_id``, else a new one)."""
    job = (durable.get_job(job_id) if job_id else None) or \
        durable.new_job(agent_id, thread_id, source, prompt, hops, from_id)
    try:
        msg = await _run(job, agent_id, thread_id, source, prompt, hops, from_id, resumed)
    except asyncio.CancelledError:
        raise  # shutting down: the job stays open and is picked up on the next start
    except Exception:
        durable.job_end(job["id"], "failed")
        raise
    if (durable.get_job(job["id"]) or {}).get("status") != "failed":
        durable.job_end(job["id"], "done")
    return msg


async def _run(job: dict, agent_id: str, thread_id: str, source: str, prompt: str | None,
               hops: int, from_id: str | None, resumed: bool) -> dict | None:
    agent = db.one("SELECT * FROM agents WHERE id=?", agent_id)
    thread = db.one("SELECT * FROM threads WHERE id=?", thread_id)
    if not agent or not thread:
        return None
    if source == "handoff" and not resumed:
        # Echo prevention: don't let a teammate re-answer a round it already answered
        # (e.g. handed off to twice, or handed off to after already replying).
        # A reply-back (the coordinator handed work to the sender, who now hands the
        # result back) must go through — only skip if the target already spoke after
        # the sender's latest message, i.e. it has already seen what's being handed over.
        since = db.one("SELECT created FROM messages WHERE thread_id=? AND role='agent' AND "
                       "agent_id=? ORDER BY created DESC LIMIT 1", thread_id, from_id) \
            if from_id else None
        since = since or db.one("SELECT created FROM messages WHERE thread_id=? AND "
                                "role='user' ORDER BY created DESC LIMIT 1", thread_id)
        if since and db.one("SELECT id FROM messages WHERE thread_id=? AND agent_id=? "
                            "AND role='agent' AND created > ? LIMIT 1", thread_id, agent_id,
                            since["created"]):
            log.info("skipping handoff to %s: already replied since the last user message",
                     agent["name"])
            return None
    lock = _locks.setdefault(agent_id, asyncio.Lock())
    async with lock:
        run_id = job["run_id"] or new_id("run_")  # a resumed job keeps its run (and Todo card)
        ctx = Ctx(agent=agent, thread_id=thread_id, run_id=run_id)
        ctx.extra.update(source=source, job_id=job["id"],
                         attempt=durable.job_started(job["id"], run_id))
        if resumed:
            from .ext.lang import tr
            note = db.insert("messages", id=new_id("m_"), thread_id=thread_id, role="agent",
                             agent_id=agent_id, content=tr("(picked up again after a restart)",
                                                           "（刚重启了，我接着做）"),
                             meta={"run_id": run_id, "source": source, "resumed": True})
            bus.emit("message", message=note)
            prompt = durable.resume_prompt(job["run_id"], prompt)
        if source == "handoff" and prompt:
            ctx.extra["prompt"] = prompt
        _live[run_id] = {"agent_id": agent_id, "thread_id": thread_id, "run_id": run_id,
                         "text": "", "thought": "", "workers": {}}
        bus.emit("run", state="start", agent_id=agent_id, thread_id=thread_id, run_id=run_id)
        msgs = [{"role": "system", "content": system_prompt(agent, thread, source)}]
        msgs += history(thread, agent)
        if prompt:
            msgs.append({"role": "user", "content": prompt})
        if len(msgs) == 1 or msgs[-1]["role"] != "user":
            msgs.append({"role": "user", "content": "(continue)"})
        _live[run_id]["inbox"] = []  # from here on, new messages join this run
        # apps with big tool lists load when opened (open_app); the app tools this chat
        # used lately come ready, so a conversation about Notion doesn't reopen it each turn
        opened = mcp_hub.light_apps(agent_id)
        used = {d["function"]["name"]: d for d in mcp_hub.tool_defs(agent_id)
                if d["function"]["name"] in _tools_used_here(thread_id)}
        app_defs = mcp_hub.tool_defs(agent_id, apps=opened)
        app_defs += [d for n, d in used.items() if n not in {x["function"]["name"] for x in app_defs}]
        tools = tool_list(ctx, thread["kind"] == "group", app_defs)
        ctx.extra.update(tools=tools, opened=opened)
        from .ext import workfiles
        try:
            before = await asyncio.to_thread(workfiles.snapshot, agent_id)
        except Exception:
            log.exception("file snapshot failed")
            before = None
        failed = False
        try:
            _loops[run_id] = asyncio.ensure_future(agent_loop(ctx, msgs, tools))
            try:
                text = await _loops[run_id]
            except asyncio.CancelledError:
                cur = asyncio.current_task()
                if run_id not in _cancel or (cur and cur.cancelling()):
                    raise  # shutting down, not you pressing Stop
                text = "(stopped)"
        except Exception as e:
            log.exception("run failed")
            failed = True
            from .ext.lang import tr
            text = tr(f"😵 I hit an error: {e}", f"😵 出了点错：{e}")
        finally:
            set_status(agent_id, "idle")
            _loops.pop(run_id, None)
            stopped = run_id in _cancel
            _cancel.discard(run_id)
            unread = (_live.pop(run_id, None) or {}).get("inbox")
        if unread and not stopped:
            # said just as it finished: answer it as a turn of its own (it's saved already)
            spawn(run_agent(agent_id, thread_id, "chat"))
        msg = None
        attachments = ctx.extra.get("attachments", [])
        if before is not None:
            try:
                await asyncio.to_thread(workfiles.record_diff, before, agent_id, thread_id,
                                        run_id)
                # files the reply names by path become clickable cards on it
                have = {a.get("path") for a in attachments} | {a.get("title") for a in attachments}
                for card in await asyncio.to_thread(workfiles.mentioned_files, text, agent_id,
                                                    thread_id):
                    if card["path"] not in have and card["title"] not in have:
                        attachments.append(card)
            except Exception:
                log.exception("work-file tracking failed")
        # a run you stopped just stops: no "(stopped)" bubble
        silent = text.strip().upper().startswith("NO_REPLY") or text == "(stopped)"
        if silent and attachments:
            # a deliverable-only turn still needs a bubble to hang the attachments off
            silent = False
            from .ext.lang import tr
            text = tr("Here you go:", "给你～")
        if not silent:
            meta = {"run_id": run_id, "source": source}
            if attachments:
                meta["attachments"] = attachments
            if ctx.extra.get("choices"):
                meta["choices"] = ctx.extra["choices"]
            msg = db.insert("messages", id=new_id("m_"), thread_id=thread_id, role="agent",
                            agent_id=agent_id, content=text, meta=meta)
            db.update("threads", thread_id, updated=time.time())
            bus.emit("message", message=msg)
            # background work (routines, heartbeats, events) reports to the Inbox; a
            # teammate's handoff reply is part of a live conversation, so it doesn't
            if source not in ("chat", "handoff") and not ctx.extra.get("notified"):
                item = db.insert("inbox", id=new_id("in_"), agent_id=agent_id, kind="report",
                                 title=f"{agent['name']} · {_source_label(source)}",
                                 body=text[:4000],
                                 status="unread", thread_id=thread_id)
                bus.emit("inbox", item=item)
            memory.journal(agent_id, f"[{source}] {(prompt or 'chat')[:80]} → {text[:120]}")
        if failed:
            durable.job_end(job["id"], "failed")
        try:
            from .ext import todo
            todo.close(run_id, "failed" if failed else "stopped" if text == "(stopped)"
                       else "waiting" if ctx.extra.get("choices") else "done", text)
        except Exception:
            log.exception("todo close failed")
        bus.emit("run", state="end", agent_id=agent_id, thread_id=thread_id, run_id=run_id)
        # fold what scrolled out of the word-for-word window into this chat's summary,
        # after the reply is out so nobody waits for it
        spawn(context.update_summary(thread_id, agent))
    for target_id, note in ctx.extra.get("handoffs", [])[:3]:
        if hops < 6 and target_id != agent_id:
            ask = f"[{agent['name']} → you]: {note}"
            # saved before this run counts as done, so a restart can't drop the handoff
            j = durable.new_job(target_id, thread_id, "handoff", ask, hops + 1, agent_id)
            spawn(run_agent(target_id, thread_id, "handoff", ask, hops + 1, agent_id,
                            job_id=j["id"]))
    return msg


# ---------------- entry points ----------------
MENTION = re.compile(r"@([\w\-]+)")


_YES = re.compile(r"^(ok|okay|yes|yep|yeah|sure|go|go ahead|send it|do it|fine|可以|好|好的|行|"
                  r"发吧|发|去吧|同意|确认|没问题|嗯|对)[\s!！。.~～👍]*$", re.I)
_NO = re.compile(r"^(no|nope|don'?t|stop|cancel|not now|不|不要|别|算了|先别|不用|取消)"
                 r"[\s!！。.~～]*$", re.I)


def _yes_no(text: str) -> bool | None:
    t = (text or "").strip()
    return True if _YES.match(t) else False if _NO.match(t) else None


async def on_user_message(thread_id: str, text: str, attachments: list | None = None,
                          source: str = "app", sender: str = "") -> dict:
    """Entry point for everything the human says — the web app or a channel (telegram…)."""
    thread = db.one("SELECT * FROM threads WHERE id=?", thread_id)
    answer = _yes_no(text) if not attachments else None
    pending = db.q("SELECT id FROM approvals WHERE thread_id=? AND status='pending'", thread_id)
    if answer is not None and len(pending) == 1:
        # "ok" / "可以" while an agent is waiting on exactly one question is the tap itself,
        # not a new request that would interrupt it
        msg = db.insert("messages", id=new_id("m_"), thread_id=thread_id, role="user",
                        agent_id=None, content=text, meta={"source": source, "sender": sender})
        db.update("threads", thread_id, updated=time.time())
        bus.emit("message", message=msg)
        from .gatekeeper import decide
        decide(pending[0]["id"], answer, scope="once" if answer else "deny")
        return msg
    from .ext import uploads
    text, extra_atts = await uploads.maybe_long_text(text, thread_id, source)
    attachments = [*(attachments or []), *extra_atts]
    targets, coordinator_note = _targets(thread, text)
    busy = {} if attachments else _working_here(thread_id, targets)
    # the message and the replies it needs are saved together: a restart can't lose either
    jobs = [durable.job_row(aid, thread_id, "chat", coordinator_note) for aid in targets
            if aid not in busy]
    msg, *_ = db.insert_many(
        ("messages", dict(id=new_id("m_"), thread_id=thread_id, role="user", agent_id=None,
                          content=text, meta={"attachments": attachments or [],
                                              "source": source, "sender": sender})),
        *(("jobs", j) for j in jobs))
    db.update("threads", thread_id, updated=time.time())
    bus.emit("message", message=msg)
    for run in busy.values():
        run["inbox"].append(text)
    try:
        from .ext import todo
        todo.answered(thread_id)
    except Exception:
        log.exception("todo answered failed")
    up_ids = [a["id"] for a in attachments if a.get("upload")]
    spawn(_dispatch(thread_id, [(j["agent_id"], j["id"]) for j in jobs], coordinator_note,
                    up_ids, thread["members"]))
    return msg


def _working_here(thread_id: str, targets: list[str]) -> dict[str, dict]:
    """Of these agents, the ones mid-run in this chat that can take a message now."""
    return {r["agent_id"]: r for r in _live.values()
            if r["thread_id"] == thread_id and r["agent_id"] in targets and "inbox" in r
            and r["run_id"] not in _cancel}


def _targets(thread: dict, text: str) -> tuple[list[str], str | None]:
    """Who answers a message: (agent ids, a note for the coordinator if several)."""
    members = thread["members"]
    coordinator_note = None
    if thread["kind"] == "group":
        by_name = {a["name"].lower(): a["id"] for a in db.q("SELECT id, name FROM agents")}
        raw = [m.lower() for m in MENTION.findall(text)]
        if "all" in raw:
            mentioned = list(members)
        else:
            mentioned = [by_name[m] for m in raw if m in by_name]
            mentioned = [m for m in dict.fromkeys(mentioned) if m in members]
        targets = mentioned or members[:1]  # default: the group lead answers and hands off
        if len(targets) > 1:
            # Several teammates mentioned at once: route to the first as coordinator
            # instead of running them all in parallel (they'd duplicate each other's
            # research). The coordinator splits the work via `handoff`.
            lead_id, teammates = targets[0], targets[1:]
            teammate_names = ", ".join(a["name"] for a in db.q(
                "SELECT name FROM agents WHERE id IN (%s)" % ",".join("?" * len(teammates)),
                *teammates))
            coordinator_note = (
                f"[system] You were @mentioned together with: {teammate_names}. You are the "
                "coordinator for this request — don't research/produce everything yourself. "
                "Use `handoff` to give each of them one distinct sub-task (or, if this is just "
                "small talk, handoff a short 'reply briefly' to each), then synthesise once you "
                "see their replies.")
            targets = [lead_id]
    else:
        targets = members[:1]
    return targets, coordinator_note


async def _dispatch(thread_id: str, targets: list[tuple[str, str]], note: str | None,
                    up_ids: list[str], members: list[str]) -> None:
    if up_ids:
        from .ext import uploads
        for aid, _ in targets:
            set_status(aid, "thinking", _tr("reading your files…", "在看你的文件…"))
        await uploads.wait_ready(up_ids)
        rows = [r for r in (db.one("SELECT * FROM uploads WHERE id=?", u) for u in up_ids) if r]
        for r in rows:
            if not r["thread_id"]:
                db.update("uploads", r["id"], thread_id=thread_id)
        await uploads.push_to_sandboxes(rows, members)
    for aid, job_id in targets:
        spawn(run_agent(aid, thread_id, "chat", prompt=note, job_id=job_id))


def resume_jobs(jobs: list[dict] | None = None) -> list[asyncio.Task]:
    """Start-up: pick up (once) the jobs a restart cut off — see durable.py."""
    jobs = durable.recover() if jobs is None else jobs
    if jobs:
        log.info("picking up %d job(s) after a restart", len(jobs))
    return [spawn(run_agent(j["agent_id"], j["thread_id"], j["source"] or "chat", j["prompt"],
                            j["hops"] or 0, j["from_id"], job_id=j["id"], resumed=True))
            for j in jobs]


def stop_thread(thread_id: str) -> None:
    """Stop: every run in this chat stops now, mid-reply or mid-tool, helpers too."""
    runs = {r["run_id"] for r in db.q("SELECT DISTINCT run_id FROM steps WHERE thread_id=? "
                                      "AND status IN ('running','waiting')", thread_id)}
    runs |= {rid for rid, r in _live.items() if r["thread_id"] == thread_id}
    from .gatekeeper import _close_notice
    for ap in db.q("SELECT id FROM approvals WHERE thread_id=? AND status='pending'", thread_id):
        # not a "no" (nothing to learn from): the question just isn't open any more
        db.update("approvals", ap["id"], status="expired", decided=time.time())
        _close_notice(ap["id"])
        bus.emit("approval", approval=db.one("SELECT * FROM approvals WHERE id=?", ap["id"]))
    for rid in runs:
        _cancel.add(rid)
        task = _loops.get(rid)
        if task and not task.done():
            task.cancel()
    for st in db.q("SELECT * FROM steps WHERE thread_id=? AND status IN ('running','waiting')",
                   thread_id):
        db.update("steps", st["id"], status="stopped")
        bus.emit("step", step={**st, "status": "stopped"})


async def run_workers(parent: Ctx, tasks: list[dict]) -> list[dict]:
    """Wide-research style fan-out: N short-lived helpers in parallel."""
    agent = parent.agent

    async def one(i: int, t: dict) -> dict:
        wid = f"{agent['id']}-w{i}"
        worker = {**agent, "id": wid, "name": f"{agent['name']}·{t['title'][:24]}"}
        ctx = Ctx(agent=worker, thread_id=parent.thread_id, run_id=parent.run_id, depth=1)
        ctx.extra.update({k: parent.extra[k] for k in ("job_id", "attempt") if k in parent.extra})
        ev = {"parent_id": agent["id"], "worker_id": wid, "title": t["title"],
              "state": "start", "thread_id": parent.thread_id, "run_id": parent.run_id}
        if parent.run_id in _live:
            _live[parent.run_id]["workers"][wid] = ev
        bus.emit("worker", **ev)
        msgs = [
            {"role": "system", "content": (
                f"You are a focused helper of {agent['name']}. Complete ONE sub-task using your "
                "tools, then reply with a compact, factual result (bullets, include source "
                "URLs and file paths). If you create files meant for others, put them in "
                f"shared/. Now: {memory.now_local():%Y-%m-%d %H:%M}.")},
            {"role": "user", "content": f"Sub-task: {t['title']}\n\n{t['instructions']}"},
        ]
        try:
            out = await agent_loop(ctx, msgs, tool_list(ctx, False, []), max_steps=12)
        except Exception as e:
            out = f"failed: {e}"
        if ctx.extra.get("attachments"):
            # bubble a helper's deliverables up so the parent's final message carries them
            parent.extra.setdefault("attachments", []).extend(ctx.extra["attachments"])
        if parent.run_id in _live:
            _live[parent.run_id]["workers"].pop(wid, None)
        bus.emit("worker", parent_id=agent["id"], worker_id=wid, title=t["title"],
                 state="done", thread_id=parent.thread_id, run_id=parent.run_id,
                 result=out[:300])
        return {"title": t["title"], "result": out}

    results = await asyncio.gather(*(one(i, t) for i, t in enumerate(tasks)))
    set_status(agent["id"], "working", _tr("putting the helpers' work together", "在汇总帮手们的结果"))
    return list(results)
