"""Common infrastructure shared by every messaging channel (Telegram, WeChat,
Feishu, Slack, Discord, …).

Each external chat is bound to one OpenDot thread (a DM with an agent, or a
group thread with the whole team). This module owns:

- the ``channel_bindings`` table (chat <-> thread/agent mapping)
- pairing: a one-time code from the UI turns into an allow-listed chat/user id
  (only the owner may talk to the bot — everyone else is silently ignored)
- in-chat commands: ``/agents``, ``/to <Name>``, ``/team``, ``/new``, ``/stop``,
  ``/pair CODE``
- outbound mirroring: agent ``message`` events on a bound thread are pushed
  back out to the channel; ``approval`` events become an interactive prompt;
  ``inbox`` notes are mirrored to a channel's "home chat" for proactive pings
- the shared ``/api/channels`` REST router used by the web UI and by every
  channel module (each module imports ``router`` under a private name and
  adds its own routes to the same object, so only this module exports the
  module-level ``router`` the extension loader mounts).

A concrete channel subclasses :class:`Channel`, implements ``configured``,
``start``/``stop`` and ``_send_text``/``_send_approval``, and calls
``register_outbound(self)`` once it is constructed.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, Body, HTTPException

from .. import gatekeeper, runtime
from ..bus import bus
from ..connectors import ingest_event
from ..db import db, new_id
from ..ext.lang import tr

log = logging.getLogger("opendot.channels")

db.ensure_schema("""
CREATE TABLE IF NOT EXISTS channel_bindings (
  id TEXT PRIMARY KEY, channel TEXT, chat_id TEXT, thread_id TEXT,
  agent_id TEXT, title TEXT, created REAL
);
CREATE INDEX IF NOT EXISTS ix_bindings_chat ON channel_bindings(channel, chat_id);
""")

REGISTRY: dict[str, "Channel"] = {}

# per-channel outbound message-length limits (platform hard caps, minus margin)
CHUNK_LIMIT = {"telegram": 3900, "wechat": 3800, "feishu": 9000, "slack": 3800, "discord": 1900}


# ---------------------------------------------------------------------- base
@dataclass
class Channel:
    """Subclassed by each platform. ``name`` must match the kv/db channel key."""

    name: str = "base"
    label: str = "Channel"
    emoji: str = "💬"
    status: str = "disabled"     # disabled | connecting | connected | error
    error: str = ""
    setup: list[str] = field(default_factory=list)   # short setup steps shown in the UI
    fields: list[dict] = field(default_factory=list)  # [{key,label,secret?}] config form

    def __post_init__(self) -> None:
        REGISTRY[self.name] = self
        register_outbound(self)

    # -- config -------------------------------------------------------
    def config(self) -> dict:
        return gatekeeper.inject_secrets(db.kv_get(f"channel_config:{self.name}", {}))

    def raw_config(self) -> dict:
        """Config as saved (with ``{{vault:...}}`` placeholders, not the secret)."""
        return db.kv_get(f"channel_config:{self.name}", {})

    def configured(self) -> bool:
        cfg = self.config()
        return all(cfg.get(f["key"]) for f in self.fields if f.get("required", True))

    def set_status(self, status: str, error: str = "") -> None:
        self.status, self.error = status, error[:300]
        bus.emit("channel", name=self.name, status=status, error=self.error)

    # -- overridden by subclasses --------------------------------------
    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        pass

    async def _send_text(self, chat_id: str, text: str, thread_id: str | None = None) -> None:
        raise NotImplementedError

    async def _send_approval(self, chat_id: str, approval: dict) -> None:  # noqa: D401
        """Default: plain-text fallback (reply 1 / 2 / 0). Rich channels override."""
        pend = db.kv_get(f"channel_pending_appr:{self.name}", {})
        pend[chat_id] = approval["id"]
        db.kv_set(f"channel_pending_appr:{self.name}", pend)
        agent = db.one("SELECT name FROM agents WHERE id=?", approval["agent_id"]) or {}
        await self._send_text(chat_id,
            tr(f"👋 {agent.get('name', 'Pip')}: can I {_what(approval)}?\n"
               f"{approval.get('reason', '')}\n\n"
               "Reply 1 = go · 2 = go, and don't ask again · 0 = not now",
               f"👋 {agent.get('name', 'Pip')}：我可以{_what(approval)}吗？\n"
               f"{approval.get('reason', '')}\n\n"
               "回复 1 = 去吧 · 2 = 去吧，以后别问了 · 0 = 先不用"))

    # -- helpers every channel calls -----------------------------------
    async def send_text(self, chat_id: str, text: str, thread_id: str | None = None) -> None:
        for chunk in chunk_text(text, CHUNK_LIMIT.get(self.name, 3500)):
            try:
                await self._send_text(chat_id, chunk, thread_id)
            except Exception:
                log.exception("%s: send_text failed", self.name)

    async def handle_inbound(self, chat_id: str, sender_id: str, text: str,
                             title: str | None = None,
                             files: list[tuple[str, bytes]] | None = None) -> None:
        """Call this from the platform's update loop for every inbound message.
        ``files`` = [(filename, bytes)] the user sent along (become chat uploads)."""
        text = (text or "").strip()
        if not text and not files:
            return
        # text-fallback approval reply?
        pend = db.kv_get(f"channel_pending_appr:{self.name}", {})
        if chat_id in pend and text.strip() in ("0", "1", "2"):
            ap_id = pend.pop(chat_id)
            db.kv_set(f"channel_pending_appr:{self.name}", pend)
            approve, always = text != "0", text == "2"
            gatekeeper.decide(ap_id, approve, always)
            await self.send_text(chat_id, tr("👍 Go!", "👍 好的，去办了") if approve else tr("👌 Skipped", "👌 先不做"))
            return
        if not is_allowed(self.name, chat_id) and not is_allowed(self.name, sender_id):
            reply = try_pair(self.name, chat_id, sender_id, text)
            if reply:
                await self.send_text(chat_id, reply)
            else:
                log.info("%s: ignoring message from unpaired chat %s", self.name, chat_id)
            return
        reply = handle_command(self.name, chat_id, text) if text else None
        if reply is not None:
            await self.send_text(chat_id, reply)
            return
        thread_id = bound_thread(self.name, chat_id, title or chat_id)
        agent_hint = db.kv_get(f"channel_agent:{self.name}:{chat_id}")
        forward = f"@{agent_hint} {text}" if agent_hint and "@" not in text[:1] else text
        await ingest_event(self.name, "message", {"chat_id": chat_id, "sender": sender_id,
                                                   "text": text})
        atts = []
        if files:
            from ..ext import uploads
            for name, data in files:
                try:
                    atts.append(uploads.public(await uploads.ingest_bytes(name, data, thread_id,
                                                                          self.name)))
                except Exception as e:
                    log.warning("%s: could not store %s: %s", self.name, name, e)
        await runtime.on_user_message(thread_id, forward, attachments=atts, source=self.name,
                                      sender=sender_id)


# ---------------------------------------------------------------------- text utils
def chunk_text(text: str, limit: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    while text:
        if len(text) <= limit:
            out.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        out.append(text[:cut])
        text = text[cut:].lstrip()
    return out


def to_plain(md: str) -> str:
    """Very small markdown→plain fallback for text-only channels (WeChat)."""
    import re
    md = re.sub(r"```[\w]*\n?([\s\S]*?)```", r"\1", md)
    md = re.sub(r"[*_`#>]", "", md)
    return md


# ---------------------------------------------------------------------- bindings
def bound_thread(channel: str, chat_id: str, title: str) -> str:
    row = db.one("SELECT * FROM channel_bindings WHERE channel=? AND chat_id=?", channel, chat_id)
    if row:
        return row["thread_id"]
    # brand-new chat: DM with the default (first-created) agent
    agent = db.one("SELECT id FROM agents ORDER BY created LIMIT 1")
    thread = db.one("SELECT id FROM threads WHERE kind='dm' AND members LIKE ?",
                    f'%"{agent["id"]}"%') if agent else None
    thread_id = thread["id"] if thread else new_id("th_")
    if not thread:
        db.insert("threads", id=thread_id, title=title, kind="dm", members=[agent["id"]],
                  updated=time.time())
    db.insert("channel_bindings", id=new_id("cb_"), channel=channel, chat_id=chat_id,
             thread_id=thread_id, agent_id=agent["id"] if agent else None, title=title)
    return thread_id


def rebind(channel: str, chat_id: str, thread_id: str, agent_id: str | None,
          title: str | None = None) -> None:
    row = db.one("SELECT * FROM channel_bindings WHERE channel=? AND chat_id=?", channel, chat_id)
    if row:
        db.update("channel_bindings", row["id"], thread_id=thread_id, agent_id=agent_id,
                  title=title or row["title"])
    else:
        db.insert("channel_bindings", id=new_id("cb_"), channel=channel, chat_id=chat_id,
                 thread_id=thread_id, agent_id=agent_id, title=title or chat_id)


def bindings(channel: str | None = None) -> list[dict]:
    return db.q("SELECT * FROM channel_bindings WHERE channel=? ORDER BY created DESC", channel) \
        if channel else db.q("SELECT * FROM channel_bindings ORDER BY created DESC")


# ---------------------------------------------------------------------- commands
def handle_command(channel: str, chat_id: str, text: str) -> str | None:
    """Return a reply string if ``text`` was a recognised command, else None."""
    if not text.startswith("/"):
        return None
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower().lstrip("/")
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "agents":
        agents = db.q("SELECT name, emoji, role FROM agents ORDER BY created")
        return "\n".join(f"{a['emoji']} *{a['name']}* — {a['role']}" for a in agents) or \
            tr("No agents yet.", "还没有助理。")

    if cmd == "to":
        if not arg:
            return tr("Usage: /to <Name>", "用法：/to <名字>")
        agent = db.one("SELECT * FROM agents WHERE lower(name)=lower(?)", arg.strip())
        if not agent:
            return tr(f"I don't know anyone called “{arg}”. Try /agents.",
                      f"没有叫「{arg}」的助理。试试 /agents。")
        db.kv_set(f"channel_agent:{channel}:{chat_id}", agent["name"])
        row = db.one("SELECT * FROM channel_bindings WHERE channel=? AND chat_id=?", channel, chat_id)
        thread = db.one("SELECT * FROM threads WHERE id=?", row["thread_id"]) if row else None
        if thread and thread["kind"] == "dm":
            own = db.one("SELECT id FROM threads WHERE kind='dm' AND members LIKE ?",
                        f'%"{agent["id"]}"%')
            if own:
                rebind(channel, chat_id, own["id"], agent["id"])
        return tr(f"Okay — talking to {agent['emoji']} {agent['name']} now.",
                  f"好，现在和 {agent['emoji']} {agent['name']} 聊。")

    if cmd == "team":
        group = db.one("SELECT id FROM threads WHERE kind='group' ORDER BY created LIMIT 1")
        if not group:
            return tr("No group chat yet — make one in the app first.",
                      "还没有群聊，先在 App 里建一个吧。")
        rebind(channel, chat_id, group["id"], None, title="Dream Team")
        return tr("👥 This chat now goes to the group. @Name to talk to someone.",
                  "👥 这个对话现在连到群里了。@名字 可以点名找人。")

    if cmd == "new":
        agent_name = db.kv_get(f"channel_agent:{channel}:{chat_id}")
        agent = (db.one("SELECT * FROM agents WHERE name=?", agent_name) if agent_name else None) \
            or db.one("SELECT * FROM agents ORDER BY created LIMIT 1")
        thread = db.insert("threads", id=new_id("th_"), title=f"{agent['name']} · new",
                           kind="dm", members=[agent["id"]], updated=time.time())
        rebind(channel, chat_id, thread["id"], agent["id"])
        return tr("🐣 Fresh chat started.", "🐣 开了个新对话。")

    if cmd == "stop":
        row = db.one("SELECT * FROM channel_bindings WHERE channel=? AND chat_id=?", channel, chat_id)
        if row:
            runtime.stop_thread(row["thread_id"])
        return tr("⏹️ Stopped.", "⏹️ 已经停下了。")

    if cmd == "pair":
        return try_pair(channel, chat_id, chat_id, text)

    return tr(f"Unknown command {cmd}. Try /agents, /to, /team, /new, /stop.",
              f"不认识 {cmd} 这个指令。试试 /agents、/to、/team、/new、/stop。")


# ---------------------------------------------------------------------- pairing
def new_pairing_code(channel: str) -> str:
    codes = {k: v for k, v in db.kv_get(f"channel_pair:{channel}", {}).items() if v > time.time()}
    code = secrets.token_hex(3).upper()
    codes[code] = time.time() + 600
    db.kv_set(f"channel_pair:{channel}", codes)
    return code


def try_pair(channel: str, chat_id: str, sender_id: str, text: str) -> str | None:
    parts = text.split(maxsplit=1)
    if parts[0].lower().lstrip("/") != "pair" or len(parts) < 2:
        return None
    code = parts[1].strip().upper()
    codes = db.kv_get(f"channel_pair:{channel}", {})
    if codes.get(code, 0) <= time.time():
        return tr("❌ That pairing code is wrong or expired. Get a new one in the OpenDot app.",
                  "❌ 配对码不对或已过期，在 OpenDot App 里再拿一个吧。")
    codes.pop(code, None)
    db.kv_set(f"channel_pair:{channel}", codes)
    allow = set(db.kv_get(f"channel_allow:{channel}", []))
    allow.add(chat_id)
    allow.add(sender_id)
    db.kv_set(f"channel_allow:{channel}", sorted(allow))
    return tr("✅ Paired! I'm all yours now. Try /agents or just say hi.",
              "✅ 配对好了！试试 /agents，或者直接打个招呼。")


def is_allowed(channel: str, ident: str) -> bool:
    if not ident:
        return False
    return ident in set(db.kv_get(f"channel_allow:{channel}", []))


# ---------------------------------------------------------------------- outbound mirroring
_registered: set[str] = set()


def register_outbound(ch: Channel) -> None:
    if ch.name in _registered:
        return
    _registered.add(ch.name)

    async def on_event(kind: str, data: dict) -> None:
        if ch.status != "connected":
            return
        try:
            if kind == "message":
                await _mirror_message(ch, data.get("message") or {})
            elif kind == "approval":
                await _mirror_approval(ch, data.get("approval") or {})
            elif kind == "inbox":
                await _mirror_inbox(ch, data.get("item") or {})
        except Exception:
            log.exception("%s: outbound mirror failed", ch.name)

    bus.listen(on_event)


async def _mirror_message(ch: Channel, msg: dict) -> None:
    if not msg or msg.get("role") != "agent" or not msg.get("content"):
        return
    rows = db.q("SELECT * FROM channel_bindings WHERE channel=? AND thread_id=?",
               ch.name, msg["thread_id"])
    if not rows:
        return
    thread = db.one("SELECT * FROM threads WHERE id=?", msg["thread_id"])
    agent = db.one("SELECT * FROM agents WHERE id=?", msg["agent_id"]) or {}
    text = msg["content"]
    if thread and thread["kind"] == "group":
        text = f"{agent.get('emoji', '')} *{agent.get('name', '')}*: {text}"
    for row in rows:
        await ch.send_text(row["chat_id"], text, msg["thread_id"])


async def _mirror_approval(ch: Channel, ap: dict) -> None:
    if not ap or ap.get("status") != "pending":
        return
    rows = db.q("SELECT * FROM channel_bindings WHERE channel=? AND thread_id=?",
               ch.name, ap.get("thread_id"))
    for row in rows:
        try:
            await ch._send_approval(row["chat_id"], ap)
        except Exception:
            log.exception("%s: approval prompt failed", ch.name)


async def _mirror_inbox(ch: Channel, item: dict) -> None:
    if not item or item.get("kind") == "approval":
        return  # approvals are handled above
    home = db.kv_get(f"channel_home:{ch.name}")
    if not home:
        return
    agent = db.one("SELECT name, emoji FROM agents WHERE id=?", item.get("agent_id")) or {}
    text = f"{agent.get('emoji', '📥')} *{item.get('title', '')}*\n{item.get('body', '')}"
    await ch.send_text(home, text)


# ---------------------------------------------------------------------- shared REST router
router = APIRouter(prefix="/api/channels")


# 中文 setup text for the Channels page: label, steps, field labels by key
ZH = {
    "wechat": ("微信（ClawBot）",
               ["点下面的「获取二维码」", "微信 → 设置 → 插件 → ClawBot → 扫一扫",
                "在手机上确认，机器人令牌会自动保存"],
               {"WECHAT_BOT_TOKEN": "机器人令牌（扫码登录后自动获取）"}),
    "telegram": ("Telegram",
                 ["给 @BotFather 发 /newbot，复制它给你的令牌", "粘贴到下面的 TELEGRAM_BOT_TOKEN",
                  "用本页的配对码给你的机器人发「/pair 配对码」"],
                 {"TELEGRAM_BOT_TOKEN": "机器人令牌"}),
    "slack": ("Slack",
              ["在 api.slack.com/apps 新建一个 Slack 应用",
               "打开 Socket Mode，生成一个应用级令牌（xapp-…）",
               "OAuth & Permissions → 安装 → 复制机器人令牌（xoxb-…）",
               "添加权限：chat:write、im:history、channels:history、app_mentions:read"],
              {"SLACK_BOT_TOKEN": "机器人令牌（xoxb-…）", "SLACK_APP_TOKEN": "应用级令牌（xapp-…）"}),
    "discord": ("Discord",
                ["discord.com/developers/applications → New Application → Bot",
                 "打开「Message Content Intent」",
                 "把机器人令牌复制到下面，再邀请它进你的服务器（OAuth2 → URL Generator → bot、Send Messages）"],
                {"DISCORD_BOT_TOKEN": "机器人令牌"}),
    "feishu": ("飞书 / Lark",
               ["在 open.feishu.cn 创建一个企业自建应用",
                "开启「机器人」能力，订阅 im.message.receive_v1，并使用长连接接收事件",
                "把 App ID / App Secret 粘贴到下面"],
               {"FEISHU_APP_ID": "App ID", "FEISHU_APP_SECRET": "App Secret"}),
}


def _texts(c) -> tuple[str, list, list]:
    from ..ext.lang import lang
    if lang() != "zh" or c.name not in ZH:
        return c.label, c.setup, c.fields
    label, setup, fl = ZH[c.name]
    return label, setup, [{**f, "label": fl.get(f["key"], f["label"])} for f in c.fields]


@router.get("")
async def list_channels():
    out = []
    for c in REGISTRY.values():
        label, setup, fields = _texts(c)
        out.append({"name": c.name, "label": label, "emoji": c.emoji, "status": c.status,
                    "error": c.error, "configured": c.configured(), "setup": setup,
                    "fields": fields, "config": {f["key"]: c.raw_config().get(f["key"], "")
                                                 for f in c.fields},
                    "home": db.kv_get(f"channel_home:{c.name}", "")})
    return out


@router.put("/{name}/config")
async def put_config(name: str, body: dict = Body(...)):
    if name not in REGISTRY:
        raise HTTPException(404)
    db.kv_set(f"channel_config:{name}", body)
    return {"ok": True}


@router.put("/{name}/home")
async def put_home(name: str, body: dict = Body(...)):
    if name not in REGISTRY:
        raise HTTPException(404)
    db.kv_set(f"channel_home:{name}", body.get("chat_id") or "")
    return {"ok": True}


@router.post("/{name}/pair/new")
async def pair_new(name: str):
    if name not in REGISTRY:
        raise HTTPException(404)
    return {"code": new_pairing_code(name)}


@router.get("/{name}/allowlist")
async def get_allowlist(name: str):
    return db.kv_get(f"channel_allow:{name}", [])


@router.delete("/{name}/allowlist/{ident}")
async def del_allowlist(name: str, ident: str):
    allow = [i for i in db.kv_get(f"channel_allow:{name}", []) if i != ident]
    db.kv_set(f"channel_allow:{name}", allow)
    return {"ok": True}


@router.get("/{name}/bindings")
async def get_bindings(name: str):
    return bindings(name)


@router.delete("/{name}/bindings/{bid}")
async def del_binding(name: str, bid: str):
    db.delete("channel_bindings", bid)
    return {"ok": True}


@router.patch("/{name}/bindings/{bid}")
async def patch_binding(name: str, bid: str, body: dict = Body(...)):
    fields = {k: v for k, v in body.items() if k in ("agent_id", "title")}
    db.update("channel_bindings", bid, **fields)
    return db.one("SELECT * FROM channel_bindings WHERE id=?", bid)


@router.post("/{name}/restart")
async def restart(name: str):
    ch = REGISTRY.get(name)
    if not ch:
        raise HTTPException(404)
    try:
        await ch.stop()
    except Exception:
        pass
    from ..runtime import spawn
    spawn(_guarded_start(ch))
    return {"ok": True}


async def _guarded_start(ch: Channel) -> None:
    try:
        await ch.start()
    except Exception as e:
        log.exception("%s: restart failed", ch.name)
        ch.set_status("error", str(e))


def _what(approval: dict) -> str:
    return (approval.get("args") or {}).get("_what") or approval.get("tool", "")


def approval_title(agent_name: str, approval: dict) -> str:
    """“Christine: can I email the hotel?” — the line every channel shows."""
    return tr(f"{agent_name}: can I {_what(approval)}?", f"{agent_name}：我可以{_what(approval)}吗？")


def approval_buttons() -> list[tuple[str, str]]:
    """(label, action) for rich channels; actions are what gatekeeper.decide expects."""
    return [(tr("👍 Go", "👍 去吧"), "once"),
            (tr("⭐ Go, don't ask again", "⭐ 去吧，以后别问"), "always"),
            (tr("✋ Not now", "✋ 先不用"), "deny")]
