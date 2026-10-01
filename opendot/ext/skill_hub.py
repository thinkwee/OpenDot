"""Skill Hub — search the open Agent-Skills ecosystem (SKILL.md) and install from it.

Sources (both free, both index public GitHub repos; we never host skills ourselves):
  1. skills.sh — Vercel's open directory, ranked by real install counts. We call the
     same public search endpoint its MIT-licensed CLI (`npx skills find`) uses.
  2. SkillsMP — the largest crawl of public SKILL.md files, with descriptions and
     GitHub stars. Anonymous: 50 requests/day; set SKILLSMP_API_KEY (free) for 500.

Installing goes through ``skills.install_skill`` → sparse git checkout of just that
skill's folder → the same static safety scan as any other URL install; skills with
findings stay disabled until the human confirms (see the safety-scan notes in skills.py).
Agents can search (``find_skills``) and propose an install (``install_skill``), which
always needs the human's OK.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time

import httpx
from fastapi import APIRouter, HTTPException

from ..db import db
from ..tools import fn, register_tool

log = logging.getLogger("opendot.ext.skill_hub")
router = APIRouter(prefix="/api/skillhub")

SKILLS_SH = "https://skills.sh/api/search"
SKILLSMP = "https://skillsmp.com/api/v1/skills/search"
UA = {"User-Agent": "OpenDot/0.2 (+https://github.com/thinkwee/OpenDot)"}
_cache: dict[str, tuple[float, list]] = {}
TTL = 600


async def _skills_sh(c: httpx.AsyncClient, q: str, limit: int) -> list[dict]:
    r = await c.get(SKILLS_SH, params={"q": q, "limit": limit})
    r.raise_for_status()
    out = []
    for s in r.json().get("skills", []):
        src, sid = s.get("source", ""), s.get("skillId") or s.get("name")
        if "/" not in src:
            continue
        out.append({"name": s.get("name") or sid, "description": "", "source": src,
                    "install": f"github:{src}#{sid}", "installs": s.get("installs") or 0,
                    "stars": None, "url": f"https://skills.sh/{src}/{sid}", "from": "skills.sh",
                    "raw": [f"https://raw.githubusercontent.com/{src}/HEAD/{p}" for p in (
                        f"skills/{sid}/SKILL.md", f"{sid}/SKILL.md", f".claude/skills/{sid}/SKILL.md",
                        "SKILL.md")]})
    return out


async def _skillsmp(c: httpx.AsyncClient, q: str, limit: int) -> list[dict]:
    headers = dict(UA)
    if key := os.environ.get("SKILLSMP_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    r = await c.get(SKILLSMP, params={"q": q, "limit": min(limit, 50), "sortBy": "stars"},
                    headers=headers)
    r.raise_for_status()
    out = []
    for s in (r.json().get("data") or {}).get("skills", []):
        gh = s.get("githubUrl") or ""
        m = re.match(r"https://github\.com/([\w.-]+/[\w.-]+)/tree/([^/]+)/(.+)", gh)
        if not m:
            continue
        src, ref, path = m[1], m[2], m[3].rstrip("/")
        out.append({"name": s.get("name"), "description": s.get("description") or "",
                    "source": src, "install": gh, "installs": None, "stars": s.get("stars"),
                    "url": s.get("skillUrl") or gh, "from": "skillsmp",
                    "raw": [f"https://raw.githubusercontent.com/{src}/{ref}/{path}/SKILL.md"]})
    return out


async def search(q: str, limit: int = 20) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    key = f"{q.lower()}|{limit}"
    if key in _cache and time.time() - _cache[key][0] < TTL:
        return _cache[key][1]
    async with httpx.AsyncClient(timeout=15, headers=UA, follow_redirects=True) as c:
        res = await asyncio.gather(_skills_sh(c, q, limit), _skillsmp(c, q, limit),
                                   return_exceptions=True)
    merged: dict[tuple, dict] = {}
    for src_res in res:
        if isinstance(src_res, Exception):
            log.info("skill search source failed: %s", src_res)
            continue
        for s in src_res:
            k = (s["source"].lower(), (s["name"] or "").lower())
            if k in merged:  # same skill from both: keep installs + description
                m = merged[k]
                m["description"] = m["description"] or s["description"]
                m["stars"] = m["stars"] or s["stars"]
                m["installs"] = m["installs"] or s["installs"]
                m["raw"] = m["raw"] + [r for r in s["raw"] if r not in m["raw"]]
            else:
                merged[k] = s
    installed = {r["id"].lower() for r in db.q("SELECT id FROM skills")}
    items = list(merged.values())
    await _fill_descriptions([i for i in items if not i["description"]][:24])
    for s in items:
        s["installed"] = (s["name"] or "").lower() in installed
        s["official"] = s["source"].split("/")[0].lower() in {"anthropics", "openai", "vercel-labs",
                                                              "microsoft", "google"}
    # rank: relevance first (fuzzy search returns popular-but-unrelated hits), then
    # popularity (real installs > stars), with a nudge for well-known publishers
    terms = [t for t in re.split(r"[\s,/_-]+", q.lower()) if t]

    def has(t: str, text: str) -> bool:
        if not t.isascii():  # CJK etc.: no word boundaries to rely on
            return t in text
        words = set(re.split(r"[^a-z0-9]+", text))
        return t in words or t + "s" in words or t + "es" in words

    def relevance(s: dict) -> int:
        name = (s["name"] or "").lower()
        desc = (s["description"] or "").lower()
        if all(has(t, name) for t in terms):
            return 3
        if any(has(t, name) for t in terms) or all(has(t, desc) for t in terms):
            return 2
        return 1 if any(has(t, desc) or has(t, s["source"].lower()) for t in terms) else 0

    import math
    for s in items:
        s["relevance"] = relevance(s)
    if any(s["relevance"] > 0 for s in items):
        items = [s for s in items if s["relevance"] > 0]
    items.sort(key=lambda s: (s["relevance"], math.log1p((s["installs"] or 0) * 3 +
                                                         (s["stars"] or 0) / 50) +
                              (1.5 if s["official"] else 0)), reverse=True)
    items = items[:limit]
    _cache[key] = (time.time(), items)
    return items


_desc_cache: dict[str, str] = {}


async def _fill_descriptions(items: list[dict]) -> None:
    """skills.sh results have no description — read it from each SKILL.md's frontmatter."""
    async def one(c: httpx.AsyncClient, it: dict) -> None:
        key = it["install"]
        if key not in _desc_cache:
            _desc_cache[key] = ""
            for url in it["raw"][:3]:
                try:
                    r = await c.get(url)
                except httpx.HTTPError:
                    continue
                if r.status_code == 200:
                    m = re.search(r"^description:\s*[\"']?(.+?)[\"']?\s*$", r.text[:3000], re.M)
                    _desc_cache[key] = (m[1] if m else "")[:300]
                    break
        it["description"] = _desc_cache[key]
    async with httpx.AsyncClient(timeout=8, headers=UA, follow_redirects=True) as c:
        await asyncio.gather(*(one(c, i) for i in items), return_exceptions=True)


