"""Telegram channel — Bot API long polling (``getUpdates``), plain httpx.

A poller + sender on OpenDot's single ``Channel`` base: one bot token, long polling (no extra
dependency — the whole Bot API surface used here is ``getUpdates``,
``sendMessage``, ``answerCallbackQuery``).

Setup: talk to @BotFather → /newbot → paste the token into the vault as
``TELEGRAM_BOT_TOKEN`` (the Channels tab does this for you), then send
``/pair CODE`` to your bot from the phone showing the pairing code.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

from .base import Channel, is_allowed

log = logging.getLogger("opendot.channels.telegram")

_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _md_to_html(text: str) -> str:
    """Minimal Markdown → Telegram HTML: bold, inline code, fenced code."""
    import html
    text = html.escape(text)
    text = re.sub(r"```(\w*)\n?([\s\S]*?)```", lambda m: f"<pre>{m.group(2)}</pre>", text)
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    text = _BOLD.sub(r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    return text


class TelegramChannel(Channel):
    def __init__(self) -> None:
        super().__init__(name="telegram", label="Telegram", emoji="✈️",
                         setup=["Message @BotFather → /newbot → copy the token",
                                "Paste it below as TELEGRAM_BOT_TOKEN",
                                "DM your bot “/pair CODE” using the code from this page"],
                         fields=[{"key": "TELEGRAM_BOT_TOKEN", "label": "Bot token",
                                 "secret": True}])
        self._offset = 0
        self._client: httpx.AsyncClient | None = None
        self._task: asyncio.Task | None = None
        self._running = False

    def _base(self, token: str) -> str:
        return f"https://api.telegram.org/bot{token}"

    async def start(self) -> None:
        token = self.config().get("TELEGRAM_BOT_TOKEN")
        if not token:
            self.set_status("disabled")
            return
        self.set_status("connecting")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(40))
        try:
            r = await self._client.get(f"{self._base(token)}/getMe")
            r.raise_for_status()
            if not r.json().get("ok"):
                raise RuntimeError(r.text[:200])
        except Exception as e:
            self.set_status("error", str(e))
            return
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
                r = await self._client.get(f"{self._base(token)}/getUpdates", params={
                    "offset": self._offset, "timeout": 30,
                    "allowed_updates": ["message", "callback_query"]})
                r.raise_for_status()
                data = r.json()
                if not data.get("ok"):
                    raise RuntimeError(str(data))
                backoff = 1.0
                for upd in data.get("result", []):
                    self._offset = upd["update_id"] + 1
                    await self._handle_update(token, upd)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("telegram poll error (retry %.0fs): %s", backoff, e)
                self.set_status("error", str(e))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                if self._running:
                    self.set_status("connected")

    async def _handle_update(self, token: str, upd: dict) -> None:
        if "callback_query" in upd:
            await self._handle_callback(token, upd["callback_query"])
            return
        msg = upd.get("message") or {}
        text = msg.get("text") or msg.get("caption") or ""
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        sender = str((msg.get("from") or {}).get("id", ""))
        files = await self._download_files(token, msg)
        if not chat_id or not (text or files):
            return
        title = chat.get("title") or chat.get("username") or chat.get("first_name") or chat_id
        await self.handle_inbound(chat_id, sender, text, title, files=files)

    async def _download_files(self, token: str, msg: dict) -> list[tuple[str, bytes]]:
        """Documents, photos, audio, voice and video sent to the bot (Bot API ≤ 20 MB)."""
        refs = []
        if msg.get("document"):
            d = msg["document"]
            refs.append((d["file_id"], d.get("file_name") or "document"))
        if msg.get("photo"):
            refs.append((msg["photo"][-1]["file_id"], f"photo-{msg.get('message_id', '')}.jpg"))
        for k, ext in (("audio", "mp3"), ("voice", "ogg"), ("video", "mp4"), ("video_note", "mp4")):
            if msg.get(k):
                refs.append((msg[k]["file_id"], msg[k].get("file_name") or f"{k}-{msg.get('message_id', '')}.{ext}"))
        out = []
        for fid, name in refs:
            try:
                r = await self._client.get(f"{self._base(token)}/getFile", params={"file_id": fid})
                path = r.json()["result"]["file_path"]
                data = await self._client.get(f"https://api.telegram.org/file/bot{token}/{path}")
                data.raise_for_status()
                out.append((name, data.content))
            except Exception as e:
                log.warning("telegram: could not download %s: %s", name, e)
        return out

    async def _handle_callback(self, token: str, cq: dict) -> None:
        data = cq.get("data") or ""
        chat_id = str((cq.get("message") or {}).get("chat", {}).get("id", ""))
        if not data.startswith("appr:"):
            return
        _, ap_id, action = (data.split(":", 2) + ["", ""])[:3]
        if not is_allowed(self.name, chat_id):
            return
        from .. import gatekeeper
        approve, always = action != "deny", action == "always"
        gatekeeper.decide(ap_id, approve, always)
        try:
            await self._client.post(f"{self._base(token)}/answerCallbackQuery",
                                    json={"callback_query_id": cq["id"],
                                         "text": "✅ done" if approve else "🚫 denied"})
        except Exception:
            log.exception("answerCallbackQuery failed")

    async def _send_text(self, chat_id: str, text: str, thread_id: str | None = None) -> None:
        token = self.config().get("TELEGRAM_BOT_TOKEN")
        if not token or not self._client:
            return
        r = await self._client.post(f"{self._base(token)}/sendMessage", json={
            "chat_id": chat_id, "text": _md_to_html(text), "parse_mode": "HTML"})
        if r.status_code != 200:
            log.warning("telegram sendMessage %s: %s", r.status_code, r.text[:200])

    async def _send_approval(self, chat_id: str, approval: dict) -> None:
        token = self.config().get("TELEGRAM_BOT_TOKEN")
        if not token or not self._client:
            return
        agent = None
        from ..db import db
        agent = db.one("SELECT name, emoji FROM agents WHERE id=?", approval["agent_id"]) or {}
        from .base import approval_buttons, approval_title
        text = (f"{agent.get('emoji', '')} <b>{approval_title(agent.get('name', 'Pip'), approval)}</b>"
                f"\n{approval.get('reason', '')}")
        kb = {"inline_keyboard": [[
            {"text": label, "callback_data": f"appr:{approval['id']}:{action}"}
            for label, action in approval_buttons()]]}
        await self._client.post(f"{self._base(token)}/sendMessage", json={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML", "reply_markup": kb})


telegram = TelegramChannel()
