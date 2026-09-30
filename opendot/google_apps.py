"""Gmail, Google Calendar and Google Drive, built into OpenDot.

Google's own MCP servers for these only answer projects enrolled in its Workspace
preview programme (business accounts), so OpenDot talks to the ordinary Google APIs
instead, which every account can use. You sign in with the Google client you made
once (Settings → Apps → Google); the sign-in is kept in the encrypted vault and
refreshed on its own. Mail is drafts only: agents write, you send.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import html
import json
import logging
import secrets
import time
from email.message import EmailMessage
from urllib.parse import quote, urlencode

import httpx

from . import mcp_oauth

log = logging.getLogger("opendot.google_apps")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CAL = "https://www.googleapis.com/calendar/v3"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
DRIVE = "https://www.googleapis.com/drive/v3"

KINDS = ("google_calendar", "gmail", "google_drive")
# the Google API each one needs switched on in your Google Cloud project
API = {"google_calendar": ("calendar-json.googleapis.com", "Google Calendar API"),
       "gmail": ("gmail.googleapis.com", "Gmail API"),
       "google_drive": ("drive.googleapis.com", "Google Drive API")}


def _errors():
    from .builtin_apps import AppError, SignInNeeded
    return AppError, SignInNeeded


class GoogleAuthError(Exception):
    """Raised as the built-in apps' SignInNeeded (see ``_api``)."""


def _client() -> tuple[str, str]:
    from .gatekeeper import vault_all
    v = vault_all()
    return v.get("GOOGLE_CLIENT_ID", ""), v.get("GOOGLE_CLIENT_SECRET", "")


def signed_in(name: str) -> bool:
    return bool((mcp_oauth._load(name).get("tokens") or {}).get("refresh_token"))


# ---------------- sign-in ----------------
_tasks: dict[str, asyncio.Task] = {}


