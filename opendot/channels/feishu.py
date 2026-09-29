"""Feishu / Lark channel — long-connection websocket event stream.

Uses the official ``lark-oapi`` SDK (``lark_oapi.ws.Client``): its WS client needs
only ``APP_ID`` / ``APP_SECRET`` and pushes events directly — no public URL, webhook
signature or Encrypt Key required. Interactive cards (card 1.0) carry the approval
buttons.
"""

from __future__ import annotations

import json
import logging

from ..db import db
from .base import Channel

log = logging.getLogger("opendot.channels.feishu")


class FeishuChannel(Channel):
    def __init__(self) -> None:
        super().__init__(name="feishu", label="Feishu / Lark", emoji="🕊️",
                         setup=["Create a self-built app at open.feishu.cn",
                                "Enable “Bot” + subscribe im.message.receive_v1 "
                                "and event long-connection mode",
                                "Paste App ID / App Secret below"],
                         fields=[{"key": "FEISHU_APP_ID", "label": "App ID"},
                                {"key": "FEISHU_APP_SECRET", "label": "App secret",
                                 "secret": True}])
        self._ws = None
        self._client = None
        self._task = None

    async def start(self) -> None:
        cfg = self.config()
        app_id, app_secret = cfg.get("FEISHU_APP_ID"), cfg.get("FEISHU_APP_SECRET")
        if not app_id or not app_secret:
            self.set_status("disabled")
            return
        try:
            import lark_oapi as lark
        except ImportError:
            self.set_status("error", "pip install lark-oapi")
            return
        self.set_status("connecting")
        self._client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        handler = (lark.EventDispatcherHandler.builder("", "")
                  .register_p2_im_message_receive_v1(self._on_message)
                  .register_p2_card_action_trigger(self._on_card)
                  .build())
        self._ws = lark.ws.Client(app_id, app_secret, event_handler=handler,
                                  log_level=lark.LogLevel.WARNING)
        import asyncio
        loop = asyncio.get_running_loop()
        self._task = loop.run_in_executor(None, self._ws.start)
        self.set_status("connected")

    async def stop(self) -> None:
        if self._ws:
            try:
                self._ws._disconnect()  # noqa: SLF001 — SDK has no public stop()
            except Exception:
                pass
            self._ws = None
        self._task = None
        self.set_status("disabled")

    # -- inbound (called from the SDK's own thread — hop back via asyncio) --
    def _on_message(self, data) -> None:
        from ..runtime import spawn
        try:
            ev = data.event
            msg = ev.message
            if msg.message_type != "text":
                return
            text = json.loads(msg.content or "{}").get("text", "")
            sender = ev.sender.sender_id.open_id if ev.sender and ev.sender.sender_id else ""
            chat_id = msg.chat_id or ""
        except Exception:
            log.exception("feishu: bad message event")
            return
        if not chat_id or not text:
            return
        spawn(self.handle_inbound(chat_id, sender, text, chat_id))

    def _on_card(self, data) -> dict | None:
        from ..runtime import spawn
        try:
            action = data.event.action.value or {}
            ap_id, decision = action.get("appr"), action.get("d")
        except Exception:
            return None
        if ap_id:
            spawn(self._decide(ap_id, decision))
        return {"toast": {"type": "success", "content": "Got it ✅"}}

    async def _decide(self, ap_id: str, decision: str) -> None:
        from .. import gatekeeper
        gatekeeper.decide(ap_id, decision != "deny", decision == "always")

    # -- outbound -------------------------------------------------------
    async def _send_text(self, chat_id: str, text: str, thread_id: str | None = None) -> None:
        await self._create_message(chat_id, "text", json.dumps({"text": text}))

    async def _send_approval(self, chat_id: str, approval: dict) -> None:
        from .base import approval_buttons, approval_title
        agent = db.one("SELECT name, emoji FROM agents WHERE id=?", approval["agent_id"]) or {}
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text",
                                 "content": f"{agent.get('emoji', '')} "
                                            f"{approval_title(agent.get('name', 'Pip'), approval)}"}},
            "elements": [
                {"tag": "div", "text": {"tag": "plain_text", "content": approval.get("reason", "")}},
                {"tag": "action", "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": label},
                     "type": {"once": "primary", "always": "default"}.get(action, "default"),
                     "value": {"appr": approval["id"], "d": action}}
                    for label, action in approval_buttons()]},
            ],
        }
        await self._create_message(chat_id, "interactive", json.dumps(card))

    async def _create_message(self, chat_id: str, msg_type: str, content: str) -> None:
        if not self._client:
            return
        try:
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
            req = (CreateMessageRequest.builder().receive_id_type("chat_id")
                  .request_body(CreateMessageRequestBody.builder().receive_id(chat_id)
                                .msg_type(msg_type).content(content).build())
                  .build())
            await self._client.im.v1.message.acreate(req)
        except Exception:
            log.exception("feishu: send failed")


feishu = FeishuChannel()
