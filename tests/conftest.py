"""Shared test fixtures.

The suite is **offline**. The MCP server it talks to is ``tests/fake_mcp.py``,
a real Streamable HTTP server on a loopback port; the model is
``tests/scripted.py``; the authorization server is an ``httpx.MockTransport``.
``/healthz``'s probe is substituted, and the SPA is a small bundle written to a
temporary directory, so no test needs a network or a ``vite build``.

``tests/test_scenario_live.py`` is the exception, and it is skipped unless
``FMD_LIVE=1`` (``make test-live``).

The environment is configured **before** ``app`` is imported: ``Settings`` is
read once and cached, and ``app.main`` builds the application at import time.
Hence the ``os.environ`` block above the imports, and ruff's E402 waiver.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

os.environ.setdefault("FMD_ENV", "test")
os.environ.setdefault("FMD_PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("FMD_MCP_URL", "https://mcp.fanmoot.test/mcp")
# The suite never reaches a model provider: the chain is a scripted model.
os.environ.setdefault("FMD_MODEL_CHAIN", "scripted/primary")
os.environ.setdefault("FMD_GEMINI_API_KEY", "")
os.environ.setdefault("FMD_ADMIN_TOKEN", "")
os.environ.setdefault("FMD_DEMO_FAN_EMAIL", "")
# The corner ticker (W2-7) calls `live_scores` on its own schedule, which would
# interleave with the calls a test asserts. The tests that are about it turn it on.
os.environ.setdefault("FMD_AMBIENT_INTERVAL_S", "0")
# The guided scenarios (W4) read what is on the same way, once per session.
os.environ.setdefault("FMD_GUIDE_TTL_S", "0")

import pytest
from fastapi.testclient import TestClient

from app import spa
from app.agent import models
from app.main import create_app
from app.settings import Settings, get_settings
from tests import fake_mcp

INDEX_HTML = "<!doctype html><title>Fanmoot on Alexa+</title><div id=root></div>"


@pytest.fixture
def web_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A built bundle, the shape `vite build` writes."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "fonts").mkdir()
    (tmp_path / "index.html").write_text(INDEX_HTML)
    (tmp_path / "assets" / "index-3f2a1b.js").write_text("console.log('fm')")
    (tmp_path / "fonts" / "lato-400.woff2").write_bytes(b"wOF2")
    (tmp_path / "theme.js").write_text("// theme")
    monkeypatch.setattr(spa, "WEB_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def unbuilt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No bundle at all — `make dev` before `make build-web`."""
    empty = tmp_path / "web"
    monkeypatch.setattr(spa, "WEB_DIR", empty)
    return empty


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    """Every test reads settings from the environment it set up, and the model
    chain override one test sets never leaks into the next."""
    get_settings.cache_clear()
    models.set_override(None)
    yield
    get_settings.cache_clear()
    models.set_override(None)


@pytest.fixture
def configure(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Settings]:
    """Set ``FMD_*`` variables for this test: ``configure(mcp_url=…)``."""

    def apply(**values: Any) -> Settings:
        for key, value in values.items():
            monkeypatch.setenv(f"FMD_{key.upper()}", str(value))
        get_settings.cache_clear()
        return get_settings()

    return apply


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def mcp_server() -> Iterator[fake_mcp.RunningServer]:
    """One fake MCP server for the whole run; each test opens its own session."""
    with fake_mcp.serve() as server:
        yield server
