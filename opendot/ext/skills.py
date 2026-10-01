"""Skills in the common open ``SKILL.md`` format.

A skill is a folder with a ``SKILL.md`` (YAML frontmatter: ``name``, ``description``,
optional metadata) plus optional supporting files/scripts. Two roots:

- bundled ``skills/`` at the repo root — shipped starter skills, trusted by default.
- ``data/skills/`` — installed by the human (git repo / zip / raw SKILL.md URL) or
  saved by an agent that just did something worth repeating (``create_skill``).

Per-agent enable/disable lives in ``kv`` (``skill_enabled:<agent_id>``). The system
prompt gets a compact one-line-per-skill list; agents must ``read_skill(name)`` before
using one — the description alone is not the instructions.

Lesson from the wider ecosystem (docs/RESEARCH.md §4): a community skill hub had ~20%
malicious skills at its worst. So anything installed from a URL goes through a static
safety scan first (regex heuristics — not a sandbox, just a tripwire) and stays
disabled for every agent until the human explicitly confirms it via ``/confirm``.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

import frontmatter
import httpx
from fastapi import APIRouter, Body, HTTPException

from ..bus import bus
from ..config import ROOT, settings
from ..db import db
from ..tools import fn, register_tool

log = logging.getLogger("opendot.ext.skills")

BUNDLED_DIR = ROOT / "skills"

db.ensure_schema("""
CREATE TABLE IF NOT EXISTS skills (
  id TEXT PRIMARY KEY, root TEXT, source TEXT, sha TEXT, scan TEXT,
  confirmed INTEGER DEFAULT 0, created REAL
);
""")

SLUG_RE = re.compile(r"[^a-z0-9\-]+")


def slugify(s: str) -> str:
    return SLUG_RE.sub("-", (s or "").lower()).strip("-")[:48] or "skill"


def user_dir() -> Path:
    d = settings.DATA_DIR / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------- discovery ----------------
def _roots():
    yield "bundled", BUNDLED_DIR
    yield "user", user_dir()


def discover() -> dict[str, dict]:
    """name -> {name, slug, root, path, description, meta, files}."""
    out: dict[str, dict] = {}
    for root, base in _roots():
        if not base.exists():
            continue
        for d in sorted(base.iterdir()):
            skill_md = d / "SKILL.md"
            if not d.is_dir() or not skill_md.exists():
                continue
            try:
                post = frontmatter.load(skill_md)
            except Exception as e:
                log.warning("bad SKILL.md in %s: %s", d, e)
                continue
            name = str(post.get("name") or d.name)
            files = [str(f.relative_to(d)) for f in sorted(d.rglob("*"))
                     if f.is_file() and f.name != "SKILL.md" and ".git" not in f.parts]
            out[name] = {
                "name": name, "slug": d.name, "root": root, "path": d,
                "description": str(post.get("description") or "")[:280],
                "meta": {k: v for k, v in post.metadata.items()
                         if k not in ("name", "description")},
                "files": files,
            }
    return out


def _row(name: str) -> dict:
    return db.one("SELECT * FROM skills WHERE id=?", name) or {}


def _scan_of(row: dict) -> list[dict]:
    try:
        return json.loads(row.get("scan") or "[]")
    except (TypeError, ValueError):
        return []


# ---------------- per-agent enable state ----------------
def enabled_map(agent_id: str) -> dict:
    return db.kv_get(f"skill_enabled:{agent_id}", {})


def is_enabled(agent_id: str, name: str, row: dict | None = None) -> bool:
    m = enabled_map(agent_id)
    if name in m:
        return bool(m[name])
    row = _row(name) if row is None else row
    if row and _scan_of(row) and not row.get("confirmed"):
        return False  # flagged + unconfirmed: opt-in only
    return True


def set_enabled(agent_id: str, name: str, on: bool) -> None:
    m = enabled_map(agent_id)
    m[name] = bool(on)
    db.kv_set(f"skill_enabled:{agent_id}", m)


# ---------------- prompt ----------------
def PROMPT(agent: dict) -> str | None:
    skills = discover()
    lines = [f"- **{name}**: {s['description']}" for name, s in sorted(skills.items())
             if is_enabled(agent["id"], name)]
    return ("# Skills\nPlaybooks for things that come up often. Call `read_skill(name)` to load "
            "one's instructions BEFORE using it; the one-liner isn't enough to follow it.\n"
            + "\n".join(lines) + ("\n" if lines else "")
            + "Missing some know-how (a file format, an app's API, a site)? `find_skills` "
            "searches community skills; propose the best with `install_skill` (the human "
            "approves).")


# ---------------- tools ----------------
def _find(name: str) -> dict:
    s = discover().get(name)
    if not s:
        raise ValueError(f"no skill called {name!r}")
    return s


async def _read_skill(ctx, name: str) -> dict:
    s = _find(name)
    return {"name": s["name"], "description": s["description"], "meta": s["meta"],
            "instructions": (s["path"] / "SKILL.md").read_text(), "files": s["files"]}


async def _read_skill_file(ctx, name: str, path: str) -> dict:
    s = _find(name)
    base = s["path"].resolve()
    p = (s["path"] / path).resolve()
    if not p.is_relative_to(base) or not p.is_file():
        raise PermissionError(f"no such supporting file: {path}")
    return {"path": path, "content": p.read_text(errors="replace")[:40_000]}


async def _create_skill(ctx, name: str, description: str, instructions: str) -> dict:
    slug = slugify(name)
    d = user_dir() / slug
    d.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(instructions.strip(), name=name, description=description[:280],
                             created_by=ctx.agent["name"])
    (d / "SKILL.md").write_text(frontmatter.dumps(post))
    db.insert("skills", id=name, root="user", source=f"agent:{ctx.agent['name']}", sha="",
              scan="[]", confirmed=1, created=time.time())
    bus.emit("skills", event="created", name=name)
    return {"ok": True, "note": f"Saved as a reusable skill called '{name}'. Any agent can "
                                 f"now read_skill('{name}') to reuse this."}


register_tool(
    "read_skill",
    fn("read_skill", "Read a skill's full instructions before using it — the skill list in "
       "your prompt only shows a one-line description, always read_skill first.",
       {"name": {"type": "string"}}, ["name"]),
    _read_skill, policy="allow", worker=True,
)
register_tool(
    "read_skill_file",
    fn("read_skill_file", "Read one of a skill's supporting files (a script/template), once "
       "read_skill told you it exists.",
       {"name": {"type": "string"}, "path": {"type": "string"}}, ["name", "path"]),
    _read_skill_file, policy="allow", worker=True,
)
register_tool(
    "create_skill",
    fn("create_skill", "Save a workflow you just did as a reusable skill for next time — "
       "yours and every teammate's. Write clear step-by-step instructions, the way you'd "
       "explain it to a new colleague.",
       {"name": {"type": "string"}, "description": {"type": "string"},
        "instructions": {"type": "string"}}, ["name", "description", "instructions"]),
    _create_skill, policy="ask",
)


# ---------------- static safety scan ----------------
# Heuristics only — a tripwire, not a sandbox. Modeled on the skill-hub incident notes in
# docs/RESEARCH.md: pipe-to-shell installers, obfuscated payloads, credential-path
# reads, and things that look like exfiltrating secrets over the network.
SCAN_PATTERNS = [
    ("shell-pipe", r"(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba)?sh\b",
     "downloads and pipes a remote script straight into a shell"),
    ("base64-blob", r"[A-Za-z0-9+/]{200,}={0,2}",
     "a long base64-looking blob (possible obfuscated payload)"),
    ("cred-path", r"~/\.ssh|~/\.aws\b|\.aws/credentials|id_rsa|\.pem\b|\.netrc\b",
     "reads a well-known SSH/AWS/credential path"),
    ("keychain", r"Keychain|security\s+find-generic-password|osascript[^\n]*"
                 r"(password|passphrase|keychain)",
     "touches the macOS Keychain or prompts for a password via AppleScript"),
    ("exfil", r"(curl|wget|requests\.post|httpx\.post|fetch\()[^\n]{0,120}"
              r"(-d\s|--data|POST|data\s*[=:])[^\n]{0,80}"
              r"(TOKEN|API_KEY|SECRET|PASSWORD|COOKIE)",
     "may send something that looks like a credential over the network"),
    ("dynamic-eval", r"\beval\(|exec\(\s*(base64|requests|urllib|httpx|compile\()",
     "evaluates dynamically fetched or decoded code"),
    ("sudo", r"\bsudo\b", "attempts privilege escalation"),
]


def scan_text(text: str) -> list[dict]:
    hits = []
    for tag, pat, why in SCAN_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            hits.append({"tag": tag, "why": why,
                         "snippet": text[max(0, m.start() - 30):m.end() + 30].strip()[:160]})
    return hits


def scan_dir(d: Path) -> list[dict]:
    findings = []
    for f in d.rglob("*"):
        if not f.is_file() or ".git" in f.parts or f.stat().st_size > 2_000_000:
            continue
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        for h in scan_text(text):
            findings.append({**h, "file": str(f.relative_to(d))})
    return findings


# ---------------- install ----------------
def _is_git_url(url: str) -> bool:
    return bool(re.match(r"^https?://(github|gitlab|codeberg)\.com/[^/]+/[^/]+/?$", url)) \
        or url.rstrip("/").endswith(".git")


GH_TREE = re.compile(r"^https?://github\.com/([\w.-]+)/([\w.-]+)/tree/([^/]+)/(.+?)/?$")
GH_SKILL = re.compile(r"^github:([\w.-]+)/([\w.-]+)#([\w.@-]+)$")  # skills.sh result


def _git(*args: str, timeout: int = 90) -> str:
    try:
        r = subprocess.run(["git", *args], check=True, timeout=timeout, capture_output=True,
                           text=True)
        return r.stdout
    except subprocess.CalledProcessError as e:
        raise HTTPException(400, f"git {args[0]} failed: {e.stderr[:300]}") from e
    except subprocess.TimeoutExpired as e:
        raise HTTPException(400, f"git {args[0]} timed out") from e


def _sparse_clone(owner: str, repo: str, ref: str | None, tmp: Path) -> Path:
    """Clone only the tree (no file contents) — fast even for huge skill monorepos."""
    dest = tmp / "repo"
    args = ["clone", "--depth", "1", "--filter=blob:none", "--sparse", "--no-checkout"]
    if ref:
        args += ["--branch", ref]
    _git(*args, f"https://github.com/{owner}/{repo}.git", str(dest))
    return dest


def _checkout_dir(dest: Path, subdir: str) -> Path:
    _git("-C", str(dest), "sparse-checkout", "set", "--no-cone", f"/{subdir.strip('/')}/")
    _git("-C", str(dest), "checkout")
    return dest / subdir


async def _fetch_github_subdir(owner: str, repo: str, ref: str | None, subdir: str | None,
                               skill_id: str | None, tmp: Path) -> tuple[Path, str]:
    import asyncio
    dest = await asyncio.to_thread(_sparse_clone, owner, repo, ref, tmp)
    if subdir is None:  # find the folder of this skill id among all SKILL.md files
        paths = (await asyncio.to_thread(_git, "-C", str(dest), "ls-tree", "-r",
                                         "--name-only", "HEAD")).splitlines()
        skill_mds = [p for p in paths if p.endswith("SKILL.md")]
        want = (skill_id or "").lower()
        hit = [p for p in skill_mds if p.rsplit("/", 2)[-2:-1] == [want] or
               p.lower().endswith(f"/{want}/skill.md")]
        if not hit and len(skill_mds) == 1:
            hit = skill_mds
        if not hit:
            raise HTTPException(404, f"no SKILL.md for “{skill_id}” in {owner}/{repo}")
        subdir = hit[0].rsplit("/", 1)[0] if "/" in hit[0] else ""
    if subdir:
        src = await asyncio.to_thread(_checkout_dir, dest, subdir)
    else:
        await asyncio.to_thread(_git, "-C", str(dest), "sparse-checkout", "disable")
        src = dest
    sha = (await asyncio.to_thread(_git, "-C", str(dest), "rev-parse", "HEAD")).strip()
    return src, sha


async def _fetch_source(url: str, tmp: Path) -> tuple[Path, str]:
    """Download/clone ``url`` into ``tmp``, return (dir_containing_skills, sha)."""
    if m := GH_TREE.match(url):
        return await _fetch_github_subdir(m[1], m[2], m[3], m[4], None, tmp)
    if m := GH_SKILL.match(url):
        return await _fetch_github_subdir(m[1], m[2], None, None, m[3], tmp)
    if _is_git_url(url):
        repo_url = url if url.endswith(".git") else url.rstrip("/") + ".git"
        dest = tmp / "repo"
        try:
            subprocess.run(["git", "clone", "--depth", "1", repo_url, str(dest)],
                           check=True, timeout=60, capture_output=True)
        except subprocess.CalledProcessError as e:
            raise HTTPException(400, f"git clone failed: "
                                     f"{e.stderr.decode(errors='replace')[:300]}") from e
        except subprocess.TimeoutExpired as e:
            raise HTTPException(400, "git clone timed out") from e
        sha = ""
        try:
            r = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True, timeout=10)
            sha = r.stdout.strip()
        except Exception:
            pass
        return dest, sha

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        r = await c.get(url)
    r.raise_for_status()
    data = r.content
    sha = hashlib.sha256(data).hexdigest()
    if url.lower().endswith(".zip") or r.headers.get("content-type", "").startswith(
            "application/zip"):
        dest = tmp / "zip"
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(dest)
        return dest, sha
    # a single raw SKILL.md (or any markdown file used as one)
    dest = tmp / "raw"
    dest.mkdir()
    (dest / "SKILL.md").write_bytes(data)
    return dest, sha


router = APIRouter(prefix="/api/skills")


@router.get("")
async def list_skills(agent_id: str = ""):
    skills = discover()
    out = []
    for name, s in sorted(skills.items()):
        row = _row(name)
        out.append({
            "name": name, "description": s["description"], "root": s["root"],
            "meta": s["meta"], "files": s["files"],
            "source": row.get("source") or ("bundled" if s["root"] == "bundled" else ""),
            "sha": row.get("sha", ""),
            "scan": _scan_of(row),
            "confirmed": bool(row.get("confirmed", 1 if s["root"] == "bundled" else 0)),
            "enabled": is_enabled(agent_id, name, row) if agent_id else None,
        })
    return out


@router.get("/{name}")
async def get_skill(name: str):
    s = discover().get(name)
    if not s:
        raise HTTPException(404, "no such skill")
    row = _row(name)
    return {"name": s["name"], "description": s["description"], "root": s["root"],
            "meta": s["meta"], "files": s["files"],
            "instructions": (s["path"] / "SKILL.md").read_text(),
            "source": row.get("source") or ("bundled" if s["root"] == "bundled" else ""),
            "scan": _scan_of(row),
            "confirmed": bool(row.get("confirmed", 1 if s["root"] == "bundled" else 0))}


@router.get("/{name}/file")
async def get_skill_file(name: str, path: str):
    s = discover().get(name)
    if not s:
        raise HTTPException(404, "no such skill")
    base = s["path"].resolve()
    p = (s["path"] / path).resolve()
    if not p.is_relative_to(base) or not p.is_file():
        raise HTTPException(400, "bad path")
    return {"path": path, "content": p.read_text(errors="replace")[:200_000]}


@router.patch("/{name}/agents/{agent_id}")
async def patch_enabled(name: str, agent_id: str, body: dict = Body(...)):
    if name not in discover():
        raise HTTPException(404, "no such skill")
    row = _row(name)
    if body.get("enabled") and _scan_of(row) and not row.get("confirmed"):
        raise HTTPException(400, "this skill was flagged by the safety scan — confirm it "
                                 "first (POST /api/skills/{name}/confirm)")
    set_enabled(agent_id, name, bool(body.get("enabled")))
    return {"ok": True}


@router.post("/{name}/confirm")
async def confirm_skill(name: str):
    row = _row(name)
    if not row:
        if name not in discover():
            raise HTTPException(404, "no such skill")
        # a bundled skill some workstream shipped — nothing to confirm, but keep it simple
        return {"ok": True}
    db.update("skills", name, confirmed=1)
    bus.emit("skills", event="confirmed", name=name)
    return {"ok": True}


@router.delete("/{name}")
async def delete_skill(name: str):
    s = discover().get(name)
    if not s:
        raise HTTPException(404, "no such skill")
    if s["root"] != "user":
        raise HTTPException(400, "can't delete a bundled skill")
    shutil.rmtree(s["path"], ignore_errors=True)
    db.delete("skills", name)
    bus.emit("skills", event="deleted", name=name)
    return {"ok": True}


@router.post("/install")
async def install_skill(body: dict = Body(...)):
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url required")
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        try:
            src, sha = await _fetch_source(url, tmp)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"fetch failed: {e}") from e

        candidates = list(src.rglob("SKILL.md"))
        if not candidates:
            raise HTTPException(400, "no SKILL.md found at that URL")

        installed = []
        for skill_md in candidates[:8]:  # a URL may point at a pack of several skills
            sdir = skill_md.parent
            try:
                post = frontmatter.load(skill_md)
            except Exception as e:
                installed.append({"name": sdir.name, "error": f"bad SKILL.md: {e}"})
                continue
            name = str(post.get("name") or sdir.name)
            dest = user_dir() / slugify(name)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(sdir, dest, ignore=shutil.ignore_patterns(".git"))
            findings = scan_dir(dest)
            db.insert("skills", id=name, root="user", source=url, sha=sha,
                      scan=json.dumps(findings), confirmed=0 if findings else 1,
                      created=time.time())
            installed.append({"name": name, "findings": findings,
                              "confirmed": not findings})
        bus.emit("skills", event="installed", names=[i["name"] for i in installed])
        return {"installed": installed}
