"""Work files — everything an agent makes on its computer during a chat.

Formal deliverables (make_document / attach …) are one thing, but agents also just
``write_file`` notes, save CSVs from python, render charts… Those used to be
invisible unless you dug through each agent's computer. Now:

  - ``snapshot()``/``diff()`` bracket every run (runtime calls them) and record new or
    modified files in the agent's home, its helpers' homes and the shared drive as
    ``thread_files`` for that chat → they show up under Files in the chat.
  - files a reply mentions by path are attached to that reply as cards.
  - ``/api/view/raw`` + ``/api/view/text`` serve any file on an agent computer to the
    universal viewer (raw bytes for native renderers, MarkItDown/OCR text for the rest).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from .. import fileparse
from ..bus import bus
from ..computer import computer_for
from ..computer.core import shared_dir
from ..config import settings
from ..db import db, new_id

log = logging.getLogger("opendot.workfiles")
router = APIRouter()

SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "site-packages", "dist-packages",
             ".git", ".cache", ".npm", ".local", ".config", "uploads", ".parsed"}
SKIP_EXT = {".pyc", ".pyo", ".o", ".so", ".tmp", ".swp", ".lock"}
MAX_ENTRIES = 20_000

db.ensure_schema("""
CREATE TABLE IF NOT EXISTS thread_files (
  id TEXT PRIMARY KEY, thread_id TEXT, agent_id TEXT, run_id TEXT, abspath TEXT, path TEXT,
  name TEXT, kind TEXT, mime TEXT, size INTEGER, mtime REAL, created REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_thread_files ON thread_files(thread_id, abspath);
""")


# ---------------------------------------------------------------- tracking
def _homes(agent_id: str) -> list[Path]:
    base = settings.DATA_DIR / "agents"
    homes = [base / agent_id / "home"]
    homes += sorted(base.glob(f"{agent_id}-w*/home"))
    return homes


def _scan(root: Path, out: dict) -> None:
    stack = [root]
    while stack and len(out) < MAX_ENTRIES:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                if e.name.startswith(".") or e.name in SKIP_DIRS:
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
                    elif e.is_file(follow_symlinks=False):
                        if os.path.splitext(e.name)[1].lower() in SKIP_EXT:
                            continue
                        st = e.stat()
                        out[e.path] = (st.st_mtime, st.st_size)
                except OSError:
                    continue


def snapshot(agent_id: str) -> dict:
    out: dict = {}
    for h in _homes(agent_id):
        _scan(Path(os.path.realpath(h)), out)
    _scan(Path(os.path.realpath(shared_dir())), out)
    return out


def _display(agent_id: str, p: str) -> str:
    sp = os.path.realpath(shared_dir())
    if p.startswith(sp + os.sep):
        return "shared/" + p[len(sp) + 1:]
    for h in _homes(agent_id):
        h = Path(os.path.realpath(h))
        if p.startswith(str(h) + os.sep):
            rel = p[len(str(h)) + 1:]
            return rel if h.parent.name == agent_id else f"~{h.parent.name}/{rel}"
    return p


def _card(row: dict) -> dict:
    kind = fileparse.kind_of(row["name"], row["mime"] or "")
    return {"id": row["id"], "title": row["name"], "kind": kind if kind in (
                "document", "spreadsheet", "slides", "image") else
            ("app" if row["name"].lower().endswith((".html", ".htm")) else "file"),
            "file_kind": kind, "mime": row["mime"], "size": row["size"], "path": row["path"],
            "agent_id": row["agent_id"], "workfile": True,
            "url": f"/api/view/raw?agent={row['agent_id']}&path={_q(row['path'])}",
            "preview_url": f"/api/view/raw?agent={row['agent_id']}&path={_q(row['path'])}"}


def _q(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


def record_diff(before: dict, agent_id: str, thread_id: str, run_id: str) -> list[dict]:
    after = snapshot(agent_id)
    changed = [p for p, v in after.items() if before.get(p) != v]
    rows = []
    for p in changed[:200]:
        mtime, size = after[p]
        name = os.path.basename(p)
        path = _display(agent_id, p)
        mime = mimetypes.guess_type(name)[0] or ""
        old = db.one("SELECT id FROM thread_files WHERE thread_id=? AND abspath=?", thread_id, p)
        if old:
            db.update("thread_files", old["id"], size=size, mtime=mtime, run_id=run_id,
                      agent_id=agent_id)
            row = db.one("SELECT * FROM thread_files WHERE id=?", old["id"])
        else:
            row = db.insert("thread_files", id=new_id("tf_"), thread_id=thread_id,
                            agent_id=agent_id, run_id=run_id, abspath=p, path=path, name=name,
                            kind=fileparse.kind_of(name, mime), mime=mime, size=size,
                            mtime=mtime)
        rows.append(row)
        bus.emit("thread_file", file=_card(row), thread_id=thread_id)
    return rows


PATH_RE = re.compile(r"(?:~/|\./)?(?:[\w\-.()一-鿿]+/)*[\w\-.()一-鿿]+\.[A-Za-z0-9]{1,8}")


def mentioned_files(text: str, agent_id: str, thread_id: str) -> list[dict]:
    """Files a reply refers to by path (e.g. `shared/budapest_notes.md`) → cards."""
    seen, out = set(), []
    for m in PATH_RE.findall(text or ""):
        cand = m.strip("`'\".,;:()")
        if cand in seen or "://" in cand or cand.startswith("www."):
            continue
        seen.add(cand)
        try:
            p = computer_for(agent_id).resolve(cand)
        except (PermissionError, ValueError):
            continue
        if not p.is_file():
            continue
        p = Path(os.path.realpath(p))  # home/shared/x → the one real path snapshots record
        row = db.one("SELECT * FROM thread_files WHERE thread_id=? AND abspath=?", thread_id,
                     str(p))
        if not row:
            st = p.stat()
            mime = mimetypes.guess_type(p.name)[0] or ""
            row = db.insert("thread_files", id=new_id("tf_"), thread_id=thread_id,
                            agent_id=agent_id, run_id="", abspath=str(p),
                            path=_display(agent_id, str(p)), name=p.name,
                            kind=fileparse.kind_of(p.name, mime), mime=mime, size=st.st_size,
                            mtime=st.st_mtime)
        out.append(_card(row))
        if len(out) >= 8:
            break
    return out


# ---------------------------------------------------------------- API
@router.get("/api/threads/{tid}/files")
async def thread_files(tid: str):
    rows = db.q("SELECT * FROM thread_files WHERE thread_id=? ORDER BY mtime DESC", tid)
    return {"files": [_card(r) | {"created": r["mtime"]} for r in rows
                      if Path(r["abspath"]).exists()]}


def _resolve(agent: str, path: str) -> Path:
    try:
        p = computer_for(agent).resolve(path)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    if not p.is_file():
        raise HTTPException(404, f"no such file: {path}")
    return p


@router.get("/api/view/raw")
async def view_raw(agent: str, path: str, download: int = 0):
    p = _resolve(agent, path)
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    if mime in ("text/html", "image/svg+xml", "application/xhtml+xml") and not download:
        mime = "text/plain; charset=utf-8"  # never execute on our origin; the viewer sandboxes it
    headers = {"X-Content-Type-Options": "nosniff"}
    if mime.startswith("text/"):
        headers["Content-Security-Policy"] = "sandbox"
    if download:
        return FileResponse(p, filename=p.name, media_type=mime, headers=headers)
    return FileResponse(p, media_type=mime, headers=headers)


_CACHE_LOCK = asyncio.Lock()


async def cached_text(p: Path) -> str:
    st = p.stat()
    key = hashlib.sha1(f"{p}|{st.st_mtime}|{st.st_size}".encode()).hexdigest()
    cache = settings.DATA_DIR / "cache" / "parsed" / f"{key}.md"
    if cache.exists():
        return cache.read_text(errors="replace")
    res = await asyncio.to_thread(fileparse.parse, p)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(res["text"])
    return res["text"]


@router.get("/api/view/text")
async def view_text(agent: str, path: str):
    return PlainTextResponse(await cached_text(_resolve(agent, path)))


@router.get("/api/deliverables/{did}/text")
async def deliverable_text(did: str):
    row = db.one("SELECT * FROM deliverables WHERE id=?", did)
    if not row or not Path(row["path"]).is_file():
        raise HTTPException(404)
    return PlainTextResponse(await cached_text(Path(row["path"])))
