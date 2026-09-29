"""Which models can I use? Asked live, of the provider itself — nothing is hard-coded,
so a model released this morning shows up this morning.

Each provider has a LiteLLM ``prefix`` (so a bare name like ``deepseek-chat`` is routed
correctly), where its key lives, and how to list its models. ``list_models`` returns
chat models newest first as full LiteLLM names, or raises with a readable reason.
"""

from __future__ import annotations

import os
import re

import httpx

PROVIDERS: list[dict] = [
    {"id": "openai", "label": "OpenAI", "prefix": "openai/", "env": "OPENAI_API_KEY",
     "keys": "https://platform.openai.com/api-keys", "list": "https://api.openai.com/v1/models"},
    {"id": "anthropic", "label": "Anthropic Claude", "prefix": "anthropic/", "env": "ANTHROPIC_API_KEY",
     "keys": "https://console.anthropic.com/settings/keys", "list": "https://api.anthropic.com/v1/models"},
    {"id": "gemini", "label": "Google Gemini", "prefix": "gemini/", "env": "GEMINI_API_KEY",
     "keys": "https://aistudio.google.com/apikey",
     "list": "https://generativelanguage.googleapis.com/v1beta/models"},
    {"id": "deepseek", "label": "DeepSeek", "prefix": "deepseek/", "env": "DEEPSEEK_API_KEY",
     "keys": "https://platform.deepseek.com/api_keys", "list": "https://api.deepseek.com/models"},
    {"id": "xai", "label": "xAI Grok", "prefix": "xai/", "env": "XAI_API_KEY",
     "keys": "https://console.x.ai", "list": "https://api.x.ai/v1/models"},
    # OpenAI-compatible services: the first base URL the key works on is kept (China / global)
    {"id": "qwen", "label": "Qwen (Alibaba Cloud)", "env": "DASHSCOPE_API_KEY",
     "keys": "https://bailian.console.aliyun.com/?apiKey=1",
     "bases": ["https://dashscope.aliyuncs.com/compatible-mode/v1",
               "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"]},
    {"id": "kimi", "label": "Kimi (Moonshot)", "env": "MOONSHOT_API_KEY",
     "keys": "https://platform.moonshot.cn/console/api-keys",
     "bases": ["https://api.moonshot.cn/v1", "https://api.moonshot.ai/v1"]},
    {"id": "glm", "label": "GLM (Zhipu / Z.ai)", "env": "ZAI_API_KEY",
     "keys": "https://open.bigmodel.cn/usercenter/apikeys",
     "bases": ["https://open.bigmodel.cn/api/paas/v4", "https://api.z.ai/api/paas/v4"]},
    {"id": "openrouter", "label": "OpenRouter", "prefix": "openrouter/", "env": "OPENROUTER_API_KEY",
     "keys": "https://openrouter.ai/keys", "list": "https://openrouter.ai/api/v1/models",
     "note": "one key, hundreds of models"},
    {"id": "ollama", "label": "Ollama", "prefix": "ollama_chat/", "env": "", "keys": "",
     "list": "http://localhost:11434/api/tags", "note": "free & private, runs on this computer"},
    {"id": "custom", "label": "My own server", "env": "", "keys": "",
     "note": "any OpenAI-compatible URL (vLLM, LM Studio, a gateway…)"},
]
BY_ID = {p["id"]: p for p in PROVIDERS}

# not chat models (or not usable through chat completions)
_SKIP = re.compile(r"embed|whisper|tts|transcri|audio|realtime|image|dall-e|sora|moderation|"
                   r"search|computer-use|deep-research|babbage|davinci|instruct|rerank|"
                   r"vision-preview|aqa|imagen|veo|gemma|learnlm|ocr|-asr|-mt-|paraformer|"
                   r"wanx|cogview|cogvideo", re.I)
_OPENAI_ONLY_RESPONSES = re.compile(r"codex|-pro\b|-pro-", re.I)
_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$|-\d{4}$")  # dated duplicates of an alias


def full_name(provider: str, model: str) -> str:
    """``deepseek-chat`` → ``deepseek/deepseek-chat`` (a name that already carries the
    prefix is left alone)."""
    prefix = BY_ID.get(provider, {}).get("prefix", "")
    if prefix and not model.startswith(prefix):
        return prefix + model
    return model


