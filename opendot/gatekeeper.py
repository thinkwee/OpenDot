"""Gatekeeper — the propose/permit gate every tool call passes through.

Every call gets one of three answers:
  allow → run it       ask → pause, show an approval card, wait for the human
  deny  → refuse with a reason the agent can read
Secrets live in a vault and are referenced as ``{{vault:NAME}}``; the model never
sees the values, they are injected at execution time and redacted from output.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time

from .bus import bus
from .config import ROOT, settings
from .db import db, new_id
from .durable import args_hash

# default decision per tool (overridable per agent from the UI)
DEFAULT_POLICY = {
    "shell": "allow", "python": "allow", "read_file": "allow", "write_file": "allow",
    "list_files": "allow", "web_search": "allow", "web_fetch": "allow", "browser": "allow",
    "remember": "allow", "forget": "allow", "say": "allow", "update_profile": "allow", "notify": "allow",
    "publish_page": "allow", "schedule": "allow", "list_automations": "allow",
    "cancel_automation": "allow", "offer_choices": "allow", "handoff": "allow", "delegate": "allow",
    "send_email": "ask",
}
MCP_DEFAULT = "ask"

SHELL_DENY = [
    (r"\bsudo\b|\bsu\s+-", "needs root"),
    (r"rm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME)(\s|$)", "would wipe a whole tree"),
    (r"\bmkfs|\bdd\s+.*of=/dev|:\(\)\s*\{", "destructive system command"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "power control"),
]
SHELL_ASK = [
    (r"(curl|wget)[^|]*\|\s*(ba)?sh", "pipes a remote script into a shell"),
    (r"\b(ssh|scp|rsync\s+.*:|sftp)\b", "connects to another machine"),
    (r"\bgit\s+push\b", "publishes code"),
    (r"(curl|http)\S*\s.*(-X\s*(POST|PUT|DELETE)|--data|-d\s)", "sends data to the internet"),
    (r"\bcrontab\b|\bnohup\b|\bsystemctl\b", "starts something that outlives the task"),
]
BROWSER_ASK = re.compile(r"(pay|checkout|purchase|buy now|place order|confirm order|submit)",
                         re.I)

# path guard for shell/python: what an agent may touch outside its own home
SECRETS_RE = re.compile(  # your personal keys, cloud logins, browser profiles
    r"(?:^|[\s/~'\"])\.(ssh|aws|gnupg|netrc|kube|mozilla)\b"
    r"|\.config/(gcloud|gh|google-chrome|chromium)|Library/(Keychains|Cookies)"
    r"|Application(\\ | )Support/(Google|Firefox|BraveSoftware|Chromium|Microsoft Edge)")
CDP_RE = re.compile(r"--remote-debugging|devtools/(browser|page)|DevToolsActivePort"
                    r"|/json/(version|list|new)")
LOOPBACK_PORT_RE = re.compile(r"(?:localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|::1\]?)\s*:\s*(\d{2,5})")
DELETE_RE = re.compile(r"\b(rm|rmdir|unlink)\b|shutil\.rmtree|os\.(remove|unlink|rmdir|removedirs)"
                       r"|\.unlink\(|\.rmdir\(|-delete\b")
SAFE_ROOTS = ["/tmp", "/private/tmp", "/var/tmp", "/var/folders", "/private/var/folders",
              "/etc", "/private/etc", "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64",
              "/opt", "/nix", "/snap", "/System", "/dev/null", "/dev/zero", "/dev/random",
              "/dev/urandom", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/fd",
              "/proc/self", "/proc/cpuinfo", "/proc/meminfo", "/proc/version",
              sys.prefix, sys.base_prefix]
PRIVATE_DATA = "reads OpenDot's private data (secrets or other agents)"
PERSONAL = "touches your personal keys or logins"
CDP = "talks to a browser's remote-control port"
OUTSIDE = "reaches outside its computer"
DELETES_OUTSIDE = "deletes something outside its computer"

_waiters: dict[str, asyncio.Future] = {}


# ---------------- vault ----------------
# Secrets (passwords, API keys, app sign-ins) are encrypted at rest in data/vault.enc.
# The key lives in the OS keychain when there is one (macOS Keychain, Windows Credential
# Locker, a Linux secret service) and otherwise in data/vault.key, readable only by you.
# An old plaintext data/vault.json is moved over on first use.
_vault_cache: dict[str, str] | None = None
_vault_key: bytes | None = None


def _vault_path():
    return settings.DATA_DIR / "vault.enc"


def _keychain_id() -> str:
    return f"vault:{settings.DATA_DIR.resolve()}"


def _key() -> bytes:
    global _vault_key
    if _vault_key:
        return _vault_key
    from cryptography.fernet import Fernet
    kfile = settings.DATA_DIR / "vault.key"
    keychain = None
    try:  # the OS keychain first
        import keyring
        stored = keyring.get_password("OpenDot", _keychain_id())
        keychain = keyring
    except Exception:  # none here (headless Linux, a container, a locked keychain…)
        stored = None
    if stored:
        _vault_key = stored.encode()
    elif kfile.exists():
        _vault_key = kfile.read_bytes().strip()
    elif _vault_path().exists():
        raise RuntimeError("the vault's key isn't available (is the keychain locked?)")
    else:  # first run: make a key
        new = Fernet.generate_key()
        try:
            keychain.set_password("OpenDot", _keychain_id(), new.decode())
            ok = keychain.get_password("OpenDot", _keychain_id()) == new.decode()
        except Exception:
            ok = False
        if not ok:
            kfile.write_bytes(new)
            kfile.chmod(0o600)
        _vault_key = new
    return _vault_key


def vault_all() -> dict[str, str]:
    global _vault_cache
    if _vault_cache is not None:
        return dict(_vault_cache)
    from cryptography.fernet import Fernet, InvalidToken
    p, legacy = _vault_path(), settings.DATA_DIR / "vault.json"
    data: dict[str, str] = {}
    if p.exists():
        try:
            data = json.loads(Fernet(_key()).decrypt(p.read_bytes()))
        except (InvalidToken, ValueError) as e:
            raise RuntimeError("the vault can't be opened with this computer's key") from e
    if legacy.exists():  # move an old plaintext vault into the encrypted one
        data = {**json.loads(legacy.read_text() or "{}"), **data}
        _vault_write(data)
        legacy.unlink()
    _vault_cache = data
    return dict(data)


def _vault_write(data: dict[str, str]) -> None:
    from cryptography.fernet import Fernet
    global _vault_cache
    p = _vault_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(Fernet(_key()).encrypt(json.dumps(data).encode()))
    tmp.chmod(0o600)
    tmp.replace(p)
    _vault_cache = dict(data)


def vault_set(name: str, value: str | None) -> None:
    v = vault_all()
    if value is None:
        v.pop(name, None)
    else:
        v[name] = value
    _vault_write(v)


def vault_reset_cache() -> None:
    """For tests that point DATA_DIR somewhere new."""
    global _vault_cache, _vault_key
    _vault_cache = _vault_key = None


def inject_secrets(args: dict) -> dict:
    vault = vault_all()
    raw = json.dumps(args)
    raw = re.sub(r"\{\{vault:([A-Za-z0-9_\-]+)\}\}",
                 lambda m: json.dumps(vault.get(m.group(1), ""))[1:-1], raw)
    return json.loads(raw)


def redact(text: str) -> str:
    for name, val in vault_all().items():
        if val and len(val) >= 4:
            text = text.replace(val, f"{{{{vault:{name}}}}}")
    return text


# ---------------- policy ----------------
def policy_for(agent_id: str) -> dict:
    return {**DEFAULT_POLICY, **db.kv_get(f"policy:{agent_id}", {})}


def set_policy(agent_id: str, tool: str, decision: str) -> None:
    p = db.kv_get(f"policy:{agent_id}", {})
    p[tool] = decision
    db.kv_set(f"policy:{agent_id}", p)


URL_RE = re.compile(r"https?://([^/\s'\"]+)")
DOMAIN_TOOLS = {"browser", "web_fetch", "web_search"}


def _extract_domain(tool: str, args: dict) -> str | None:
    """Best-effort host for the 'always allow this domain' approval scope: the
    ``url`` arg for browser/web tools, or the first URL seen in a shell/curl command."""
    url = args.get("url") or ""
    if not url and tool in ("shell", "python"):
        url = args.get("command") or args.get("code") or ""
    m = URL_RE.search(url)
    return m.group(1).lower() if m else None


# ---------------- approval scopes ----------------
def _session_key(agent_id: str) -> str:
    return f"session_approve:{agent_id}"


def _domain_key(agent_id: str) -> str:
    return f"domain_approve:{agent_id}"


def approve_session(agent_id: str, thread_id: str, tool: str, hours: float = 2) -> None:
    """'This session': allow ``tool`` in ``thread_id`` without asking again for a while."""
    data = db.kv_get(_session_key(agent_id), {})
    data[f"{thread_id}:{tool}"] = time.time() + hours * 3600
    db.kv_set(_session_key(agent_id), data)


def session_allowed(agent_id: str, thread_id: str, tool: str) -> bool:
    exp = db.kv_get(_session_key(agent_id), {}).get(f"{thread_id}:{tool}")
    return bool(exp and exp > time.time())


def approve_domain(agent_id: str, domain: str) -> None:
    data = db.kv_get(_domain_key(agent_id), {})
    data[domain] = True
    db.kv_set(_domain_key(agent_id), data)


def domain_allowed(agent_id: str, domain: str | None) -> bool:
    return bool(domain and db.kv_get(_domain_key(agent_id), {}).get(domain))


def _under(paths: tuple[str, str], roots) -> bool:
    """Is either spelling of the path (as written / symlinks resolved) inside a root?"""
    for r in roots:
        for root in {str(r), os.path.realpath(str(r))}:
            if any(p == root or p.startswith(root.rstrip("/") + "/") for p in paths):
                return True
    return False


def _cwd(agent_id: str, home: str) -> str:
    from .computer import term
    s = term._sessions.get(agent_id)
    return s.cwd if s is not None and s._alive and s.cwd else home


def check_paths(agent_id: str, cmd: str) -> tuple[str, str] | None:
    """Path guard for shell/python: deny secrets, other agents' data and browser
    remote-control ports; ask before touching or deleting things outside the agent's
    home. A best-effort tripwire over the command text, not a sandbox."""
    if re.search(r"vault\.(json|enc|key|tmp)", cmd):
        return "deny", PRIVATE_DATA
    if SECRETS_RE.search(cmd):
        return "deny", PERSONAL
    from .computer.core import CDP_PORT_BASE, CDP_PORT_RANGE
    ports = [int(m) for m in LOOPBACK_PORT_RE.findall(cmd)]
    if CDP_RE.search(cmd) or any(CDP_PORT_BASE <= p < CDP_PORT_BASE + CDP_PORT_RANGE
                                 or p == 9222 for p in ports):
        return "deny", CDP
    home = str(settings.DATA_DIR / "agents" / agent_id / "home")
    mine = [home, settings.DATA_DIR / "shared"]
    private = [settings.DATA_DIR, ROOT / ".env"]
    cwd = _cwd(agent_id, home)
    text = re.sub(r"\$\{?HOME\}?", home, cmd)
    paths = [] if re.match(r"\s*cd\b", cmd) else [cwd]  # where the shell is sitting now
    for tok in re.split(r"[\s'\"`;|&<>(),=:\[\]{}]+", text):
        if tok == "~" or tok.startswith("~/"):
            tok = home + tok[1:]
        if tok.startswith("//"):  # the rest of a URL ("https" + "//host/…")
            continue
        if tok.startswith("/"):
            top = tok.strip("/").split("/")[0]
            if top and os.path.exists("/" + top):  # skip "/api/v1"-style strings
                paths.append(tok)
        elif "/" in tok or tok == "..":
            paths.append(os.path.join(cwd, tok))
    deleting = bool(DELETE_RE.search(cmd))
    verdict = None
    for p in paths:
        both = (os.path.normpath(p), os.path.realpath(p))
        if _under(both, mine):
            continue
        if _under(both, private):
            return "deny", PRIVATE_DATA
        if deleting and not p.startswith("/dev/"):  # "2>/dev/null" isn't a target
            verdict = ("ask", DELETES_OUTSIDE)
        elif verdict is None and not _under(both, SAFE_ROOTS):
            verdict = ("ask", OUTSIDE)
    return verdict


# ---------------- yours to do ----------------
# Things an agent never does for you, even with your OK: it gets them ready and hands
# you the link. Checked only on actions that *do* something (clicks, forms, sends),
# never on reading — an email about a password reset is fine to read.
YOURS = {
    "password": (r"(change|reset|update|forgot|set|new)[\s_\-]*(your\s+|my\s+|a\s+)?pass(word|code)"
                 r"|pass(word|code)[\s_\-]*(reset|change|update)"
                 r"|修改密码|重置密码|更改密码|找回密码|忘记密码|设置新密码",
                 "changing a password", "改密码"),
    "2fa": (r"two[\s\-]*(factor|step)|\b2fa\b|\bmfa\b|(recovery|backup)[\s_\-]*(code|key|phrase)"
            r"|seed[\s_\-]*phrase|双重验证|两步验证|二次验证|双因素|恢复码|备用码|助记词",
            "two-factor or recovery codes", "两步验证或恢复码"),
    "account": (r"(delete|close|deactivate|terminate|remove)[\s_\-]*(my\s+|your\s+|the\s+|this\s+)?"
                r"account|注销(账号|账户|帐号|帐户)|(删除|关闭)(账号|账户|帐号|帐户)",
                "deleting an account", "注销账号"),
    "money": (r"\b(pay(\s+now)?|make\s+(a\s+)?payment|submit\s+payment|payment\s+details|checkout"
              r"|purchase(?!\s+(history|record))|buy\s+now|place\s+(the\s+|my\s+)?order|confirm\s+(and\s+pay|"
              r"order|purchase)|complete\s+(purchase|order)|send\s+money|transfer\s+(funds|money)"
              r"|wire\s+transfer|withdraw|top[\s\-]?up|card[\s_\-]*(number|num)|cc[\s_\-]*(number|"
              r"num)|cvv|cvc|iban|routing\s+number|(buy|sell|send|swap)\s+(crypto|bitcoin|btc|eth|usdt)"
              r"|donate)\b|付款|立即支付|确认支付|立即购买|提交订单|确认订单|下单|转账|汇款|提现|充值"
              r"|银行卡号|信用卡号|安全码|打赏",
              "moving money", "付钱转账"),
    "legal": (r"\b(e-?sign|sign)[\s_\-]+(the\s+|this\s+)?(contract|agreement|lease|nda)|docusign"
              r"|adobe\s*sign|hellosign|签署|签合同|签约|电子签名",
              "signing a contract", "签合同"),
    "id": (r"\b(passport|driver'?s?[\s_\-]+licen[cs]e|national[\s_\-]+id|id[\s_\-]+card|social"
           r"[\s_\-]+security|ssn|tax[\s_\-]+id)\b.{0,30}(upload|submit|number|scan|photo|verif)"
           r"|(upload|submit|verify)[\s_\-]+(your\s+|my\s+)?(id|identity|passport)\b"
           r"|身份证|护照号|上传证件|实名认证|社保号",
           "submitting a government ID", "提交证件"),
}
YOURS_RE = {k: re.compile(v[0], re.I) for k, v in YOURS.items()}
# a URL path that is the sensitive thing itself (checked when a command sends data)
YOURS_PATH = re.compile(r"/(reset[-_]?password|password[-_/]?(reset|change)|change[-_]?password"
                        r"|2fa|mfa|payments?|transfers?|payouts?|checkout|withdraw"
                        r"|account/(delete|close))\b", re.I)
SENDS_RE = re.compile(r"-X\s*(POST|PUT|PATCH|DELETE)|--data|\s-d\s|\s-F\s"
                      r"|requests\.(post|put|patch)|httpx\.(post|put|patch)"
                      r"|method=['\"](POST|PUT)", re.I)
# secrets and money in a message you'd send as the human
SHARE_RE = re.compile(r"\b(cvv|cvc|security\s+code)\b\D{0,12}\d{3,4}|(recovery|backup)\s+codes?"
                      r"|pass(word|code)\s+(is|:)|密码(是|：|:)|恢复码|验证码(是|：|:)"
                      r"|\b(wire|transfer|pay)\b[^.\n]{0,20}([$€£¥]\s?\d|\d[\d,.]*\s?(usd|eur|gbp|"
                      r"rmb|cny|dollars|euros|pounds))|(转账|汇款|打款)[^。\n]{0,10}\d", re.I)
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
MCP_YOURS = re.compile(r"pay|charge|refund|payout|transfer|withdraw|password|2fa|mfa|"
                       r"delete_account|close_account|envelope|sign_document", re.I)


def _luhn(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()][::-1]
    return 13 <= len(d) <= 19 and sum(x if i % 2 == 0 else (x * 2 - 9 if x > 4 else x * 2)
                                      for i, x in enumerate(d)) % 10 == 0


def _match_yours(text: str) -> str | None:
    for key, rx in YOURS_RE.items():
        if rx.search(text):
            return key
    return None


def browser_label(a: dict) -> str:
    """What a browser click or typing is aimed at: the element's words, text or selector."""
    return f"{a.get('element', '')} {a.get('text', '')} {a.get('selector', '')}".strip()


