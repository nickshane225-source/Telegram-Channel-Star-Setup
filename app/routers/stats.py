from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from app.repositories.sales import sales_repo
from app.repositories.settings import settings_repo
from app.routers.dashboard import get_base_context, jinja_env
from app.services.auth import auth_service
from app.services.stars import stars_service

router = APIRouter(prefix="/stats", tags=["Sales & Stats"])


@router.get("", response_class=HTMLResponse)
async def stats_index(request: Request, user=Depends(auth_service.require_auth)):
    """Render Sales & Stars financial dashboard."""
    app_settings = await settings_repo.get_settings()
    sales = await sales_repo.get_recent_sales(limit=100)
    stars_summary = await stars_service.get_dashboard_stars_summary()

    ctx = await get_base_context(request, active_page="stats")
    ctx.update({
        "sales": sales,
        "total_stars": stars_summary["total_stars"],
        "stars_today": stars_summary["today_stars"],
        "sync_interval": app_settings.sync_interval_minutes,
    })

    tmpl = jinja_env.get_template("stats/index.html")
    return HTMLResponse(tmpl.render(ctx))


@router.post("/sync")
async def sync_stars_action(user=Depends(auth_service.require_auth)):
    """Manually invoke Telegram Stars getStarTransactions sync."""
    await stars_service.sync_stars_transactions()
    return RedirectResponse(url="/stats", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/export.csv")
async def export_csv(user=Depends(auth_service.require_auth)):
    """Export transactions as downloadable CSV."""
    csv_content = await stars_service.export_sales_csv()
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="starpost_sales_export.csv"'}
    )
