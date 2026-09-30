"""The app directory: apps your agents can use, one tap to connect.

Only official servers (run by the app's own team) or the MCP project's reference
servers are listed, and every online address here was checked to answer as an MCP
server. Kinds:

- ``oauth``  — online, sign in on the app's own page and press Allow. Nothing to install.
- ``google`` — Google: Gmail, Calendar and Drive are built into OpenDot
               (``google_apps.py``), Maps is Google's own server. Google needs a sign-in
               client of your own, made once in Google Cloud (a few minutes;
               ``google_setup`` below), then each Google app is one tap like ``oauth``.
- ``open``   — online, no account needed.
- ``key``    — online, paste a key or token from the app's settings.
- ``local``  — runs as a small program on this computer (needs Node or uv).
- ``account`` — built into OpenDot; your user name and an app password (iCloud, CalDAV).
- ``link``   — built into OpenDot; paste a private link (a calendar's iCal address).
- ``microsoft`` — built into OpenDot (``builtin_apps.py``); sign in with a short code at
               microsoft.com/devicelogin after registering a client ID once.

``{field}`` in url/headers/args/env is filled from the entry's fields; secret fields go
to the vault and are referenced as ``{{vault:NAME}}``.
"""

from __future__ import annotations


def F(key, en, zh, *, secret=False, placeholder="", link="", help_en="", help_zh="",
      optional=False, default="", kind="text"):
    return {"key": key, "label": {"en": en, "zh": zh}, "secret": secret, "kind": kind,
            "placeholder": placeholder, "link": link, "optional": optional,
            "default": default, "help": {"en": help_en, "zh": help_zh}}


def T(en, zh):
    return {"en": en, "zh": zh}


CATEGORIES = [
    {"id": "life", "label": T("Everyday life", "日常生活")},
    {"id": "calendar", "label": T("Calendars", "日历")},
    {"id": "google", "label": T("Google & Microsoft", "Google 和微软")},
    {"id": "notes", "label": T("Notes & docs", "笔记和文档")},
    {"id": "work", "label": T("Work & projects", "工作和项目")},
    {"id": "design", "label": T("Design & sites", "设计和网站")},
    {"id": "research", "label": T("Look things up", "查资料")},
    {"id": "computer", "label": T("This computer & home", "这台电脑和家里")},
    {"id": "dev", "label": T("For developers", "开发者")},
]

