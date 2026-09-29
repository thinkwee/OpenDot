"""Tool definitions (OpenAI function schema) + executors."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import memory
from .bus import bus
from .computer import computer_for
from .config import settings
from .db import db, new_id


@dataclass
class Ctx:
    agent: dict
    thread_id: str
    run_id: str
    depth: int = 0                      # 0 = top-level agent, 1 = delegated worker
    extra: dict = field(default_factory=dict)


def fn(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required or []}}}


S = {"type": "string"}
I = {"type": "integer"}  # noqa: E741

TOOLS: dict[str, dict] = {
    "shell": fn("shell", "Run a bash command on your own Linux computer (cwd = your home). "
                "Python3, pip, git, curl are available. Use for real work: files, code, data.",
                {"command": S, "timeout": I}, ["command"]),
    "python": fn("python", "Run a Python 3 snippet on your computer and get stdout. Save "
                 "charts/files into your home dir.", {"code": S}, ["code"]),
    "read_file": fn("read_file", "Read any file on your computer (PDF, Word, Excel, slides, "
                    "images via OCR and code/text all come back as text). Long files are paged: "
                    "pass the returned next_offset to continue.",
                    {"path": S, "offset": {"type": "integer"}}, ["path"]),
    "write_file": fn("write_file", "Create or overwrite a file on your computer. Put files you "
                     "want other agents to see in shared/.",
                     {"path": S, "content": S, "append": {"type": "boolean"}},
                     ["path", "content"]),
    "list_files": fn("list_files", "List files on your computer.", {"path": S, "pattern": S}),
    "web_search": fn("web_search", "Search the web (DuckDuckGo). Returns titles, urls, snippets.",
                     {"query": S, "max_results": I}, ["query"]),
    "web_fetch": fn("web_fetch", "Fetch a URL and return readable text (fast, no JS).",
                    {"url": S}, ["url"]),
    "browser": fn("browser", "Drive a real Chromium browser that the human can watch live. "
                  "Actions: goto(url), read, click(text|selector), type(selector,text), "
                  "press(key), scroll(text='up'|'down'), back, screenshot.",
                  {"action": {"type": "string", "enum": ["goto", "read", "click", "type",
                                                         "press", "scroll", "back",
                                                         "screenshot"]},
                   "url": S, "selector": S, "text": S, "key": S}, ["action"]),
    "remember": fn("remember", "Save a durable fact. about_user=true for facts about the human "
                   "(preferences, people, routines) — shared with all agents.",
                   {"fact": S, "about_user": {"type": "boolean"}}, ["fact"]),
    "forget": fn("forget", "Delete remembered lines matching text (when the human asks you to "
                 "forget something).", {"text": S}, ["text"]),
    "notify": fn("notify", "Put a card in the human's Inbox (proactive update, finished "
                 "background work, reminder). Keep it short.",
                 {"title": S, "body": S}, ["title"]),
    "publish_page": fn("publish_page", "Create an interactive web page file (mini-app, "
                       "dashboard, tracker) the human can open on their phone. It is "
                       "attached to your reply as a file card.",
                       {"slug": S, "title": S, "html": S}, ["slug", "html"]),
    "schedule": fn("schedule", "Create an automation that wakes you up later. kind='cron' with "
                   "schedule like '0 8 * * *' (local time) or kind='event' with event_filter "
                   "(e.g. 'webhook:github' or 'rss:*' or 'share'). prompt = what to do then.",
                   {"name": S, "kind": {"type": "string", "enum": ["cron", "event"]},
                    "schedule": S, "event_filter": S, "prompt": S},
                   ["name", "kind", "prompt"]),
    "list_automations": fn("list_automations", "List your automations.", {}),
    "cancel_automation": fn("cancel_automation", "Disable an automation by id.", {"id": S},
                            ["id"]),
    "offer_choices": fn(
        "offer_choices", "Let the human answer with a tap instead of typing. Two forms:\n"
        "1) Quick replies (no `question`): 2-3 short replies for yes/no or go/not-now, e.g. "
        "[\"Yes, go ahead\", \"Show me others\", \"Not now\"].\n"
        "2) Question card (with `question`): when they need to pick among several REAL "
        "options you found (flights, dates, places, quotes…), give 2-6 `options` (short "
        "labels) and optional `details` (one line each, same order: price, time, why). They "
        "show as A, B, C… with a Continue button; set `multi`=true if they can pick several. "
        "Not for yes/no — use form 1 for that.\n"
        "Write everything in the human's language, as a friend would. Their answer comes "
        "back as a normal message like \"B — Porto\" or \"A + C — …\", or their own words.",
        {"options": {"type": "array", "items": S},
         "question": S,
         "details": {"type": "array", "items": S},
         "multi": {"type": "boolean"}}, ["options"]),
    "handoff": fn("handoff", "In a group chat: pass the baton to a teammate by name with a "
                  "clear instruction. They will reply in the same chat after you.",
                  {"agent": S, "message": S}, ["agent", "message"]),
    "delegate": fn("delegate", "Spin up temporary helper agents that work IN PARALLEL on "
                   "independent sub-tasks (e.g. research 5 companies at once), each with its "
                   "own computer. Returns all their results. Use for wide, parallelisable work.",
                   {"tasks": {"type": "array", "items": {"type": "object", "properties": {
                       "title": S, "instructions": S}, "required": ["title", "instructions"]}}},
                   ["tasks"]),
}

WORKER_TOOLS = ["shell", "python", "read_file", "write_file", "list_files", "web_search",
                "web_fetch", "browser"]


def tool_list(ctx: Ctx, group: bool, extra: list[dict]) -> list[dict]:
    names = [n for n in TOOLS if n not in SHOWN_WHEN or SHOWN_WHEN[n]()]
    if ctx.depth > 0:
        names = WORKER_TOOLS
    if not group:
        names = [n for n in names if n != "handoff"]
    return [TOOLS[n] for n in names] + (extra if ctx.depth == 0 else [])


# ---------------- executors ----------------
async def _python(ctx: Ctx, code: str) -> dict:
    c = computer_for(ctx.agent["id"])
    path = c.home / ".dot_snippet.py"
    path.write_text(code)
    return await c.shell(f"python3 {path}", settings.SHELL_TIMEOUT)


async def _publish(ctx: Ctx, slug: str, html: str, title: str = "") -> dict:
    slug = re.sub(r"[^a-z0-9\-]", "-", slug.lower()).strip("-")[:48] or new_id()
    d = settings.DATA_DIR / "pages" / ctx.agent["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.html").write_text(html)
    url = f"/pages/{ctx.agent['id']}/{slug}"
    bus.emit("page", agent_id=ctx.agent["id"], url=url, title=title or slug)
    return {"url": url, "note": "Tell the human they can open it from the Pages tab."}


async def _notify(ctx: Ctx, title: str, body: str = "") -> dict:
    item = db.insert("inbox", id=new_id("in_"), agent_id=ctx.agent["id"], kind="note",
                     title=title, body=body, status="unread", thread_id=ctx.thread_id)
    bus.emit("inbox", item=item)
    ctx.extra["notified"] = True  # this run's report would say the same thing again
    return {"ok": True, "note": "They've been told. End your turn with just NO_REPLY."}


async def _schedule(ctx: Ctx, name: str, kind: str, prompt: str, schedule: str = "",
                    event_filter: str = "") -> dict:
    from .scheduler import next_cron
    if kind == "cron":
        try:
            nxt = next_cron(schedule)
        except Exception as e:
            return {"error": f"bad cron expression: {e}"}
    else:
        nxt = None
    a = db.insert("automations", id=new_id("au_"), agent_id=ctx.agent["id"], name=name,
                  kind=kind, schedule=schedule, event_filter=event_filter, prompt=prompt,
                  enabled=1, next_run=nxt, thread_id=ctx.thread_id)
    bus.emit("automation", automation=a)
    return {"ok": True, "id": a["id"], "next_run": nxt}


LETTERS = "ABCDEF"


def _opt(o) -> tuple[str, str]:
    """An option as (label, detail); models sometimes send {label, detail} objects."""
    if isinstance(o, dict):
        return (str(o.get("label") or o.get("text") or "").strip(),
                str(o.get("detail") or o.get("description") or "").strip())
    return str(o).strip(), ""


async def _offer_choices(ctx: Ctx, options: list, question: str = "", details: list | None = None,
                         multi: bool = False) -> dict:
    """Quick replies → meta.choices = ["Yes", "Not now"].
    Question card → meta.choices = [{"card": "question", "question", "multi", "options":
    [{"key": "A", "label", "detail"}, …]}] (still a non-empty list, so "needs you" works)."""
    raw = [_opt(o) for o in (options or [])]
    raw = [(lbl, det) for lbl, det in raw if lbl]
    if not raw:
        return {"error": "give 2-3 options"}
    question = str(question or "").strip()
    if not question:
        ctx.extra["choices"] = [lbl[:60] for lbl, _ in raw][:4]
        return {"ok": True, "note": "They'll show under your reply. Don't list them again in text."}
    if len(raw) < 2:
        return {"error": "a question card needs 2-6 options"}
    details = [str(d or "").strip() for d in (details or [])]
    opts = [{"key": LETTERS[i], "label": lbl[:80],
             "detail": (det or (details[i] if i < len(details) else ""))[:140]}
            for i, (lbl, det) in enumerate(raw[:6])]
    ctx.extra["choices"] = [{"card": "question", "question": question[:200],
                             "multi": bool(multi), "options": opts}]
    return {"ok": True, "letters": {o["key"]: o["label"] for o in opts},
            "note": "They'll see a card with these as A, B, C… Don't list them again in text; "
                    "a one-line lead-in is enough."}


EVERY = ("day", "weekdays", "weekly", "monthly")
WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def routine_cron(every: str, time: str = "09:00", weekday: str = "mon", day: int = 1) -> str:
    """"every weekday at 10:00" → cron. The web card builds the same thing."""
    m = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*$", str(time or "09:00"))
    if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
        raise ValueError(f"bad time {time!r}, use HH:MM")
    hh, mm = int(m.group(1)), int(m.group(2) or 0)
    if every == "day":
        return f"{mm} {hh} * * *"
    if every == "weekdays":
        return f"{mm} {hh} * * 1-5"
    if every == "weekly":
        wd = str(weekday or "mon").lower()[:3]
        if wd not in WEEKDAYS:
            raise ValueError(f"bad weekday {weekday!r}")
        return f"{mm} {hh} * * {WEEKDAYS.index(wd)}"
    if every == "monthly":
        return f"{mm} {hh} {min(max(int(day or 1), 1), 28)} * *"
    raise ValueError(f"every must be one of {', '.join(EVERY)}")


async def _suggest_routine(ctx: Ctx, prompt: str, every: str = "day", time: str = "09:00",
                           weekday: str = "mon", day: int = 1, name: str = "") -> dict:
    """Nothing is created yet: the human gets an editable card and taps Create."""
    prompt = str(prompt or "").strip()
    if not prompt:
        return {"error": "say what you'll do each time (prompt)"}
    try:
        cron = routine_cron(every, time, weekday, day)
    except (ValueError, TypeError) as e:
        return {"error": str(e)}
    mm, hh = cron.split()[:2]
    ctx.extra["choices"] = [{"card": "routine", "every": every,
                             "time": f"{int(hh):02d}:{int(mm):02d}",
                             "weekday": str(weekday or "mon").lower()[:3],
                             "day": min(max(int(day or 1), 1), 28), "prompt": prompt[:400],
                             "name": (str(name or "").strip() or prompt)[:40],
                             "agent_id": ctx.agent["id"], "cron": cron}]
    return {"ok": True, "note": "They'll see a ready-to-go card (they can change the time) and "
                                "tap Create. Don't call `schedule` for this too."}


async def _handoff(ctx: Ctx, agent: str, message: str) -> dict:
    target = db.one("SELECT * FROM agents WHERE lower(name)=lower(?)", agent.strip("@ "))
    if not target:
        return {"error": f"no teammate called {agent}"}
    ctx.extra.setdefault("handoffs", []).append((target["id"], message))
    return {"ok": True, "note": f"{target['name']} will pick it up after your reply."}


async def _delegate(ctx: Ctx, tasks: list[dict]) -> dict:
    from .runtime import run_workers
    return {"results": await run_workers(ctx, tasks[:8])}


EXEC: dict[str, Callable[..., Awaitable[Any]]] = {
    "shell": lambda ctx, command, timeout=None: computer_for(ctx.agent["id"]).shell(command,
                                                                                   timeout),
    "python": _python,
    "read_file": lambda ctx, path, offset=0: _sync(computer_for(ctx.agent["id"]).read_file,
                                                   path, 40_000, offset),
    "write_file": lambda ctx, path, content, append=False: _sync(
        computer_for(ctx.agent["id"]).write_file, path, content, append),
    "list_files": lambda ctx, path=".", pattern="**/*": _sync(
        computer_for(ctx.agent["id"]).list_files, path, pattern),
    "web_search": lambda ctx, query, max_results=6: computer_for(ctx.agent["id"]).web_search(
        query, max_results),
    "web_fetch": lambda ctx, url: computer_for(ctx.agent["id"]).web_fetch(url),
    "browser": lambda ctx, **kw: computer_for(ctx.agent["id"]).browser(**kw),
    "remember": lambda ctx, fact, about_user=False: _sync(
        lambda: {"saved_to": memory.remember(ctx.agent["id"], fact, about_user)}),
    "forget": lambda ctx, text: _sync(lambda: {"removed": memory.forget(ctx.agent["id"], text)}),
    "notify": _notify,
    "publish_page": _publish,
    "schedule": _schedule,
    "list_automations": lambda ctx: _sync(lambda: {"automations": db.q(
        "SELECT id,name,kind,schedule,event_filter,enabled,next_run FROM automations "
        "WHERE agent_id=?", ctx.agent["id"])}),
    "cancel_automation": lambda ctx, id: _sync(lambda: (db.update("automations", id, enabled=0),
                                                         {"ok": True})[1]),
    "offer_choices": _offer_choices,
    "handoff": _handoff,
    "delegate": _delegate,
}


async def _sync(f, *a):
    return f(*a)


def parse_args(raw: str) -> dict:
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except json.JSONDecodeError:
        # GLM occasionally emits trailing junk; salvage the first JSON object
        m = re.search(r"\{.*\}", raw or "", re.S)
        try:
            return json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:
            return {}


# ---------------- extension registry ----------------
SHOWN_WHEN: dict[str, Callable[[], bool]] = {}  # tools that only make sense sometimes


def register_tool(name: str, schema: dict, executor: Callable[..., Awaitable[Any]],
                  policy: str = "ask", worker: bool = False,
                  shown_when: Callable[[], bool] | None = None) -> None:
    """Add a tool from an extension module.

    schema:   OpenAI function schema (use ``fn(...)``)
    executor: ``async def run(ctx: Ctx, **args) -> dict``
    policy:   default Gatekeeper decision: "allow" | "ask" | "deny"
    worker:   also expose to delegated helpers
    shown_when: offer the tool only while this returns True (e.g. a device is paired)
    """
    from .gatekeeper import DEFAULT_POLICY
    TOOLS[name] = schema
    EXEC[name] = executor
    DEFAULT_POLICY.setdefault(name, policy)
    if worker and name not in WORKER_TOOLS:
        WORKER_TOOLS.append(name)
    if shown_when:
        SHOWN_WHEN[name] = shown_when


# the routine card is only a suggestion the human edits and taps — nothing to OK first
register_tool("suggest_routine", fn(
    "suggest_routine", "Offer to set up something recurring (every day / weekdays / every "
    "Monday / monthly on day N) as a ready-to-go card the human can tweak and create with one "
    "tap. Use it when YOU think a routine would help, or when they want one but didn't say "
    "exactly when. If they already said exactly when, just use `schedule`.",
    {"prompt": {"type": "string", "description": "what you'll do each time, in their language"},
     "every": {"type": "string", "enum": list(EVERY)},
     "time": {"type": "string", "description": "HH:MM, 24h local time"},
     "weekday": {"type": "string", "enum": list(WEEKDAYS), "description": "for every=weekly"},
     "day": {"type": "integer", "description": "day of month 1-28, for every=monthly"},
     "name": {"type": "string", "description": "a short name"}},
    ["prompt", "every"]), _suggest_routine, policy="allow")