def yours_to_do(tool: str, args: dict) -> str | None:
    """Which never-for-you category an action falls in (password, 2fa, account, money,
    legal, id), or None. Reading never counts, only doing."""
    a = args or {}
    if tool == "browser":
        act = a.get("action")
        if act in ("click", "select"):
            return _match_yours(browser_label(a))
        if act == "type":  # the field, not what's typed (a search box may say "password")
            return _match_yours(f"{a.get('element', '')} {a.get('selector', '')}")
        return None
    if tool == "browse_task":
        task = str(a.get("task", ""))
        return _match_yours(task) or ("money" if YOURS_PATH.search(task) else None)
    if tool in ("shell", "python", "device_shell"):
        cmd = str(a.get("command") or a.get("code") or "")
        if URL_RE.search(cmd) and SENDS_RE.search(cmd):
            m = YOURS_PATH.search(cmd)
            if m:
                path = m.group(0).replace("-", " ").replace("_", " ")
                return _match_yours(path) or ("account" if "account" in path.lower() else "money")
        return None
    if tool in ("send_email", "send_sms") or tool.startswith("mcp__"):
        if tool.startswith("mcp__") and MCP_YOURS.search(tool.split("__", 2)[-1]):
            return _match_yours(tool.replace("_", " ")) or "money"
        text = " ".join(str(a.get(k, "")) for k in ("subject", "body", "text", "message"))
        if tool.startswith("mcp__"):
            text = json.dumps(a, ensure_ascii=False)
        if any(_luhn(m.group(0)) for m in CARD_RE.finditer(text)):
            return "money"
        m = SHARE_RE.search(text)
        if m:
            return "2fa" if re.search(r"code|码", m.group(0), re.I) else \
                "password" if re.search(r"pass|密码", m.group(0), re.I) else "money"
    return None


