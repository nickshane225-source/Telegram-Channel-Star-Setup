import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.config import MEDIA_DIR, STATIC_DIR, TEMPLATES_DIR, settings
from app.db import (
    close_db_connection,
    connect_to_db,
    get_db,
    get_db_error,
    get_db_error_category,
    init_indexes,
)
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
from app.routers.dashboard import jinja_env
from app.services.auth import auth_service
from app.services.bot_listener import bot_listener
from app.services.scheduler import schedule_service
from app.utils.security import get_security_warnings

_services_started: bool = False
_plugins_loaded: bool = False
_start_services_lock: Optional[asyncio.Lock] = None
_reconnect_task: Optional[asyncio.Task] = None


def _get_start_lock() -> asyncio.Lock:
    global _start_services_lock
    if _start_services_lock is None:
        _start_services_lock = asyncio.Lock()
    return _start_services_lock


async def start_services(target_app: Optional[FastAPI] = None) -> bool:
    """
    Initialize database indexes, feature flags, plugins (once), APScheduler, and Bot Listener.
    Guarded against running twice concurrently or repeatedly once started.
    """
    global _services_started, _plugins_loaded
    lock = _get_start_lock()
    async with lock:
        if _services_started:
            return True
        if get_db() is None:
            return False

        app_instance = target_app or app
        await init_indexes()

        # Register core feature flags
        await feature_flags_repo.register_flag(
            key="bot_delivery",
            name="Mode B: Bot Delivery",
            description="Runs a background long-polling listener to deliver hidden text directly in buyer private chats.",
            default_enabled=False,
        )

        # Auto-load plugins (only once per application lifecycle)
        if not _plugins_loaded:
            await load_plugins(app_instance)
            _plugins_loaded = True

        # Start APScheduler and reload scheduled jobs from MongoDB
        await schedule_service.start()

        # Start Bot Listener if flag is enabled
        if await feature_flags_repo.is_enabled("bot_delivery", default=False):
            await bot_listener.start()

        _services_started = True
        logger.info("All StarPost Manager background services initialized and running.")
        return True


async def _background_reconnect_loop(target_app: FastAPI) -> None:
    """Background task that periodically attempts to reconnect to MongoDB when in degraded mode."""
    while True:
        try:
            interval = max(1, settings.DB_RECONNECT_INTERVAL_SECONDS)
            await asyncio.sleep(interval)
            if get_db() is None:
                logger.info("Background reconnect task attempting to connect to MongoDB...")
                ok = await connect_to_db(max_attempts=1)
                if ok:
                    await start_services(target_app)
            elif not _services_started:
                await start_services(target_app)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning(f"Background reconnect loop error: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifespan manager."""
    global _reconnect_task, _services_started
    logger.info("Initializing StarPost Manager...")

    # Startup Security Check
    security_warnings = get_security_warnings()
    for w in security_warnings:
        logger.warning(f"SECURITY ADVISORY: {w}")

    # 1. Connect to MongoDB (with startup retry backoff)
    db_ok = await connect_to_db()
    if db_ok:
        await start_services(app)
    else:
        logger.warning(
            "Running in degraded mode: MongoDB could not be reached at startup. "
            f"Background auto-reconnect will retry every {settings.DB_RECONNECT_INTERVAL_SECONDS}s."
        )

    # Always run background reconnect monitor so degraded startup or dropped DB can recover
    _reconnect_task = asyncio.create_task(_background_reconnect_loop(app))

    yield

    # Shutdown logic
    logger.info("Shutting down StarPost Manager...")
    if _reconnect_task is not None:
        _reconnect_task.cancel()
        try:
            await _reconnect_task
        except asyncio.CancelledError:
            pass
        _reconnect_task = None

    await bot_listener.stop()
    schedule_service.shutdown()
    await close_db_connection()
    _services_started = False


def create_app() -> FastAPI:
    """FastAPI application factory."""
    app = FastAPI(
        title="StarPost Manager",
        description="Local automation studio for Telegram Stars paid-media posts",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def db_availability_middleware(request: Request, call_next):
        """
        When get_db() is None:
        - Allow /static, /media-view, /login, /auth/*, /sw.js, and /api/db/retry
        - Return JSON 503 {"detail": ...} for /api/* requests
        - Return HTML 503 error_db.html for browser routes
        """
        if get_db() is None:
            path = request.url.path
            exempt_prefixes = ("/static", "/media-view", "/login", "/auth", "/sw.js", "/api/db/retry")
            if not any(path == p or path.startswith(p + "/") or path.startswith(p) for p in exempt_prefixes):
                err_msg = get_db_error() or "Database is not connected."
                err_cat = get_db_error_category() or "network_access"
                if path.startswith("/api/") or path == "/api":
                    return JSONResponse(
                        status_code=503,
                        content={"detail": err_msg, "category": err_cat},
                    )
                error_tmpl = jinja_env.get_template("error_db.html")
                return HTMLResponse(
                    error_tmpl.render(
                        error_details=err_msg,
                        error_category=err_cat,
                    ),
                    status_code=503,
                )
        return await call_next(request)

    # Mount static assets
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Mount media folder for local previews
    if MEDIA_DIR.exists():
        app.mount("/media-view", StaticFiles(directory=str(MEDIA_DIR)), name="media-view")

    # Convenience /login alias so both /login and /auth/login work seamlessly
    @app.get("/login", include_in_schema=False)
    async def login_redirect_alias(request: Request, next: str = "/"):
        return RedirectResponse(url=f"/auth/login?next={next}", status_code=307)

    # Immediate DB reconnect endpoint (auth required)
    @app.post("/api/db/retry")
    async def api_db_retry(user=Depends(auth_service.require_auth)):
        """Attempt an immediate MongoDB reconnection and start services on success."""
        if get_db() is not None:
            await start_services(app)
            return JSONResponse(
                status_code=200,
                content={"connected": True, "status": "connected", "detail": "Database is connected."},
            )

        ok = await connect_to_db(max_attempts=1)
        if ok:
            await start_services(app)
            return JSONResponse(
                status_code=200,
                content={"connected": True, "status": "connected", "detail": "Successfully reconnected to MongoDB."},
            )

        return JSONResponse(
            status_code=503,
            content={
                "connected": False,
                "status": "reconnecting",
                "category": get_db_error_category() or "network_access",
                "detail": get_db_error() or "Could not connect to MongoDB.",
            },
        )

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
