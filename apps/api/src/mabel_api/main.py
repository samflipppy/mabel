"""The FastAPI app. The `web` process group.

Serves the portal API, the inbound webhooks, and the MCP server from one
process. They share a connection pool and none of them holds a socket open —
unlike `media`, which does, and is therefore separate so a portal deploy never
drops a live call.

**Route order is not arbitrary.** Webhooks mount before the portal API so a
mistake in the portal's auth dependency can never end up applying to a webhook,
which authenticates by signature and not by session.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the pool on start, close it on stop.

    Fly sends SIGTERM on deploy. Closing cleanly means in-flight portal
    requests finish rather than erroring in somebody's browser.
    """
    del app
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    yield
    from mabel_db.tenant import dispose_engine

    await dispose_engine()


def allowed_origins() -> list[str]:
    """The portal, and nothing else.

    Read from the environment rather than hardcoded so staging works, but with
    no wildcard fallback: `*` with credentials is the CORS mistake that makes
    every other auth control moot.
    """
    configured = os.environ.get("PORTAL_ORIGINS", "")
    origins = [origin.strip() for origin in configured.split(",") if origin.strip()]
    return origins or ["https://app.hiremabel.com"]


def create_app() -> FastAPI:
    app = FastAPI(
        title="Mabel",
        version="2.0.0",
        description="Mabel answers the phone when a contractor can't.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Webhooks first. They authenticate by signature, and mounting them above
    # the portal API keeps a mistake in the session dependency away from them.
    from mabel_api.webhooks import stripe, telnyx

    app.include_router(telnyx.router)
    app.include_router(stripe.router)

    from mabel_mcp.server import router as mcp_router

    app.include_router(mcp_router)

    from mabel_api.routes import (
        billing,
        calls,
        config,
        contacts,
        dashboard,
        leads,
        onboarding,
        reports,
        settings,
        test_call,
    )

    app.include_router(dashboard.router)
    app.include_router(billing.router)
    app.include_router(calls.router)
    app.include_router(leads.router)
    app.include_router(contacts.router)
    app.include_router(config.router)
    app.include_router(test_call.router)
    app.include_router(reports.router)
    app.include_router(settings.router)
    app.include_router(onboarding.router)

    @app.get("/health")
    async def health() -> JSONResponse:
        """Liveness only.

        Deliberately does not touch the database. A health check that fails
        when Postgres blips takes the whole app out of rotation over something
        the app cannot fix, and then the MCP server stops answering tool calls
        mid-conversation.
        """
        return JSONResponse({"status": "ok", "version": "2.0.0"})

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        """Readiness. This one does touch the database, because a process that
        cannot reach Postgres should not receive portal traffic."""
        from mabel_db.tenant import admin_scope
        from sqlalchemy import text

        try:
            async with admin_scope(reason="readiness probe", engine=None) as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - the probe reports, never raises
            logger.warning("readiness probe failed: %s", type(exc).__name__)
            return JSONResponse({"status": "degraded"}, status_code=503)
        return JSONResponse({"status": "ready"})

    return app


app = create_app()


def openapi_schema() -> dict[str, Any]:
    """Exposed so `tests/property/` can assert on the whole route surface, and
    so the portal's TypeScript client can be generated from it."""
    return app.openapi()
