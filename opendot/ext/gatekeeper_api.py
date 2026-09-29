"""Scoped approval decisions (once / this session / always-for-domain / always-for-tool /
deny). ``opendot/server.py`` is lead-owned and its ``POST /api/approvals/{id}`` only knows
the old ``{approve, always}`` shape, so this adds a sibling endpoint the frontend calls
instead; both update the same ``approvals``/``inbox`` rows. (Noted as a change request in
docs/ws/W1.md — the lead may want to fold this into the original route later.)
"""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from .. import gatekeeper
from ..db import db

router = APIRouter(prefix="/api/gatekeeper")


@router.post("/approvals/{apid}/decide")
async def decide(apid: str, body: dict = Body(...)):
    if not db.one("SELECT id FROM approvals WHERE id=?", apid):
        raise HTTPException(404)
    ap = gatekeeper.decide(apid, bool(body.get("approve")), bool(body.get("always")),
                         scope=body.get("scope"))
    with db.lock:
        db.conn.execute("UPDATE inbox SET status='done' WHERE ref=?", (apid,))
        db.conn.commit()
    return ap