LINK_RE = re.compile(r"https?://[^\s'\"<>)]+")


def _yours_link(agent_id: str, tool: str, args: dict) -> str:
    a = args or {}
    m = LINK_RE.search(str(a.get("url") or a.get("task") or a.get("command") or ""))
    if m:
        return m.group(0)
    try:  # where the agent's browser is sitting now
        from .computer.core import _computers
        c = _computers.get(agent_id)
        for t in (c.open_tabs() if c else []):
            if t["owner_id"] == agent_id and t["url"].startswith("http"):
                return t["url"]
    except Exception:
        pass
    return ""


def yours_reason(agent_id: str, tool: str, args: dict, key: str) -> str:
    from .ext.lang import tr
    _, en, zh = YOURS[key]
    link = _yours_link(agent_id, tool, args)
    human = tr(f"This one's yours to do yourself ({en}) — here's the link: {link}",
               f"这件事得你亲自来（{zh}），链接在这：{link}") if link else \
        tr(f"This one's yours to do yourself ({en}).", f"这件事得你亲自来（{zh}）。")
    return (f"{human} Tell the human that in your own words, hand over the link and "
            f"everything you prepared; don't look for another way around it.")


# ---------------- look, don't touch ----------------
# background check-ins (heartbeat / research) may only read, then propose
READ_ONLY = {
    "web_search", "web_fetch", "read_file", "list_files", "list_emails",
    "read_email", "iphone_status", "iphone_health", "iphone_calendar", "iphone_reminders",
    "iphone_location", "list_automations", "list_goals", "read_skill", "read_skill_file",
    "find_skills", "read_upload", "device_list", "notify", "offer_choices", "open_app",
}
BROWSER_READ = {"goto", "read", "scroll", "back", "screenshot"}
LOOK_ONLY = ("just looking around on a check-in, so only reading is allowed. Propose it "
             "instead: tell the human what you found and offer_choices like “Go ahead” / "
             "“Not now”; the real work happens once they say go.")


