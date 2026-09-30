"""Apps that live inside OpenDot, for services without an official MCP server of their
own. They are ordinary MCP servers (so the Apps page, "who can use it", per-action
rules and the Gatekeeper all work the same), connected in memory instead of over HTTP.

Calendars: iCloud (or any CalDAV account: Fastmail, Nextcloud…) with an app-specific
password, read and add events; or any calendar's private iCal link, read only.

Microsoft (Outlook mail, Outlook calendar, OneDrive) signs in with Microsoft's device
code flow: you open microsoft.com/devicelogin on any device and type a short code, so
there's no redirect address to register and it works from your phone too. It needs a
client ID you register once (free, a few minutes, no secret). Mail is drafts-only:
agents write, you send.
"""

from __future__ import annotations

import asyncio
import functools
import html
import logging
import re
import time

import httpx
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import google_apps, mcp_oauth

log = logging.getLogger("opendot.builtin_apps")

RO = ToolAnnotations(read_only_hint=True, open_world_hint=False)
RW = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)


class AppError(ToolError):
    """Something the agent should read: the message goes back to it as is."""


class SignInNeeded(AppError):
    def __init__(self, why: str = "") -> None:
        super().__init__("this app needs you to sign in again (Settings → Apps)"
                         + (f": {why}" if why else ""))


def tool(s, **kw):
    """``s.tool`` for built-in apps: any failure reaches the agent as a readable message
    (the SDK would otherwise hide it behind "Error executing tool")."""
    def deco(fn):
        @functools.wraps(fn)
        async def run(*a, **k):
            try:
                return await fn(*a, **k)
            except ToolError:
                raise
            except (httpx.HTTPError, asyncio.TimeoutError) as e:
                raise AppError(f"couldn't reach the service ({type(e).__name__})") from e
            except Exception as e:  # noqa: BLE001
                raise AppError(str(e)[:400] or type(e).__name__) from e
        return s.tool(**kw)(run)
    return deco

MS_AUTHORITY = "https://login.microsoftonline.com/common/oauth2/v2.0"
MS_GRAPH = "https://graph.microsoft.com/v1.0"
MS_SCOPES = "offline_access User.Read Mail.ReadWrite Calendars.ReadWrite Files.Read"

# name -> {"user_code", "verification_uri", "expires": ts, "task"}
_device: dict[str, dict] = {}


# ---------------- Microsoft sign-in (device code) ----------------
def ms_client_id() -> str:
    from .gatekeeper import vault_all
    return vault_all().get("MICROSOFT_CLIENT_ID", "")


async def ms_start_sign_in(name: str, on_done) -> dict:
    """Ask Microsoft for a code to show you; wait for you in the background."""
    cid = ms_client_id()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{MS_AUTHORITY}/devicecode", data={"client_id": cid, "scope": MS_SCOPES})
    if r.status_code != 200:
        raise PermissionError(_ms_error(r))
    d = r.json()
    old = _device.pop(name, None)
    if old and old.get("task"):
        old["task"].cancel()
    entry = {"user_code": d["user_code"], "verification_uri": d["verification_uri"],
             "expires": time.time() + int(d.get("expires_in", 900))}
    entry["task"] = asyncio.create_task(_ms_poll(name, cid, d, on_done))
    _device[name] = entry
    return {k: v for k, v in entry.items() if k != "task"}


def device_code(name: str) -> dict | None:
    e = _device.get(name)
    if not e or e["expires"] < time.time():
        return None
    return {k: v for k, v in e.items() if k != "task"}


