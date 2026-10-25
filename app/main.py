from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.config import MEDIA_DIR, STATIC_DIR, TEMPLATES_DIR, settings
from app.db import close_db_connection, connect_to_db, init_indexes
from app.plugins.manager import load_plugins
from app.repositories.feature_flags import feature_flags_repo
from app.routers import (
    auth_router,
    buyers_router,
    dashboard_router,
    posts_router,
    schedule_router,
    settings_router,
    stats_router,
    templates_router,
)
from app.services.bot_listener import bot_listener
from app.services.scheduler import schedule_service
from app.utils.security import get_security_warnings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifespan manager."""
    logger.info("Initializing StarPost Manager...")

    # Startup Security Check
    security_warnings = get_security_warnings()
    for w in security_warnings:
        logger.warning(f"SECURITY ADVISORY: {w}")

    # 1. Connect to MongoDB
    db_ok = await connect_to_db()
    if db_ok:
        await init_indexes()
        # 2. Register core feature flags
        await feature_flags_repo.register_flag(
            key="bot_delivery",
            name="Mode B: Bot Delivery",
            description="Runs a background long-polling listener to deliver hidden text directly in buyer private chats.",
            default_enabled=False,
        )

        # 3. Auto-load plugins
        await load_plugins(app)
        # 4. Start APScheduler and reload jobs from MongoDB
        await schedule_service.start()

        # 5. Start Bot Listener if flag is enabled
        if await feature_flags_repo.is_enabled("bot_delivery", default=False):
            await bot_listener.start()
    else:
        logger.warning(
            "Running in degraded mode: MongoDB Atlas could not be reached. "
            "Please check your IP Access List in Atlas Network Access."
        )

    yield

    # Shutdown logic
    logger.info("Shutting down StarPost Manager...")
    await bot_listener.stop()
    schedule_service.shutdown()
    await close_db_connection()


def create_app() -> FastAPI:
    """FastAPI application factory."""
    app = FastAPI(
        title="StarPost Manager",
        description="Local automation studio for Telegram Stars paid-media posts",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Mount static assets
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Mount media folder for local previews
    if MEDIA_DIR.exists():
        app.mount("/media-view", StaticFiles(directory=str(MEDIA_DIR)), name="media-view")

    # Mount routers
    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(posts_router)
    app.include_router(schedule_router)
    app.include_router(stats_router)
    app.include_router(templates_router)
    app.include_router(settings_router)
    app.include_router(buyers_router)

    # Clean fallback for browser service worker probe
    @app.get("/sw.js", include_in_schema=False)
    async def service_worker():
        from fastapi import Response
        return Response(content="// StarPost service worker", media_type="application/javascript")

    return app


app = create_app()
