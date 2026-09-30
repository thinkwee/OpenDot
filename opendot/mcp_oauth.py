"""Sign in to an app with its own "Allow" page (OAuth 2.1 + PKCE, the MCP standard).

The flow, when you press Connect:
1. The MCP SDK finds the app's sign-in server and registers OpenDot with it (dynamic
   client registration), or uses the client id you made yourself for Google.
2. It hands us the app's sign-in link; the browser opens it in a small window.
3. You sign in and press Allow; the app sends the browser back to
   ``/oauth/callback`` with a one-time code, which the SDK trades for tokens.

Tokens and the registration are kept in the encrypted vault (``OAUTH_<APP>``), are
refreshed automatically, and never reach the model — they only ride in the HTTPS
header to that one app. The callback is matched to the sign-in it belongs to by a
random ``state`` that works once and expires with the sign-in.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .bus import bus
from .gatekeeper import vault_all, vault_set

log = logging.getLogger("opendot.oauth")


class _QuietSignIn(logging.Filter):
    """The SDK logs a traceback when we stop a flow because nobody's there to sign in;
    that's expected, not an error."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return type(exc).__name__ != "NeedsSignIn"


logging.getLogger("mcp.client.auth.oauth2").addFilter(_QuietSignIn())
SIGN_IN_WAIT = 600  # seconds you have to finish signing in

# state -> {"future": Future, "app": name, "created": ts}
_pending: dict[str, dict] = {}


def vault_name(app: str) -> str:
    return "OAUTH_" + re.sub(r"[^A-Z0-9]+", "_", app.upper()).strip("_")


def _load(app: str) -> dict:
    raw = vault_all().get(vault_name(app))
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def _save(app: str, data: dict) -> None:
    vault_set(vault_name(app), json.dumps(data))


def forget(app: str) -> None:
    vault_set(vault_name(app), None)


def signed_in(app: str) -> bool:
    return bool(_load(app).get("tokens"))