async def _ms_poll(name: str, cid: str, d: dict, on_done) -> None:
    wait = int(d.get("interval", 5))
    deadline = time.time() + int(d.get("expires_in", 900))
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            while time.time() < deadline:
                await asyncio.sleep(wait)
                r = await c.post(f"{MS_AUTHORITY}/token", data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": cid, "device_code": d["device_code"]})
                j = r.json()
                if r.status_code == 200:
                    _ms_store(name, j)
                    _device.pop(name, None)
                    on_done()
                    return
                err = j.get("error")
                if err == "slow_down":
                    wait += 5
                elif err != "authorization_pending":
                    log.warning("microsoft sign-in for %s: %s", name, err)
                    break
    finally:
        _device.pop(name, None)


def _ms_store(name: str, tok: dict) -> None:
    data = mcp_oauth._load(name)
    prev = (data.get("tokens") or {}).get("refresh_token")
    data["tokens"] = {k: tok[k] for k in ("access_token", "refresh_token", "expires_in", "scope")
                      if tok.get(k)}
    if not data["tokens"].get("refresh_token") and prev:
        data["tokens"]["refresh_token"] = prev
    data["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
    mcp_oauth._save(name, data)


def _ms_error(r: httpx.Response) -> str:
    try:
        j = r.json()
        return j.get("error_description", "").split("\r\n")[0] or j.get("error", "") or r.text[:200]
    except ValueError:
        return r.text[:200]


class MSAuthError(SignInNeeded):
    pass


async def _ms_token(name: str) -> str:
    data = mcp_oauth._load(name)
    tok = data.get("tokens") or {}
    if tok.get("access_token") and data.get("expires_at", 0) > time.time() + 60:
        return tok["access_token"]
    if not tok.get("refresh_token"):
        raise MSAuthError("needs sign-in")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{MS_AUTHORITY}/token", data={
            "grant_type": "refresh_token", "client_id": ms_client_id(),
            "refresh_token": tok["refresh_token"], "scope": MS_SCOPES})
    if r.status_code != 200:
        raise MSAuthError(_ms_error(r))
    j = r.json()
    j.setdefault("refresh_token", tok["refresh_token"])
    _ms_store(name, j)
    return j["access_token"]


async def _graph(name: str, method: str, path: str, **kw) -> httpx.Response:
    token = await _ms_token(name)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.request(method, MS_GRAPH + path, headers={
            "Authorization": f"Bearer {token}", "Prefer": 'outlook.body-content-type="text"',
            "ConsistencyLevel": "eventual"}, **kw)
    if r.status_code == 401:
        raise MSAuthError("the sign-in expired")
    if r.status_code >= 400:
        raise RuntimeError(f"Microsoft said {r.status_code}: {_ms_error(r)}")
    return r


def _text(s: str | None, n: int = 4000) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()[:n]


def _who(x: dict | None) -> str:
    e = (x or {}).get("emailAddress") or {}
    return f"{e.get('name', '')} <{e.get('address', '')}>".strip()


