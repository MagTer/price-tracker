"""FastAPI application factory with scheduler lifespan."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from api.admin import router as admin_router
from domain.scheduler import PriceCheckScheduler
from infra.db import async_session_factory, engine
from infra.logbuffer import install as install_log_buffer
from infra.providers import get_email_service, get_fetcher, get_rate_limiter
from mcp_server.server import get_mcp_app

logger = logging.getLogger(__name__)

# Tail the app's own log records into the in-memory ring buffer the portal's Loggar page
# reads. Done at import (before any request) and idempotent, so tests and prod share it.
install_log_buffer()

mcp_app, mcp_http_app = get_mcp_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp_http_app.lifespan(mcp_http_app):
        # Mirrors the MCP_BEARER_TOKEN pattern in mcp_server/server.py: loud at
        # startup, but never a crash — the app still serves everything that does
        # not need an LLM.
        if not os.getenv("OPENROUTER_API_KEY"):
            logger.error(
                "OPENROUTER_API_KEY not set - LLM price extraction and enrichment "
                "are effectively disabled until it is configured"
            )
        fetcher = get_fetcher()
        email_service = get_email_service()
        # Only pass email_service if it is actually configured
        scheduler = PriceCheckScheduler(
            session_factory=async_session_factory,
            fetcher=fetcher,
            email_service=email_service if email_service.is_configured() else None,
            # Share the process-wide politeness ledger so background checks and the
            # interactive quick-add fetches throttle against ONE per-store budget.
            rate_limiter=get_rate_limiter(),
        )
        await scheduler.start()
        app.state.scheduler = scheduler
        yield
        await scheduler.stop()
        await fetcher.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Price Tracker", lifespan=lifespan)

    # Old bookmark compatibility: the UI lived under /admin until 2026-07-13
    # (holdover from the source platform where OpenWebUI owned "/").
    @app.get("/admin", include_in_schema=False)
    @app.get("/admin/", include_in_schema=False)
    async def legacy_admin_redirect():
        return RedirectResponse(url="/", status_code=308)

    @app.get("/health")
    async def health():
        db_ok = True
        try:
            async with asyncio.timeout(3):
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
        except Exception:
            db_ok = False

        scheduler = getattr(app.state, "scheduler", None)
        scheduler_running = bool(scheduler and scheduler.get_status()["running"])

        return JSONResponse(
            {
                "status": "ok" if db_ok else "degraded",
                "db": db_ok,
                "scheduler_running": scheduler_running,
            },
            # 503 makes the container healthcheck surface a dead DB in Dokploy
            status_code=200 if db_ok else 503,
        )

    app.include_router(admin_router)

    # Vendored JS (Chart.js) — self-hosted so the portal has no CDN
    # dependency; public library files, so no auth on this mount.
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="static",
    )

    # MCP server mounted at /mcp — the ingress bypasses the Entra gate for
    # exactly this path; bearer-only, fail-closed auth (D-29)
    app.mount("/mcp", mcp_app)

    return app


app = create_app()