# icon: a Simple Icons slug the web app bundles (else a letter tile); color: brand hex.
APPS: list[dict] = [
    # ---------------- everyday life
    {"id": "kiwi", "category": "life", "kind": "open", "icon": "", "color": "#00A991",
     "name": T("Kiwi.com flights", "Kiwi.com 机票"),
     "blurb": T("Search flights and fares, and hand you the booking link.",
                "查航班和票价，把订票链接交给你。"),
     "url": "https://mcp.kiwi.com", "docs": "https://mcp-install-instructions.alpic.cloud/servers/kiwi-com-flight-search"},
    {"id": "amap", "category": "life", "kind": "key", "icon": "", "color": "#1677FF",
     "name": T("Amap (Gaode Maps)", "高德地图"),
     "blurb": T("Places, routes, travel times and weather in China.",
                "查地点、路线、路上要多久和天气。"),
     "url": "https://mcp.amap.com/mcp?key={key}", "docs": "https://lbs.amap.com/api/mcp-server/summary",
     "fields": [F("key", "Web service key", "Web 服务 Key", secret=True,
                  link="https://console.amap.com/dev/key/app",
                  help_en="In the Amap console: create an app, add a key of type “Web service”.",
                  help_zh="在高德开放平台控制台：创建应用，添加一个“Web服务”类型的 Key。")]},
    {"id": "todoist", "category": "life", "kind": "oauth", "icon": "todoist", "color": "#E44332",
     "name": T("Todoist", "Todoist"),
     "blurb": T("Read and add to your to-do lists.", "查看和添加你的待办清单。"),
     "url": "https://ai.todoist.net/mcp", "docs": "https://developer.todoist.com/"},
    {"id": "evernote", "category": "life", "kind": "oauth", "icon": "evernote", "color": "#00A82D",
     "name": T("Evernote", "印象笔记（国际版）"),
     "blurb": T("Search and write notes.", "搜索和记笔记。"),
     "url": "https://mcp.evernote.com/mcp"},

    # ---------------- Google (one sign-in client, then one tap each)
    {"id": "google-calendar", "category": "calendar", "kind": "google", "icon": "googlecalendar",
     "color": "#4285F4", "name": T("Google Calendar", "Google 日历"),
     "blurb": T("See your schedule and add events.", "看你的日程，也能往里加日程。"),
     "builtin": "google_calendar", "api": "calendar-json.googleapis.com",
     "scopes": "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.readonly"},
    {"id": "gmail", "category": "google", "kind": "google", "icon": "gmail", "color": "#EA4335",
     "name": T("Gmail", "Gmail"),
     "blurb": T("Read and search your mail, and write drafts for you to send.",
                "读和搜索你的邮件，替你写好草稿由你来发。"),
     "builtin": "gmail", "api": "gmail.googleapis.com",
     "scopes": "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.compose"},
    {"id": "google-drive", "category": "google", "kind": "google", "icon": "googledrive",
     "color": "#1FA463", "name": T("Google Drive", "Google 云端硬盘"),
     "blurb": T("Find and read your files, and save new ones.", "查找和阅读你的文件，也能存新文件。"),
     "builtin": "google_drive", "api": "drive.googleapis.com",
     "scopes": "https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/drive.file"},
    {"id": "google-maps", "category": "google", "kind": "google", "icon": "googlemaps",
     "color": "#34A853", "name": T("Google Maps", "Google 地图"),
     "blurb": T("Places, opening hours, routes and travel times.", "查地点、营业时间、路线和路上要多久。"),
     "url": "https://mapstools.googleapis.com/mcp", "api": "mapstools.googleapis.com",
     "scopes": "https://www.googleapis.com/auth/maps-platform.mapstools"},

    # ---------------- calendars built into OpenDot (builtin_apps.py)
    {"id": "icloud-calendar", "category": "calendar", "kind": "account", "icon": "icloud", "color": "#3693F3",
     "name": T("iCloud Calendar", "iCloud 日历"),
     "blurb": T("See your iCloud calendars and add events to them.", "查看你的 iCloud 日历，也能往里加日程。"),
     "builtin": "caldav",
     "fields": [F("user", "Apple ID email", "Apple ID 邮箱", placeholder="you@icloud.com"),
                F("password", "App-specific password", "App 专用密码", secret=True,
                  placeholder="xxxx-xxxx-xxxx-xxxx", link="https://account.apple.com/account/manage",
                  help_en="account.apple.com → Sign-In and Security → App-Specific Passwords → +. "
                          "Not your usual Apple ID password.",
                  help_zh="account.apple.com → 登录与安全 → App 专用密码 → 点 +。不是你平时的 Apple ID 密码。")]},
    {"id": "calendar-link", "category": "calendar", "kind": "link", "icon": "", "color": "#FF8A3D",
     "name": T("Any calendar, by link", "任意日历（用链接）"),
     "blurb": T("Read-only: paste a calendar's private iCal link (Google, iCloud, Outlook…). No setup.",
                "只读：贴一个日历的私密 iCal 链接（Google、iCloud、Outlook……都行），不用任何设置。"),
     "builtin": "ics",
     "fields": [F("url", "iCal link", "iCal 链接", secret=True, placeholder="https://…/basic.ics",
                  help_en="Google: Calendar settings → your calendar → “Secret address in iCal format”. "
                          "iCloud: Calendar app → share next to the calendar → Public Calendar. "
                          "Outlook: Settings → Calendar → Shared calendars → Publish → ICS.",
                  help_zh="Google：日历设置 → 选中日历 → “iCal 格式的私密地址”。"
                          "iCloud：日历 App → 日历旁的共享按钮 → 公开日历。"
                          "Outlook：设置 → 日历 → 共享日历 → 发布 → ICS。")]},
    {"id": "caldav", "category": "calendar", "kind": "account", "icon": "", "color": "#5B6CFF",
     "name": T("Other calendar account (CalDAV)", "其他日历账号（CalDAV）"),
     "blurb": T("Fastmail, Nextcloud, Zoho and others: see your calendars and add events.",
                "Fastmail、Nextcloud、Zoho 等：查看日历，也能加日程。"),
     "builtin": "caldav",
     "fields": [F("url", "CalDAV address", "CalDAV 地址", placeholder="https://caldav.fastmail.com",
                  help_en="Your provider's help pages list it (search “CalDAV”).",
                  help_zh="在你的服务商帮助页里搜“CalDAV”就能找到。"),
                F("user", "User name", "用户名"),
                F("password", "Password (an app password if the service offers one)",
                  "密码（服务商提供 App 密码的话就用 App 密码）", secret=True)]},

    # ---------------- Microsoft (built into OpenDot; one client ID of your own)
    {"id": "microsoft", "category": "google", "kind": "microsoft", "icon": "", "color": "#0078D4",
     "name": T("Outlook & OneDrive", "Outlook 和 OneDrive"),
     "blurb": T("Outlook mail and calendar, and your OneDrive files. Mail is drafts only: you send.",
                "Outlook 邮件和日历，还有你的 OneDrive 文件。邮件只写草稿，由你来发。"),
     "builtin": "microsoft"},

    # ---------------- notes & docs
    {"id": "notion", "category": "notes", "kind": "oauth", "icon": "notion", "color": "#000000",
     "name": T("Notion", "Notion"),
     "blurb": T("Search, read and write your pages and databases.", "搜索、阅读和编辑你的页面和数据库。"),
     "url": "https://mcp.notion.com/mcp", "docs": "https://developers.notion.com/docs/mcp"},
    {"id": "feishu", "category": "notes", "kind": "oauth", "icon": "", "color": "#3370FF",
     "name": T("Feishu Docs", "飞书文档"),
     "blurb": T("Search, read and create Feishu docs.", "搜索、阅读和新建飞书文档。"),
     "url": "https://mcp.feishu.cn/mcp", "docs": "https://open.feishu.cn/", "local_only": True},
    {"id": "confluence-jira", "category": "notes", "kind": "oauth", "icon": "atlassian",
     "color": "#0052CC", "name": T("Jira & Confluence", "Jira 和 Confluence"),
     "blurb": T("Find and update issues and wiki pages.", "查找和更新工单与知识库页面。"),
     "url": "https://mcp.atlassian.com/v1/mcp", "docs": "https://www.atlassian.com/platform/remote-mcp-server"},
    {"id": "airtable", "category": "notes", "kind": "oauth", "icon": "airtable", "color": "#18BFFF",
     "name": T("Airtable", "Airtable"),
     "blurb": T("Read and update your bases.", "读取和更新你的表格。"),
     "url": "https://mcp.airtable.com/mcp"},
    {"id": "granola", "category": "notes", "kind": "oauth", "icon": "", "color": "#2F6E3B",
     "name": T("Granola", "Granola"),
     "blurb": T("Your meeting notes and what was agreed.", "你的会议记录和会上定下的事。"),
     "url": "https://mcp.granola.ai/mcp"},
    {"id": "fireflies", "category": "notes", "kind": "oauth", "icon": "", "color": "#7B3FE4",
     "name": T("Fireflies", "Fireflies"),
     "blurb": T("Meeting transcripts and summaries.", "会议转写和纪要。"),
     "url": "https://api.fireflies.ai/mcp"},

    # ---------------- work & projects
    {"id": "linear", "category": "work", "kind": "oauth", "icon": "linear", "color": "#5E6AD2",
     "name": T("Linear", "Linear"),
     "blurb": T("Find, create and update issues.", "查找、新建和更新任务。"),
     "url": "https://mcp.linear.app/mcp", "docs": "https://linear.app/docs/mcp"},
    {"id": "asana", "category": "work", "kind": "oauth", "icon": "asana", "color": "#F06A6A",
     "name": T("Asana", "Asana"),
     "blurb": T("Tasks, projects and who's doing what.", "任务、项目和分工。"),
     "url": "https://mcp.asana.com/sse", "transport": "sse",
     "docs": "https://developers.asana.com/docs/using-asanas-mcp-server"},
    {"id": "monday", "category": "work", "kind": "oauth", "icon": "", "color": "#FF3D57",
     "name": T("monday.com", "monday.com"),
     "blurb": T("Boards, items and updates.", "看板、事项和进展。"),
     "url": "https://mcp.monday.com/mcp"},
    {"id": "clickup", "category": "work", "kind": "oauth", "icon": "clickup", "color": "#7B68EE",
     "name": T("ClickUp", "ClickUp"),
     "blurb": T("Tasks, docs and time.", "任务、文档和时间。"),
     "url": "https://mcp.clickup.com/mcp"},
    {"id": "intercom", "category": "work", "kind": "oauth", "icon": "intercom", "color": "#1F8DED",
     "name": T("Intercom", "Intercom"),
     "blurb": T("Customer conversations and contacts.", "客户对话和联系人。"),
     "url": "https://mcp.intercom.com/mcp"},
    {"id": "zapier", "category": "work", "kind": "oauth", "icon": "zapier", "color": "#FF4F00",
     "name": T("Zapier", "Zapier"),
     "blurb": T("Thousands more apps, through the actions you set up in Zapier.",
                "通过你在 Zapier 里设好的动作，连上更多应用。"),
     "url": "https://mcp.zapier.com/api/mcp/mcp", "docs": "https://zapier.com/mcp"},
    {"id": "github", "category": "work", "kind": "key", "icon": "github", "color": "#181717",
     "name": T("GitHub", "GitHub"),
     "blurb": T("Repositories, issues and pull requests.", "代码仓库、Issue 和 PR。"),
     "url": "https://api.githubcopilot.com/mcp/", "headers": {"Authorization": "Bearer {token}"},
     "docs": "https://github.com/github/github-mcp-server",
     "fields": [F("token", "Personal access token", "个人访问令牌", secret=True,
                  link="https://github.com/settings/personal-access-tokens/new",
                  help_en="A fine-grained token with only the repositories and permissions it needs.",
                  help_zh="建一个细粒度令牌，只勾选需要的仓库和权限。")]},

    # ---------------- design & sites
    {"id": "canva", "category": "design", "kind": "oauth", "icon": "", "color": "#00C4CC",
     "name": T("Canva", "Canva 可画"),
     "blurb": T("Find, create and export designs.", "查找、制作和导出设计。"),
     "url": "https://mcp.canva.com/mcp"},
    {"id": "miro", "category": "design", "kind": "oauth", "icon": "miro", "color": "#FFD02F",
     "name": T("Miro", "Miro"),
     "blurb": T("Read and add to your boards.", "读取和添加白板内容。"),
     "url": "https://mcp.miro.com/"},
    {"id": "webflow", "category": "design", "kind": "oauth", "icon": "webflow", "color": "#146EF5",
     "name": T("Webflow", "Webflow"),
     "blurb": T("Your sites, pages and CMS.", "你的网站、页面和内容。"),
     "url": "https://mcp.webflow.com/mcp"},
    {"id": "wix", "category": "design", "kind": "oauth", "icon": "wix", "color": "#0C6EFC",
     "name": T("Wix", "Wix"),
     "blurb": T("Your Wix sites and their content.", "你的 Wix 网站和内容。"),
     "url": "https://mcp.wix.com/mcp"},

    # ---------------- look things up
    {"id": "exa", "category": "research", "kind": "open", "icon": "", "color": "#1F40ED",
     "name": T("Exa search", "Exa 搜索"),
     "blurb": T("Deeper web search that reads the pages.", "更深入的网页搜索，会读网页内容。"),
     "url": "https://mcp.exa.ai/mcp"},
    {"id": "tavily", "category": "research", "kind": "oauth", "icon": "", "color": "#2E5BFF",
     "name": T("Tavily search", "Tavily 搜索"),
     "blurb": T("Web search built for agents.", "为助理设计的网页搜索。"),
     "url": "https://mcp.tavily.com/mcp"},
    {"id": "brave-search", "category": "research", "kind": "local", "icon": "brave", "color": "#FB542B",
     "needs": "node", "name": T("Brave Search", "Brave 搜索"),
     "blurb": T("Private web, news and image search.", "注重隐私的网页、新闻和图片搜索。"),
     "command": "npx", "args": ["-y", "@brave/brave-search-mcp-server", "--transport", "stdio"],
     "env": {"BRAVE_API_KEY": "{key}"},
     "fields": [F("key", "API key", "API Key", secret=True, link="https://api-dashboard.search.brave.com/app/keys")]},
    {"id": "huggingface", "category": "research", "kind": "open", "icon": "huggingface",
     "color": "#FFD21E", "name": T("Hugging Face", "Hugging Face"),
     "blurb": T("Look up AI models, datasets and papers.", "查 AI 模型、数据集和论文。"),
     "url": "https://huggingface.co/mcp"},

    # ---------------- this computer & home
    {"id": "files", "category": "computer", "kind": "local", "icon": "", "color": "#8B5CF6",
     "needs": "node", "name": T("A folder on this computer", "这台电脑上的一个文件夹"),
     "blurb": T("Read and write files in one folder you choose, and nowhere else.",
                "只在你选的那个文件夹里读写文件。"),
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "{folder}"],
     "fields": [F("folder", "Folder", "文件夹", placeholder="~/Documents/Shared with agents",
                  kind="path")]},
    {"id": "obsidian", "category": "computer", "kind": "local", "icon": "obsidian", "color": "#7C3AED",
     "needs": "node", "name": T("Obsidian vault", "Obsidian 笔记库"),
     "blurb": T("Read and write the notes in your vault.", "读写你笔记库里的笔记。"),
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "{folder}"],
     "fields": [F("folder", "Vault folder", "笔记库文件夹", placeholder="~/Obsidian/My vault", kind="path")]},
    {"id": "home-assistant", "category": "computer", "kind": "key", "icon": "homeassistant",
     "color": "#18BCF2", "name": T("Home Assistant", "Home Assistant"),
     "blurb": T("Lights, heating and the devices in your home.", "家里的灯、暖气和各种设备。"),
     "url": "{ha_url}/api/mcp", "headers": {"Authorization": "Bearer {token}"},
     "docs": "https://www.home-assistant.io/integrations/mcp_server/",
     "fields": [F("ha_url", "Home Assistant address", "Home Assistant 地址",
                  placeholder="http://homeassistant.local:8123"),
                F("token", "Long-lived access token", "长期访问令牌", secret=True,
                  help_en="Your profile → Security → Long-lived access tokens. Turn on the MCP Server integration first.",
                  help_zh="个人资料 → 安全 → 长期访问令牌。先在集成里启用 MCP Server。")]},

    # ---------------- developers
    {"id": "sentry", "category": "dev", "kind": "oauth", "icon": "sentry", "color": "#362D59",
     "name": T("Sentry", "Sentry"), "blurb": T("Errors and what caused them.", "报错和原因。"),
     "url": "https://mcp.sentry.dev/mcp"},
    {"id": "vercel", "category": "dev", "kind": "oauth", "icon": "vercel", "color": "#000000",
     "name": T("Vercel", "Vercel"), "blurb": T("Deployments, logs and projects.", "部署、日志和项目。"),
     "url": "https://mcp.vercel.com"},
    {"id": "netlify", "category": "dev", "kind": "oauth", "icon": "netlify", "color": "#00C7B7",
     "name": T("Netlify", "Netlify"), "blurb": T("Sites, deploys and forms.", "网站、部署和表单。"),
     "url": "https://netlify-mcp.netlify.app/mcp"},
    {"id": "supabase", "category": "dev", "kind": "oauth", "icon": "supabase", "color": "#3FCF8E",
     "name": T("Supabase", "Supabase"), "blurb": T("Your projects and databases.", "你的项目和数据库。"),
     "url": "https://mcp.supabase.com/mcp"},
    {"id": "context7", "category": "dev", "kind": "open", "icon": "", "color": "#0F766E",
     "name": T("Context7", "Context7"),
     "blurb": T("Up-to-date docs for code libraries.", "代码库的最新文档。"),
     "url": "https://mcp.context7.com/mcp"},
    {"id": "deepwiki", "category": "dev", "kind": "open", "icon": "", "color": "#2563EB",
     "name": T("DeepWiki", "DeepWiki"),
     "blurb": T("Ask questions about any public GitHub repository.", "问任何公开 GitHub 仓库的问题。"),
     "url": "https://mcp.deepwiki.com/mcp"},
    {"id": "git", "category": "dev", "kind": "local", "icon": "git", "color": "#F03C2E", "needs": "uv",
     "name": T("A Git repository", "一个 Git 仓库"),
     "blurb": T("History, diffs and commits in one repository.", "一个仓库的历史、改动和提交。"),
     "command": "uvx", "args": ["mcp-server-git", "--repository", "{repo}"],
     "fields": [F("repo", "Repository folder", "仓库文件夹", placeholder="~/code/my-project", kind="path")]},
]

