"""``/api/admin/models`` — the model chain and what it has cost today (HAD-9).

The operator's two levers, on a running demo:

``GET``     the chain in force, today's calls, tokens and estimated spend per
            provider, each provider's ceiling (W1-3c), and today's turns and
            spend against the demo-wide ceilings (W6-3)
``PUT``     ``{"chain": ["gemini/gemini-3.1-flash-lite", …]}`` — replace the chain.
            The next turn uses it. This is "Bedrock got expensive, switch to
            Gemini" without a redeploy (W1-3)
``DELETE``  drop the override and go back to ``FMD_MODEL_CHAIN``

A bearer token (``FMD_ADMIN_TOKEN``) guards all three. With none configured the
routes answer 404, exactly as if they did not exist: a public demo should not
advertise an operator surface it has not been given a key for.
"""

from __future__ import annotations

import hmac
import sys
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.agent import models
from app.agent.usage import LEDGER
from app.logging_config import get_logger
from app.settings import get_settings

router = APIRouter(prefix="/api/admin", include_in_schema=False)
log = get_logger("admin")


def _operator(authorization: str = Header(default="")) -> None:
    expected = get_settings().admin_token
    if not expected:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    scheme, _, presented = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "A bearer token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


class ChainUpdate(BaseModel):
    chain: list[str] = Field(min_length=1, max_length=8)


def _report(request: Request) -> dict[str, Any]:
    settings = get_settings()
    chain = models.current_chain(settings)
    registry = request.app.state.registry
    return {
        "chain": [
            {
                "model": rung.qualified,
                "provider": rung.provider,
                "unconfigured": models.unconfigured_reason(rung, settings),
            }
            for rung in chain
        ],
        "override": models.override_active(),
        "configured_chain": [rung.qualified for rung in models.parse_chain(settings.model_chain)],
        "usage": LEDGER.snapshot(
            settings.provider_caps,
            cap_usd=settings.daily_cap_usd,
            turn_cap=settings.daily_turn_cap,
        ),
        "sessions": len(registry.sessions),
        "process": {"rss_mb": _rss_mb()},
    }


def _rss_mb() -> float | None:
    """This process's resident memory, for the load check (W6-2).

    Current on Linux, where the container runs; elsewhere the peak, which is
    what ``getrusage`` offers.
    """
    try:
        with open("/proc/self/status") as status_file:
            for line in status_file:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    try:
        import resource  # noqa: PLC0415 - POSIX only, and only for this fallback
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Bytes on macOS, kilobytes on Linux and the BSDs.
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


@router.get("/models", dependencies=[Depends(_operator)])
async def read_models(request: Request) -> dict[str, Any]:
    return _report(request)


@router.put("/models", dependencies=[Depends(_operator)])
async def replace_chain(update: ChainUpdate, request: Request) -> dict[str, Any]:
    # A bare Gemini or Bedrock id gets its provider, as in FMD_MODEL_CHAIN.
    chain = [models.qualify(name.strip()) for name in update.chain]
    if any("/" not in name for name in chain):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Each entry is a LiteLLM model id with its provider: bedrock/…, gemini/…",
        )
    models.set_override(chain)
    log.info("model_chain_replaced", chain=chain)
    return _report(request)


@router.delete("/models", dependencies=[Depends(_operator)])
async def clear_override(request: Request) -> dict[str, Any]:
    models.set_override(None)
    return _report(request)
