from typing import Optional
from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from app.config import settings as env_settings
from app.repositories.feature_flags import feature_flags_repo
from app.repositories.settings import settings_repo
from app.routers.dashboard import get_base_context, jinja_env
from app.services.auth import auth_service
from app.services.bot_listener import bot_listener
from app.services.telegram import telegram_service
from app.utils.security import get_security_warnings

router = APIRouter(prefix="/settings", tags=["Settings"])


@router.get("", response_class=HTMLResponse)
async def settings_index(request: Request, user=Depends(auth_service.require_auth)):
    """Render Settings and diagnostics page."""
    app_settings = await settings_repo.get_settings()
    feature_flags = await feature_flags_repo.get_all()
    security_warnings = get_security_warnings()

    ctx = await get_base_context(request, active_page="settings")
    ctx.update({
        "settings": app_settings,
        "feature_flags": feature_flags,
        "masked_bot_token": env_settings.masked_bot_token,
        "security_warnings": security_warnings,
        "bot_listener_running": bot_listener.is_running,
    })

    tmpl = jinja_env.get_template("settings/index.html")
    return HTMLResponse(tmpl.render(ctx))


@router.post("/save")
async def save_settings_action(
    bot_token: Optional[str] = Form(None),
    channel_id: Optional[str] = Form(None),
    default_price: int = Form(50),
    timezone: str = Form("UTC"),
    theme: str = Form("midnight"),
    sync_interval_minutes: int = Form(15),
    default_caption_footer: str = Form(""),
    user=Depends(auth_service.require_auth),
):
    """Save application settings."""
    update_data = {
        "default_price": default_price,
        "timezone": timezone,
        "theme": theme,
        "sync_interval_minutes": sync_interval_minutes,
        "default_caption_footer": default_caption_footer.strip(),
    }
    if bot_token and bot_token.strip():
        update_data["bot_token"] = bot_token.strip()
        telegram_service.token = bot_token.strip()
    if channel_id and channel_id.strip():
        update_data["channel_id"] = channel_id.strip()

    await settings_repo.update_settings(update_data)
    return RedirectResponse(url="/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/test-connection")
async def test_connection_endpoint(user=Depends(auth_service.require_auth)):
    """Run diagnostic test on Telegram token and channel permissions."""
    app_settings = await settings_repo.get_settings()
    if app_settings.bot_token:
        telegram_service.token = app_settings.bot_token

    report = await telegram_service.test_connection(channel_id=app_settings.channel_id)
    return JSONResponse(report)


@router.post("/feature-flags/{key}/toggle")
async def toggle_flag_action(key: str, user=Depends(auth_service.require_auth)):
    """Toggle a feature flag or plugin."""
    current_state = await feature_flags_repo.is_enabled(key, default=False)
    new_state = not current_state
    await feature_flags_repo.set_enabled(key, new_state)

    if key == "bot_delivery":
        if new_state:
            await bot_listener.start()
        else:
            await bot_listener.stop()

    return RedirectResponse(url="/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/set-theme")
async def set_theme_action(theme: str = Query(...), redirect: str = Query("/"), user=Depends(auth_service.require_auth)):
    """Quick theme switcher."""
    if theme in ("midnight", "neon", "clean-light"):
        await settings_repo.update_settings({"theme": theme})
    return RedirectResponse(url=redirect or "/", status_code=status.HTTP_303_SEE_OTHER)