async def preview(item: dict) -> str:
    async with httpx.AsyncClient(timeout=15, headers=UA, follow_redirects=True) as c:
        for url in item.get("raw") or []:
            try:
                r = await c.get(url)
                if r.status_code == 200 and "---" in r.text[:400]:
                    return r.text[:60_000]
            except httpx.HTTPError:
                continue
    return ""


# ---------------------------------------------------------------- API
@router.get("/search")
async def api_search(q: str, limit: int = 20):
    try:
        return {"results": await search(q, max(1, min(limit, 50)))}
    except Exception as e:
        raise HTTPException(502, f"skill search failed: {e}") from e


@router.post("/preview")
async def api_preview(item: dict):
    text = await preview(item)
    if not text:
        raise HTTPException(404, "couldn't fetch this SKILL.md — open it on GitHub instead")
    return {"text": text}


# ---------------------------------------------------------------- agent tools
async def _find_skills(ctx, query: str) -> dict:
    res = await search(query, 8)
    return {"results": [{k: s[k] for k in ("name", "description", "source", "install",
                                          "installs", "stars", "installed")} for s in res],
            "note": "To add one, call install_skill with its `install` value — the human will "
                    "be asked to approve. Prefer well-installed skills from known publishers."}


async def _install_skill(ctx, install: str) -> dict:
    from .skills import install_skill
    try:
        r = await install_skill({"url": install})
    except HTTPException as e:
        return {"error": e.detail}
    out = []
    for i in r.get("installed", []):
        if i.get("findings"):
            out.append(f"{i['name']}: installed but DISABLED — the safety scan flagged "
                       f"{len(i['findings'])} thing(s); the human must review it in Settings → Skills")
        elif "error" in i:
            out.append(f"{i['name']}: {i['error']}")
        else:
            out.append(f"{i['name']}: installed — read_skill('{i['name']}') to use it")
    return {"result": out}


register_tool("find_skills", fn(
    "find_skills", "Search the open skill ecosystem (skills.sh, SkillsMP — thousands of "
    "community SKILL.md skills) for a capability you're missing, e.g. 'pdf forms', "
    "'notion', 'excel charts'.", {"query": {"type": "string"}}, ["query"]),
    _find_skills, policy="allow")
register_tool("install_skill", fn(
    "install_skill", "Install a skill found with find_skills (pass its `install` value). "
    "Needs the human's approval; the skill is safety-scanned first.",
    {"install": {"type": "string"}}, ["install"]),
    _install_skill, policy="ask")
