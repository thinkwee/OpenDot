"""WeChat ClawBot channel (Tencent's iLink bot API).

iLink is Tencent's "ClawBot" personal-account bot surface: a QR login binds a
persistent ``bot_token`` to your own WeChat account, after which the server
long-polls ``getupdates`` for inbound text and posts replies through
``sendmessage`` using a per-user ``context_token`` handed back by the last
inbound message (iLink does not allow cold-start broadcasts).

The QR login is exposed as two REST endpoints so the web UI can show the QR and
poll for confirmation.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import struct
import time
from typing import Any

import httpx

from ..db import db
from .base import Channel
from .base import router as _router

log = logging.getLogger("opendot.channels.wechat")

ILINK_BASE = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "1.0.2"
BOT_TYPE = 3  # personal-account ClawBot
_LONG_POLL_TIMEOUT = 50.0


def _uin_header() -> str:
    return base64.b64encode(struct.pack("I", random.randint(0, 0xFFFFFFFF))).decode("ascii")


def _headers(bot_token: str | None = None, extra: dict | None = None) -> dict:
    h = {"Content-Type": "application/json", "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _uin_header()}
    if bot_token:
        h["Authorization"] = f"Bearer {bot_token}"
    if extra:
        h.update(extra)
    return h


class WeChatChannel(Channel):
    def __init__(self) -> None:
        super().__init__(name="wechat", label="WeChat (ClawBot)", emoji="🟢",
                         setup=["Click “Get QR code” below",
                                "WeChat → Settings → Plugins → ClawBot → scan it",
                                "Confirm on your phone — the bot token is saved for you"],
                         fields=[{"key": "WECHAT_BOT_TOKEN", "label": "Bot token (from QR login)",
                                 "secret": True, "required": False}])
        self._client: httpx.AsyncClient | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._cursor = ""
        self._seen: set[str] = set()
        self._context: dict[str, str] = {}   # from_user_id -> context_token

    def configured(self) -> bool:
        return bool(self.config().get("WECHAT_BOT_TOKEN"))

    async def start(self) -> None:
        token = self.config().get("WECHAT_BOT_TOKEN")
        if not token:
            self.set_status("disabled")
            return
        self.set_status("connecting")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(_LONG_POLL_TIMEOUT))
        self._running = True
        self.set_status("connected")
        self._task = asyncio.create_task(self._poll_loop(token))

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        if self._client:
            await self._client.aclose()
            self._client = None
        self.set_status("disabled")

    async def _poll_loop(self, token: str) -> None:
        backoff = 1.0
        while self._running:
            try:
                updates = await self._fetch_updates(token)
                backoff = 1.0
                for upd in updates:
                    await self._process(upd)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("wechat poll error (retry %.0fs): %s", backoff, e)
                self.set_status("error", str(e))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                if self._running:
                    self.set_status("connected")

    async def _fetch_updates(self, token: str) -> list[dict[str, Any]]:
        body = {"get_updates_buf": self._cursor, "base_info": {"channel_version": CHANNEL_VERSION}}
        r = await self._client.post(f"{ILINK_BASE}/ilink/bot/getupdates", json=body,
                                    headers=_headers(token))
        if r.status_code == 401:
            raise RuntimeError("bot_token expired — re-run the QR login")
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        if data.get("ret") not in (None, 0):
            raise RuntimeError(f"ret={data.get('ret')} {data.get('err_msg', '')}")
        self._cursor = data.get("get_updates_buf") or self._cursor
        return data.get("msgs") or []

    async def _process(self, upd: dict[str, Any]) -> None:
        msg_id = str(upd.get("message_id") or upd.get("id") or "")
        if msg_id:
            if msg_id in self._seen:
                return
            self._seen.add(msg_id)
            if len(self._seen) > 500:
                self._seen = set(list(self._seen)[-250:])
        from_user = str(upd.get("from_user_id") or "")
        if not from_user:
            return
        ctx = str(upd.get("context_token") or "")
        if ctx:
            self._context[from_user] = ctx
        text = "\n".join(
            (it.get("text_item") or {}).get("text", "") for it in (upd.get("item_list") or [])
            if isinstance(it, dict) and it.get("text_item")).strip()
        if not text:
            return
        await self.handle_inbound(from_user, from_user, text, from_user)

    async def _send_text(self, chat_id: str, text: str, thread_id: str | None = None) -> None:
        token = self.config().get("WECHAT_BOT_TOKEN")
        ctx = self._context.get(chat_id)
        if not token or not self._client or not ctx:
            log.warning("wechat: no context_token for %s yet — can't send unsolicited", chat_id)
            return
        body = {"to_user_id": chat_id, "context_token": ctx,
               "item_list": [{"type": 1, "text_item": {"text": text[:4000]}}]}
        r = await self._client.post(f"{ILINK_BASE}/ilink/bot/sendmessage", json=body,
                                    headers=_headers(token))
        if r.status_code != 200:
            log.warning("wechat sendmessage %s: %s", r.status_code, r.text[:200])


wechat = WeChatChannel()


# ---------------------------------------------------------------------- QR login
_qr_sessions: dict[str, dict] = {}


@_router.post("/wechat/qr")
async def wechat_qr_new():
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{ILINK_BASE}/ilink/bot/get_bot_qrcode", params={"bot_type": BOT_TYPE},
                        headers=_headers())
        r.raise_for_status()
        data = r.json()
    qrcode = data.get("qrcode") or ""
    _qr_sessions["current"] = {"qrcode": qrcode, "started": time.time()}
    return {"qrcode": qrcode, "image_url": data.get("qrcode_img_content", "")}


@_router.get("/wechat/qr/status")
async def wechat_qr_status():
    sess = _qr_sessions.get("current")
    if not sess:
        return {"status": "none"}
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(f"{ILINK_BASE}/ilink/bot/get_qrcode_status",
                        params={"qrcode": sess["qrcode"]},
                        headers=_headers(extra={"iLink-App-ClientVersion": "1"}))
    if r.status_code != 200:
        return {"status": "error"}
    payload = r.json()
    if payload.get("bot_token"):
        from .. import gatekeeper
        gatekeeper.vault_set("WECHAT_BOT_TOKEN", payload["bot_token"])
        cfg = db.kv_get("channel_config:wechat", {})
        cfg["WECHAT_BOT_TOKEN"] = "{{vault:WECHAT_BOT_TOKEN}}"
        db.kv_set("channel_config:wechat", cfg)
        _qr_sessions.pop("current", None)
        from ..runtime import spawn
        spawn(wechat.start())
        return {"status": "confirmed"}
    return {"status": payload.get("status", "wait")}
