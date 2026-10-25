from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from app.models.post import PostStatus
from app.repositories.posts import posts_repo
from app.repositories.settings import settings_repo
from app.routers.dashboard import get_base_context, jinja_env
from app.services.auth import auth_service
from app.services.scheduler import schedule_service
from app.utils.timezone import parse_local_datetime_to_utc

router = APIRouter(prefix="/schedule", tags=["Schedule"])


@router.get("", response_class=HTMLResponse)
async def schedule_index(request: Request, user=Depends(auth_service.require_auth)):
    """Render Schedule Manager with List and Calendar representations."""
    scheduled_posts = await posts_repo.get_all_scheduled()

    # Build calendar month days representation
    now = datetime.utcnow()
    # 28-day window starting from start of current week
    start_of_week = now - timedelta(days=(now.weekday() + 1) % 7)
    calendar_days = []

    for i in range(28):
        current_day = start_of_week + timedelta(days=i)
        is_today = current_day.date() == now.date()

        matching_posts = [
            p for p in scheduled_posts
            if p.scheduled_at and p.scheduled_at.date() == current_day.date()
        ]

        calendar_days.append({
            "date": current_day,
            "day_number": current_day.day,
            "is_today": is_today,
            "posts": matching_posts,
        })

    ctx = await get_base_context(request, active_page="schedule")
    ctx.update({
        "scheduled_posts": scheduled_posts,
        "calendar_days": calendar_days,
    })

    tmpl = jinja_env.get_template("schedule/index.html")
    return HTMLResponse(tmpl.render(ctx))


@router.post("/{post_id}/reschedule")
async def reschedule_action(
    post_id: str,
    new_scheduled_at: str = Form(...),
    user=Depends(auth_service.require_auth),
):
    """Update scheduled release date/time with timezone conversion and update APScheduler."""
    try:
        app_settings = await settings_repo.get_settings()
        new_dt = parse_local_datetime_to_utc(new_scheduled_at, app_settings.timezone)
        if new_dt:
            await posts_repo.update(post_id, {"scheduled_at": new_dt, "status": PostStatus.SCHEDULED.value})
            schedule_service.schedule_post(post_id, new_dt)
    except Exception:
        pass
    return RedirectResponse(url="/schedule", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{post_id}/trigger-now")
async def trigger_now_action(post_id: str, user=Depends(auth_service.require_auth)):
    """Trigger an immediate release for a scheduled post."""
    await schedule_service.trigger_now(post_id)
    return RedirectResponse(url="/schedule", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{post_id}/toggle-pause")
async def toggle_pause_action(post_id: str, user=Depends(auth_service.require_auth)):
    """Pause or resume scheduled job."""
    # Attempt pause; if already paused or not found, try resume
    paused = schedule_service.pause_post_schedule(post_id)
    if not paused:
        schedule_service.resume_post_schedule(post_id)
    return RedirectResponse(url="/schedule", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{post_id}/cancel")
async def cancel_schedule_action(post_id: str, user=Depends(auth_service.require_auth)):
    """Cancel schedule and move post to draft."""
    schedule_service.cancel_post_schedule(post_id)
    await posts_repo.update(post_id, {"status": PostStatus.DRAFT.value, "scheduled_at": None})
    return RedirectResponse(url="/schedule", status_code=status.HTTP_303_SEE_OTHER)
