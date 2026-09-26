"""Signing in from the browser: popup, callback, grant on the session (W1-7, W1-8).

The whole round trip, with the authorization server faked at the HTTP layer
(``tests/fake_as.py``) and the MCP server real (``tests/fake_mcp.py``): the
popup's URL starts a PKCE authorization, the fan signs in, the callback redeems
the code server-side, and the visitor's socket hears who they now are — with
their handle read back through an authenticated ``whoami``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.agent.oauth import DemoOAuthClient
from app.main import create_app
from app.settings import Settings
from tests.fake_as import FakeAuthorizationServer
from tests.fake_mcp import RunningServer
from tests.test_ws import until


@pytest.fixture
def server() -> FakeAuthorizationServer:
    return FakeAuthorizationServer()


@pytest.fixture
def app_client(
    configure: Callable[..., Settings], mcp_server: RunningServer, server: FakeAuthorizationServer
) -> Iterator[TestClient]:
    settings = configure(mcp_url=mcp_server.url)
    with TestClient(create_app()) as test_client:
        registry = test_client.app.state.registry  # type: ignore[attr-defined]
        registry.oauth = DemoOAuthClient(settings, transport=server.transport)
        yield test_client


def test_a_visitor_signs_in_through_the_popup(
    app_client: TestClient, server: FakeAuthorizationServer
) -> None:
    with app_client.websocket_connect("/ws") as socket:
        session = until(socket, "session")

        start = app_client.get(session["sign_in_url"], follow_redirects=False)
        assert start.status_code == 302
        authorize = start.headers["location"]
        assert authorize.startswith("https://mcp.fanmoot.test/authorize?")
        assert "code_challenge_method=S256" in authorize
        # The ticket is single-use: the session sends the next one.
        assert until(socket, "session.ticket")["sign_in_url"] != session["sign_in_url"]

        returned = server.sign_in_as_a_browser(authorize)
        done = app_client.get(f"/oauth/callback?{urlencode(returned)}", follow_redirects=False)
        assert done.status_code == 303
        assert done.headers["location"].startswith("/linked?status=linked")

        auth = until(socket, "auth")

    assert auth["mode"] == "linked"
    assert auth["handle"] == "demo_fan"  # read back through an authenticated whoami
    assert auth["method"] == "OAuth 2.1 + PKCE S256"
    assert auth["client_mode"] == "dcr"
    assert "moot.write" in auth["scopes"]


def test_a_declined_sign_in_is_said_plainly(
    app_client: TestClient, server: FakeAuthorizationServer
) -> None:
    with app_client.websocket_connect("/ws") as socket:
        session = until(socket, "session")
        authorize = app_client.get(session["sign_in_url"], follow_redirects=False).headers[
            "location"
        ]
        returned = server.sign_in_as_a_browser(authorize, decision="deny")
        done = app_client.get(f"/oauth/callback?{urlencode(returned)}", follow_redirects=False)

    assert done.headers["location"] == "/linked?status=denied"


def test_an_unknown_ticket_or_state_goes_nowhere(app_client: TestClient) -> None:
    start = app_client.get("/oauth/start?ticket=forged", follow_redirects=False)
    back = app_client.get("/oauth/callback?state=forged&code=x", follow_redirects=False)

    for response in (start, back):
        assert response.status_code == 303
        assert parse_qs(urlsplit(response.headers["location"]).query)["status"] == ["error"]


def test_the_client_metadata_document(app_client: TestClient) -> None:
    document = app_client.get("/oauth/client-metadata.json").json()

    assert document["client_id"] == "http://testserver/oauth/client-metadata.json"
    assert document["redirect_uris"] == ["http://testserver/oauth/callback"]
    assert document["token_endpoint_auth_method"] == "none"
    assert document["grant_types"] == ["authorization_code", "refresh_token"]


def test_oauth_paths_are_never_the_spa(app_client: TestClient) -> None:
    assert app_client.get("/oauth/nothing-here").status_code == 404
