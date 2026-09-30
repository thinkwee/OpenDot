"""Token usage: every model call, who made it and for what (Settings → Usage).

``llm.py`` records each reply's usage here; ``tag`` (a context variable) says which
agent and what kind of work it was for, set where a run starts, so the model layer
needn't know about agents. The cost is LiteLLM's estimate from its price list; a model
it has no price for (your own endpoint, a local model) is counted with no cost.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import time

from .db import db, new_id

log = logging.getLogger("opendot.usage")

# {"agent_id", "thread_id", "kind"} for the model calls made under it
tag: contextvars.ContextVar[dict] = contextvars.ContextVar("usage_tag", default={})

# what a run was for (``runtime`` sources) → the kinds the Usage page shows
KINDS = {"chat": "chat", "handoff": "chat", "automation": "routine", "watch": "watch",
         "event": "watch", "heartbeat": "check-in", "goal": "goal", "email": "message",
         "sms": "message", "call": "message"}


def kind_of(source: str) -> str:
    return KINDS.get((source or "chat").split(":", 1)[0], "chat")


@contextlib.contextmanager
def tagged(**t):
    """Model calls inside this block are counted for this agent / kind of work."""
    token = tag.set({**tag.get(), **{k: v for k, v in t.items() if v is not None}})
    try:
        yield
    finally:
        tag.reset(token)


def _num(d, *path) -> int:
    for k in path:
        d = d.get(k) if isinstance(d, dict) else getattr(d, k, None)
        if d is None:
            return 0
    try:
        return int(d)
    except (TypeError, ValueError):
        return 0


def _cost(route: str, u) -> float | None:
    try:
        import litellm
        from litellm.types.utils import Usage
        a, b = litellm.cost_per_token(model=route, prompt_tokens=_num(u, "prompt_tokens"),
                                      completion_tokens=_num(u, "completion_tokens"),
                                      usage_object=u if isinstance(u, Usage) else None)
        return round(a + b, 8)
    except Exception:  # not on LiteLLM's price list
        return None


def record(model: str, route: str, u) -> None:
    """One model reply's usage (``u``: the provider's usage object)."""
    if not u:
        return
    inp, out = _num(u, "prompt_tokens"), _num(u, "completion_tokens")
    if not inp and not out:
        return
    t = tag.get()
    try:
        db.insert("usage", id=new_id("u_"), agent_id=t.get("agent_id") or "",
                  thread_id=t.get("thread_id") or "", kind=t.get("kind") or "other",
                  model=model, input=inp, output=out,
                  cached=_num(u, "prompt_tokens_details", "cached_tokens")
                  or _num(u, "cache_read_input_tokens"),
                  reasoning=_num(u, "completion_tokens_details", "reasoning_tokens"),
                  cost=_cost(route, u), created=time.time())
    except Exception:  # counting must never break a reply
        log.exception("couldn't record token usage")