def microsoft_server(name: str):
    """The in-process MCP server for one Microsoft account."""
    from mcp.server.mcpserver import MCPServer

    ro, rw = RO, RW
    s = MCPServer("Microsoft 365", instructions="Outlook mail (read + drafts), calendar and OneDrive.")

    @tool(s, title="Search mail", annotations=ro)
    async def search_mail(query: str = "", folder: str = "inbox", top: int = 10) -> str:
        """Search Outlook mail (newest first). Empty query = latest messages in the folder."""
        params = {"$top": str(min(max(top, 1), 25)),
                  "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead,webLink"}
        if query:
            params["$search"] = f'"{query}"'
        else:
            params["$orderby"] = "receivedDateTime desc"
        r = await _graph(name, "GET", f"/me/mailFolders/{folder}/messages", params=params)
        rows = r.json().get("value", [])
        return "\n\n".join(f"[{m['id']}] {m.get('receivedDateTime', '')[:16]} · {_who(m.get('from'))}"
                           f"{'' if m.get('isRead') else ' · unread'}\n{m.get('subject', '')}\n"
                           f"{_text(m.get('bodyPreview'), 300)}" for m in rows) or "No messages."

    @tool(s, title="Read an email", annotations=ro)
    async def read_mail(message_id: str) -> str:
        """The full text of one message (by the id from search_mail)."""
        r = await _graph(name, "GET", f"/me/messages/{message_id}", params={
            "$select": "subject,from,toRecipients,ccRecipients,receivedDateTime,body,webLink"})
        m = r.json()
        to = ", ".join(_who(x) for x in m.get("toRecipients", []))
        return (f"From: {_who(m.get('from'))}\nTo: {to}\nDate: {m.get('receivedDateTime')}\n"
                f"Subject: {m.get('subject')}\nLink: {m.get('webLink')}\n\n"
                f"{_text((m.get('body') or {}).get('content'), 12000)}")

    @tool(s, title="Write a draft", annotations=rw)
    async def create_draft(to: str, subject: str, body: str, reply_to_message_id: str = "") -> str:
        """Save a draft in Outlook for the human to check and send. Never sends."""
        if reply_to_message_id:
            r = await _graph(name, "POST", f"/me/messages/{reply_to_message_id}/createReply",
                             json={"comment": body})
        else:
            rcpts = [{"emailAddress": {"address": a.strip()}} for a in to.split(",") if a.strip()]
            r = await _graph(name, "POST", "/me/messages", json={
                "subject": subject, "body": {"contentType": "Text", "content": body},
                "toRecipients": rcpts})
        m = r.json()
        return f"Draft saved: {m.get('webLink', '')}"

    @tool(s, title="Calendar events", annotations=ro)
    async def list_events(start: str, end: str) -> str:
        """Events between two ISO dates/times (e.g. 2026-10-01 and 2026-10-08)."""
        r = await _graph(name, "GET", "/me/calendarView", params={
            "startDateTime": start, "endDateTime": end, "$top": "50", "$orderby": "start/dateTime",
            "$select": "id,subject,start,end,location,isAllDay,organizer,webLink"})
        ev = r.json().get("value", [])
        return "\n".join(f"[{e['id']}] {e['start']['dateTime'][:16]}–{e['end']['dateTime'][11:16]} "
                         f"{e.get('subject', '')}"
                         f"{' @ ' + e['location']['displayName'] if (e.get('location') or {}).get('displayName') else ''}"
                         for e in ev) or "Nothing on the calendar then."

    @tool(s, title="Add an event", annotations=rw)
    async def create_event(subject: str, start: str, end: str, time_zone: str = "UTC",
                           location: str = "", notes: str = "") -> str:
        """Add an event to the Outlook calendar (no invitations are sent)."""
        body = {"subject": subject, "start": {"dateTime": start, "timeZone": time_zone},
                "end": {"dateTime": end, "timeZone": time_zone}}
        if location:
            body["location"] = {"displayName": location}
        if notes:
            body["body"] = {"contentType": "Text", "content": notes}
        r = await _graph(name, "POST", "/me/events", json=body)
        return f"Added: {r.json().get('webLink', '')}"

    @tool(s, title="Search OneDrive", annotations=ro)
    async def search_files(query: str) -> str:
        """Find files in OneDrive by name or content."""
        q = query.replace("'", "''")
        r = await _graph(name, "GET", f"/me/drive/root/search(q='{q}')", params={
            "$top": "20", "$select": "id,name,size,lastModifiedDateTime,webUrl,file,folder"})
        rows = r.json().get("value", [])
        return "\n".join(f"[{f['id']}] {f['name']}{'/' if f.get('folder') else ''} · "
                         f"{f.get('lastModifiedDateTime', '')[:10]} · {f.get('webUrl', '')}"
                         for f in rows) or "No files found."

    @tool(s, title="Read a file", annotations=ro)
    async def read_file(item_id: str) -> str:
        """The text of a OneDrive file (text, Markdown, CSV, JSON…); other kinds give a link."""
        meta = (await _graph(name, "GET", f"/me/drive/items/{item_id}")).json()
        mime = (meta.get("file") or {}).get("mimeType", "")
        if not (mime.startswith("text/") or mime in ("application/json", "application/xml")) \
                or meta.get("size", 0) > 2_000_000:
            return f"{meta.get('name')} ({mime or 'folder'}) — open it here: {meta.get('webUrl')}"
        r = await _graph(name, "GET", f"/me/drive/items/{item_id}/content", follow_redirects=True)
        return r.text[:20000]

    return s


# ---------------- calendars: CalDAV (iCloud…) and iCal links ----------------
_dav_cache: dict[str, tuple[float, list[dict]]] = {}
DAV_TTL = 300


def _settings(spec: dict) -> dict:
    from .gatekeeper import inject_secrets
    return inject_secrets(spec.get("settings") or {})


def _dav_client(st: dict):
    import caldav
    return caldav.DAVClient(url=st.get("url") or "https://caldav.icloud.com",
                            username=st.get("user"), password=st.get("password"), timeout=30)


def _dav_calendars(st: dict) -> list:
    cals = [c for c in _dav_client(st).principal().calendars()
            if "VEVENT" in (c.get_supported_components() or ["VEVENT"])]
    if not cals:
        raise RuntimeError("this account has no calendars")
    return cals


def _dav_name(c) -> str:
    try:
        return c.get_display_name() or "Calendar"
    except Exception:
        return "Calendar"


def _dav_events(st: dict, start, end) -> list[dict]:
    from .ext.calendar import _ev_dict
    out = []
    for c in _dav_calendars(st):
        name = _dav_name(c)
        for ev in c.search(start=start, end=end, event=True, expand=True):
            for comp in ev.icalendar_instance.walk("VEVENT"):
                out.append({**_ev_dict(comp), "calendar": name})
    return sorted(out, key=lambda e: e["start"])


