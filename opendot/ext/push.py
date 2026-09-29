"""APNs push for the iOS client.

Registers device tokens (``POST /api/push/register``) and sends alerts on:

* an approval going pending — category ``APPROVAL``, so the iOS notification
  can show Allow once / Always allow / Deny actions and resolve them without
  opening the app (``opendot.gatekeeper.decide`` via ``POST /api/approvals/{id}``);
* an agent's proactive inbox note/report (heartbeat, routine, etc.);
* an agent chat reply that's unlikely to be seen right away: anything from a
  non-chat source (automation/heartbeat/handoff), or a chat reply that lands
  more than ``DOT_PUSH_REPLY_DELAY`` seconds after the user's own message —
  a simple proxy for "the user probably isn't watching this chat anymore".
  A reply that lands quickly is *not* pushed, since the user is almost
  certainly still looking at the open chat (avoids double-notifying).

Token-based auth (JWT / ES256, PyJWT + cryptography) against a ``.p8`` key —
no per-message certificates, HTTP/2 via httpx. Every public entry point is a
no-op when APNs isn't configured (no key), so the server runs fine without
push set up (it's only used by a native iPhone client).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
import jwt
from fastapi import APIRouter, Body, HTTPException

from ..bus import bus
from ..db import db, new_id
from ..gatekeeper import vault_all

log = logging.getLogger("opendot.ext.push")

router = APIRouter(prefix="/api/push")

db.ensure_schema("""
CREATE TABLE IF NOT EXISTS push_tokens (
  id TEXT PRIMARY KEY, device_token TEXT UNIQUE, platform TEXT, bundle_id TEXT, created REAL
);
""")

REPLY_PUSH_DELAY = float(os.environ.get("DOT_PUSH_REPLY_DELAY", "25"))
# attention budget: at most this many *unprompted* pushes a day (things agents did on
# their own). Replies to you and anything that needs your answer are never held.
DAILY_NUDGES = int(os.environ.get("DOT_DAILY_NUDGES", "4"))
QUIET = (int(os.environ.get("DOT_QUIET_FROM", "22")), int(os.environ.get("DOT_QUIET_TO", "8")))
DIGEST_HOUR = 20


def _today() -> str:
    from ..memory import now_local
    return now_local().strftime("%Y-%m-%d")


def _quiet_now() -> bool:
    from ..memory import now_local
    h = now_local().hour
    start, end = QUIET
    return (h >= start or h < end) if start > end else (start <= h < end)


def spend_nudge(title: str) -> bool:
    """True if an unprompted push may go out now; otherwise it's held for the evening
    line (it's still in the chat and the Inbox)."""
    day = _today()
    used = db.kv_get(f"nudges:{day}", 0)
    if used < DAILY_NUDGES and not _quiet_now():
        db.kv_set(f"nudges:{day}", used + 1)
        return True
    held = db.kv_get(f"held:{day}", [])
    held.append(title[:80])
    db.kv_set(f"held:{day}", held[-50:])
    return False


def budget_status() -> dict:
    day = _today()
    return {"limit": DAILY_NUDGES, "used": db.kv_get(f"nudges:{day}", 0),
            "held": len(db.kv_get(f"held:{day}", [])), "quiet": list(QUIET)}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


class APNsSender:
    """Token-based (JWT/ES256) APNs sender over HTTP/2.

    Every method degrades to a no-op when ``.enabled`` is False, so callers
    never need to check configuration themselves.
    """

    def __init__(self) -> None:
        self._jwt: str | None = None
        self._jwt_at = 0.0
        self._client: httpx.AsyncClient | None = None

    @property
    def key_id(self) -> str:
        return _env("APNS_KEY_ID")

    @property
    def team_id(self) -> str:
        return _env("APNS_TEAM_ID")

    @property
    def topic(self) -> str:
        """The push topic — your app's bundle id (``APNS_TOPIC``)."""
        return _env("APNS_TOPIC")

    @property
    def sandbox(self) -> bool:
        return _env("APNS_SANDBOX", "false").strip().lower() in ("1", "true", "yes")

    def _key_pem(self) -> str | None:
        path = _env("APNS_KEY_PATH")
        if path and os.path.exists(path):
            try:
                return open(path, encoding="utf-8").read()
            except OSError:
                log.exception("push: cannot read APNS_KEY_PATH=%s", path)
        return vault_all().get("APNS_KEY") or None

    @property
    def enabled(self) -> bool:
        return bool(self.key_id and self.team_id and self.topic and self._key_pem())

    def _token(self) -> str | None:
        # Apple accepts a JWT for up to an hour; refresh well before that.
        if self._jwt and time.time() - self._jwt_at < 45 * 60:
            return self._jwt
        pem = self._key_pem()
        if not pem:
            return None
        try:
            token = jwt.encode(
                {"iss": self.team_id, "iat": int(time.time())},
                pem, algorithm="ES256", headers={"kid": self.key_id},
            )
        except Exception:
            log.exception("push: JWT generation failed (check APNS_KEY_* / the .p8 key)")
            return None
        self._jwt, self._jwt_at = token, time.time()
        return token

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            host = "api.sandbox.push.apple.com" if self.sandbox else "api.push.apple.com"
            self._client = httpx.AsyncClient(base_url=f"https://{host}", http2=True, timeout=10)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def build_payload(self, title: str, body: str, category: str,
                       user_info: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "aps": {
                "alert": {"title": title, "body": body},
                "sound": "default",
                "category": category,
                "mutable-content": 1,
            },
            **(user_info or {}),
        }

    async def send(self, title: str, body: str, *, category: str = "MESSAGE",
                    user_info: dict[str, Any] | None = None) -> int:
        """Push to every registered device. Returns how many accepted it."""
        return await self._deliver(self.build_payload(title, body, category, user_info),
                                   "alert", "10")

    async def send_silent(self, user_info: dict[str, Any] | None = None) -> int:
        """Background (content-available) push: wakes the iPhone app for ~30s so it can
        sync or run a queued command — no banner, no sound."""
        return await self._deliver({"aps": {"content-available": 1}, **(user_info or {})},
                                   "background", "5")

    async def _deliver(self, payload: dict[str, Any], push_type: str, priority: str) -> int:
        if not self.enabled:
            return 0
        token = self._token()
        if not token:
            return 0
        rows = db.q("SELECT device_token FROM push_tokens")
        if not rows:
            return 0
        headers = {
            "authorization": f"bearer {token}",
            "apns-topic": self.topic,
            "apns-push-type": push_type,
            "apns-priority": priority,
        }
        client = self._http()
        sent = 0
        for row in rows:
            dtoken = row["device_token"]
            try:
                r = await client.post(f"/3/device/{dtoken}", json=payload, headers=headers)
                if r.status_code == 200:
                    sent += 1
                elif r.status_code in (400, 410):
                    reason = ""
                    try:
                        reason = r.json().get("reason", "")
                    except Exception:
                        pass
                    if reason in ("BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"):
                        _revoke(dtoken)
                        log.info("push: revoked dead token (%s)", reason)
                else:
                    log.warning("push: APNs rejected (%s): %s", r.status_code, r.text[:200])
            except httpx.HTTPError:
                log.warning("push: send failed for a device", exc_info=True)
        return sent


def _revoke(device_token: str) -> None:
    with db.lock:
        db.conn.execute("DELETE FROM push_tokens WHERE device_token=?", (device_token,))
        db.conn.commit()


apns = APNsSender()


# ---------------- REST ----------------
@router.post("/register")
async def register_push(body: dict = Body(...)):
    device_token = (body.get("device_token") or "").strip()
    if not device_token:
        raise HTTPException(400, "device_token required")
    _revoke(device_token)  # keep the unique constraint simple: replace on re-register
    db.insert("push_tokens", id=new_id("pt_"), device_token=device_token,
              platform=body.get("platform") or "ios", bundle_id=body.get("bundle_id") or "")
    return {"ok": True, "enabled": apns.enabled}


# ---------------- bus listener ----------------
# thread_id -> the last time the *user* sent a message in it (epoch seconds).
# In-memory only; a server restart just means the first reply after restart
# uses "now" as the baseline (see _on_event), which favors not pushing over
# spamming a chat the user is actively in.
_last_user_msg: dict[str, float] = {}


async def _on_event(kind: str, data: dict) -> None:
    try:
        if kind == "approval":
            await _on_approval(data)
        elif kind == "inbox":
            await _on_inbox(data)
        elif kind == "message":
            await _on_message(data)
    except Exception:
        log.exception("push: failed handling bus event %r", kind)


async def _on_approval(data: dict) -> None:
    ap = data.get("approval") or {}
    if ap.get("status") != "pending":
        return
    agent = db.one("SELECT name FROM agents WHERE id=?", ap.get("agent_id")) or {"name": "An agent"}
    what = (ap.get("args") or {}).get("_what") or ap.get("tool")
    from .lang import tr
    await apns.send(agent["name"], tr(f"Can I {what}?", f"我可以{what}吗？"),
                     category="APPROVAL",
                     user_info={"approval_id": ap.get("id"), "thread_id": ap.get("thread_id")})


async def _on_inbox(data: dict) -> None:
    item = data.get("item") or {}
    if item.get("kind") != "note":
        return  # approvals get a richer push; a report's chat message is pushed already
    agent = db.one("SELECT name FROM agents WHERE id=?", item.get("agent_id")) or {"name": "Pip"}
    if not spend_nudge(item.get("title") or agent["name"]):
        return
    from .lang import tr
    await apns.send(item.get("title") or tr(f"{agent['name']} has an update",
                                            f"{agent['name']} 有新消息"),
                     (item.get("body") or "")[:200], category="MESSAGE",
                     user_info={"thread_id": item.get("thread_id")})


async def _on_message(data: dict) -> None:
    m = data.get("message") or {}
    tid = m.get("thread_id")
    if not tid:
        return
    now = m.get("created") or time.time()
    if m.get("role") == "user":
        _last_user_msg[tid] = now
        return
    if m.get("role") != "agent":
        return
    source = (m.get("meta") or {}).get("source") or "chat"
    baseline = _last_user_msg.get(tid, now)  # unknown baseline → treat as "just now" (don't spam)
    elapsed = now - baseline
    if source != "chat" or elapsed > REPLY_PUSH_DELAY:
        agent = db.one("SELECT name FROM agents WHERE id=?", m.get("agent_id")) or {"name": "Pip"}
        unprompted = source not in ("chat", "handoff", "hello")
        # a check-in's "Go ahead?" is a suggestion, not a question you owe an answer to
        checkin = source.startswith(("heartbeat", "research"))
        needs_you = bool((m.get("meta") or {}).get("choices")) and not checkin
        if unprompted and not needs_you and not spend_nudge(agent["name"]):
            return
        await apns.send(agent["name"], (m.get("content") or "")[:180], category="MESSAGE",
                         user_info={"thread_id": tid})


bus.listen(_on_event)


@router.get("/budget")
async def get_budget():
    return budget_status()


async def _evening_line() -> None:
    """Once a day: one quiet line about whatever the budget held back."""
    import asyncio

    from ..memory import now_local
    while True:
        try:
            day = _today()
            if now_local().hour >= DIGEST_HOUR and not db.kv_get(f"digest:{day}"):
                db.kv_set(f"digest:{day}", True)
                held = db.kv_get(f"held:{day}", [])
                if held:
                    n = len(held)
                    from .lang import tr
                    await apns.send("OpenDot", tr(f"{n} little update{'s' if n > 1 else ''} "
                                                  "from today are waiting — no rush.",
                                                  f"今天还有 {n} 条小消息在等你，不着急～"),
                                    category="MESSAGE",
                                    user_info={"open": "inbox"})
        except Exception:
            log.exception("evening line failed")
        await asyncio.sleep(300)


async def start() -> None:
    status = "configured" if apns.enabled else "not configured"
    log.info("push: APNs %s", status)
    await _evening_line()


async def stop() -> None:
    await apns.close()
