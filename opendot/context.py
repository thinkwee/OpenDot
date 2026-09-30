"""Keeping what the model reads lean: cheaper, faster, and better answers (a model gets
worse at finding what matters as its context fills up).

Within a run (``fit``, before every model call):
1. Tool-result clearing: once the run passes ``trigger`` tokens, older tool results
   (all but the last few) become a one-line stub. Done in one go, not one at a time:
   each clearing changes the start of the conversation and so costs a prompt-cache
   rebuild, so it should happen rarely.
2. Compaction: if that isn't enough, the middle of the run is summarised by the model
   into one note (task, what was found, decisions, what's left) and the run goes on.

Across turns (``history``): the chat's recent messages within a token budget, plus a
rolling summary of everything older (``update_summary``, after a run, off the
critical path). The summary sits right after the system prompt and changes only when
it's rewritten, so it stays in the prompt cache.
"""

from __future__ import annotations

import json
import logging
import re

from .config import settings

log = logging.getLogger("opendot.context")

KEEP_RESULTS = 3  # tool results kept in full, newest first
STUB_OVER = 600  # results shorter than this aren't worth clearing
HISTORY_BUDGET = 8000  # tokens of recent chat messages kept word for word
MIN_RECENT = 6  # …but always at least this many messages
SUMMARY_OVER = 1500  # tokens of older messages worth folding into the summary
CLEARED = "[cleared to save space]"

_NON_ASCII = re.compile(r"[^\x00-\x7f]")


def tokens(x) -> int:
    """A quick estimate (no tokenizer round trip): ~4 characters per token for Latin
    text, ~1 per token for Chinese/Japanese and other wide characters."""
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, default=str)
    wide = len(_NON_ASCII.findall(s))
    return (len(s) - wide) // 4 + wide


def window(route: str) -> int:
    try:
        import litellm
        n = litellm.get_model_info(route).get("max_input_tokens")
        if n:
            return int(n)
    except Exception:  # a model LiteLLM doesn't know
        pass
    return settings.CONTEXT_WINDOW


