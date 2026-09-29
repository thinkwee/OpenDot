"""Second look settings, and learning from "Not now".

    GET/PUT /api/settings/reviewer   {"on": true|false}

When the human turns down an approval card, the agent gets a one-line note in its
MEMORY.md, so it asks less for things they refuse.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Body

from .. import memory, reviewer
from ..bus import bus
from ..db import db

log = logging.getLogger("opendot.second_look")
router = APIRouter(prefix="/api/settings")


@router.get("/reviewer")
async def get_reviewer():
    return {"on": reviewer.enabled(),
            "locked": os.environ.get("DOT_REVIEWER", "").lower() in ("off", "0", "false", "no")}


@router.put("/reviewer")
async def put_reviewer(body: dict = Body(...)):
    db.kv_set("reviewer", bool(body.get("on", True)))
    return await get_reviewer()


def _on_event(kind: str, data: dict) -> None:
    if kind != "approval":
        return
    ap = data.get("approval") or {}
    if ap.get("status") != "denied" or not ap.get("agent_id"):
        return
    what = (ap.get("args") or {}).get("_what") or str(ap.get("tool", "")).replace("_", " ")
    try:
        memory.note_refusal(ap["agent_id"], what)
    except Exception:
        log.exception("couldn't note the refusal")


bus.listen(_on_event)
