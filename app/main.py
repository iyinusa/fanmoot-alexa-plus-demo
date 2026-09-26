"""Application factory.

One process, one origin, one Traefik route (HAD-10): the built SPA, the
``/ws`` channel it talks over, the OAuth popup's routes, Alexa's voice
(``/api/speech``), the ``/sandbox`` the
server's views render in, ``/healthz`` and the operator's ``/api/admin``.
``fanmoot``'s AD-1 shape, so there is no CORS to configure and no second
service to deploy.

*Why a backend at all:* ADK is Python, the Bedrock credentials must never reach
a browser, and the MCP session — including a fan's OAuth grant — has to be held
server-side. The lifespan builds the two things every visitor shares: the
OAuth client and the session registry.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__, admin, health, oauth_routes, sandbox, spa, ws
from app.agent import models
from app.agent.oauth import DemoOAuthClient
from app.logging_config import configure_logging, get_logger
from app.middleware import RequestIdMiddleware, SecurityHeadersMiddleware
from app.session import SessionRegistry
from app.settings import Settings, get_settings
from app.voice import routes as voice_routes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.env)
    oauth = DemoOAuthClient(settings)
    registry = SessionRegistry(settings, oauth=oauth)
    app.state.registry = registry
    registry.start_sweeper()
    # Production only: a test or a laptop does not need a thread importing
    # LiteLLM behind it. The task is kept so it is not collected mid-run.
    preloading = (
        asyncio.create_task(asyncio.to_thread(models.preload, settings), name="model-preload")
        if settings.is_production
        else None
    )
    get_logger("startup").info(
        "demo_starting",
        env=settings.env,
        version=__version__,
        base_url=settings.public_base_url,
        mcp_url=settings.mcp_url,
        model_chain=settings.model_chain,
        oauth_client_mode=settings.oauth_client_mode,
        voice=f"{settings.voice_id} ({settings.voice_engine})" if settings.voice_enabled else "off",
        # Said out loud because an image built without it serves no demo.
        spa_built=spa.is_built(),
    )
    yield
    if preloading is not None and not preloading.done():
        preloading.cancel()
    get_logger("shutdown").info("demo_stopping", sessions=len(registry.sessions))
    # Ends every MCP session and revokes every visitor's own grant, so a
    # redeploy leaves nothing linked on the server.
    await registry.close_all()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Fanmoot on Alexa+",
        version=__version__,
        lifespan=lifespan,
        # No public schema: the API is `/ws`, which OpenAPI cannot describe,
        # and a docs page on a judge-facing origin is noise.
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
    app.include_router(oauth_routes.router)
    app.include_router(voice_routes.router)
    app.include_router(admin.router)
    app.include_router(sandbox.router)
    # Last: the catch-all answers only what nothing above claimed.
    app.include_router(spa.router)

    return app


app = create_app()
