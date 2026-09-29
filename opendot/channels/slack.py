"""Slack channel — Socket Mode via ``slack_bolt`` (no public URL needed).

Needs a bot token (``xoxb-…``, scopes ``chat:write``, ``im:history``,
``channels:history``) and an app-level token (``xapp-…``, ``connections:write``)
with Socket Mode enabled.
"""

from __future__ import annotations

import logging

from ..db import db
from .base import Channel

log = logging.getLogger("opendot.channels.slack")


class SlackChannel(Channel):
    def __init__(self) -> None:
        super().__init__(name="slack", label="Slack", emoji="💜",
                         setup=["Create a Slack app at api.slack.com/apps",
                                "Enable Socket Mode → generate an app-level token (xapp-…)",
                                "OAuth & Permissions → install → copy the bot token (xoxb-…)",
                                "Add scopes: chat:write, im:history, channels:history, "
                                "app_mentions:read"],
                         fields=[{"key": "SLACK_BOT_TOKEN", "label": "Bot token (xoxb-…)",
                                 "secret": True},
                                {"key": "SLACK_APP_TOKEN", "label": "App-level token (xapp-…)",
                                 "secret": True}])
        self._app = None
        self._handler = None
        self._client = None

    async def start(self) -> None:
        cfg = self.config()
        bot, app_tok = cfg.get("SLACK_BOT_TOKEN"), cfg.get("SLACK_APP_TOKEN")
        if not bot or not app_tok:
            self.set_status("disabled")
            return
        try:
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
            from slack_bolt.async_app import AsyncApp
        except ImportError:
            self.set_status("error", "pip install slack_bolt")
            return
        self.set_status("connecting")
        self._app = AsyncApp(token=bot)
        self._client = self._app.client

        @self._app.event("message")
        async def on_message(event, say):  # noqa: ARG001 — bolt calls with kwargs
            if event.get("bot_id") or event.get("subtype"):
                return
            chat_id = event.get("channel", "")
            text = event.get("text", "")
            await self.handle_inbound(chat_id, event.get("user", ""), text, chat_id)

        @self._app.action("dot_approval")
        async def on_action(ack, body):
            await ack()
            val = body["actions"][0]["value"]  # "apprId:action"
            ap_id, _, action = val.partition(":")
            from .. import gatekeeper
            gatekeeper.decide(ap_id, action != "deny", action == "always")

        try:
            self._handler = AsyncSocketModeHandler(self._app, app_tok)
            import asyncio
            asyncio.create_task(self._handler.start_async())
        except Exception as e:
            self.set_status("error", str(e))
            return
        self.set_status("connected")

    async def stop(self) -> None:
        if self._handler:
            try:
                await self._handler.close_async()
            except Exception:
                pass
        self._handler = None
        self._app = None
        self.set_status("disabled")

    async def _send_text(self, chat_id: str, text: str, thread_id: str | None = None) -> None:
        if not self._client:
            return
        await self._client.chat_postMessage(channel=chat_id, text=text, mrkdwn=True)

    async def _send_approval(self, chat_id: str, approval: dict) -> None:
        if not self._client:
            return
        from .base import approval_buttons, approval_title
        agent = db.one("SELECT name, emoji FROM agents WHERE id=?", approval["agent_id"]) or {}
        title = approval_title(agent.get("name", "Pip"), approval)
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn",
             "text": f"*{title}*\n{approval.get('reason', '')}"}},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": label},
                 "action_id": "dot_approval", "value": f"{approval['id']}:{action}"}
                for label, action in approval_buttons()]},
        ]
        await self._client.chat_postMessage(channel=chat_id, blocks=blocks, text=title)


slack = SlackChannel()
