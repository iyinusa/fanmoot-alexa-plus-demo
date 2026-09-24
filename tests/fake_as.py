"""An authorization server shaped like ``fanmoot-mcp``'s, as an httpx transport.

Enough of the real one to hold the demo to it: discovery at the path-inserted
RFC 9728 address, RFC 8414 metadata advertising S256, DCR and client ID
metadata documents, a sign-in page and a consent page carrying a ``flow``
field, a redirect with ``code``, ``state`` and ``iss``, **PKCE verified at the
token endpoint**, and refresh tokens that rotate and whose reuse is refused.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

ISSUER = "https://mcp.fanmoot.test"
RESOURCE = f"{ISSUER}/mcp"
SCOPES = ["fan.read", "moot.write", "predict.write", "fan.write"]
EMAIL = "demo@fanmoot.test"
PASSWORD = "correct horse"


class FakeAuthorizationServer:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.registered: list[dict[str, Any]] = []
        self.pending: dict[str, dict[str, Any]] = {}
        self.codes: dict[str, dict[str, Any]] = {}
        self.refresh_tokens: dict[str, bool] = {}
        self.revoked: list[str] = []
        self.issued_access: list[str] = []
        self.iss = ISSUER

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        form = dict(parse_qs(request.content.decode())) if request.content else {}

        if path == "/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(
                200,
                json={
                    "resource": RESOURCE,
                    "authorization_servers": [ISSUER],
                    "scopes_supported": SCOPES,
                },
            )
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "revocation_endpoint": f"{ISSUER}/oauth/revoke",
                    "registration_endpoint": f"{ISSUER}/oauth/register",
                    "code_challenge_methods_supported": ["S256"],
                    "authorization_response_iss_parameter_supported": True,
                    "client_id_metadata_document_supported": True,
                    "scopes_supported": SCOPES,
                },
            )
        if path == "/oauth/register":
            body = json.loads(request.content)
            self.registered.append(body)
            return httpx.Response(201, json={**body, "client_id": f"fmcp_{len(self.registered)}"})
        if path == "/authorize":
            query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
            flow = secrets.token_urlsafe(8)
            self.pending[flow] = query
            return httpx.Response(200, html=self._login(flow))
        if path == "/oauth/login":
            flow = form["flow"][0]
            if form.get("password", [""])[0] != PASSWORD:
                return httpx.Response(400, html=self._login(flow, error=True))
            return httpx.Response(200, html=self._consent(flow))
        if path == "/oauth/consent":
            query = self.pending.pop(form["flow"][0])
            code = secrets.token_urlsafe(8)
            self.codes[code] = {**query, "scope": " ".join(form.get("scope", []))}
            location = f"{query['redirect_uri']}?" + urlencode(
                {"code": code, "state": query["state"], "iss": self.iss}
            )
            return httpx.Response(302, headers={"location": location})
        if path == "/token":
            return self._token({k: v[0] for k, v in form.items()})
        if path == "/oauth/revoke":
            self.revoked.append(form["token"][0])
            return httpx.Response(200)
        return httpx.Response(404)

    def _token(self, form: dict[str, str]) -> httpx.Response:
        if form.get("grant_type") == "authorization_code":
            issued = self.codes.pop(form.get("code", ""), None)
            if issued is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            if challenge != issued["code_challenge"] or issued["code_challenge_method"] != "S256":
                return httpx.Response(400, json={"error": "invalid_grant"})
            if (
                form.get("redirect_uri") != issued["redirect_uri"]
                or form.get("resource") != RESOURCE
            ):
                return httpx.Response(400, json={"error": "invalid_grant"})
            return self._issue(issued["scope"])
        if form.get("grant_type") == "refresh_token":
            presented = form.get("refresh_token", "")
            if not self.refresh_tokens.get(presented):
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.refresh_tokens[presented] = False  # rotated: spent
            return self._issue(" ".join(SCOPES))
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def _issue(self, scope: str) -> httpx.Response:
        access, refresh = f"at-{secrets.token_hex(6)}", f"rt-{secrets.token_hex(6)}"
        self.refresh_tokens[refresh] = True
        self.issued_access.append(access)
        return httpx.Response(
            200,
            json={
                "access_token": access,
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": refresh,
                "scope": scope,
            },
        )

    @staticmethod
    def _login(flow: str, *, error: bool = False) -> str:
        return (
            '<form method="post" action="/oauth/login" novalidate>'
            f'<input type="hidden" name="flow" value="{flow}">'
            + ("<p class=error>Email or password is incorrect</p>" if error else "")
            + '<input id="email" name="email" type="email" value="">'
            '<input id="password" name="password" type="password"></form>'
        )

    @staticmethod
    def _consent(flow: str) -> str:
        boxes = "".join(
            f'<input type="checkbox" id="scope-{i}" name="scope" value="{s}" checked>'
            for i, s in enumerate(SCOPES)
        )
        return (
            '<form method="post" action="/oauth/consent">'
            f'<input type="hidden" name="flow" value="{flow}">{boxes}'
            '<button type="submit" name="decision" value="allow">Allow</button></form>'
        )

    def sign_in_as_a_browser(
        self, authorize_url: str, *, decision: str = "allow"
    ) -> dict[str, str]:
        """What the visitor's popup does: sign in, allow, and follow the redirect.

        Returns the callback's query, as the demo's `/oauth/callback` receives it.
        """
        page = self.handle(httpx.Request("GET", authorize_url))
        flow = page.text.split('name="flow" value="')[1].split('"')[0]
        self.handle(
            httpx.Request(
                "POST",
                f"{ISSUER}/oauth/login",
                data={"flow": flow, "email": EMAIL, "password": PASSWORD},
            )
        )
        if decision != "allow":
            query = self.pending.pop(flow)
            return {"error": "access_denied", "state": query["state"]}
        done = self.handle(
            httpx.Request(
                "POST",
                f"{ISSUER}/oauth/consent",
                data={"flow": flow, "decision": "allow", "scope": SCOPES},
            )
        )
        return {k: v[0] for k, v in parse_qs(urlsplit(done.headers["location"]).query).items()}
