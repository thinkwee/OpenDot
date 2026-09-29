"""Connectors: how OpenDot plugs into your other apps.

1. MCP servers (stdio or streamable-HTTP) — their tools appear as ``mcp__<server>__<tool>``
   and are gated by the Gatekeeper ("ask" by default until you trust them).
2. Inbound events — webhooks (``POST /hook/<token>/<source>``), the iOS Share
   Sheet / Shortcuts (``source=share``), and RSS feeds polled in the background.
   Events wake up any automation whose ``event_filter`` matches.

Config lives in data/connectors.json:
{"mcp": {"github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": "{{vault:GITHUB_TOKEN}}"}},
         "notion": {"url": "https://mcp.notion.com/mcp"}},
 "rss":  {"hn": "https://news.ycombinator.com/rss"}}
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
import logging
import time
import xml.etree.ElementTree as ET

import httpx

from .bus import bus
from .config import settings
from .db import db, new_id

log = logging.getLogger("opendot.connectors")


def load_config() -> dict:
    p = settings.DATA_DIR / "connectors.json"
    if not p.exists():
        p.write_text(json.dumps({"mcp": {}, "rss": {}}, indent=2))
    return json.loads(p.read_text())


def save_config(cfg: dict) -> None:
    (settings.DATA_DIR / "connectors.json").write_text(json.dumps(cfg, indent=2))


class MCPHub:
    def __init__(self) -> None:
        self.sessions: dict[str, object] = {}
        self.tools: dict[str, dict] = {}       # full name -> {server, tool, schema}
        self.status: dict[str, str] = {}
        self._stack: contextlib.AsyncExitStack | None = None

    async def start(self) -> None:
        await self.stop()
        self._stack = contextlib.AsyncExitStack()
        from .gatekeeper import inject_secrets
        for name, spec in load_config().get("mcp", {}).items():
            spec = inject_secrets(spec)
            try:
                await asyncio.wait_for(self._connect(name, spec), 40)
                self.status[name] = "connected"
            except Exception as e:
                log.warning("MCP %s failed: %s", name, e)
                self.status[name] = f"error: {e}"[:200]
        bus.emit("connectors", status=self.status)

    async def _connect(self, name: str, spec: dict) -> None:
        from mcp import ClientSession
        if spec.get("url"):
            from mcp.client.streamable_http import streamablehttp_client
            read, write, _ = await self._stack.enter_async_context(
                streamablehttp_client(spec["url"], headers=spec.get("headers")))
        else:
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(command=spec["command"], args=spec.get("args", []),
                                           env=spec.get("env") or None)
            read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self.sessions[name] = session
        for t in (await session.list_tools()).tools:
            full = f"mcp__{name}__{t.name}"[:64]
            self.tools[full] = {"server": name, "tool": t.name, "schema": {
                "type": "function", "function": {
                    "name": full, "description": f"[{name}] {(t.description or '')[:400]}",
                    "parameters": _schema(t)}}}

    def tool_defs(self) -> list[dict]:
        return [t["schema"] for t in self.tools.values()]

    async def call(self, full: str, args: dict) -> dict:
        t = self.tools.get(full)
        if not t:
            return {"error": f"connector tool {full} is not available"}
        res = await self.sessions[t["server"]].call_tool(t["tool"], args)
        texts = [getattr(c, "text", "") for c in res.content]
        is_err = getattr(res, "is_error", None)
        if is_err is None:
            is_err = getattr(res, "isError", False)
        return {"result": "\n".join(x for x in texts if x)[:16000], "is_error": bool(is_err)}

    async def stop(self) -> None:
        if self._stack:
            with contextlib.suppress(Exception):
                await self._stack.aclose()
        self._stack = None
        self.sessions.clear()
        self.tools.clear()
        self.status.clear()


def _schema(tool) -> dict:
    """``input_schema`` in mcp>=2, ``inputSchema`` in mcp 1.x."""
    s = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
    return s or {"type": "object", "properties": {}}


mcp_hub = MCPHub()


# ---------------- inbound events ----------------
async def ingest_event(source: str, type_: str, payload: dict) -> dict:
    ev = db.insert("events", id=new_id("ev_"), source=source, type=type_, payload=payload)
    bus.emit("event", event=ev)
    from .runtime import run_agent, spawn
    for a in db.q("SELECT * FROM automations WHERE kind='event' AND enabled=1"):
        if fnmatch.fnmatch(source, a["event_filter"] or "*"):
            db.update("automations", a["id"], last_run=time.time())
            body = json.dumps(payload, ensure_ascii=False)[:6000]
            spawn(run_agent(a["agent_id"], a["thread_id"] or _default_thread(a["agent_id"]),
                            f"automation:{a['name']}",
                            f"Event from `{source}` ({type_}):\n```json\n{body}\n```\n\n"
                            f"Your standing instruction: {a['prompt']}"))
    return ev


def _default_thread(agent_id: str) -> str:
    t = db.one("SELECT id FROM threads WHERE kind='dm' AND members LIKE ?", f'%"{agent_id}"%')
    return t["id"] if t else ""


async def rss_loop() -> None:
    seen: set[str] = set(db.kv_get("rss_seen", []))
    first = not seen
    while True:
        feeds = load_config().get("rss", {})
        for name, url in feeds.items():
            try:
                async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
                    root = ET.fromstring((await c.get(url)).content)
                for item in list(root.iter("item"))[:15]:
                    link = (item.findtext("link") or "").strip()
                    if not link or link in seen:
                        continue
                    seen.add(link)
                    if not first:
                        await ingest_event(f"rss:{name}", "item", {
                            "title": item.findtext("title"), "link": link,
                            "summary": (item.findtext("description") or "")[:800]})
            except Exception as e:
                log.debug("rss %s: %s", name, e)
        first = False
        db.kv_set("rss_seen", list(seen)[-3000:])
        await asyncio.sleep(900)


# ---------------- app catalog ----------------
# A short, curated list of MCP servers that are official (made by the app's own team)
# or the MCP project's reference servers. Each entry says exactly what runs, what it
# needs installed (Node for npx, uv for uvx), and which token to paste where. "{key}"
# in command/args/env/url/headers is filled from the entry's fields; secret fields go
# to the vault and are written as {{vault:NAME}}. Nothing here is OAuth: where an app
# offers a hosted server we use its personal-token option.
def _f(key, en, zh, *, secret=False, placeholder="", link="", help_en="", help_zh="",
       optional=False, default=""):
    return {"key": key, "label": {"en": en, "zh": zh}, "secret": secret,
            "placeholder": placeholder, "link": link, "optional": optional,
            "default": default, "help": {"en": help_en, "zh": help_zh}}


CATALOG_CATEGORIES = [
    {"id": "files", "label": {"en": "Files & notes", "zh": "文件和笔记"}},
    {"id": "work", "label": {"en": "Work & tasks", "zh": "工作和待办"}},
    {"id": "web", "label": {"en": "The web", "zh": "上网"}},
    {"id": "home", "label": {"en": "Home", "zh": "家里"}},
    {"id": "other", "label": {"en": "Anything else", "zh": "其他"}},
]

CATALOG: list[dict] = [
    {"id": "files", "category": "files", "emoji": "📁", "needs": "node",
     "name": {"en": "A folder", "zh": "一个文件夹"},
     "blurb": {"en": "Let your agents read and tidy one folder on this computer — and nothing "
                     "outside it.",
               "zh": "让助理读写这台电脑上的某个文件夹，别的地方一概碰不到。"},
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "{folder}"],
     "fields": [_f("folder", "Folder path", "文件夹路径", placeholder="/Users/me/Documents")],
     "source": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem"},
    {"id": "obsidian", "category": "files", "emoji": "🟣", "needs": "node",
     "name": {"en": "Obsidian vault", "zh": "Obsidian 笔记库"},
     "blurb": {"en": "Your notes are just Markdown files, so this hands your vault folder to "
                     "your agents: search, read, jot things down.",
               "zh": "Obsidian 的笔记就是一堆 Markdown 文件，"
                     "把笔记库文件夹交给助理：能搜、能读、能记。"},
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "{folder}"],
     "fields": [_f("folder", "Vault folder", "笔记库文件夹",
                   placeholder="/Users/me/Obsidian/MyVault")],
     "source": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem"},
    {"id": "notion", "category": "files", "emoji": "📝", "needs": "node",
     "name": {"en": "Notion", "zh": "Notion"},
     "blurb": {"en": "Search, read and write the Notion pages you share with it. Notion's own "
                     "server.",
               "zh": "搜索、阅读、编辑你分享给它的 Notion 页面。Notion 官方出品。"},
     "command": "npx", "args": ["-y", "@notionhq/notion-mcp-server"],
     "env": {"NOTION_TOKEN": "{token}"},
     "fields": [_f("token", "Integration secret", "集成密钥 (Internal Integration Secret)",
                   secret=True, placeholder="ntn_…",
                   link="https://www.notion.so/profile/integrations",
                   help_en="Create an internal integration, copy its secret, then on each page "
                           "you want shared: ••• → Connections → add it.",
                   help_zh="新建一个内部集成，复制密钥；"
                           "然后在想让助理看到的页面上点 ••• → 连接 → 加上它。")],
     "source": "https://github.com/makenotion/notion-mcp-server"},
    {"id": "github", "category": "work", "emoji": "🐙", "needs": None,
     "name": {"en": "GitHub", "zh": "GitHub"},
     "blurb": {"en": "Issues, pull requests and code. GitHub's own hosted server, nothing to "
                     "install.",
               "zh": "Issue、PR、代码都能看能管。GitHub 官方托管，不用装任何东西。"},
     "url": "https://api.githubcopilot.com/mcp/",
     "headers": {"Authorization": "Bearer {token}"},
     "fields": [_f("token", "Personal access token", "个人访问令牌 (PAT)", secret=True,
                   placeholder="github_pat_…",
                   link="https://github.com/settings/personal-access-tokens/new",
                   help_en="A fine-grained token with only the repos and permissions you want "
                           "them to have.",
                   help_zh="建一个 fine-grained 令牌，只勾你愿意给的仓库和权限。")],
     "source": "https://github.com/github/github-mcp-server"},
    {"id": "linear", "category": "work", "emoji": "📐", "needs": None,
     "name": {"en": "Linear", "zh": "Linear"},
     "blurb": {"en": "Find, create and update issues and projects. Linear's own hosted server.",
               "zh": "查找、新建、更新 issue 和项目。Linear 官方托管。"},
     "url": "https://mcp.linear.app/mcp",
     "headers": {"Authorization": "Bearer {token}"},
     "fields": [_f("token", "Personal API key", "个人 API key", secret=True,
                   placeholder="lin_api_…",
                   link="https://linear.app/settings/account/security",
                   help_en="Settings → Security & access → Personal API keys.",
                   help_zh="设置 → Security & access → Personal API keys。")],
     "source": "https://linear.app/docs/mcp"},
    {"id": "todoist", "category": "work", "emoji": "✅", "needs": "node",
     "name": {"en": "Todoist", "zh": "Todoist"},
     "blurb": {"en": "Add, move and tick off your tasks. Made by the Todoist team.",
               "zh": "添加、挪动、勾掉你的待办。Todoist 团队自己做的。"},
     "command": "npx", "args": ["-y", "@doist/todoist-mcp"],
     "env": {"TODOIST_API_KEY": "{token}"},
     "fields": [_f("token", "API token", "API 令牌", secret=True,
                   link="https://app.todoist.com/app/settings/integrations/developer",
                   help_en="Settings → Integrations → Developer → copy the API token.",
                   help_zh="设置 → 集成 → 开发者 → 复制 API 令牌。")],
     "source": "https://github.com/Doist/todoist-ai"},
    {"id": "git", "category": "work", "emoji": "🌿", "needs": "uv",
     "name": {"en": "A git repo", "zh": "一个 Git 仓库"},
     "blurb": {"en": "Read history, diffs and branches of one repository on this computer.",
               "zh": "查看这台电脑上某个仓库的历史、改动和分支。"},
     "command": "uvx", "args": ["mcp-server-git", "--repository", "{repo}"],
     "fields": [_f("repo", "Repository path", "仓库路径", placeholder="/Users/me/code/project")],
     "source": "https://github.com/modelcontextprotocol/servers/tree/main/src/git"},
    {"id": "fetch", "category": "web", "emoji": "🌐", "needs": "uv",
     "name": {"en": "Read web pages", "zh": "读网页"},
     "blurb": {"en": "Open any link and read it as clean text. No account needed.",
               "zh": "打开任何链接，读成干净的文字。不用注册。"},
     "command": "uvx", "args": ["mcp-server-fetch"], "fields": [],
     "source": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch"},
    {"id": "brave-search", "category": "web", "emoji": "🦁", "needs": "node",
     "name": {"en": "Brave Search", "zh": "Brave 搜索"},
     "blurb": {"en": "Web, news and image search. Brave's own server; the free plan is plenty "
                     "for one person.",
               "zh": "网页、新闻、图片搜索。Brave 官方出品，个人用免费额度就够。"},
     "command": "npx", "args": ["-y", "@brave/brave-search-mcp-server", "--transport", "stdio"],
     "env": {"BRAVE_API_KEY": "{token}"},
     "fields": [_f("token", "API key", "API key", secret=True,
                   link="https://api-dashboard.search.brave.com/app/keys",
                   help_en="Sign up, pick the free plan, then copy a key from the dashboard.",
                   help_zh="注册后选免费套餐，在控制台复制一个 key。")],
     "source": "https://github.com/brave/brave-search-mcp-server"},
    {"id": "browser", "category": "web", "emoji": "🧭", "needs": "node",
     "name": {"en": "A web browser", "zh": "浏览器"},
     "blurb": {"en": "Click through sites and fill in forms in a real (headless) browser. From "
                     "the Playwright team.",
               "zh": "在真的（无界面）浏览器里点网页、填表单。Playwright 团队出品。"},
     "command": "npx", "args": ["-y", "@playwright/mcp@latest", "--headless", "--isolated"],
     "fields": [],
     "note": {"en": "If it can't find a browser, run `npx playwright install chrome` once.",
              "zh": "如果提示找不到浏览器，先运行一次 `npx playwright install chrome`。"},
     "source": "https://github.com/microsoft/playwright-mcp"},
    {"id": "home-assistant", "category": "home", "emoji": "🏠", "needs": None,
     "name": {"en": "Home Assistant", "zh": "Home Assistant"},
     "blurb": {"en": "Lights, heating, sensors — whatever you've exposed to assistants. Built "
                     "into Home Assistant.",
               "zh": "灯、暖气、传感器——你开放给语音助手的设备都能用。Home Assistant 自带。"},
     "url": "{ha_url}/api/mcp",
     "headers": {"Authorization": "Bearer {token}"},
     "fields": [_f("ha_url", "Home Assistant address", "Home Assistant 地址",
                   default="http://homeassistant.local:8123",
                   help_en="First add the “Model Context Protocol Server” integration in "
                           "Settings → Devices & services.",
                   help_zh="先在 设置 → 设备与服务 里添加“Model Context Protocol Server”集成。"),
                _f("token", "Long-lived access token", "长期访问令牌", secret=True,
                   link="https://my.home-assistant.io/redirect/profile_security/",
                   help_en="Your profile → Security → Long-lived access tokens → Create.",
                   help_zh="个人资料 → 安全 → 长期访问令牌 → 创建。")],
     "source": "https://www.home-assistant.io/integrations/mcp_server/"},
    {"id": "hosted", "category": "other", "emoji": "🔗", "needs": None,
     "name": {"en": "Any hosted MCP link", "zh": "任意在线 MCP 链接"},
     "blurb": {"en": "Got an MCP server URL from somewhere else (for example an automation "
                     "service that bundles many apps)? Paste it here.",
               "zh": "从别处拿到了一个 MCP 服务器链接"
                     "（比如把很多应用打包好的自动化服务）？贴在这里。"},
     "url": "{url}", "headers": {"Authorization": "Bearer {token}"},
     "fields": [_f("url", "Server URL", "服务器链接", placeholder="https://…/mcp"),
                _f("token", "Token (if it asks for one)", "令牌（需要的话）", secret=True,
                   optional=True)],
     "note": {"en": "Only links that work with a token or no login. Ones that need a browser "
                    "sign-in (OAuth) won't connect yet.",
              "zh": "只支持用令牌或不用登录的链接；需要在浏览器里登录授权（OAuth）的暂时连不上。"},
     "source": ""},
]


def catalog_entry(entry_id: str) -> dict | None:
    return next((e for e in CATALOG if e["id"] == entry_id), None)


def build_spec(entry: dict, values: dict, vault_names: dict) -> dict:
    """The data/connectors.json ``mcp`` spec for a catalog entry.

    ``values``: field key → what the human typed (non-secrets). ``vault_names``: secret
    field key → vault entry name. Optional fields left empty drop whatever used them."""
    subs: dict[str, str | None] = {}
    for f in entry.get("fields", []):
        k = f["key"]
        if f["secret"]:
            subs[k] = f"{{{{vault:{vault_names[k]}}}}}" if k in vault_names else None
        else:
            v = str(values.get(k) or f.get("default") or "").strip()
            subs[k] = (v.rstrip("/") if k.endswith("url") else v) or None
        if subs[k] is None and not f.get("optional"):
            raise ValueError(f"missing {k}")

    def fill(s: str) -> str | None:
        for k, v in subs.items():
            if "{" + k + "}" in s:
                if v is None:
                    return None
                s = s.replace("{" + k + "}", v)
        return s

    spec: dict = {}
    if entry.get("url"):
        spec["url"] = fill(entry["url"])
        hdrs = {h: fill(v) for h, v in (entry.get("headers") or {}).items()}
        hdrs = {h: v for h, v in hdrs.items() if v is not None}
        if hdrs:
            spec["headers"] = hdrs
    else:
        spec["command"] = entry["command"]
        spec["args"] = [fill(a) for a in entry.get("args", [])]
        env = {k: fill(v) for k, v in (entry.get("env") or {}).items()}
        env = {k: v for k, v in env.items() if v is not None}
        if env:
            spec["env"] = env
    return spec