def read_only_run(source: str | None) -> bool:
    return bool(source) and source.split(":")[0] in ("heartbeat", "research")


def look_only_ok(tool: str, args: dict) -> bool:
    if tool == "browser":
        return (args or {}).get("action") in BROWSER_READ
    if tool.startswith("mcp__"):
        return app_tool_rule(tool)[1]
    return tool in READ_ONLY


def app_tool_rule(tool: str) -> tuple[str | None, bool]:
    """(your rule for this app action — "allow" | "ask" | "never" | None, whether the app
    marks it read-only)."""
    from .connectors import load_config, mcp_hub
    info = mcp_hub.tool_info(tool)
    if not info:
        return None, False
    spec = load_config()["mcp"].get(info["server"]) or {}
    return (spec.get("tools") or {}).get(info["tool"]), bool(info["read_only"])


def assess(agent_id: str, tool: str, args: dict, thread_id: str | None = None,
           source: str | None = None) -> tuple[str, str]:
    """Return (decision, reason). ``source`` is what woke the agent (chat, heartbeat…)."""
    if read_only_run(source) and not look_only_ok(tool, args):
        return "deny", LOOK_ONLY
    mine = yours_to_do(tool, args)
    if mine:
        return "deny", yours_reason(agent_id, tool, args, mine)  # even if trusted
    pol = policy_for(agent_id)
    decision = pol.get(tool, MCP_DEFAULT if tool.startswith("mcp__") else "ask")
    reason = ""
    if tool.startswith("mcp__"):
        rule, read_only = app_tool_rule(tool)
        if rule == "never":
            return "deny", "you've set this app action to “never”"
        if rule in ("allow", "ask"):
            decision = rule
        elif tool not in pol:  # nothing set: reading is fine, changing things asks
            decision = "allow" if read_only else "ask"
    if tool in ("shell", "python"):
        cmd = args.get("command") or args.get("code") or ""
        for pat, why in SHELL_DENY:
            if re.search(pat, cmd):
                return "deny", why  # deny always wins, never overridable by a scope
        guard = check_paths(agent_id, cmd)
        if guard and guard[0] == "deny":
            return guard
        for pat, why in SHELL_ASK:
            if re.search(pat, cmd) and decision != "trusted":
                decision, reason = "ask", why
                break
        if guard and decision in ("allow", "ask") and not reason:
            decision, reason = guard
    if tool == "browser" and args.get("action") in ("click", "press"):
        label = browser_label(args)
        if BROWSER_ASK.search(label):
            decision, reason = "ask", "looks like a purchase / irreversible submit"
    if tool.startswith("mcp__") and decision == "ask":
        reason = reason or "uses a connected app on your behalf"
    if tool == "send_email":
        reason = reason or "sends an email as you"
    if decision == "ask":
        if thread_id and session_allowed(agent_id, thread_id, tool):
            return "allow", reason
        if domain_allowed(agent_id, _extract_domain(tool, args)):
            return "allow", reason
    return ("allow" if decision == "trusted" else decision), reason


