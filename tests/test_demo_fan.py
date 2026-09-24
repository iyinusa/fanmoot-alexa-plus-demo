"""The demo fan: linked through the real flow, kept linked, rationed (W1-8)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent.demo_fan import DemoFan, DemoFanUnavailable, WriteBudget
from app.agent.oauth import DemoOAuthClient
from app.agent.trace import Tracer
from app.settings import Settings
from tests.fake_as import EMAIL, PASSWORD, RESOURCE, SCOPES, FakeAuthorizationServer


def _demo_fan(
    server: FakeAuthorizationServer, *, password: str = PASSWORD
) -> tuple[DemoFan, Tracer, list[dict[str, Any]]]:
    settings = Settings(
        mcp_url=RESOURCE,
        public_base_url="http://localhost:8090",
        demo_fan_email=EMAIL,
        demo_fan_password=password,
    )
    frames: list[dict[str, Any]] = []
    fan = DemoFan(settings, DemoOAuthClient(settings, transport=server.transport))
    return fan, Tracer(sink=frames.append), frames


async def test_the_account_is_linked_through_the_same_flow_a_visitor_uses() -> None:
    server = FakeAuthorizationServer()
    fan, tracer, frames = _demo_fan(server)

    holder = await fan.holder(tracer)

    assert holder.grant is not None
    assert holder.scopes == SCOPES
    paths = [request.url.path for request in server.requests]
    # authorize → sign in → consent → token, with PKCE verified at the end.
    assert paths[-4:] == ["/authorize", "/oauth/login", "/oauth/consent", "/token"]
    # The consent form was posted back with every scope it offered, as ticked.
    consent = next(r for r in server.requests if r.url.path == "/oauth/consent")
    assert consent.content.decode().count("scope=") == len(SCOPES)
    # And neither the address nor the password is on the trace.
    dumped = json.dumps(frames)
    assert EMAIL not in dumped and PASSWORD not in dumped


async def test_it_is_linked_once_and_shared() -> None:
    server = FakeAuthorizationServer()
    fan, tracer, _frames = _demo_fan(server)

    first = await fan.holder(tracer)
    second = await fan.holder(tracer)

    assert first is second
    assert [r.url.path for r in server.requests].count("/token") == 1


async def test_a_refused_sign_in_says_so() -> None:
    fan, tracer, _frames = _demo_fan(FakeAuthorizationServer(), password="wrong")

    with pytest.raises(DemoFanUnavailable, match="refused"):
        await fan.holder(tracer)


async def test_unconfigured_is_unavailable() -> None:
    fan = DemoFan(Settings(), DemoOAuthClient(Settings()))

    assert fan.configured is False
    with pytest.raises(DemoFanUnavailable):
        await fan.holder(Tracer())


def test_the_hourly_budget_is_shared_and_runs_out() -> None:
    budget = WriteBudget(per_hour=2)

    assert budget.spend() and budget.spend()
    assert budget.spend() is False
    assert budget.remaining() == 0
