"""The SPA shell, its assets, and the paths it must never answer for."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import INDEX_HTML


def test_root_serves_the_shell_uncached(client: TestClient, web_dir: Path) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.text == INDEX_HTML
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache, must-revalidate"


def test_head_is_answered_like_get(client: TestClient, web_dir: Path) -> None:
    for path in ("/", "/fonts/lato-400.woff2", "/assets/index-3f2a1b.js"):
        assert client.head(path).status_code == 200, path


def test_a_client_route_falls_back_to_the_shell(client: TestClient, web_dir: Path) -> None:
    response = client.get("/scenario/matchday")

    assert response.status_code == 200
    assert response.text == INDEX_HTML


def test_hashed_assets_are_immutable(client: TestClient, web_dir: Path) -> None:
    response = client.get("/assets/index-3f2a1b.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_fonts_go_out_as_fonts(client: TestClient, web_dir: Path) -> None:
    # python:3.12-slim has no mime.types; without the registration this would
    # be application/octet-stream in the container and correct on a laptop.
    response = client.get("/fonts/lato-400.woff2")

    assert response.status_code == 200
    assert response.headers["content-type"] == "font/woff2"


def test_a_root_file_is_served(client: TestClient, web_dir: Path) -> None:
    assert client.get("/theme.js").text == "// theme"


def test_a_missing_asset_is_a_404_not_the_shell(client: TestClient, web_dir: Path) -> None:
    for path in ("/assets/gone-000000.js", "/fonts/inter-400.woff2", "/icons/nope.png"):
        assert client.get(path).status_code == 404, path


def test_the_api_namespace_is_never_the_shell(client: TestClient, web_dir: Path) -> None:
    response = client.get("/api/nope")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"


def test_a_path_outside_the_bundle_is_not_served(
    client: TestClient, web_dir: Path, tmp_path: Path
) -> None:
    (tmp_path.parent / "secret.txt").write_text("nope")

    response = client.get("/%2e%2e/secret.txt")

    assert "nope" not in response.text


def test_an_unbuilt_bundle_says_so(client: TestClient, unbuilt: Path) -> None:
    response = client.get("/")

    assert response.status_code == 503
    assert "make build-web" in response.text
