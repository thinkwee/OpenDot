"""One chat client for every model: tool calling and streaming through LiteLLM.

``LLM_MODEL`` is a LiteLLM model name, ``provider/model`` — ``openai/…``, ``anthropic/…``,
``gemini/…``, ``deepseek/…``, ``openrouter/<vendor>/<model>``, ``ollama_chat/…`` (100+
providers, each called on its own official API; ``opendot/models.py`` lists what a
provider offers right now). The key comes from
``LLM_API_KEY`` or the provider's usual variable (``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``…).
With ``LLM_BASE_URL`` set, the model is served by that OpenAI-compatible endpoint
(vLLM, LM Studio, a company gateway…) and ``LLM_MODEL`` is its bare model name.

Instances can be parametrised so different agents run on different named profiles
(see ``opendot/ext/profiles.py``); the module-level ``llm`` is the default from ``.env``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import AsyncIterator

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # no network fetch on import
import litellm  # noqa: E402

from .config import settings  # noqa: E402

log = logging.getLogger("opendot.llm")
litellm.drop_params = True  # a setting one provider lacks is dropped, not an error
litellm.suppress_debug_info = True


@dataclass
class LLMReply:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)


class LLM:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                model: str | None = None, reasoning_effort: str | None = None,
                max_tokens: int | None = None, concurrency: int | None = None,
                record: bool = True) -> None:
        self.base_url = (settings.LLM_BASE_URL if base_url is None else base_url) or None
        self.api_key = (settings.LLM_API_KEY if api_key is None else api_key) or None
        self.model = model or settings.LLM_MODEL
        self.reasoning_effort = (settings.LLM_REASONING_EFFORT if reasoning_effort is None
                                 else reasoning_effort)
        self.max_tokens = max_tokens or settings.LLM_MAX_TOKENS
        self.sem = asyncio.Semaphore(concurrency or settings.LLM_CONCURRENCY)
        self.record = record  # count tokens on the Usage page (not for the setup test)

    @property
    def _explicit_cache(self) -> bool:
        """Providers that need cache markers; OpenAI, DeepSeek, Gemini… cache on their own."""
        r = self.route.lower()
        return not self.base_url and "claude" in r and \
            r.startswith(("anthropic/", "bedrock/", "vertex_ai/", "openrouter/anthropic/"))

    @property
    def route(self) -> str:
        """The LiteLLM model string (a custom endpoint speaks the OpenAI API)."""
        if self.base_url and not self.model.startswith("openai/"):
            return "openai/" + self.model
        return self.model

    def _kwargs(self, messages: list[dict], tools: list[dict] | None, max_tokens: int | None,
               stream: bool = False) -> dict:
        kwargs: dict = {
            "model": self.route,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "timeout": 300,
        }
        if self.base_url:
            kwargs["api_base"] = self.base_url
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
            if self.base_url:  # custom endpoints decide for themselves (e.g. GLM needs "low")
                kwargs["allowed_openai_params"] = ["reasoning_effort"]
        if tools:
            kwargs["tools"] = tools
        if self._explicit_cache:
            # Claude caches only where told: after the system prompt (tools come before it,
            # so they're covered) and at the newest message, so each step reuses the last
            kwargs["cache_control_injection_points"] = [
                {"location": "message", "role": "system"}, {"location": "message", "index": -1}]
        if stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
        return kwargs

    async def chat(self, messages: list[dict], tools: list[dict] | None = None,
                   max_tokens: int | None = None) -> LLMReply:
        kwargs = self._kwargs(messages, tools, max_tokens)
        last: Exception | None = None
        for attempt in range(3):
            try:
                async with self.sem:
                    r = await litellm.acompletion(**kwargs)
                break
            except Exception as e:  # network / 5xx / 429
                last = e
                code = getattr(e, "status_code", None)
                if code and code < 500 and code != 429:
                    raise
                log.warning("LLM call failed (attempt %d): %s", attempt + 1, e)
                await asyncio.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError(f"LLM unavailable: {last}")
        msg = r.choices[0].message
        calls = [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments or "{}"}
            for tc in (msg.tool_calls or [])
        ]
        usage = _usage(getattr(r, "usage", None))
        self._count(getattr(r, "usage", None))
        return LLMReply(content=(msg.content or "").strip(), tool_calls=calls, usage=usage)

    async def chat_stream(self, messages: list[dict], tools: list[dict] | None = None,
                          max_tokens: int | None = None) -> AsyncIterator[tuple[str, object]]:
        """Yield ``("delta", text)`` chunks as they arrive, then a final
        ``("done", LLMReply)``. Tool-call argument deltas are accumulated by index
        (some backends split a single call's JSON across many chunks). Raises on
        failure — the caller (``runtime.agent_loop``) falls back to ``chat()``.
        """
        kwargs = self._kwargs(messages, tools, max_tokens, stream=True)
        async with self.sem:
            stream = await litellm.acompletion(**kwargs)
            parts: list[str] = []
            calls: dict[int, dict] = {}
            usage: dict = {}
            raw = None
            async for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage, raw = _usage(chunk.usage), chunk.usage
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                if delta.content:
                    parts.append(delta.content)
                    yield ("delta", delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] += tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["arguments"] += tc.function.arguments
                            yield ("tool_delta", (slot["name"], len(slot["arguments"])))
            ordered = [calls[i] for i in sorted(calls)]
            self._count(raw)
            yield ("done", LLMReply(content="".join(parts).strip(), tool_calls=ordered,
                                    usage=usage))


    def _count(self, u) -> None:
        if self.record and u:
            from . import usage
            usage.record(self.model, self.route, u)


def _usage(u) -> dict:
    if not u:
        return {}
    d = u.model_dump() if hasattr(u, "model_dump") else dict(u)
    return {k: d.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}


llm = LLM()
