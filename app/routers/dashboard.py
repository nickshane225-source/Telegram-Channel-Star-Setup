import os
from pathlib import Path
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader
from app.config import TEMPLATES_DIR, settings
from app.db import get_db, get_db_error
from app.plugins.manager import get_plugin_context
from app.repositories.activity import activity_repo
from app.repositories.posts import posts_repo
from app.repositories.sales import sales_repo
from app.repositories.settings import settings_repo
from app.services.auth import auth_service
from app.services.bot_listener import bot_listener
from app.services.stars import stars_service
from app.utils.sanitizer import sanitize_telegram_html_markup
from app.utils.timezone import format_utc_to_local

router = APIRouter(tags=["Dashboard"])

jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
jinja_env.filters["basename"] = lambda path: Path(path).name if path else ""
jinja_env.filters["sanitize_html"] = lambda text: sanitize_telegram_html_markup(text)
jinja_env.filters["to_timezone"] = lambda dt, tz="UTC", fmt="%b %d, %Y %H:%M": format_utc_to_local(dt, tz, fmt)


async def get_base_context(request: Request, active_page: str = "dashboard"):
    """Prepare shared context variables across all dashboard pages."""
    app_settings = await settings_repo.get_settings()
    user = await auth_service.get_current_user_optional(request)
    plugin_ctx = get_plugin_context()

    return {
        "request": request,
        "user": user,
        "active_page": active_page,
        "current_theme": app_settings.theme or "midnight",
        "current_timezone": app_settings.timezone or "UTC",
        "bot_listener_status": "running" if bot_listener.is_running else "stopped",
        "telegram_configured": bool(app_settings.bot_token and app_settings.channel_id),
        "active_channel": app_settings.channel_id,
        "plugin_nav_items": plugin_ctx.nav_items if plugin_ctx else [],
    }


@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request, user=Depends(auth_service.require_auth)):
    """Render the main dashboard overview page."""
    # Check if MongoDB is reachable
    if get_db() is None:
        error_tmpl = jinja_env.get_template("error_db.html")
        return HTMLResponse(
            error_tmpl.render(error_details=get_db_error()),
            status_code=503
        )

    counts = await posts_repo.get_dashboard_counts()
    recent_activity = await activity_repo.get_recent(limit=10)
    next_scheduled = await posts_repo.get_next_scheduled(limit=5)
    stars_summary = await stars_service.get_dashboard_stars_summary()

    ctx = await get_base_context(request, active_page="dashboard")
    ctx.update({
        "counts": counts,
        "recent_activity": recent_activity,
        "next_scheduled": next_scheduled,
        "total_stars": stars_summary["total_stars"],
        "stars_today": stars_summary["today_stars"],
    })

    tmpl = jinja_env.get_template("dashboard.html")
    return HTMLResponse(tmpl.render(ctx))


@router.get("/api/stats/chart")
async def get_chart_data(days: int = Query(7, ge=1, le=90), user=Depends(auth_service.require_auth)):
    """API endpoint providing Chart.js JSON series combining sales and purchases."""
    data = await stars_service.get_chart_data(days=days)
    return JSONResponse(data)
