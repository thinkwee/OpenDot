"""The human's language (English / 中文), set from the app.

Everything the server says on its own — push notifications, approval wording,
agent templates, the evening line — follows it, and agents are told to write
unprompted messages (routines, watches) in it. Replies in chat still mirror
whatever language the human writes in.

    GET/PUT /api/settings/lang   {"lang": "en" | "zh"}
"""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from ..db import db

router = APIRouter(prefix="/api/settings")
LANGS = ("en", "zh")


def lang() -> str:
    v = db.kv_get("lang", "en")
    return v if v in LANGS else "en"


def tr(en: str, zh: str) -> str:
    """Pick the string for the human's language."""
    return zh if lang() == "zh" else en


@router.get("/lang")
async def get_lang():
    return {"lang": lang()}


@router.put("/lang")
async def put_lang(body: dict = Body(...)):
    v = body.get("lang")
    if v not in LANGS:
        raise HTTPException(400, "lang must be en or zh")
    db.kv_set("lang", v)
    _localize_front_desk(v)
    return {"lang": v}


def _localize_front_desk(v: str) -> None:
    """The front desk's built-in blurb follows the language (unless you edited it)."""
    from ..runtime import FRONT_DESK_TEXT
    for a in db.q("SELECT * FROM agents WHERE origin='default'"):
        fields = {}
        for key, texts in FRONT_DESK_TEXT.items():
            if (a.get(key) or "") in texts.values() or not a.get(key):
                fields[key] = texts[v]
        if fields:
            db.update("agents", a["id"], **fields)
            from ..bus import bus
            bus.emit("agent_updated", agent=db.one("SELECT * FROM agents WHERE id=?", a["id"]))


def PROMPT(agent: dict) -> str | None:
    if lang() != "zh":
        return None
    return ("# Language\nThe human uses the app in Chinese (简体中文). Anything you send on "
            "your own (routines, watches, check-ins, reply chips) must be in Chinese. In chat, "
            "answer in whatever language they write in.")
