"""Shared test fixtures.

The suite is **offline**. The one outbound call W0 makes — ``/healthz``'s probe
of the MCP server — is substituted, and the SPA is a small bundle written to a
temporary directory, so no test needs a network or a ``vite build``.

The environment is configured **before** ``app`` is imported: ``Settings`` is
read once and cached, and ``app.main`` builds the application at import time.
Hence the ``os.environ`` block above the imports, and ruff's E402 waiver.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("FMD_ENV", "test")
os.environ.setdefault("FMD_PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("FMD_MCP_URL", "https://mcp.fanmoot.test/mcp")

import pytest
from fastapi.testclient import TestClient

from app import spa
from app.main import create_app

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


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client