GOOGLE_SETUP = {
    "redirect_path": "/oauth/callback",
    "console": "https://console.cloud.google.com/",
    "steps": [
        T("Open Google Cloud and create a project (any name, e.g. “OpenDot”).",
          "打开 Google Cloud，新建一个项目（名字随意，比如“OpenDot”）。"),
        T("Turn on the APIs for the Google apps you want (links below).",
          "为你想用的 Google 应用开启对应的 API（链接在下面）。"),
        T("Google Auth Platform → Branding: fill in an app name and your email. Audience: “External”, "
          "then add yourself under Test users (or press Publish so you stay signed in for longer than 7 days).",
          "Google Auth Platform → 品牌：填应用名和你的邮箱。受众：选“外部”，把你自己加为测试用户"
          "（或者点“发布”，这样登录状态能保持超过 7 天）。"),
        T("Clients → Create client → “Web application”. Under Authorised redirect URIs add the address "
          "shown below, then Create.",
          "客户端 → 创建客户端 → 类型选“Web 应用”。在“已获授权的重定向 URI”里加上下面显示的地址，然后创建。"),
        T("Copy the Client ID and Client secret here.", "把客户端 ID 和客户端密钥复制到这里。"),
    ],
}


MICROSOFT_SETUP = {
    "portal": "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
    "steps": [
        T("Open Microsoft Entra (sign in with the Microsoft account you'll connect) → App registrations → New registration.",
          "打开 Microsoft Entra（用你要连接的微软账号登录）→ 应用注册 → 新注册。"),
        T("Name it “OpenDot”. Supported account types: “Accounts in any organizational directory and personal "
          "Microsoft accounts”. Leave the redirect URI empty and press Register.",
          "名字填“OpenDot”。支持的账户类型选“任何组织目录中的帐户和个人 Microsoft 帐户”。重定向 URI 留空，点“注册”。"),
        T("Authentication → Advanced settings → “Allow public client flows”: Yes → Save.",
          "身份验证 → 高级设置 → “允许公共客户端流”：是 → 保存。"),
        T("Copy the “Application (client) ID” from the Overview page here. No secret is needed.",
          "从“概述”页复制“应用程序(客户端) ID”到这里。不需要密钥。"),
    ],
}


