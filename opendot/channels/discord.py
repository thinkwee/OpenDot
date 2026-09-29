"""Discord channel — gateway bot via ``discord.py``. Lower priority: text +
button approvals, no voice, no slash-commands (plain ``/commands`` in chat
still work since we parse them ourselves, same as every other channel).
"""

from __future__ import annotations

import logging

from ..db import db
from .base import Channel, chunk_text

log = logging.getLogger("opendot.channels.discord")


class DiscordChannel(Channel):
    def __init__(self) -> None:
        super().__init__(name="discord", label="Discord", emoji="🎮",
                         setup=["discord.com/developers/applications → New Application → Bot",
                                "Enable “Message Content Intent”",
                                "Copy the bot token below, then invite it to your server "
                                "(OAuth2 → URL Generator → bot, Send Messages)"],
                         fields=[{"key": "DISCORD_BOT_TOKEN", "label": "Bot token", "secret": True}])
        self._client = None
        self._task = None

    async def start(self) -> None:
        token = self.config().get("DISCORD_BOT_TOKEN")
        if not token:
            self.set_status("disabled")
            return
        try:
            import discord
        except ImportError:
            self.set_status("error", "pip install discord.py")
            return
        self.set_status("connecting")
        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_ready():
            self.set_status("connected")

        @client.event
        async def on_message(message):
            if message.author.bot:
                return
            await self.handle_inbound(str(message.channel.id), str(message.author.id),
                                      message.content, str(message.channel))

        @client.event
        async def on_interaction(interaction):
            if interaction.data.get("component_type") != 2:
                return
            val = interaction.data.get("custom_id", "")
            if not val.startswith("dot_appr:"):
                return
            _, ap_id, action = val.split(":", 2)
            from .. import gatekeeper
            gatekeeper.decide(ap_id, action != "deny", action == "always")
            await interaction.response.send_message("✅ done" if action != "deny" else "🚫 denied",
                                                     ephemeral=True)

        import asyncio
        self._task = asyncio.create_task(self._run(client, token))

    async def _run(self, client, token: str) -> None:
        try:
            await client.start(token)
        except Exception as e:
            log.exception("discord: connection failed")
            self.set_status("error", str(e))

    async def stop(self) -> None:
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass
        self._client = None
        if self._task:
            self._task.cancel()
            self._task = None
        self.set_status("disabled")

    async def _send_text(self, chat_id: str, text: str, thread_id: str | None = None) -> None:
        if not self._client:
            return
        channel = self._client.get_channel(int(chat_id)) or await self._client.fetch_channel(int(chat_id))
        for chunk in chunk_text(text, 1900):
            await channel.send(chunk)

    async def _send_approval(self, chat_id: str, approval: dict) -> None:
        if not self._client:
            return
        import discord
        agent = db.one("SELECT name, emoji FROM agents WHERE id=?", approval["agent_id"]) or {}
        view = discord.ui.View(timeout=None)
        from .base import approval_buttons, approval_title
        styles = {"once": discord.ButtonStyle.success, "always": discord.ButtonStyle.primary,
                  "deny": discord.ButtonStyle.secondary}
        for label, action in approval_buttons():
            style = styles[action]
            view.add_item(discord.ui.Button(label=label, style=style,
                                            custom_id=f"dot_appr:{approval['id']}:{action}"))
        channel = self._client.get_channel(int(chat_id)) or await self._client.fetch_channel(int(chat_id))
        await channel.send(f"{approval_title(agent.get('name', 'Pip'), approval)}\n"
                           f"{approval.get('reason', '')}", view=view)


discord_channel = DiscordChannel()