class VaultStorage:
    """The SDK's TokenStorage, kept in the vault."""

    def __init__(self, app: str, redirect_uri: str | None, client: dict | None) -> None:
        self.app, self.redirect_uri, self.client = app, redirect_uri, client

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        t = _load(self.app).get("tokens")
        return OAuthToken.model_validate(t) if t else None

    async def set_tokens(self, tokens) -> None:
        d = _load(self.app)
        d["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        d["saved"] = time.time()
        # the SDK only keeps the expiry in memory; remember it so a restart refreshes in time
        d["expires_at"] = time.time() + tokens.expires_in if tokens.expires_in else None
        _save(self.app, d)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        if self.client:  # a client you registered yourself (Google…)
            return OAuthClientInformationFull.model_validate({
                "client_id": self.client["client_id"],
                "client_secret": self.client.get("client_secret") or None,
                "redirect_uris": [self.redirect_uri or self.client.get("redirect_uri")
                                  or "http://localhost/"],
                "token_endpoint_auth_method":
                    "client_secret_post" if self.client.get("client_secret") else "none",
            })
        c = _load(self.app).get("client")
        if not c:
            return None
        info = OAuthClientInformationFull.model_validate(c)
        # registered for another address (e.g. you signed in on localhost, now on your
        # phone link): register again rather than send the app a mismatched address
        if self.redirect_uri and info.redirect_uris and \
                self.redirect_uri not in [str(u) for u in info.redirect_uris]:
            return None
        return info

    async def set_client_info(self, info) -> None:
        if self.client:
            return
        d = _load(self.app)
        d["client"] = info.model_dump(mode="json", exclude_none=True)
        _save(self.app, d)


def provider_for(srv, spec: dict, interactive: dict | None):
    """An httpx auth that signs requests to this app, starting a sign-in if needed."""
    from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

    from .connectors import NeedsSignIn

    redirect_uri = (interactive or {}).get("redirect_uri") or _load(srv.name).get("redirect_uri")
    client = spec.get("oauth_client")  # {"client_id", "client_secret"} for Google & co.
    scope = spec.get("scopes")  # pin the scopes instead of "everything the app offers"
    state_box: dict = {}

    class Pinned(OAuthClientMetadata):
        def __setattr__(self, k, v):
            if k == "scope" and scope:
                return
            super().__setattr__(k, v)

    meta = Pinned(
        client_name="OpenDot", client_uri="https://github.com/thinkwee/OpenDot",
        redirect_uris=[redirect_uri or "http://localhost/oauth/callback"],
        grant_types=["authorization_code", "refresh_token"], response_types=["code"],
        token_endpoint_auth_method="client_secret_post" if (client or {}).get("client_secret") else "none",
        scope=scope or None,
    )

    async def on_redirect(url: str) -> None:
        if not interactive:
            raise NeedsSignIn()
        url = _polish(url, spec)
        state = parse_qs(urlparse(url).query).get("state", [""])[0]
        fut = asyncio.get_running_loop().create_future()
        _pending[state] = {"future": fut, "app": srv.name, "created": time.time()}
        state_box["state"] = state
        srv.auth_url, srv.status = url, "sign_in"
        srv.ready.set()
        bus.emit("app_sign_in", app=srv.name, url=url)

    async def on_callback() -> AuthorizationCodeResult:
        state = state_box.get("state", "")
        entry = _pending.get(state)
        if not entry:
            raise NeedsSignIn()
        try:
            got = await asyncio.wait_for(entry["future"], SIGN_IN_WAIT)
        finally:
            _pending.pop(state, None)
        if got.get("error"):
            raise PermissionError(got.get("error_description") or got["error"])
        srv.auth_url = ""
        srv.status = "connecting"
        if redirect_uri:
            d = _load(srv.name)
            d["redirect_uri"] = redirect_uri
            _save(srv.name, d)
        return AuthorizationCodeResult(code=got["code"], state=got.get("state"), iss=got.get("iss"))

    return _Provider(
        srv.name,
        server_url=spec["url"], client_metadata=meta,
        storage=VaultStorage(srv.name, redirect_uri, client),
        redirect_handler=on_redirect, callback_handler=on_callback,
    )


def sign_in_first(inner, app: str, url: str):
    """Some apps (Google's) answer "hello" and "what can you do" without a sign-in and
    only refuse the real work. Until the app has been signed in, its unsigned requests
    are answered here with a 401, so Connect starts the sign-in instead of showing an
    app that looks connected but can't do anything."""
    import httpx2

    class SignInFirst(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if str(request.url).startswith(url) and "authorization" not in request.headers \
                    and not signed_in(app):
                return httpx2.Response(401, request=request)
            return await inner.handle_async_request(request)

        async def aclose(self) -> None:
            await inner.aclose()

    return SignInFirst()


def _provider_class():
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata

    class Provider(OAuthClientProvider):
        """The SDK's provider, plus memory across restarts: when the token runs out and
        where to refresh it (otherwise a restart after an hour means signing in again)."""

        def __init__(self, app: str, **kw) -> None:
            super().__init__(**kw)
            self._app = app

        async def _initialize(self) -> None:
            await super()._initialize()
            d, c = _load(self._app), self.context
            if c.current_tokens and d.get("expires_at"):
                c.token_expiry_time = d["expires_at"] - 60  # refresh a minute early
            try:
                if d.get("as_meta") and not c.oauth_metadata:
                    c.oauth_metadata = OAuthMetadata.model_validate(d["as_meta"])
                if d.get("prm") and not c.protected_resource_metadata:
                    c.protected_resource_metadata = ProtectedResourceMetadata.model_validate(d["prm"])
                if d.get("as_url") and not c.auth_server_url:
                    c.auth_server_url = d["as_url"]
            except Exception:  # an app changed its metadata format: rediscover on demand
                log.debug("stale sign-in metadata for %s", self._app)

        def _select_authorization_server(self, advertised: list[str]) -> str:
            # Google lists "https://accounts.google.com/" but its metadata says
            # "https://accounts.google.com": at the root, with or without the slash is the
            # same server (RFC 8414), so don't fail the sign-in over it
            url = super()._select_authorization_server(advertised)
            u = urlparse(url)
            return url.rstrip("/") if u.path in ("", "/") and not u.query else url

        async def _handle_token_response(self, response) -> None:
            await super()._handle_token_response(response)
            self._remember()

        def _remember(self) -> None:
            d, c = _load(self._app), self.context
            if c.oauth_metadata:
                d["as_meta"] = c.oauth_metadata.model_dump(mode="json", exclude_none=True)
            if c.protected_resource_metadata:
                d["prm"] = c.protected_resource_metadata.model_dump(mode="json", exclude_none=True)
            if c.auth_server_url:
                d["as_url"] = c.auth_server_url
            _save(self._app, d)

    return Provider


def _Provider(app: str, **kw):
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = _provider_class()
    return _PROVIDER(app, **kw)


_PROVIDER = None


def _polish(url: str, spec: dict) -> str:
    """Per-provider touches the SDK doesn't know about: Google only hands out a refresh
    token (so you stay signed in) with access_type=offline and a consent prompt."""
    u = urlparse(url)
    if u.netloc.endswith("accounts.google.com"):
        q = parse_qs(u.query)
        q.update({"access_type": ["offline"], "prompt": ["consent"],
                  "include_granted_scopes": ["true"]})
        u = u._replace(query=urlencode({k: v[0] for k, v in q.items()}))
        return urlunparse(u)
    return url


def complete(params: dict) -> str | None:
    """The browser came back from the app. Returns the app's name, or None."""
    state = params.get("state") or ""
    entry = _pending.get(state)
    if not entry or entry["future"].done():
        return None
    entry["future"].set_result(params)
    return entry["app"]


def cancel(app: str) -> None:
    for state, e in list(_pending.items()):
        if e["app"] == app and not e["future"].done():
            e["future"].set_result({"error": "cancelled"})