async def gate(ctx, tool: str, args: dict) -> tuple[str, str]:
    """What runtime asks for every tool call: the rules, then a second look for anything
    that leaves the house or speaks for the human (it can only make things stricter)."""
    aid = ctx.agent["id"]
    decision, reason = assess(aid, tool, args, ctx.thread_id, ctx.extra.get("source"))
    if decision == "deny":
        return decision, reason
    from . import reviewer
    return await reviewer.second_look(ctx, tool, args, decision, reason,
                                      granted=granted(aid, tool, args, ctx.thread_id))


def granted(agent_id: str, tool: str, args: dict, thread_id: str | None = None) -> bool:
    """Did the human say “don't ask me” for this (always, in this chat, or this site)?"""
    return (policy_for(agent_id).get(tool) == "trusted"
            or bool(thread_id and session_allowed(agent_id, thread_id, tool))
            or domain_allowed(agent_id, _extract_domain(tool, args)))


def say(tool: str, args: dict) -> str:
    """What the agent wants to do, as a friend would put it ("email the hotel …")."""
    from .ext.lang import tr
    a = args or {}
    who = a.get("to") or tr("someone", "某人")
    if tool == "send_email":
        subj = (a.get("subject") or "")[:60]
        return tr(f"email {who} — “{subj}”", f"给 {who} 发邮件「{subj}」")
    if tool == "send_sms":
        return tr(f"text {who}", f"给 {who} 发短信")
    if tool == "make_call":
        return tr(f"call {who}", f"给 {who} 打电话")
    if tool in ("shell", "python"):
        return tr("run something on its computer", "在它的电脑上运行点东西")
    if tool == "browser":
        what = (a.get("element") or a.get("text") or a.get("selector") or "")[:40]
        return tr(f"press “{what}” on a website", f"在网页上点「{what}」") if what else \
            tr("do something on a website", "在网页上操作一下")
    if tool == "install_skill":
        return tr("learn a new skill", "学一个新技能")
    if tool.startswith("mcp__"):
        app = tool.split("__")[1] if tool.count("__") >= 2 else tr("a connected app", "一个已连接的应用")
        return tr(f"use {app}", f"用一下 {app}")
    if tool.startswith("iphone_"):
        name = a.get("name", "")
        return {"iphone_add_event": tr("add something to your calendar", "往你的日历里加个日程"),
                "iphone_contacts": tr("look at your contacts", "看一下你的通讯录"),
                "iphone_run_shortcut": tr(f"run the “{name}” shortcut on your phone",
                                          f"在你手机上运行「{name}」快捷指令"),
                }.get(tool, tr("use your phone", "用一下你的手机"))
    return tool.replace("_", " ")


