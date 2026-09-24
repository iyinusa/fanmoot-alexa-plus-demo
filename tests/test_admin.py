"""``/api/admin/models``: the chain on a running demo, and what it has cost (HAD-9)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from app.agent.usage import LEDGER
from app.main import create_app
from app.settings import Settings

TOKEN = "operator-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def operator(configure: Callable[..., Settings]) -> Iterator[TestClient]:
    configure(
        admin_token=TOKEN,
        model_chain="bedrock/a,gemini/b",
        provider_daily_cap_usd='{"bedrock": 25}',
    )
    with TestClient(create_app()) as test_client:
        yield test_client


def test_absent_without_a_configured_token(client: TestClient) -> None:
    assert client.get("/api/admin/models", headers=AUTH).status_code == 404


def test_a_wrong_token_is_refused(operator: TestClient) -> None:
    assert operator.get("/api/admin/models").status_code == 401
    assert (
        operator.get("/api/admin/models", headers={"Authorization": "Bearer no"}).status_code == 401
    )


def test_reports_the_chain_and_todays_spend(operator: TestClient) -> None:
    LEDGER.record("bedrock", 1000, 100, 0.25)

    report = operator.get("/api/admin/models", headers=AUTH).json()

    assert [rung["model"] for rung in report["chain"]] == ["bedrock/a", "gemini/b"]
    assert report["override"] is False
    bedrock = report["usage"]["providers"]["bedrock"]
    assert bedrock["cap_usd"] == 25
    assert bedrock["cost_usd"] >= 0.25


def test_the_chain_is_reordered_without_a_redeploy(operator: TestClient) -> None:
    replaced = operator.put(
        "/api/admin/models", headers=AUTH, json={"chain": ["gemini/b", "bedrock/a"]}
    )
    assert replaced.status_code == 200
    assert [rung["model"] for rung in replaced.json()["chain"]] == ["gemini/b", "bedrock/a"]
    assert replaced.json()["override"] is True

    # The next session's facts already name the new primary.
    with operator.websocket_connect("/ws") as socket:
        from tests.test_ws import until

        assert until(socket, "facts")["models"]["primary"] == "gemini/b"

    cleared = operator.delete("/api/admin/models", headers=AUTH).json()
    assert [rung["model"] for rung in cleared["chain"]] == ["bedrock/a", "gemini/b"]


def test_a_model_without_its_provider_is_refused(operator: TestClient) -> None:
    response = operator.put("/api/admin/models", headers=AUTH, json={"chain": ["nova-pro"]})

    assert response.status_code == 422
