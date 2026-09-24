"""``FMD_`` settings, request ids and the headers every response carries."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import VITE_DEV_ORIGINS, Settings


def test_settings_read_the_fmd_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FMD_MCP_URL", "http://127.0.0.1:8080/mcp/")
    monkeypatch.setenv("FMCP_MCP_URL", "https://wrong.example/mcp")

    settings = Settings()

    # Its own prefix only, and no trailing slash.
    assert settings.mcp_url == "http://127.0.0.1:8080/mcp"
    assert settings.mcp_health_url == "http://127.0.0.1:8080/healthz"


def test_ws_origins_in_production() -> None:
    settings = Settings(
        env="production",
        public_base_url="https://alexaplus.fanmoot.com/",
        ws_allowed_origins="https://staging.alexaplus.fanmoot.com/, ",
    )

    assert settings.ws_origins == {
        "https://alexaplus.fanmoot.com",
        "https://staging.alexaplus.fanmoot.com",
    }
    assert settings.is_production


def test_malformed_json_settings_are_refused_at_boot() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        Settings(provider_daily_cap_usd="{bedrock: 25}")
    with pytest.raises(ValueError, match="one of"):
        Settings(oauth_client_mode="password")


def test_json_settings_are_read_as_what_they_mean() -> None:
    settings = Settings(
        provider_daily_cap_usd='{"Bedrock": 25, "gemini": 5.5}',
        model_prices='{"custom/model": [1, 2]}',
    )

    assert settings.provider_caps == {"bedrock": 25.0, "gemini": 5.5}
    assert settings.model_price_overrides == {"custom/model": (1.0, 2.0)}


def test_the_oauth_addresses_follow_the_public_url() -> None:
    settings = Settings(public_base_url="https://alexaplus.fanmoot.com/")

    assert settings.oauth_redirect_uri == "https://alexaplus.fanmoot.com/oauth/callback"
    assert settings.oauth_metadata_document_url == (
        "https://alexaplus.fanmoot.com/oauth/client-metadata.json"
    )


def test_development_allows_the_vite_dev_server() -> None:
    settings = Settings(env="development", public_base_url="http://localhost:8090")

    assert set(VITE_DEV_ORIGINS) <= settings.ws_origins


def test_request_id_is_minted_or_honoured(client: TestClient, web_dir: Path) -> None:
    assert client.get("/").headers["x-request-id"]
    assert client.get("/", headers={"X-Request-ID": "trace-me"}).headers["x-request-id"] == (
        "trace-me"
    )


def test_the_shell_carries_a_strict_policy(client: TestClient, web_dir: Path) -> None:
    headers = client.get("/").headers

    csp = headers["content-security-policy"]
    assert "script-src 'self';" in csp
    assert "frame-ancestors 'none'" in csp
    assert "https://fanmoot.com" in csp  # crests, fetched at runtime (HAD-11)
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    # Plain http in the test settings, so no HSTS.
    assert "strict-transport-security" not in headers


def test_hsts_only_behind_an_https_origin(web_dir: Path) -> None:
    app = create_app(Settings(public_base_url="https://alexaplus.fanmoot.com"))
    with TestClient(app) as client:
        assert "max-age=63072000" in client.get("/").headers["strict-transport-security"]