REASON_ZH = {
    "needs root": "需要管理员权限", "would wipe a whole tree": "会删掉一整个目录",
    "destructive system command": "有破坏性的系统命令", "power control": "要关机或重启",
    "pipes a remote script into a shell": "要直接运行网上下载的脚本",
    "connects to another machine": "要连到另一台机器", "publishes code": "要发布代码",
    "sends data to the internet": "要往网上发送数据",
    "starts something that outlives the task": "要启动一个长期运行的东西",
    "looks like a purchase / irreversible submit": "看起来像是付款或不能撤回的提交",
    "uses a connected app on your behalf": "要替你用一个已连接的应用",
    "sends an email as you": "要以你的名义发邮件",
    PRIVATE_DATA: "要读 OpenDot 的私密数据（密钥或其他助手的东西）",
    PERSONAL: "要碰你的私人密钥或登录信息", CDP: "要连浏览器的远程控制端口",
    OUTSIDE: "要访问它电脑以外的地方", DELETES_OUTSIDE: "要删除它电脑以外的东西",
}


def _reason(reason: str) -> str:
    from .ext.lang import lang
    return REASON_ZH.get(reason, reason) if lang() == "zh" else reason


def _want(name: str, what: str) -> str:
    from .ext.lang import tr
    return tr(f"{name} wants to {what}", f"{name} 想{what}")


