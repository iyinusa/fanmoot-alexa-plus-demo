"""``GET /healthz`` — which build this is, and whether what it needs is there.

Two dependencies, each failing differently:

* **the MCP server** — the demo is a client of ``mcp.fanmoot.com`` (HAD-8).
  When it is unreachable there is nothing to demonstrate, and the cause is not
  in this repo. Its own ``/healthz`` is probed, and the protocol version it
  reports is passed through, because that is the number the track turns on.
* **the SPA bundle** — an image built without it serves an API and no demo.

**It always answers 200**, like ``fanmoot-mcp``'s. Docker's HEALTHCHECK and
Traefik act on the status code, and restarting this container does not fix an
unreachable MCP server — it turns a recoverable dependency failure into a
restart loop. Liveness is the status code; dependency health is the body.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter

from app import __version__, spa
from app.logging_config import get_logger
from app.settings import get_settings

router = APIRouter(tags=["health"])
log = get_logger("health")

#: Well inside Docker's 5-second timeout, so the probe is never why it fails.
_PROBE_TIMEOUT_S = 2.0


async def probe_mcp_server(url: str) -> dict[str, Any] | None:
    """The MCP server's health body, or ``None`` when it did not answer."""
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            response = await client.get(url)
        if response.status_code != httpx.codes.OK:
            log.warning("health_mcp_status", status=response.status_code)
            return None
        body = response.json()
        return body if isinstance(body, dict) else {}
    except Exception as exc:
        log.warning("health_mcp_unreachable", error=str(exc))
        return None


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, Any]:
    settings = get_settings()
    upstream = await probe_mcp_server(settings.mcp_health_url)

    checks = {
        "mcp_server": "ok" if upstream is not None else "unreachable",
        "spa": "ok" if spa.is_built() else "not_built",
    }
    return {
        "status": "ok" if all(state == "ok" for state in checks.values()) else "degraded",
        "service": settings.service_name,
        "version": __version__,
        "env": settings.env,
        "mcp_url": settings.mcp_url,
        # Read from the server, never written here: a demo that asserted a
        # protocol version the server no longer speaks would be the demo lying.
        "mcp_protocol_version": (upstream or {}).get("mcp_protocol_version"),
        "checks": checks,
    }
