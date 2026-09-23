"""Application factory.

One process, one origin, one Traefik route (HAD-10): the built SPA, the
``/ws`` channel it talks over, and ``/healthz``. ``fanmoot``'s AD-1 shape, so
there is no CORS to configure and no second service to deploy.

*Why a backend at all:* ADK is Python, the Bedrock credentials must never reach
a browser, and the MCP session — including a fan's OAuth grant — has to be held
server-side. W1 fills that in behind ``/ws``; W0 is the frame it hangs on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__, health, spa, ws
from app.logging_config import configure_logging, get_logger
from app.middleware import RequestIdMiddleware, SecurityHeadersMiddleware
from app.settings import Settings, get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.env)
    get_logger("startup").info(
        "demo_starting",
        env=settings.env,
        version=__version__,
        base_url=settings.public_base_url,
        mcp_url=settings.mcp_url,
        # Said out loud because an image built without it serves no demo.
        spa_built=spa.is_built(),
    )
    yield
    get_logger("shutdown").info("demo_stopping")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Fanmoot on Alexa+",
        version=__version__,
        lifespan=lifespan,
        # No public schema: the only API is `/ws`, which OpenAPI cannot
        # describe, and a docs page on a judge-facing origin is noise.
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )

    app.add_middleware(
        SecurityHeadersMiddleware, https=settings.public_base_url.startswith("https://")
    )
    # Outermost, so every request that is handled gets an id and a log line.
    app.add_middleware(RequestIdMiddleware)

    app.include_router(health.router)
    app.include_router(ws.router)
    # Last: the catch-all answers only what nothing above claimed.
    app.include_router(spa.router)

    return app


app = create_app()