def trigger(route: str) -> int:
    """When to start clearing: half the model's window, and never above the cap. A
    million-token window doesn't mean a million tokens a step is a good idea."""
    return min(window(route) // 2, settings.CONTEXT_TRIGGER)


# ---------------- within a run ----------------
def clear_old_results(messages: list[dict], keep: int = KEEP_RESULTS) -> int:
    """Stub out tool results older than the last ``keep``. Returns tokens saved."""
    names = {c["id"]: c["function"]["name"] for m in messages if m.get("tool_calls")
             for c in m["tool_calls"]}
    results = [m for m in messages if m.get("role") == "tool"]
    saved = 0
    for m in results[:-keep] if keep else results:
        body = m.get("content") or ""
        if body.startswith(CLEARED) or len(body) < STUB_OVER:
            continue
        name = names.get(m.get("tool_call_id"), "a tool")
        head = re.sub(r"\s+", " ", body[:200]).strip()
        m["content"] = f"{CLEARED} {name} returned {len(body):,} characters starting: {head}… " \
                       "Call it again if you need the details."
        saved += tokens(body) - tokens(m["content"])
    return saved


COMPACT_PROMPT = """You are compressing the middle of an AI agent's working session so it can keep going
with less to read. Write the note the agent will continue from. Keep everything it will
need; drop the rest.

Include, as short bullets:
- The task, in the human's words, and any preferences or constraints they gave.
- What has been done so far and what each step found: names, numbers, links, file names,
  ids, dates. Copy exact values; don't paraphrase them.
- Decisions made and why, and anything that failed (so it isn't retried blindly).
- What's left to do.

Don't include raw tool output, greetings or anything the agent won't use. Write in the
language of the conversation."""


async def fit(messages: list[dict], tools: list[dict], client) -> None:
    """Make this run's next model call fit its budget (edits ``messages`` in place)."""
    limit = trigger(getattr(client, "route", "") or "")
    size = tokens(messages) + tokens(tools)
    if size <= limit:
        return
    saved = clear_old_results(messages)
    if saved:
        log.info("context: cleared old tool results (%s → %s tokens)", size, size - saved)
        size -= saved
    if size <= limit * 1.3:
        return
    await compact(messages, client)


def _cut(messages: list[dict], tail: int = 6) -> int:
    """Where the kept tail starts: never on a tool result (its call must come with it)."""
    i = max(1, len(messages) - tail)
    while i < len(messages) and messages[i].get("role") == "tool":
        i -= 1
    return max(1, i)


async def compact(messages: list[dict], client) -> None:
    start = 1 if messages and messages[0].get("role") == "system" else 0
    cut = _cut(messages)
    middle = messages[start:cut]
    if len(middle) < 3:
        return
    from . import usage
    text = "\n\n".join(_plain(m) for m in middle)[-120_000:]
    try:
        with usage.tagged(kind="compaction"):
            reply = await client.chat([{"role": "system", "content": COMPACT_PROMPT},
                                       {"role": "user", "content": text}], max_tokens=1500)
        note = reply.content.strip()
    except Exception as e:  # fall back to clearing everything but the last result
        log.warning("compaction failed: %s", e)
        clear_old_results(messages, keep=1)
        return
    new = [{"role": "user", "content": "[Summary of the work so far in this task — the "
                                       "details were compacted to save space]\n" + note}]
    if cut < len(messages) and messages[cut].get("role") == "user":  # keep turns alternating
        new.append({"role": "assistant", "content": "Got it, continuing from there."})
    messages[start:cut] = new
    log.info("context: compacted %d messages into a %d-token note", len(middle), tokens(note))


def _plain(m: dict) -> str:
    role = m.get("role")
    if m.get("tool_calls"):
        calls = "; ".join(f"{c['function']['name']}({c['function']['arguments'][:300]})"
                          for c in m["tool_calls"])
        return f"[agent] {m.get('content') or ''}\n[calls] {calls}".strip()
    body = m.get("content")
    if isinstance(body, list):  # images and text parts
        body = " ".join(p.get("text", "") for p in body if isinstance(p, dict))
    body = (body or "")[:6000]
    return f"[{'result' if role == 'tool' else role}] {body}"


# ---------------- across turns ----------------
SUMMARY_PROMPT = """You keep a running summary of a long chat between a human and their AI agent, so the
agent can remember what happened earlier without rereading it all.

Update the summary with the new messages. Keep: what the human asked for and decided,
their preferences, facts and results the agent found (names, numbers, links, dates —
exact), promises made, and anything still open. Drop small talk and anything superseded.
At most 350 words, short bullets, in the language of the chat. Reply with the summary only."""


def summary_key(thread_id: str, agent_id: str) -> str:
    return f"chat_summary:{thread_id}:{agent_id}"


def split_history(rows: list[dict], upto: float, budget: int = HISTORY_BUDGET) -> tuple[list, list]:
    """(older messages not yet in the summary, recent messages kept word for word)."""
    rows = [r for r in rows if r["created"] > upto]
    keep, used = [], 0
    for r in reversed(rows):
        cost = tokens(r.get("content") or "")
        if len(keep) >= MIN_RECENT and used + cost > budget:
            break
        keep.append(r)
        used += cost
    keep.reverse()
    return rows[:len(rows) - len(keep)], keep


async def update_summary(thread_id: str, agent: dict) -> None:
    """Fold messages that fell out of the word-for-word window into the chat summary."""
    from . import usage
    from .db import db
    from .ext.profiles import llm_for_agent
    key = summary_key(thread_id, agent["id"])
    s = db.kv_get(key) or {"upto": 0, "text": ""}
    rows = db.q("SELECT * FROM messages WHERE thread_id=? ORDER BY created", thread_id)
    older, _ = split_history(rows, s["upto"])
    if not older or tokens([r["content"] for r in older]) < SUMMARY_OVER:
        return
    names = {a["id"]: a["name"] for a in db.q("SELECT id, name FROM agents")}
    lines = [f"[{'human' if r['role'] == 'user' else names.get(r['agent_id'], r['role'])}] "
             f"{(r['content'] or '')[:4000]}" for r in older]
    body = (f"Summary so far:\n{s['text'] or '(none yet)'}\n\nNew messages:\n" + "\n\n".join(lines))
    try:
        with usage.tagged(agent_id=agent["id"], thread_id=thread_id, kind="compaction"):
            reply = await llm_for_agent(agent["id"]).chat(
                [{"role": "system", "content": SUMMARY_PROMPT},
                 {"role": "user", "content": body[-100_000:]}], max_tokens=900)
    except Exception as e:
        log.warning("chat summary for %s failed: %s", thread_id, e)
        return
    if reply.content.strip():
        db.kv_set(key, {"upto": older[-1]["created"], "text": reply.content.strip()})