async def request_approval(agent_id: str, thread_id: str, tool: str, args: dict,
                           reason: str, timeout: float = 1800, job_id: str | None = None,
                           attempt: int = 1) -> bool:
    """Show an approval card and wait. The ask is a DB row, so it outlives a restart: a
    resumed job (``attempt`` > 1) reuses the one it made before — and if you already
    answered it, even while the server was down, that answer stands."""
    ap = _earlier_approval(job_id, attempt, tool, args)
    if ap and ap["status"] in ("approved", "denied"):
        return ap["status"] == "approved"
    if not ap:
        has_domain = tool in DOMAIN_TOOLS or tool in ("shell", "python")
        domain = _extract_domain(tool, args) if has_domain else None
        display_args = {**args, "_domain": domain} if domain else dict(args)
        display_args["_what"] = say(tool, args)
        ap = db.insert("approvals", id=new_id("ap_"), agent_id=agent_id, thread_id=thread_id,
                       tool=tool, args=display_args, reason=_reason(reason), status="pending",
                       job_id=job_id, args_hash=args_hash(tool, args), attempt=attempt)
        agent = db.one("SELECT name FROM agents WHERE id=?", agent_id) or {"name": "Pip"}
        db.insert("inbox", id=new_id("in_"), agent_id=agent_id, kind="approval",
                  title=_want(agent["name"], display_args["_what"]), body=_reason(reason),
                  status="unread",
                  ref=ap["id"], thread_id=thread_id)
        bus.emit("approval", approval=ap)
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _waiters[ap["id"]] = fut
    try:
        ok = await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        ok = False
        db.update("approvals", ap["id"], status="expired", decided=time.time())
        _close_notice(ap["id"])
        bus.emit("approval", approval=db.one("SELECT * FROM approvals WHERE id=?", ap["id"]))
    finally:
        _waiters.pop(ap["id"], None)
    return ok


