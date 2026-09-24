"""The demo as an OAuth 2.1 client: discovery, PKCE, exchange, refresh (W1-7)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from app.agent.oauth import DemoOAuthClient, GrantHolder, OAuthError, pkce_pair
from app.agent.trace import Tracer
from app.settings import Settings
from tests.fake_as import ISSUER, RESOURCE, SCOPES, FakeAuthorizationServer


def _client(
    server: FakeAuthorizationServer, **settings: Any
) -> tuple[DemoOAuthClient, Tracer, list[dict[str, Any]]]:
    frames: list[dict[str, Any]] = []
    config = Settings(mcp_url=RESOURCE, public_base_url="http://localhost:8090", **settings)
    return DemoOAuthClient(config, transport=server.transport), Tracer(sink=frames.append), frames


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_pkce_is_s256_over_an_unreserved_verifier() -> None:
    verifier, challenge = pkce_pair()

    assert 43 <= len(verifier) <= 128
    digest = hashlib.sha256(verifier.encode()).digest()
    assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def test_the_authorize_url_is_built_from_discovery() -> None:
    server = FakeAuthorizationServer()
    oauth, tracer, frames = _client(server)

    url, pending = await oauth.begin(tracer)

    assert url.startswith(f"{ISSUER}/authorize?")
    query = _query(url)
    assert query["response_type"] == "code"
    assert query["code_challenge_method"] == "S256"
    assert query["resource"] == RESOURCE
    assert query["scope"].split() == SCOPES
    assert query["state"] == pending.state
    assert query["redirect_uri"] == "http://localhost:8090/oauth/callback"
    # Discovery took the RFC 9728 path-inserted address first.
    assert str(server.requests[0].url) == f"{ISSUER}/.well-known/oauth-protected-resource/mcp"
    # On http://localhost the demo registers itself (DCR), once.
    assert query["client_id"] == "fmcp_1" and pending.client.mode == "dcr"
    await oauth.begin(tracer)
    assert len(server.registered) == 1
    assert server.registered[0]["token_endpoint_auth_method"] == "none"
    # And the trace names what was used, without the one-time values.
    start = next(f for f in frames if f["method"] == "GET /authorize")
    assert start["pkce"] == "S256"
    assert start["payload"]["state"] == "[redacted]"


async def test_an_https_deployment_uses_a_client_id_metadata_document() -> None:
    server = FakeAuthorizationServer()
    oauth = DemoOAuthClient(
        Settings(mcp_url=RESOURCE, public_base_url="https://alexaplus.fanmoot.com"),
        transport=server.transport,
    )

    url, pending = await oauth.begin(Tracer())

    assert pending.client.mode == "metadata_document"
    assert _query(url)["client_id"] == "https://alexaplus.fanmoot.com/oauth/client-metadata.json"
    assert server.registered == []
    document = oauth.metadata_document()
    assert document["client_id"] == pending.client.client_id
    assert document["redirect_uris"] == ["https://alexaplus.fanmoot.com/oauth/callback"]


async def test_a_static_client_is_used_when_configured() -> None:
    server = FakeAuthorizationServer()
    oauth, tracer, _frames = _client(server, oauth_client_id="demo-static", oauth_client_secret="s")

    _url, pending = await oauth.begin(tracer)

    assert pending.client.mode == "static"
    assert pending.client.client_secret == "s"


async def test_the_code_is_redeemed_with_its_verifier() -> None:
    server = FakeAuthorizationServer()
    oauth, tracer, frames = _client(server)
    url, pending = await oauth.begin(tracer)
    returned = server.sign_in_as_a_browser(url)

    grant = await oauth.exchange(tracer, pending, code=returned["code"], iss=returned["iss"])

    assert grant.access_token in server.issued_access
    assert grant.scopes == SCOPES
    assert grant.fresh
    # No token, verifier or code ever reaches a trace frame.
    dumped = json.dumps(frames)
    assert grant.access_token not in dumped
    assert grant.refresh_token is not None and grant.refresh_token not in dumped
    assert pending.verifier not in dumped


async def test_a_code_from_another_issuer_is_refused() -> None:
    server = FakeAuthorizationServer()
    oauth, tracer, _frames = _client(server)
    url, pending = await oauth.begin(tracer)
    returned = server.sign_in_as_a_browser(url)

    with pytest.raises(OAuthError) as refused:
        await oauth.exchange(tracer, pending, code=returned["code"], iss="https://evil.example")
    assert refused.value.error == "invalid_issuer"


async def test_an_expiring_token_is_refreshed_once_even_when_asked_twice() -> None:
    server = FakeAuthorizationServer()
    oauth, tracer, _frames = _client(server)
    url, pending = await oauth.begin(tracer)
    returned = server.sign_in_as_a_browser(url)
    grant = await oauth.exchange(tracer, pending, code=returned["code"], iss=returned["iss"])
    grant.expires_at = time.time()  # due now
    holder = GrantHolder(oauth, grant)

    first, second = await asyncio.gather(holder.token(tracer), holder.token(tracer))

    # One refresh, not two: the server treats a reused refresh token as a
    # replay, so two concurrent refreshes would lose the grant.
    assert first == second
    refreshes = [
        r for r in server.requests if r.url.path == "/token" and b"refresh_token" in r.content
    ]
    assert len(refreshes) == 1


async def test_a_lost_grant_is_dropped_or_relinked() -> None:
    server = FakeAuthorizationServer()
    oauth, tracer, _frames = _client(server)
    url, pending = await oauth.begin(tracer)
    returned = server.sign_in_as_a_browser(url)
    grant = await oauth.exchange(tracer, pending, code=returned["code"], iss=returned["iss"])
    grant.expires_at = time.time()
    server.refresh_tokens.clear()  # revoked on the server

    assert await GrantHolder(oauth, grant).token(tracer) is None

    async def relink(_tracer: Tracer) -> Any:
        fresh_url, fresh_pending = await oauth.begin(tracer)
        again = server.sign_in_as_a_browser(fresh_url)
        return await oauth.exchange(tracer, fresh_pending, code=again["code"], iss=again["iss"])

    grant.expires_at = time.time()
    assert await GrantHolder(oauth, grant, relink=relink).token(tracer) is not None


async def test_revocation_is_best_effort() -> None:
    server = FakeAuthorizationServer()
    oauth, tracer, _frames = _client(server)
    url, pending = await oauth.begin(tracer)
    returned = server.sign_in_as_a_browser(url)
    grant = await oauth.exchange(tracer, pending, code=returned["code"], iss=returned["iss"])
    holder = GrantHolder(oauth, grant)

    await holder.revoke(tracer)

    assert server.revoked == [grant.refresh_token]
    assert holder.grant is None
    assert await holder.token(tracer) is None