def entry(app_id: str) -> dict | None:
    return next((a for a in APPS if a["id"] == app_id), None)


def build_spec(app: dict, values: dict, vault_names: dict) -> dict:
    """The data/connectors.json ``mcp`` spec for a directory entry.

    ``values``: field key → what you typed (non-secrets). ``vault_names``: secret field
    key → vault entry name. Optional fields left empty drop whatever used them."""
    import os
    subs: dict[str, str | None] = {}
    for f in app.get("fields", []):
        k = f["key"]
        if f["secret"]:
            subs[k] = f"{{{{vault:{vault_names[k]}}}}}" if k in vault_names else None
        else:
            v = str(values.get(k) or f.get("default") or "").strip()
            if f.get("kind") == "path" and v:
                v = os.path.expanduser(v)
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

    spec: dict = {"app": app["id"], "label": app["name"]["en"]}
    if app.get("builtin"):
        spec["builtin"] = app["builtin"]
        if app.get("scopes"):
            spec["scopes"] = app["scopes"]
        settings = {k: v for k, v in subs.items() if v is not None}
        if settings:
            spec["settings"] = settings
    elif app.get("url"):
        spec["url"] = fill(app["url"])
        hdrs = {h: fill(v) for h, v in (app.get("headers") or {}).items()}
        hdrs = {h: v for h, v in hdrs.items() if v is not None}
        if hdrs:
            spec["headers"] = hdrs
        if app.get("transport"):
            spec["transport"] = app["transport"]
        if app["kind"] in ("oauth", "google"):
            spec["auth"] = "oauth"
        if app.get("scopes"):
            spec["scopes"] = app["scopes"]
        if app["kind"] == "google":
            spec["oauth_client"] = {"client_id": "{{vault:GOOGLE_CLIENT_ID}}",
                                    "client_secret": "{{vault:GOOGLE_CLIENT_SECRET}}"}
    else:
        spec["command"] = app["command"]
        spec["args"] = [fill(a) for a in app.get("args", [])]
        env = {k: fill(v) for k, v in (app.get("env") or {}).items()}
        env = {k: v for k, v in env.items() if v is not None}
        if env:
            spec["env"] = env
    return spec
