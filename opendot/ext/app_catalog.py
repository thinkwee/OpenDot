"""App catalog: one-click MCP connectors (Apps & Connectors → "Add an app").

The curated list lives in ``connectors.CATALOG``. Adding an entry puts its secrets in
the vault, writes the finished spec into data/connectors.json and reconnects — the
same place the hand-written JSON editor saves to.
"""

from __future__ import annotations

import re
import shutil

from fastapi import APIRouter, Body, HTTPException

from .. import gatekeeper
from ..connectors import (
    CATALOG,
    CATALOG_CATEGORIES,
    build_spec,
    catalog_entry,
    load_config,
    mcp_hub,
    save_config,
)
from ..runtime import spawn

router = APIRouter()


def _have() -> dict:
    return {"node": bool(shutil.which("npx")), "uv": bool(shutil.which("uvx")),
            "docker": bool(shutil.which("docker"))}


@router.get("/api/connectors/catalog")
async def api_catalog():
    return {"categories": CATALOG_CATEGORIES, "entries": CATALOG, "have": _have(),
            "installed": sorted((load_config().get("mcp") or {}).keys())}


def _free_name(base: str, taken: dict) -> str:
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


@router.post("/api/connectors/catalog/{entry_id}")
async def api_add_from_catalog(entry_id: str, body: dict = Body(default={})):
    """Body: {"values": {field: value}, "name": optional connector name}."""
    entry = catalog_entry(entry_id)
    if not entry:
        raise HTTPException(404, "no such app in the catalog")
    values = body.get("values") or {}
    cfg = load_config()
    mcp = cfg.setdefault("mcp", {})
    wanted = re.sub(r"[^a-z0-9_-]+", "-", (body.get("name") or entry_id).lower()).strip("-")
    name = _free_name(wanted or entry_id, mcp)
    vault_names = {}
    for f in entry.get("fields", []):
        v = str(values.get(f["key"]) or "").strip()
        if f["secret"] and v:
            vname = re.sub(r"[^A-Z0-9_]", "_", f"MCP_{name}_{f['key']}".upper())
            gatekeeper.vault_set(vname, v)
            vault_names[f["key"]] = vname
    try:
        spec = build_spec(entry, values, vault_names)
    except ValueError as e:
        for vname in vault_names.values():
            gatekeeper.vault_set(vname, None)
        raise HTTPException(400, str(e)) from e
    mcp[name] = spec
    save_config(cfg)
    spawn(mcp_hub.start())
    return {"ok": True, "name": name, "spec": spec}
