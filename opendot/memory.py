"""Plain-markdown memory — editable, diffable, portable.

data/USER.md                       who the human is (shared by every agent)
data/agents/<id>/SOUL.md           the agent's identity & voice
data/agents/<id>/MEMORY.md         durable facts & decisions the agent chose to keep
data/agents/<id>/HEARTBEAT.md      what to check when it wakes up on its own
data/agents/<id>/journal/DATE.md   daily log of what it did
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import settings

TEMPLATES = Path(__file__).parent / "templates"


def now_local() -> dt.datetime:
    return dt.datetime.now(ZoneInfo(settings.TIMEZONE))


def agent_dir(agent_id: str) -> Path:
    d = settings.DATA_DIR / "agents" / agent_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_file() -> Path:
    p = settings.DATA_DIR / "USER.md"
    if not p.exists():
        p.write_text((TEMPLATES / "USER.md").read_text())
    return p


FILES = {"SOUL.md", "MEMORY.md", "HEARTBEAT.md"}


def mem_path(agent_id: str, name: str) -> Path:
    if name == "USER.md":
        return user_file()
    if name not in FILES:
        raise ValueError(f"unknown memory file {name}")
    p = agent_dir(agent_id) / name
    if not p.exists():
        p.write_text((TEMPLATES / name).read_text())
    return p


def read(agent_id: str, name: str) -> str:
    return mem_path(agent_id, name).read_text()


def write(agent_id: str, name: str, content: str) -> None:
    mem_path(agent_id, name).write_text(content)


def remember(agent_id: str, fact: str, about_user: bool = False) -> str:
    p = user_file() if about_user else mem_path(agent_id, "MEMORY.md")
    stamp = now_local().strftime("%Y-%m-%d")
    with open(p, "a") as f:
        f.write(f"\n- {fact.strip()} _(noted {stamp})_")
    return p.name


def forget(agent_id: str, pattern: str) -> int:
    removed = 0
    for p in (mem_path(agent_id, "MEMORY.md"), user_file()):
        lines = p.read_text().splitlines()
        keep = [ln for ln in lines if not (ln.startswith("- ") and re.search(re.escape(pattern),
                                                                            ln, re.I))]
        removed += len(lines) - len(keep)
        p.write_text("\n".join(keep) + "\n")
    return removed


def journal(agent_id: str, line: str) -> None:
    d = agent_dir(agent_id) / "journal"
    d.mkdir(exist_ok=True)
    t = now_local()
    with open(d / f"{t:%Y-%m-%d}.md", "a") as f:
        f.write(f"- {t:%H:%M} {line.strip()}\n")


def recent_journal(agent_id: str, days: int = 2, max_chars: int = 3000) -> str:
    d = agent_dir(agent_id) / "journal"
    if not d.exists():
        return ""
    files = sorted(d.glob("*.md"))[-days:]
    text = "\n".join(f"### {f.stem}\n{f.read_text()}" for f in files)
    return text[-max_chars:]


def note_refusal(agent_id: str, what: str) -> bool:
    """Remember a "Not now" so the agent asks less for things the human turns down.
    One line per thing per day; returns False if it was already noted today."""
    what = " ".join((what or "").split())[:120]
    if not what:
        return False
    p = mem_path(agent_id.split("-w")[0], "MEMORY.md")
    line = f"- Human said not now to {what} on {now_local():%Y-%m-%d}"
    if line in p.read_text():
        return False
    with open(p, "a") as f:
        f.write(f"\n{line}")
    return True
