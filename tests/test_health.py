"""``/healthz``: always 200, with dependency health in the body."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import __version__, health


def _probe_returning(body: dict[str, Any] | None) -> Any:
    seen: list[str] = []

    async def probe(url: str) -> dict[str, Any] | None:
        seen.append(url)
        return body

    probe.seen = seen  # type: ignore[attr-defined]
    return probe


def test_ok_when_the_server_answers_and_the_bundle_is_built(
    client: TestClient, web_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = _probe_returning({"status": "ok", "mcp_protocol_version": "2025-11-25"})
    monkeypatch.setattr(health, "probe_mcp_server", probe)

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["service"] == "fanmoot-alexa-plus-demo"
    assert body["checks"] == {"mcp_server": "ok", "spa": "ok"}
    # Passed through from the server, never asserted by the demo.
    assert body["mcp_protocol_version"] == "2025-11-25"
    # The probe goes to the server's /healthz, beside /mcp — not to /mcp.
    assert probe.seen == ["https://mcp.fanmoot.test/healthz"]
    # Which models a turn would try, by name, and never a credential.
    assert body["model_chain"] == [{"model": "scripted/primary", "ready": True}]


def test_degraded_but_still_200_when_the_server_is_unreachable(
    client: TestClient, web_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "probe_mcp_server", _probe_returning(None))

    response = client.get("/healthz")

    # 200 regardless: restarting this container does not fix the MCP server.
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["mcp_server"] == "unreachable"
    assert response.json()["mcp_protocol_version"] is None


def test_reports_an_unbuilt_bundle(
    client: TestClient, unbuilt: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "probe_mcp_server", _probe_returning({"status": "ok"}))

    body = client.get("/healthz").json()

    assert body["status"] == "degraded"
    assert body["checks"]["spa"] == "not_built"


async def test_the_probe_never_raises() -> None:
    # Port 1 refuses at once; the probe must turn that into None, not an error.
    assert await health.probe_mcp_server("http://127.0.0.1:1/healthz") is None