async def start_sign_in(name: str, scopes: str, redirect_uri: str, on_done) -> str:
    """The Google page to send you to. When you press Allow, the browser comes back to
    /oauth/callback, which hands the code to the task started here."""
    cid, _ = _client()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    fut = asyncio.get_running_loop().create_future()
    mcp_oauth.cancel(name)
    mcp_oauth._pending[state] = {"future": fut, "app": name, "created": time.time()}
    old = _tasks.pop(name, None)
    if old:
        old.cancel()
    _tasks[name] = asyncio.create_task(_finish(name, state, fut, verifier, redirect_uri, on_done))
    return f"{AUTH_URL}?" + urlencode({
        "client_id": cid, "redirect_uri": redirect_uri, "response_type": "code", "scope": scopes,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"})


async def _finish(name, state, fut, verifier, redirect_uri, on_done) -> None:
    try:
        got = await asyncio.wait_for(fut, mcp_oauth.SIGN_IN_WAIT)
        if got.get("error"):
            log.info("google sign-in for %s: %s", name, got["error"])
            return
        cid, secret = _client()
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(TOKEN_URL, data={
                "grant_type": "authorization_code", "code": got["code"], "redirect_uri": redirect_uri,
                "client_id": cid, "client_secret": secret, "code_verifier": verifier})
        if r.status_code != 200:
            log.warning("google sign-in for %s: %s", name, r.text[:200])
            return
        _store(name, r.json(), redirect_uri)
        on_done()
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    finally:
        mcp_oauth._pending.pop(state, None)
        _tasks.pop(name, None)


def _store(name: str, tok: dict, redirect_uri: str | None = None) -> None:
    d = mcp_oauth._load(name)
    prev = (d.get("tokens") or {}).get("refresh_token")
    d["tokens"] = {k: tok[k] for k in ("access_token", "refresh_token", "expires_in", "scope", "token_type")
                   if tok.get(k)}
    if not d["tokens"].get("refresh_token") and prev:
        d["tokens"]["refresh_token"] = prev
    d["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
    if redirect_uri:
        d["redirect_uri"] = redirect_uri
    mcp_oauth._save(name, d)


async def _token(name: str) -> str:
    d = mcp_oauth._load(name)
    tok = d.get("tokens") or {}
    if tok.get("access_token") and (d.get("expires_at") or 0) > time.time() + 60:
        return tok["access_token"]
    if not tok.get("refresh_token"):
        raise GoogleAuthError("needs sign-in")
    cid, secret = _client()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(TOKEN_URL, data={"grant_type": "refresh_token", "client_id": cid,
                                          "client_secret": secret, "refresh_token": tok["refresh_token"]})
    if r.status_code != 200:
        raise GoogleAuthError(r.json().get("error_description") or r.text[:200])
    _store(name, r.json())
    return r.json()["access_token"]


async def _api(name: str, kind: str, method: str, url: str, **kw) -> httpx.Response:
    AppError, SignInNeeded = _errors()
    try:
        return await _call(name, kind, method, url, **kw)
    except GoogleAuthError as e:
        raise SignInNeeded(str(e)) from e
    except RuntimeError as e:
        raise AppError(str(e)) from e


async def _call(name: str, kind: str, method: str, url: str, **kw) -> httpx.Response:
    token = await _token(name)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kw)
    if r.status_code == 401:
        raise GoogleAuthError("the sign-in expired")
    if r.status_code >= 400:
        try:
            msg = (r.json().get("error") or {}).get("message", "")
        except ValueError:
            msg = r.text[:200]
        if "has not been used in project" in msg or "is disabled" in msg:
            api_id, api_name = API[kind]
            raise RuntimeError(f"the {api_name} isn't switched on in your Google Cloud project yet. "
                               f"Turn it on here, wait a minute, then try again: "
                               f"https://console.cloud.google.com/apis/library/{api_id}")
        if r.status_code == 403 and "insufficient" in msg.lower():
            raise GoogleAuthError("the sign-in doesn't cover this; sign in again")
        raise RuntimeError(f"Google said {r.status_code}: {msg or r.text[:200]}")
    return r


# ---------------- Calendar ----------------
def _when(v: dict) -> str:
    return v.get("dateTime") or v.get("date") or ""


def _parse(s: str) -> dt.datetime:
    from .ext.calendar import _parse_dt
    return _parse_dt(s)


async def calendars(name: str) -> list[dict]:
    r = await _api(name, "google_calendar", "GET", f"{CAL}/users/me/calendarList",
                   params={"minAccessRole": "reader", "maxResults": "100"})
    return r.json().get("items", [])


async def calendar_events(name: str, start: dt.datetime, end: dt.datetime,
                          only: str = "") -> list[dict]:
    """Events from the calendars ticked in Google Calendar (or one, by name or id)."""
    cals = await calendars(name)
    if only:
        cals = [c for c in cals if only.lower() in (c.get("id", "").lower(),
                                                    c.get("summary", "").lower(),
                                                    (c.get("summaryOverride") or "").lower())]
    else:
        cals = [c for c in cals if c.get("selected") or c.get("primary")]

    async def one(c):
        r = await _api(name, "google_calendar", "GET", f"{CAL}/calendars/{quote(c['id'], safe='')}/events", params={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
            "orderBy": "startTime", "maxResults": "250"})
        cal_name = c.get("summaryOverride") or c.get("summary") or "Calendar"
        return [{"title": e.get("summary") or "(no title)", "start": _when(e.get("start", {})),
                 "end": _when(e.get("end", {})), "location": e.get("location") or "",
                 "notes": (e.get("description") or "")[:800], "uid": e.get("id", ""),
                 "calendar": cal_name, "color": c.get("backgroundColor") or "",
                 "link": e.get("htmlLink") or ""}
                for e in r.json().get("items", []) if e.get("status") != "cancelled"]

    out = []
    for evs in await asyncio.gather(*(one(c) for c in cals[:20])):
        out += evs
    return sorted(out, key=lambda e: e["start"])


def calendar_server(name: str, spec: dict):
    from mcp.server.mcpserver import MCPServer

    from .builtin_apps import RO, RW, _fmt, _range, tool
    s = MCPServer("Google Calendar", instructions="Your human's Google Calendar: read it, and add events.")

    @tool(s, title="Calendars", annotations=RO)
    async def list_calendars() -> str:
        """The calendars in this Google account."""
        return "\n".join(f"{c.get('summaryOverride') or c.get('summary')}"
                         f"{' (main)' if c.get('primary') else ''}" for c in await calendars(name))

    @tool(s, title="Calendar events", annotations=RO)
    async def list_events(start: str = "", end: str = "", calendar: str = "") -> str:
        """Events between two ISO dates/times (default: the next 7 days), from every calendar
        shown in Google Calendar, or just one (its name from list_calendars)."""
        return _fmt(await calendar_events(name, *_range(start, end), only=calendar))

    @tool(s, title="Add an event", annotations=RW)
    async def create_event(title: str, start: str, end: str, calendar: str = "",
                           location: str = "", notes: str = "") -> str:
        """Add an event (ISO start/end; without a time zone it's the human's local time).
        calendar: its name from list_calendars (default: the main one). No invitations are sent."""
        s_, e_ = _range(start, end)
        cal_id = "primary"
        if calendar:
            match = [c for c in await calendars(name)
                     if calendar.lower() in ((c.get("summaryOverride") or c.get("summary") or "").lower(),
                                             c.get("id", "").lower())]
            if not match:
                raise RuntimeError(f"no calendar called {calendar!r}")
            cal_id = match[0]["id"]
        body = {"summary": title, "start": {"dateTime": s_.isoformat()}, "end": {"dateTime": e_.isoformat()}}
        if location:
            body["location"] = location
        if notes:
            body["description"] = notes
        r = await _api(name, "google_calendar", "POST", f"{CAL}/calendars/{quote(cal_id, safe='')}/events",
                       params={"sendUpdates": "none"}, json=body)
        return f"Added “{title}”: {r.json().get('htmlLink', '')}"

    return s


# ---------------- Gmail ----------------
def _hdr(msg: dict, key: str) -> str:
    for h in (msg.get("payload") or {}).get("headers", []):
        if h.get("name", "").lower() == key.lower():
            return h.get("value", "")
    return ""


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _body(part: dict) -> tuple[str, str]:
    """(plain, html) text of a message payload."""
    plain, rich = "", ""
    mime = part.get("mimeType", "")
    data = (part.get("body") or {}).get("data")
    if data and mime == "text/plain":
        plain = _b64(data)
    elif data and mime == "text/html":
        rich = _b64(data)
    for p in part.get("parts") or []:
        a, b = _body(p)
        plain, rich = plain or a, rich or b
    return plain, rich


def gmail_server(name: str, spec: dict):
    from mcp.server.mcpserver import MCPServer

    from .builtin_apps import RO, RW, _text, tool
    s = MCPServer("Gmail", instructions="Your human's Gmail: search and read mail, write drafts. Never sends.")

    @tool(s, title="Search mail", annotations=RO)
    async def search_mail(query: str = "", max_results: int = 10) -> str:
        """Search Gmail with Gmail's own search words (from:, subject:, newer_than:2d,
        is:unread…). Empty query = the latest mail in the inbox."""
        r = await _api(name, "gmail", "GET", f"{GMAIL}/messages", params={
            "q": query or "in:inbox", "maxResults": str(min(max(max_results, 1), 25))})
        ids = [m["id"] for m in r.json().get("messages", [])]

        async def meta(i):
            m = (await _api(name, "gmail", "GET", f"{GMAIL}/messages/{i}", params={
                "format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})).json()
            unread = " · unread" if "UNREAD" in m.get("labelIds", []) else ""
            return (f"[{i}] {_hdr(m, 'Date')[:22]} · {_hdr(m, 'From')}{unread}\n{_hdr(m, 'Subject')}\n"
                    f"{html.unescape(m.get('snippet', ''))[:300]}")
        return "\n\n".join(await asyncio.gather(*(meta(i) for i in ids))) or "No messages."

    @tool(s, title="Read an email", annotations=RO)
    async def read_mail(message_id: str) -> str:
        """The full text of one message (by the id from search_mail)."""
        m = (await _api(name, "gmail", "GET", f"{GMAIL}/messages/{message_id}",
                        params={"format": "full"})).json()
        plain, rich = _body(m.get("payload") or {})
        return (f"From: {_hdr(m, 'From')}\nTo: {_hdr(m, 'To')}\nDate: {_hdr(m, 'Date')}\n"
                f"Subject: {_hdr(m, 'Subject')}\n\n{(plain.strip() or _text(rich, 12000))[:12000]}")

    @tool(s, title="Write a draft", annotations=RW)
    async def create_draft(to: str, subject: str, body: str, reply_to_message_id: str = "") -> str:
        """Save a draft in Gmail for the human to check and send. Never sends."""
        msg = EmailMessage()
        msg["To"], msg["Subject"] = to, subject
        draft: dict = {}
        if reply_to_message_id:
            orig = (await _api(name, "gmail", "GET", f"{GMAIL}/messages/{reply_to_message_id}", params={
                "format": "metadata", "metadataHeaders": ["Message-ID", "Subject", "From"]})).json()
            mid = _hdr(orig, "Message-ID")
            if mid:
                msg["In-Reply-To"] = msg["References"] = mid
            if not to:
                msg.replace_header("To", _hdr(orig, "From"))
            draft["threadId"] = orig.get("threadId")
        msg.set_content(body)
        draft["raw"] = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        r = await _api(name, "gmail", "POST", f"{GMAIL}/drafts", json={"message": draft})
        return f"Draft saved in Gmail (id {r.json().get('id')}). It's in Drafts for the human to send."

    return s


# ---------------- Drive ----------------
EXPORT = {"application/vnd.google-apps.document": "text/plain",
          "application/vnd.google-apps.spreadsheet": "text/csv",
          "application/vnd.google-apps.presentation": "text/plain"}


def drive_server(name: str, spec: dict):
    from mcp.server.mcpserver import MCPServer

    from .builtin_apps import RO, RW, tool
    s = MCPServer("Google Drive", instructions="Your human's Google Drive: find and read files, save new ones.")

    @tool(s, title="Search Drive", annotations=RO)
    async def search_files(query: str) -> str:
        """Find files in Google Drive by name or by words inside them."""
        q = query.replace("\\", "\\\\").replace("'", "\\'")
        params = {"pageSize": "20", "fields": "files(id,name,mimeType,modifiedTime,webViewLink)"}
        if query:
            params["q"] = f"(name contains '{q}' or fullText contains '{q}') and trashed = false"
        else:  # nothing to look for: the latest files
            params.update(q="trashed = false", orderBy="modifiedTime desc")
        r = await _api(name, "google_drive", "GET", f"{DRIVE}/files", params=params)
        return "\n".join(f"[{f['id']}] {f['name']} · {f.get('modifiedTime', '')[:10]} · "
                         f"{f.get('webViewLink', '')}" for f in r.json().get("files", [])) or "No files found."

    @tool(s, title="Read a file", annotations=RO)
    async def read_file(file_id: str) -> str:
        """The text of a Drive file: Google Docs, Sheets (as CSV), Slides, and text files;
        other kinds give a link."""
        meta = (await _api(name, "google_drive", "GET", f"{DRIVE}/files/{file_id}", params={
            "fields": "id,name,mimeType,size,webViewLink"})).json()
        mime = meta.get("mimeType", "")
        if mime in EXPORT:
            r = await _api(name, "google_drive", "GET", f"{DRIVE}/files/{file_id}/export",
                           params={"mimeType": EXPORT[mime]})
            return r.text[:20000]
        if mime.startswith("text/") or mime in ("application/json", "application/xml"):
            if int(meta.get("size") or 0) <= 2_000_000:
                r = await _api(name, "google_drive", "GET", f"{DRIVE}/files/{file_id}",
                               params={"alt": "media"})
                return r.text[:20000]
        return f"{meta.get('name')} ({mime}) — open it here: {meta.get('webViewLink')}"

    @tool(s, title="Save a file", annotations=RW)
    async def save_file(file_name: str, content: str, as_google_doc: bool = True) -> str:
        """Save text as a new file in Drive (a Google Doc by default). Never changes existing files."""
        meta = {"name": file_name}
        if as_google_doc:
            meta["mimeType"] = "application/vnd.google-apps.document"
        boundary = "opendot" + secrets.token_hex(8)
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                f"{json.dumps(meta)}\r\n--{boundary}\r\n"
                f"Content-Type: text/plain; charset=UTF-8\r\n\r\n{content}\r\n--{boundary}--")
        r = await _upload(name, body, boundary)
        return f"Saved: {r.get('webViewLink', '')}"

    return s


async def _upload(name: str, body: str, boundary: str) -> dict:
    try:
        token = await _token(name)
    except GoogleAuthError as e:
        raise _errors()[1](str(e)) from e
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://www.googleapis.com/upload/drive/v3/files",
                         params={"uploadType": "multipart", "fields": "id,webViewLink"},
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": f"multipart/related; boundary={boundary}"},
                         content=body.encode())
    if r.status_code >= 400:
        raise RuntimeError(f"Google said {r.status_code}: {r.text[:200]}")
    return r.json()


SERVERS = {"google_calendar": calendar_server, "gmail": gmail_server, "google_drive": drive_server}