def _close_notice(approval_id: str) -> None:
    """An answered (or expired) ask no longer needs you, so its inbox notice is done."""
    with db.lock:
        db.conn.execute("UPDATE inbox SET status='done' WHERE kind='approval' AND ref=?",
                        (approval_id,))
        db.conn.commit()


def _earlier_approval(job_id: str | None, attempt: int, tool: str, args: dict) -> dict | None:
    """The same ask from an earlier attempt of this job (claimed, so it's reused once)."""
    if not job_id or attempt <= 1:
        return None
    ap = db.one("SELECT * FROM approvals WHERE job_id=? AND tool=? AND args_hash=? AND "
                "attempt<? AND status IN ('pending','approved','denied') ORDER BY created DESC "
                "LIMIT 1", job_id, tool, args_hash(tool, args), attempt)
    if ap:
        db.update("approvals", ap["id"], attempt=attempt)
    return ap


def decide(approval_id: str, approve: bool, always: bool = False,
          scope: str | None = None) -> dict | None:
    """Resolve a pending approval. ``scope`` (new): "once" | "session" | "domain" |
    "always" | "deny" — a superset of the original ``always`` bool, kept working for
    older callers (``always=True`` behaves like ``scope="always"``)."""
    ap = db.one("SELECT * FROM approvals WHERE id=?", approval_id)
    if not ap or ap["status"] != "pending":
        return ap
    # committed before anyone waiting is woken — a restart can't lose the answer
    db.update("approvals", approval_id, status="approved" if approve else "denied",
              decided=time.time())
    _close_notice(approval_id)
    if approve:
        if scope == "always" or (always and scope is None):
            set_policy(ap["agent_id"], ap["tool"], "trusted")
        elif scope == "session":
            approve_session(ap["agent_id"], ap["thread_id"], ap["tool"])
        elif scope == "domain":
            domain = (ap["args"] or {}).get("_domain")
            if domain:
                approve_domain(ap["agent_id"], domain)
    fut = _waiters.get(approval_id)
    if fut and not fut.done():
        fut.set_result(approve)
    ap = db.one("SELECT * FROM approvals WHERE id=?", approval_id)
    bus.emit("approval", approval=ap)
    return ap
