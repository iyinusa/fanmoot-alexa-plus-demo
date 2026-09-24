"""The SPA shell, its assets, and the paths it must never answer for."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import spa
from app.settings import get_settings
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


# ── W5: compression, and the share link ─────────────────────────────────────
REPO = Path(__file__).resolve().parent.parent


def test_text_assets_go_out_gzipped(client: TestClient, web_dir: Path) -> None:
    # W5-3: neither uvicorn nor Traefik compresses, and the bundle is a third
    # of its size gzipped.
    body = "export const fm = 1;\n" * 200
    (web_dir / "assets" / "index-9a8b7c.js").write_text(body)

    response = client.get("/assets/index-9a8b7c.js", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["vary"] == "Accept-Encoding"
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.text == body  # httpx decodes it


def test_a_client_without_gzip_gets_the_plain_file(client: TestClient, web_dir: Path) -> None:
    (web_dir / "assets" / "index-9a8b7c.js").write_text("export const fm = 1;\n" * 200)

    response = client.get("/assets/index-9a8b7c.js", headers={"Accept-Encoding": "identity"})

    assert "content-encoding" not in response.headers


def test_compressed_formats_are_not_gzipped_again(client: TestClient, web_dir: Path) -> None:
    (web_dir / "fonts" / "lato-400.woff2").write_bytes(b"wOF2" * 1000)

    response = client.get("/fonts/lato-400.woff2", headers={"Accept-Encoding": "gzip"})

    assert "content-encoding" not in response.headers


def test_the_shell_names_the_public_origin(
    client: TestClient, web_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # W5-6: Open Graph wants absolute URLs, and the built file cannot know
    # which origin it is deployed on.
    monkeypatch.setenv("FMD_PUBLIC_BASE_URL", "https://alexaplus.fanmoot.com")
    get_settings.cache_clear()
    (web_dir / "index.html").write_text(
        '<!doctype html><meta property="og:image" content="__FMD_PUBLIC_URL__/share/og.png">'
        '<link rel="canonical" href="__FMD_PUBLIC_URL__/">'
    )

    response = client.get("/")

    assert 'content="https://alexaplus.fanmoot.com/share/og.png"' in response.text
    assert 'href="https://alexaplus.fanmoot.com/"' in response.text
    assert spa.PUBLIC_URL_PLACEHOLDER not in response.text
    assert response.headers["cache-control"] == "no-cache, must-revalidate"


def test_the_share_directory_is_files_not_routes(client: TestClient, web_dir: Path) -> None:
    (web_dir / "share").mkdir()
    (web_dir / "share" / "og.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    assert client.get("/share/og.png").headers["content-type"] == "image/png"
    assert client.get("/share/missing.png").status_code == 404


def test_the_page_declares_a_share_card() -> None:
    # The source the bundle is built from: every tag a link preview reads,
    # with the origin left for app.spa to write in.
    # Whitespace folded, since a long tag is wrapped across lines.
    html = " ".join((REPO / "web" / "index.html").read_text().split())

    for tag in (
        '<meta property="og:title"',
        '<meta property="og:description"',
        '<meta property="og:image" content="__FMD_PUBLIC_URL__/share/og.png"',
        '<meta property="og:image:width" content="1200"',
        '<meta property="og:image:height" content="630"',
        '<meta property="og:image:alt"',
        '<meta property="og:url" content="__FMD_PUBLIC_URL__/"',
        '<meta name="twitter:card" content="summary_large_image"',
        '<link rel="canonical" href="__FMD_PUBLIC_URL__/"',
        '<meta name="description"',
    ):
        assert tag in html, tag


def test_the_share_image_is_open_graph_sized() -> None:
    # `make share-image` redraws it; this holds it to the size the tags claim.
    header = (REPO / "web" / "public" / "share" / "og.png").read_bytes()[:24]

    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", header[16:24]) == (1200, 630)


def test_robots_keeps_crawlers_off_the_machinery() -> None:
    robots = (REPO / "web" / "public" / "robots.txt").read_text()

    assert "Allow: /\n" in robots
    for path in ("/api/", "/oauth/", "/sandbox"):
        assert f"Disallow: {path}" in robots