def _natural(s: str) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def _get(url: str, headers: dict | None = None, params: dict | None = None) -> dict:
    r = httpx.get(url, headers=headers or {}, params=params or {}, timeout=15, follow_redirects=True)
    if r.status_code in (400, 401, 403):  # a malformed key is a 400 at some providers
        raise PermissionError("the key was not accepted")
    r.raise_for_status()
    return r.json()


def _openai_style(base: str, key: str) -> list[tuple[str, float]]:
    data = _get(base.rstrip("/") + "/models", {"Authorization": f"Bearer {key}"} if key else {})
    return [(m["id"], m.get("created") or 0) for m in data.get("data", [])]


def list_models(provider: str, key: str = "", base_url: str = "") -> tuple[list[str], str]:
    """(model names newest first, the base URL that worked or ""). Raises on failure."""
    p = BY_ID[provider]
    key = key or (os.environ.get(p["env"], "") if p.get("env") else "")
    rows: list[tuple[str, float]] = []
    used_base = ""
    if provider == "anthropic":
        data = _get(p["list"], {"x-api-key": key, "anthropic-version": "2023-06-01"},
                    {"limit": 100})
        rows = [(m["id"], i) for i, m in enumerate(reversed(data.get("data", [])))]
    elif provider == "gemini":
        data = _get(p["list"], {"x-goog-api-key": key}, {"pageSize": 1000})
        rows = [(m["name"].split("/", 1)[-1], 0) for m in data.get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])]
        # no dates: newer versions sort higher, previews and "-lite" after the main model
        rows.sort(key=lambda r: (_natural(re.sub(r"-(preview|exp|lite).*", "", r[0])),
                                 "preview" not in r[0] and "exp" not in r[0],
                                 "lite" not in r[0]))
        rows = [(n, i) for i, (n, _) in enumerate(rows)]
    elif provider == "ollama":
        data = _get(p["list"])
        rows = [(m["name"], 0) for m in data.get("models", [])]
    elif provider == "openrouter":
        data = _get(p["list"])
        rows = [(m["id"], m.get("created") or 0) for m in data.get("data", [])
                if "tools" in (m.get("supported_parameters") or [])
                and (":" not in m["id"] or m["id"].endswith(":free"))]
    elif "bases" in p or provider == "custom":
        bases = [base_url] if base_url else p.get("bases", [])
        err: Exception | None = None
        for b in bases:
            try:
                rows = _openai_style(b, key)
                used_base = b
                break
            except Exception as e:  # wrong region for this key → try the next
                err = e
        else:
            raise err or RuntimeError("no base URL")
    else:
        rows = _openai_style(p["list"].rsplit("/models", 1)[0], key)

    names = [n for n, _ in sorted(rows, key=lambda r: r[1], reverse=True) if not _SKIP.search(n)]
    if provider == "openai":
        names = [n for n in names if not _OPENAI_ONLY_RESPONSES.search(n)]
    if provider in ("openai", "xai", "deepseek"):
        aliases = set(names)
        names = [n for n in names
                 if not (_SNAPSHOT.search(n) and _SNAPSHOT.sub("", n) in aliases)]
    seen: set[str] = set()
    out = [full_name(provider, n) for n in names if not (n in seen or seen.add(n))]
    return out, used_base


def friendly_error(e: Exception) -> str:
    """One line a person can act on, instead of a traceback."""
    name = type(e).__name__
    msg = str(e).replace("\n", " ")
    msg = re.sub(r"^litellm\.\w+:\s*", "", msg)
    low = msg.lower()
    if (isinstance(e, PermissionError) or name == "AuthenticationError" or "401" in msg[:40]
            or "authentication" in low or "api key" in low or "api_key" in low):
        return "the key was not accepted — check it was copied in full"
    if name == "NotFoundError" or "does not exist" in msg or "not found" in msg.lower()[:200]:
        return "the provider doesn't know that model name"
    if name == "RateLimitError" or "429" in msg[:40] or "quota" in msg.lower() or "balance" in msg.lower():
        return "the account is out of credit or rate-limited — check billing on the provider's site"
    if "Provider NOT provided" in msg:
        return "that isn't a full LiteLLM model name (e.g. provider/model)"
    if name in ("APIConnectionError", "ConnectError", "ConnectTimeout", "Timeout") or "connect" in msg.lower()[:120]:
        return "couldn't reach it — check the internet connection (or that the server is running)"
    if name == "HTTPStatusError":
        return f"the provider answered {getattr(getattr(e, 'response', None), 'status_code', '?')}"
    return msg[:240]
