"""The demo as an OAuth 2.1 client of ``mcp.fanmoot.com`` (W1-7).

**A genuine flow, not a faked one.** A visitor who presses "Sign in to Fanmoot"
goes through exactly what Claude Desktop or Alexa+ would: the authorization
server is *discovered* from the MCP server (RFC 9728, then RFC 8414), the demo
identifies itself as a client, sends the fan to ``/authorize`` with a PKCE S256
challenge, a ``state`` and a ``resource`` indicator (RFC 8707), checks the
``iss`` on the way back (RFC 9207), and redeems the code at ``/token``. Every
one of those requests is on the visitor's trace.

How the demo identifies itself is ``FMD_OAUTH_CLIENT_MODE`` (see settings). On
an ``https`` deployment the default is a **client ID metadata document**
(SEP-991, the mechanism ``2025-11-25`` recommends): the demo's ``client_id``
*is* the URL of a JSON document it serves about itself, so there is nothing to
register, nothing to lose on a restart and no secret to hold. On a laptop,
where the authorization server cannot fetch ``http://localhost``, it falls back
to Dynamic Client Registration (RFC 7591), once per process.

**Tokens stay on this server.** The browser gets a popup URL and, afterwards, a
handle. The access and refresh tokens live in a ``GrantHolder`` in this
process's memory, and ``app.agent.trace`` strikes them out of every frame.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from app import __version__
from app.agent.trace import Tracer
from app.logging_config import get_logger
from app.settings import Settings

log = get_logger("agent.oauth")

#: The PKCE method this client uses, and the only one the server accepts.
PKCE_METHOD = "S256"

#: Discovery documents change when the server is redeployed, not per request.
_DISCOVERY_TTL_S = 3600

#: An access token this close to expiry is refreshed rather than presented.
_REFRESH_MARGIN_S = 60

#: A visitor who opens the popup and wanders off: the pending authorization is
#: forgotten after this. The server's own codes live 60 seconds.
PENDING_TTL_S = 600


class OAuthError(Exception):
    """The authorization server refused, or could not be reached."""

    def __init__(self, error: str, description: str = "") -> None:
        super().__init__(f"{error}: {description}" if description else error)
        self.error = error
        self.description = description


@dataclass
class ServerAuth:
    """What discovery found: the protected resource and its authorization server."""

    resource: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None
    registration_endpoint: str | None
    scopes: list[str]
    code_challenge_methods: list[str]
    iss_parameter_supported: bool
    metadata_documents_supported: bool
    #: ``prompt`` values the server honours. ``create`` opens the flow on the
    #: sign-up form, which the "New here" scenario asks for (W4-5).
    prompt_values: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "resource": self.resource,
            "pkce": PKCE_METHOD if PKCE_METHOD in self.code_challenge_methods else None,
            "scopes": self.scopes,
        }


@dataclass
class RegisteredClient:
    client_id: str
    #: ``static``, ``metadata_document`` or ``dcr`` — shown on the trace.
    mode: str
    client_secret: str | None = None


@dataclass
class PendingAuthorization:
    """One sign-in in flight: what the callback needs to finish it."""

    state: str
    verifier: str
    client: RegisteredClient
    redirect_uri: str
    resource: str
    created: float = field(default_factory=time.time)

    @property
    def expired(self) -> bool:
        return time.time() - self.created > PENDING_TTL_S


@dataclass
class Grant:
    access_token: str
    refresh_token: str | None
    expires_at: float
    scope: str
    client: RegisteredClient

    @property
    def scopes(self) -> list[str]:
        return self.scope.split()

    @property
    def fresh(self) -> bool:
        return self.expires_at - _REFRESH_MARGIN_S > time.time()


def pkce_pair() -> tuple[str, str]:
    """A code verifier and its S256 challenge (RFC 7636 §4.1–4.2)."""
    verifier = secrets.token_urlsafe(64)  # 86 unreserved characters
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class DemoOAuthClient:
    """Discovery, registration, authorization, token exchange, refresh, revocation.

    One per process. Discovery and a DCR registration are cached here; nothing
    about any one fan is.
    """

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._transport = transport
        self._auth: ServerAuth | None = None
        self._auth_at = 0.0
        self._registered: RegisteredClient | None = None
        self._lock = asyncio.Lock()

    # ── Discovery ────────────────────────────────────────────────────────────
    @property
    def discovered(self) -> ServerAuth | None:
        """What discovery last found, while it is fresh — without asking again.

        The trace header strip (W3-4) states the PKCE method from this: what
        the authorization server advertised, not what this client assumes.
        """
        if self._auth is not None and time.time() - self._auth_at < _DISCOVERY_TTL_S:
            return self._auth
        return None

    async def server_auth(self, tracer: Tracer) -> ServerAuth:
        async with self._lock:
            if self.discovered is not None:
                return self.discovered
            self._auth = await self._discover(tracer)
            self._auth_at = time.time()
            return self._auth

    async def _discover(self, tracer: Tracer) -> ServerAuth:
        """RFC 9728 → RFC 8414, the way a spec-following MCP client does it.

        The protected-resource document is looked for at the path-inserted
        address first (``/.well-known/oauth-protected-resource/mcp`` for a
        resource at ``/mcp``), which is what the specification derives, and at
        the bare path second.
        """
        parts = urlsplit(self.settings.mcp_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        candidates = [f"{origin}/.well-known/oauth-protected-resource{parts.path}"]
        if parts.path not in ("", "/"):
            candidates.append(f"{origin}/.well-known/oauth-protected-resource")

        prm: dict[str, Any] | None = None
        for url in candidates:
            response = await self._request(tracer, "GET", url, label="protected-resource metadata")
            if response.status_code == 200:
                prm = response.json()
                break
        if not prm or not prm.get("authorization_servers"):
            raise OAuthError(
                "discovery_failed", "The MCP server published no authorization server."
            )

        issuer = str(prm["authorization_servers"][0]).rstrip("/")
        response = await self._request(
            tracer,
            "GET",
            f"{issuer}/.well-known/oauth-authorization-server",
            label="authorization-server metadata",
        )
        if response.status_code != 200:
            raise OAuthError("discovery_failed", "The authorization server published no metadata.")
        meta = response.json()
        return ServerAuth(
            resource=str(prm.get("resource") or self.settings.mcp_url),
            issuer=str(meta.get("issuer") or issuer),
            authorization_endpoint=str(meta["authorization_endpoint"]),
            token_endpoint=str(meta["token_endpoint"]),
            revocation_endpoint=meta.get("revocation_endpoint"),
            registration_endpoint=meta.get("registration_endpoint"),
            scopes=list(prm.get("scopes_supported") or meta.get("scopes_supported") or []),
            code_challenge_methods=list(meta.get("code_challenge_methods_supported") or []),
            iss_parameter_supported=bool(
                meta.get("authorization_response_iss_parameter_supported")
            ),
            metadata_documents_supported=bool(meta.get("client_id_metadata_document_supported")),
            prompt_values=[str(v) for v in meta.get("prompt_values_supported") or []],
        )

    # ── Who this demo is ─────────────────────────────────────────────────────
    def metadata_document(self) -> dict[str, Any]:
        """This demo's own client metadata, served at its client id (SEP-991)."""
        settings = self.settings
        return {
            "client_id": settings.oauth_metadata_document_url,
            "client_name": "Fanmoot on Alexa+ (demo)",
            "client_uri": settings.public_base_url,
            "redirect_uris": [settings.oauth_redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            # A public client: PKCE carries the proof, and a secret in a
            # public repository's deployment would be a secret in name only.
            "token_endpoint_auth_method": "none",
            "software_id": "fanmoot-alexa-plus-demo",
            "software_version": __version__,
        }

    async def client(self, tracer: Tracer, auth: ServerAuth) -> RegisteredClient:
        settings = self.settings
        mode = settings.oauth_client_mode
        if mode == "static" or (mode == "auto" and settings.oauth_client_id):
            if not settings.oauth_client_id:
                raise OAuthError(
                    "misconfigured", "FMD_OAUTH_CLIENT_MODE=static needs FMD_OAUTH_CLIENT_ID."
                )
            return RegisteredClient(
                client_id=settings.oauth_client_id,
                client_secret=settings.oauth_client_secret or None,
                mode="static",
            )
        https = settings.public_base_url.startswith("https://")
        if mode == "metadata_document" or (
            mode == "auto" and https and auth.metadata_documents_supported
        ):
            return RegisteredClient(
                client_id=settings.oauth_metadata_document_url, mode="metadata_document"
            )
        return await self._registered_by_dcr(tracer, auth)

    async def _registered_by_dcr(self, tracer: Tracer, auth: ServerAuth) -> RegisteredClient:
        async with self._lock:
            if self._registered is not None:
                return self._registered
            if not auth.registration_endpoint:
                raise OAuthError(
                    "registration_unavailable", "The server offers no registration endpoint."
                )
            document = self.metadata_document()
            document.pop("client_id")
            response = await self._request(
                tracer,
                "POST",
                auth.registration_endpoint,
                label="dynamic client registration",
                json=document,
            )
            if response.status_code not in (200, 201):
                raise _refusal(response)
            body = response.json()
            self._registered = RegisteredClient(
                client_id=str(body["client_id"]),
                client_secret=body.get("client_secret"),
                mode="dcr",
            )
            return self._registered

    # ── The flow ─────────────────────────────────────────────────────────────
    async def begin(
        self, tracer: Tracer, *, signup: bool = False
    ) -> tuple[str, PendingAuthorization]:
        """The ``/authorize`` URL to send the fan to, and what to remember.

        ``signup`` asks for ``prompt=create`` — the sign-up form first — but
        only of a server that says it honours it. One that does not would
        ignore an unknown prompt at best, so it is not sent, and the fan finds
        the sign-up link on the ordinary sign-in page instead.
        """
        auth = await self.server_auth(tracer)
        if PKCE_METHOD not in auth.code_challenge_methods:
            raise OAuthError("pkce_unsupported", "The authorization server does not offer S256.")
        client = await self.client(tracer, auth)
        verifier, challenge = pkce_pair()
        pending = PendingAuthorization(
            state=secrets.token_urlsafe(24),
            verifier=verifier,
            client=client,
            redirect_uri=self.settings.oauth_redirect_uri,
            resource=auth.resource,
        )
        query = {
            "response_type": "code",
            "client_id": client.client_id,
            "redirect_uri": pending.redirect_uri,
            "scope": " ".join(auth.scopes),
            "state": pending.state,
            "code_challenge": challenge,
            "code_challenge_method": PKCE_METHOD,
            "resource": auth.resource,
        }
        if signup and "create" in auth.prompt_values:
            query["prompt"] = "create"
        url = f"{auth.authorization_endpoint}?{urlencode(query)}"
        tracer.emit(
            "oauth",
            direction="out",
            method="GET /authorize",
            summary=(
                f"Sending the fan to {'sign up' if 'prompt' in query else 'sign in'}"
                f" — PKCE {PKCE_METHOD}, client {client.mode}"
            ),
            payload={**query, "client_id": client.client_id},
            pkce=PKCE_METHOD,
            client_mode=client.mode,
        )
        return url, pending

    async def exchange(
        self, tracer: Tracer, pending: PendingAuthorization, *, code: str, iss: str | None
    ) -> Grant:
        """Redeem the code the fan came back with."""
        auth = await self.server_auth(tracer)
        if auth.iss_parameter_supported and iss is not None and iss.rstrip("/") != auth.issuer:
            # RFC 9207: a code from a different issuer is a mix-up attack or a
            # misconfiguration, and either way it is not redeemed here.
            raise OAuthError("invalid_issuer", "The sign-in came back from an unexpected issuer.")
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "code_verifier": pending.verifier,
            "resource": pending.resource,
        }
        return await self._token(tracer, auth, pending.client, form, label="code exchange")

    async def refresh(self, tracer: Tracer, grant: Grant) -> Grant:
        if not grant.refresh_token:
            raise OAuthError("invalid_grant", "No refresh token.")
        auth = await self.server_auth(tracer)
        # No `resource` on refresh: absent means "the one this grant has".
        form = {"grant_type": "refresh_token", "refresh_token": grant.refresh_token}
        return await self._token(tracer, auth, grant.client, form, label="refresh")

    async def revoke(self, tracer: Tracer, grant: Grant) -> None:
        """RFC 7009. Best effort: signing out must never fail on the way out."""
        try:
            auth = await self.server_auth(tracer)
            if not auth.revocation_endpoint or not grant.refresh_token:
                return
            form = {"token": grant.refresh_token, "token_type_hint": "refresh_token"}
            await self._request(
                tracer,
                "POST",
                auth.revocation_endpoint,
                label="revocation",
                data=_with_client(form, grant.client),
            )
        except Exception as exc:
            log.warning("oauth_revoke_failed", error=str(exc)[:200])

    async def _token(
        self,
        tracer: Tracer,
        auth: ServerAuth,
        client: RegisteredClient,
        form: dict[str, str],
        *,
        label: str,
    ) -> Grant:
        response = await self._request(
            tracer, "POST", auth.token_endpoint, label=label, data=_with_client(form, client)
        )
        if response.status_code != 200:
            raise _refusal(response)
        body = response.json()
        return Grant(
            access_token=str(body["access_token"]),
            refresh_token=body.get("refresh_token"),
            expires_at=time.time() + float(body.get("expires_in") or 3600),
            scope=str(body.get("scope") or ""),
            client=client,
        )

    # ── HTTP, traced ─────────────────────────────────────────────────────────
    def http(self, **kwargs: Any) -> httpx.AsyncClient:
        """An httpx client on this demo's transport (a test substitutes it)."""
        return httpx.AsyncClient(
            transport=self._transport,
            timeout=httpx.Timeout(10.0),
            headers={"User-Agent": f"fanmoot-alexa-plus-demo/{__version__}"},
            **kwargs,
        )

    async def _request(
        self,
        tracer: Tracer,
        method: str,
        url: str,
        *,
        label: str,
        data: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        path = urlsplit(url).path
        tracer.emit(
            "oauth",
            direction="out",
            method=f"{method} {path}",
            status="pending",
            summary=label,
            payload=data if data is not None else json,
            # The address, so the trace panel can hand a judge the same request
            # as cURL (W3-6). Never the /authorize URL: that one is built in
            # ``begin`` and carries the one-time ``state`` in its query.
            url=url,
            encoding="form" if data is not None else "json" if json is not None else None,
        )
        started = time.perf_counter()
        try:
            async with self.http() as http:
                response = await http.request(method, url, data=data, json=json)
        except httpx.HTTPError as exc:
            tracer.emit(
                "oauth",
                direction="in",
                method=f"{method} {path}",
                status="error",
                ms=(time.perf_counter() - started) * 1000,
                summary=f"{label} — unreachable",
            )
            raise OAuthError("unreachable", f"{url} could not be reached.") from exc
        try:
            body: Any = response.json()
        except ValueError:
            body = None
        tracer.emit(
            "oauth",
            direction="in",
            method=f"{method} {path}",
            status="ok" if response.status_code < 400 else "error",
            ms=(time.perf_counter() - started) * 1000,
            summary=f"{label} — HTTP {response.status_code}",
            payload=body,
            http_status=response.status_code,
        )
        return response


def _with_client(form: dict[str, str], client: RegisteredClient) -> dict[str, str]:
    out = {**form, "client_id": client.client_id}
    if client.client_secret:
        out["client_secret"] = client.client_secret
    return out


def _refusal(response: httpx.Response) -> OAuthError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return OAuthError(
        str(body.get("error") or f"http_{response.status_code}"),
        str(body.get("error_description") or ""),
    )


class GrantHolder:
    """One fan's grant, refreshed when it needs to be, and only once at a time.

    Serialised because the server rotates refresh tokens and treats a reused
    one as a replay (``S4-6``): two tool calls refreshing together would spend
    the same token twice and lose the grant. ``relink`` is how the shared demo
    account recovers from a lost grant; a visitor's own grant has none, so it
    is simply dropped and they are asked to sign in again.
    """

    def __init__(
        self,
        oauth: DemoOAuthClient,
        grant: Grant,
        *,
        relink: Callable[[Tracer], Awaitable[Grant]] | None = None,
    ) -> None:
        self.oauth = oauth
        self.grant: Grant | None = grant
        self._relink = relink
        self._lock = asyncio.Lock()

    @property
    def scopes(self) -> list[str]:
        return self.grant.scopes if self.grant else []

    async def token(self, tracer: Tracer) -> str | None:
        async with self._lock:
            if self.grant is None:
                return None
            if self.grant.fresh:
                return self.grant.access_token
            try:
                self.grant = await self.oauth.refresh(tracer, self.grant)
            except OAuthError as exc:
                log.warning("oauth_refresh_failed", error=exc.error)
                if self._relink is None:
                    self.grant = None
                    return None
                self.grant = await self._relink(tracer)
            return self.grant.access_token

    async def revoke(self, tracer: Tracer) -> None:
        async with self._lock:
            grant, self.grant = self.grant, None
        if grant is not None:
            await self.oauth.revoke(tracer, grant)
