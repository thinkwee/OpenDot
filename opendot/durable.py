"""Durable work: a restart never loses what you asked for.

- Every agent run is a *job* row, saved before the run starts (a chat message and its
  jobs are saved together). On start-up, jobs a restart cut off are picked up once.
- Tool calls that act on the outside world leave an *effect* row before they run and
  keep their result after, so a resumed job never sends the same email twice.
- Approvals are rows too (gatekeeper.request_approval): a resumed job finds the one it
  already asked for, and a decision made while the server was down still counts.
"""

from __future__ import annotations

import hashlib
import json
import time

from .db import db, new_id

RESUME_WITHIN = 6 * 3600  # older interrupted jobs are given up, not resumed
MAX_ATTEMPTS = 2          # the first run + one resume

# tools that act on the outside world, besides every "ask"-by-default tool and MCP apps
EFFECT_TOOLS = {"send_email", "send_sms", "make_call", "phone_call", "create_event",
                "iphone_add_event", "iphone_add_reminder", "iphone_run_shortcut",
                "publish_page", "notify"}


def _kind(source: str) -> str:
    return "reply" if source == "chat" else "handoff" if source == "handoff" else "background"


def job_row(agent_id: str, thread_id: str, source: str = "chat", prompt: str | None = None,
            hops: int = 0, from_id: str | None = None) -> dict:
    """A queued job, ready for db.insert / db.insert_many."""
    return dict(id=new_id("jb_"), thread_id=thread_id, agent_id=agent_id, kind=_kind(source),
                source=source, prompt=prompt, hops=hops, from_id=from_id, status="queued",
                attempts=0)


def new_job(*args, **kw) -> dict:
    return db.insert("jobs", **job_row(*args, **kw))


def get_job(job_id: str) -> dict | None:
    return db.one("SELECT * FROM jobs WHERE id=?", job_id)


def job_started(job_id: str, run_id: str) -> int:
    """Mark the job running; returns which attempt this is (1 = first run)."""
    j = get_job(job_id) or {}
    attempt = (j.get("attempts") or 0) + 1
    db.update("jobs", job_id, status="running", run_id=run_id, attempts=attempt,
              started=time.time())
    return attempt


def job_end(job_id: str, status: str = "done") -> None:
    db.update("jobs", job_id, status=status, finished=time.time())


def recover() -> list[dict]:
    """Start-up: clear the ghosts a restart left behind and return the jobs to resume."""
    now = time.time()
    resume = []
    for j in db.q("SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY created"):
        if now - (j["created"] or 0) > RESUME_WITHIN or (j["attempts"] or 0) >= MAX_ATTEMPTS:
            job_end(j["id"], "failed")
        else:
            resume.append(j)
    keep_runs = {j["run_id"] for j in resume if j["run_id"]}
    keep_jobs = {j["id"] for j in resume}
    with db.lock:
        c = db.conn
        # nobody is working right after a start
        c.execute("UPDATE agents SET status='idle', status_text='' WHERE status!='idle'")
        # steps cut off mid-call; the resumed run is told about them
        c.execute("UPDATE steps SET status='error', finished=?, result=? WHERE status IN "
                  "('running','waiting')", (now, json.dumps({"error": "interrupted by a restart"})))
        # asks nobody will wait on any more
        c.execute("UPDATE approvals SET status='expired', decided=? WHERE status='pending' AND "
                  "(job_id IS NULL OR job_id NOT IN (%s))" % ",".join("?" * len(keep_jobs)),
                  (now, *keep_jobs))
        c.commit()
    # Todo cards: a resumed job carries on with its own card; any other live one stopped
    try:
        from .ext import todo
        for t in db.q("SELECT id, run_id, status FROM tasks WHERE status IN ('doing','waiting')"):
            if t["run_id"] in keep_runs:
                continue
            was_job = db.one("SELECT status FROM jobs WHERE run_id=?", t["run_id"])
            if t["status"] == "doing" or (was_job and was_job["status"] == "failed"):
                todo.close(t["run_id"], "stopped")  # 'waiting' on your reply stays
    except Exception:  # the todo extension is optional
        pass
    return resume


def resume_prompt(run_id: str | None, prompt: str | None) -> str:
    """What the resumed agent is told, incl. the steps it already finished."""
    lines = []
    for s in db.q("SELECT tool, args, result, status FROM steps WHERE run_id=? AND status IN "
                  "('done','error') ORDER BY created LIMIT 20", run_id or ""):
        args = json.dumps(s["args"], ensure_ascii=False, default=str)[:200]
        lines.append(f"- {s['tool']} {args} → {(s['result'] or '')[:300]}")
    note = ("[system] OpenDot restarted while you were on this. Pick up where you left off; "
            "don't redo what's already done.")
    if lines:
        note += " Steps finished before the restart:\n" + "\n".join(lines)
    return f"{prompt}\n\n{note}" if prompt else note


# ---------------- side effects ----------------
def args_hash(tool: str, args: dict) -> str:
    raw = json.dumps([tool, args], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def is_effect(tool: str) -> bool:
    from .gatekeeper import DEFAULT_POLICY
    return (tool in EFFECT_TOOLS or tool.startswith("mcp__") or "publish" in tool
            or tool.startswith("post_") or DEFAULT_POLICY.get(tool) == "ask")


def replay(job_id: str | None, attempt: int, tool: str, args: dict) -> dict | None:
    """The same call from an earlier attempt of this job, as a result to hand back —
    or None if it wasn't made before (then it runs normally)."""
    if not job_id or attempt <= 1 or not is_effect(tool):
        return None
    e = db.one("SELECT * FROM effects WHERE job_id=? AND tool=? AND args_hash=? AND attempt<? "
               "ORDER BY created LIMIT 1", job_id, tool, args_hash(tool, args), attempt)
    if not e:
        return None
    db.update("effects", e["id"], attempt=attempt)  # claimed: each earlier call replays once
    if e["finished"] is None:
        return {"error": "This may already have happened: the server restarted in the middle "
                         "of this exact call. Check (e.g. the sent folder, the calendar) "
                         "before trying again."}
    try:
        result = json.loads(e["result"] or "{}")
    except ValueError:
        result = {"result": e["result"]}
    if not isinstance(result, dict):
        result = {"result": result}
    return {**result, "note": "already done before the restart, not repeated"}


def effect_begin(job_id: str | None, attempt: int, thread_id: str, tool: str,
                 args: dict) -> str | None:
    """Write the effect row before the call runs. Returns its id (None = not tracked)."""
    if not job_id or not is_effect(tool):
        return None
    return db.insert("effects", id=new_id("ef_"), job_id=job_id, thread_id=thread_id, tool=tool,
                     args_hash=args_hash(tool, args), attempt=attempt)["id"]


def effect_done(effect_id: str | None, result: dict) -> None:
    if not effect_id:
        return
    if "error" in result:  # it didn't happen; a resumed run may try again
        db.delete("effects", effect_id)
    else:
        from .gatekeeper import redact
        db.update("effects", effect_id, finished=time.time(),
                  result=redact(json.dumps(result, ensure_ascii=False, default=str))[:16000])
