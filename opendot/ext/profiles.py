"""Per-agent LLM profiles: named model configs (model, api_key/vault ref, optional
base_url, reasoning_effort) an agent can be pointed at, with a cached client per profile.
``model`` is a LiteLLM name (``provider/model``); ``base_url`` is only for an
OpenAI-compatible server. ``POST /api/profiles/models`` asks a provider which models it
offers right now, so the Settings form never relies on a hard-coded list.

A "private" preset profile is included out of the box, pointing at a local Ollama so
privacy-sensitive agents never leave this machine (it uses whichever model you've pulled).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Body, HTTPException

from .. import usage
from ..config import settings
from ..db import db
from ..gatekeeper import inject_secrets
from ..llm import LLM
from ..models import PROVIDERS, friendly_error, list_models

log = logging.getLogger("opendot.ext.profiles")
router = APIRouter(prefix="/api/profiles")


def _default_profile() -> dict:
    return {
        "name": "default", "base_url": settings.LLM_BASE_URL, "api_key": settings.LLM_API_KEY,
        "model": settings.LLM_MODEL, "reasoning_effort": settings.LLM_REASONING_EFFORT,
        "max_tokens": settings.LLM_MAX_TOKENS, "builtin": True,
    }


def _private_profile() -> dict:
    return {
        "name": "private", "base_url": "", "api_key": "",
        "model": "", "reasoning_effort": "", "max_tokens": settings.LLM_MAX_TOKENS,
        "builtin": True, "note": "Local model (e.g. Ollama) — nothing leaves this machine.",
    }


def list_profiles() -> list[dict]:
    profs = db.kv_get("llm_profiles")
    if not profs:
        profs = [_default_profile(), _private_profile()]
        db.kv_set("llm_profiles", profs)
    return profs


def get_profile(name: str) -> dict | None:
    return next((p for p in list_profiles() if p["name"] == name), None)


def save_profile(p: dict) -> list[dict]:
    profs = [x for x in list_profiles() if x["name"] != p["name"]]
    profs.append(p)
    db.kv_set("llm_profiles", profs)
    _cache.pop(p["name"], None)
    return profs


def delete_profile(name: str) -> list[dict]:
    if name in ("default", "private"):
        raise ValueError("the default and private profiles can't be deleted, only edited")
    profs = [x for x in list_profiles() if x["name"] != name]
    db.kv_set("llm_profiles", profs)
    _cache.pop(name, None)
    for a in db.q("SELECT id FROM agents"):
        if db.kv_get(f"profile:{a['id']}") == name:
            db.kv_set(f"profile:{a['id']}", "default")
    return profs


_cache: dict[str, LLM] = {}


def _local_model() -> str:
    """The newest model pulled into Ollama, for the "private" profile."""
    try:
        return list_models("ollama")[0][0]
    except Exception:
        return ""


def _build(prof: dict) -> LLM:
    real_key = inject_secrets({"k": prof.get("api_key") or ""})["k"]
    model = prof.get("model") or (_local_model() if prof.get("name") == "private" else "")
    return LLM(base_url=prof.get("base_url") or "", api_key=real_key,
              model=model or settings.LLM_MODEL,
              reasoning_effort=prof.get("reasoning_effort", ""),
              max_tokens=int(prof.get("max_tokens") or settings.LLM_MAX_TOKENS))


def llm_for(profile_name: str | None) -> LLM:
    name = profile_name or "default"
    if name not in _cache:
        prof = get_profile(name) or _default_profile()
        _cache[name] = _build(prof)
    return _cache[name]


def llm_for_agent(agent_id: str) -> LLM:
    """The client an agent (or one of its ``<id>-wN`` delegated helpers) should use."""
    base = agent_id.split("-w")[0]
    return llm_for(db.kv_get(f"profile:{base}"))


# ---------------- API ----------------
def _public(p: dict) -> dict:
    """Never ship raw API keys to the browser — just whether one is set."""
    return {**{k: v for k, v in p.items() if k != "api_key"}, "has_key": bool(p.get("api_key"))}


@router.get("")
async def _list():
    agents_map = {a["id"]: db.kv_get(f"profile:{a['id']}", "default")
                 for a in db.q("SELECT id FROM agents")}
    return {"profiles": [_public(p) for p in list_profiles()], "agents": agents_map}


@router.put("")
async def _put(body: dict = Body(...)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    existing = get_profile(name)
    api_key = body.get("api_key")
    if not api_key and existing:  # blank in the form = "keep the current key"
        api_key = existing.get("api_key", "")
    p = {"name": name, "base_url": body.get("base_url", ""), "api_key": api_key or "",
        "model": body.get("model", ""), "reasoning_effort": body.get("reasoning_effort", ""),
        "max_tokens": int(body.get("max_tokens") or settings.LLM_MAX_TOKENS)}
    if name in ("default", "private"):
        p["builtin"] = True
    return [_public(x) for x in save_profile(p)]


@router.delete("/{name}")
async def _delete(name: str):
    try:
        return [_public(p) for p in delete_profile(name)]
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/test")
async def _test(body: dict = Body(...)):
    """Try a tiny call with either a saved profile (by name, using its stored key —
    the browser never sees that key) or an ad-hoc config; not cached. Used by the
    Settings 'Test' button."""
    saved = get_profile(body.get("name")) if body.get("name") else None
    prof = {**(saved or {}), **{k: v for k, v in body.items() if v not in (None, "")}}
    if not prof.get("model") and prof.get("name") != "private":
        raise HTTPException(404, "no such profile")
    cli = _build({**prof, "max_tokens": 32})
    try:
        with usage.tagged(kind="test"):
            r = await cli.chat([{"role": "user", "content": "Reply with just: OK"}])
        return {"ok": True, "reply": r.content[:200]}
    except Exception as e:
        return {"ok": False, "error": friendly_error(e)}


@router.get("/providers")
async def _providers():
    return [{k: p.get(k, "") for k in ("id", "label", "note", "keys")} for p in PROVIDERS]


@router.post("/models")
async def _models(body: dict = Body(...)):
    """The models a provider offers right now, newest first. A blank key falls back to
    the saved profile's key (the browser never sees it) or the provider's env variable."""
    provider = body.get("provider") or ""
    if provider not in {p["id"] for p in PROVIDERS}:
        raise HTTPException(400, "unknown provider")
    key = body.get("api_key") or ""
    if not key and body.get("name"):
        key = (get_profile(body["name"]) or {}).get("api_key", "")
    key = inject_secrets({"k": key})["k"]
    try:
        models, base = await asyncio.to_thread(list_models, provider, key, body.get("base_url") or "")
        return {"ok": True, "models": models, "base_url": base}
    except Exception as e:
        return {"ok": False, "error": friendly_error(e)}


@router.put("/agent/{aid}")
async def _set_agent_profile(aid: str, body: dict = Body(...)):
    name = body.get("profile") or "default"
    if name != "default" and not get_profile(name):
        raise HTTPException(404, "no such profile")
    db.kv_set(f"profile:{aid}", name)
    return {"profile": name}