def _dav_add(st: dict, title: str, start, end, calendar: str, location: str, notes: str) -> str:
    cals = _dav_calendars(st)
    pick = next((c for c in cals if _dav_name(c).lower() == calendar.strip().lower()), None) \
        if calendar else None
    if calendar and not pick:
        raise RuntimeError(f"no calendar called {calendar!r}; there are: "
                           + ", ".join(_dav_name(c) for c in cals))
    target = pick or cals[0]
    target.add_event(dtstart=start, dtend=end, summary=title, location=location or None,
                     description=notes or None)
    return _dav_name(target)


def _range(start: str, end: str):
    import datetime as dt

    from .ext.calendar import _parse_dt
    s = _parse_dt(start)
    return s, (_parse_dt(end) if end else s + dt.timedelta(days=7))


async def calendar_events(name: str, spec: dict, start, end) -> list[dict]:
    """Events from a calendar app (Google, CalDAV account or iCal link), for its tools
    and the Calendar page."""
    if spec.get("builtin") == "google_calendar":
        return await google_apps.calendar_events(name, start, end)
    st = _settings(spec)
    if spec.get("builtin") == "ics":
        from .ext.agenda import _ics
        return await _ics(st.get("url", ""), start, end)
    key = f"{st.get('url')}|{st.get('user')}|{start.isoformat()}|{end.isoformat()}"
    hit = _dav_cache.get(key)
    if hit and time.time() - hit[0] < DAV_TTL:
        return hit[1]
    evs = await asyncio.to_thread(_dav_events, st, start, end)
    _dav_cache[key] = (time.time(), evs)
    return evs


async def check(kind: str, spec: dict) -> None:
    """Try the account before it's saved, so a typo shows up now, not in a chat later."""
    st = _settings(spec)
    try:
        if kind == "caldav":
            await asyncio.to_thread(_dav_calendars, st)
        elif kind == "ics":
            from .ext.agenda import _range_now
            await calendar_events("", spec, *_range_now())
    except Exception as e:
        msg = str(e)
        if kind == "caldav" and ("401" in msg or "Unauthorized" in msg or "auth" in msg.lower()):
            raise PermissionError("the account didn't accept that user name and password. For "
                                  "iCloud use your Apple ID email and an app-specific password "
                                  "(account.apple.com → Sign-In and Security), not your usual one")
        if kind == "ics":
            raise PermissionError(f"couldn't read that calendar link ({msg[:120]})")
        raise PermissionError(f"couldn't reach the calendar ({msg[:160]})")


def _fmt(evs: list[dict]) -> str:
    rows = []
    for e in evs:
        where = f" @ {e['location']}" if e.get("location") else ""
        cal = f" [{e['calendar']}]" if e.get("calendar") else ""
        rows.append(f"{e['start'][:16].replace('T', ' ')} – {e['end'][:16].replace('T', ' ')} "
                    f"{e['title']}{where}{cal}")
    return "\n".join(rows) or "Nothing on the calendar then."


def calendar_server(kind: str):
    def make(name: str, spec: dict):
        from mcp.server.mcpserver import MCPServer

        ro, rw = RO, RW
        s = MCPServer(spec.get("label") or "Calendar",
                      instructions="Your human's own calendar." if kind == "ics" else
                      "Your human's own calendar: read it, and add events.")

        @tool(s, title="Calendar events", annotations=ro)
        async def list_events(start: str = "", end: str = "") -> str:
            """Events between two ISO dates/times (default: the next 7 days)."""
            return _fmt(await calendar_events(name, spec, *_range(start, end)))

        if kind == "caldav":
            @tool(s, title="Calendars", annotations=ro)
            async def list_calendars() -> str:
                """The calendars in this account."""
                cals = await asyncio.to_thread(_dav_calendars, _settings(spec))
                return "\n".join(_dav_name(c) for c in cals)

            @tool(s, title="Add an event", annotations=rw)
            async def create_event(title: str, start: str, end: str, calendar: str = "",
                                   location: str = "", notes: str = "") -> str:
                """Add an event (ISO start/end; without a time zone it's the human's local time).
                calendar: its name from list_calendars (default: the first one)."""
                s_, e_ = _range(start, end)
                where = await asyncio.to_thread(_dav_add, _settings(spec), title, s_, e_,
                                                calendar, location, notes)
                _dav_cache.clear()
                return f"Added “{title}” to {where}."
        return s
    return make


BUILTINS = {"microsoft": lambda name, spec: microsoft_server(name),
            "caldav": calendar_server("caldav"), "ics": calendar_server("ics"),
            **google_apps.SERVERS}


def signed_in(kind: str, name: str) -> bool:
    if kind in ("caldav", "ics"):
        return True  # its password or link was checked when you added it
    return bool((mcp_oauth._load(name).get("tokens") or {}).get("refresh_token"))
