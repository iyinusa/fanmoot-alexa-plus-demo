"""Configuration for the Alexa+ demo.

Every value is read from the environment with the ``FMD_`` prefix (so
``mcp_url`` comes from ``FMD_MCP_URL``) and falls back to the default declared
here. A bare ``docker run`` with nothing set boots into a *development* posture;
production is configured through the Dokploy UI, which passes environment
variables rather than writing a file.

The prefix is its own for the same reason ``fanmoot-mcp`` has ``FMCP_``: the
three services are deployed side by side on one host, and a shared prefix would
let one service's variable silently configure another.

W0 declares only what the skeleton reads. The model chain, the AWS and Gemini
credentials and the cost ceilings (HAD-9) arrive with W1, next to the code that
uses them, rather than sitting here unread.
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Where `make dev-web` serves the SPA. Its WebSocket reaches `/ws` through the
#: Vite proxy carrying *this* origin, so development allows it by default.
VITE_DEV_ORIGINS = ("http://localhost:5174", "http://127.0.0.1:5174")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FMD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Environment & identity ───────────────────────────────────────────────
    env: str = "development"
    service_name: str = "fanmoot-alexa-plus-demo"

    # Where *this* demo lives. Its origin is the one browser origin `/ws`
    # always accepts. No trailing slash.
    public_base_url: str = "http://localhost:8090"

    # ── The MCP server this demo drives (HAD-8, HAD-12) ──────────────────────
    # The **deployed** server, by default: the demo exists to show the real
    # thing answering, so a local server is something a developer opts into.
    mcp_url: str = "https://mcp.fanmoot.com/mcp"

    # ── /ws ──────────────────────────────────────────────────────────────────
    # Further browser origins allowed to open `/ws`, comma-separated. The
    # demo's own origin is always allowed and a client that sends no `Origin`
    # (a test, a script) is unaffected; this is for a staging host or a
    # preview deploy. A WebSocket is not covered by CORS, so without this check
    # any page on the internet could open a session here and spend this demo's
    # model budget through a visitor's browser.
    ws_allowed_origins: str = ""

    # The largest frame a browser may send. A turn is a sentence a fan said
    # aloud; anything near this size is not one.
    ws_max_frame_bytes: int = 16_384

    @field_validator("public_base_url", "mcp_url")
    @classmethod
    def _no_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def is_production(self) -> bool:
        return self.env.strip().lower() == "production"

    @property
    def ws_origins(self) -> frozenset[str]:
        """Every browser origin allowed to open ``/ws``."""
        origins = {_origin(self.public_base_url)}
        origins.update(_origin(item) for item in self.ws_allowed_origins.split(",") if item.strip())
        if self.env.strip().lower() == "development":
            origins.update(VITE_DEV_ORIGINS)
        return frozenset(origins)

    @property
    def mcp_health_url(self) -> str:
        """The MCP server's own ``/healthz``, beside its ``/mcp`` endpoint."""
        parts = urlsplit(self.mcp_url)
        return f"{parts.scheme}://{parts.netloc}/healthz"


def _origin(url: str) -> str:
    """``scheme://host[:port]`` — the form a browser sends in ``Origin``."""
    parts = urlsplit(url.strip())
    return f"{parts.scheme}://{parts.netloc}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
