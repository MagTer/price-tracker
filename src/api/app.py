"""FastAPI application factory with scheduler lifespan."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.admin import router as admin_router
from domain.scheduler import PriceCheckScheduler
from infra.db import async_session_factory, engine
from infra.providers import get_email_service, get_fetcher
from mcp_server.server import get_mcp_app

mcp_app, mcp_http_app = get_mcp_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp_http_app.lifespan(mcp_http_app):
        fetcher = get_fetcher()
        email_service = get_email_service()
        # Only pass email_service if it is actually configured
        scheduler = PriceCheckScheduler(
            session_factory=async_session_factory,
            fetcher=fetcher,
            email_service=email_service if email_service.is_configured() else None,
        )
        await scheduler.start()
        app.state.scheduler = scheduler
        yield
        await scheduler.stop()
        await fetcher.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Price Tracker", lifespan=lifespan)

    @app.get("/")
    async def root():
        return {"status": "ok", "service": "price-tracker"}

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

    # MCP server mounted at /mcp (served on dedicated mcp.<domain> subdomain)
    app.mount("/mcp", mcp_app)

    return app


app = create_app()
