from datetime import datetime
from math import ceil
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger
from app.models.post import PostPhoto, PostStatus
from app.plugins.manager import get_plugin_context
from app.repositories.posts import posts_repo
from app.repositories.purchases import purchases_repo
from app.repositories.settings import settings_repo
from app.repositories.templates import templates_repo
from app.routers.dashboard import get_base_context, jinja_env
from app.services.auth import auth_service
from app.services.media import media_service
from app.services.posts import post_service
from app.services.scheduler import schedule_service
from app.utils.timezone import parse_local_datetime_to_utc

router = APIRouter(prefix="/posts", tags=["Posts"])


@router.get("", response_class=HTMLResponse)
async def list_posts(
    request: Request,
    status: str = Query("all"),
    hidden_mode: str = Query("all"),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    user=Depends(auth_service.require_auth),
):
    """List posts with search, status filtering, hidden_mode filtering and pagination."""
    posts, total = await posts_repo.list_posts(
        status=status,
        search=search,
        hidden_mode=hidden_mode,
        page=page,
        page_size=page_size,
        include_deleted=False
    )
    total_pages = max(1, ceil(total / page_size))

    ctx = await get_base_context(request, active_page="posts")
    ctx.update({
        "posts": posts,
        "total": total,
        "current_status": status,
        "current_hidden_mode": hidden_mode,
        "search": search or "",
        "current_page": page,
        "total_pages": total_pages,
    })

    tmpl = jinja_env.get_template("posts/index.html")
    return HTMLResponse(tmpl.render(ctx))


@router.get("/create", response_class=HTMLResponse)
async def create_post_page(request: Request, user=Depends(auth_service.require_auth)):
    """Render the post creation studio."""
    app_settings = await settings_repo.get_settings()
    all_templates = await templates_repo.list_all()

    ctx = await get_base_context(request, active_page="posts")
    ctx.update({
        "default_price": app_settings.default_price,
        "default_timezone": app_settings.timezone,
        "templates": all_templates,
    })

    tmpl = jinja_env.get_template("posts/create.html")
    return HTMLResponse(tmpl.render(ctx))


@router.post("/create")
async def create_post_action(
    request: Request,
    photos: List[UploadFile] = File(...),
    title: Optional[str] = Form(None),
    bulk_prefix: Optional[str] = Form("Photo Drop"),
    caption: str = Form(""),
    star_count: int = Form(50),
    action_type: str = Form("publish_now"),
    is_bulk: str = Form("false"),
    scheduled_at: Optional[str] = Form(None),
    bulk_start_time: Optional[str] = Form(None),
    bulk_interval_hours: Optional[float] = Form(3.0),
    hidden_text: Optional[str] = Form(""),
    hidden_mode: Optional[str] = Form("off"),
    card_style: Optional[str] = Form("blurred photo background"),
    user=Depends(auth_service.require_auth),
):
    """Handle upload, validation, compression and post creation or bulk auto-scheduling with timezone support."""
    plugin_ctx = get_plugin_context()
    watermark_hook = plugin_ctx.hooks.get("apply_watermark", [None])[0] if plugin_ctx else None

    processed_photos: List[PostPhoto] = []
    for f in photos:
        if f.filename:
            photo = await media_service.save_and_process_upload(
                upload_file=f,
                apply_watermark_hook=watermark_hook
            )
            processed_photos.append(photo)

    if not processed_photos:
        raise HTTPException(status_code=400, detail="At least one photo must be provided.")

    app_settings = await settings_repo.get_settings()
    user_tz = app_settings.timezone or "UTC"

    # Bulk Mode
    if is_bulk.lower() == "true":
        start_dt = parse_local_datetime_to_utc(bulk_start_time, user_tz) if bulk_start_time else datetime.utcnow()
        interval = float(bulk_interval_hours or 3.0)

        created_posts = await post_service.bulk_create_posts(
            photos=processed_photos,
            star_count=star_count,
            base_caption=caption,
            start_time=start_dt,
            interval_hours=interval,
            bulk_prefix=bulk_prefix or "Photo Drop",
            hidden_text=hidden_text or "",
            hidden_mode=hidden_mode or "off",
            card_style=card_style or "blurred photo background",
        )

        for p in created_posts:
            if p.scheduled_at:
                schedule_service.schedule_post(p.id, p.scheduled_at)

        return RedirectResponse(url="/schedule", status_code=status.HTTP_303_SEE_OTHER)

    # Single Post Mode
    post_title = (title or "Paid Photo Drop").strip()
    status_to_set = PostStatus.DRAFT
    schedule_dt = None

    if action_type == "schedule" and scheduled_at:
        schedule_dt = parse_local_datetime_to_utc(scheduled_at, user_tz)
        if schedule_dt:
            status_to_set = PostStatus.SCHEDULED

    post = await post_service.create_post(
        title=post_title,
        caption=caption,
        photos=processed_photos,
        star_count=star_count,
        status=status_to_set,
        scheduled_at=schedule_dt,
        hidden_text=hidden_text or "",
        hidden_mode=hidden_mode or "off",
        card_style=card_style or "blurred photo background",
    )

    if action_type == "publish_now":
        try:
            await post_service.publish_post(post.id)
        except Exception as exc:
            logger.error(f"Immediate publish failed: {exc}")
    elif action_type == "schedule" and schedule_dt:
        schedule_service.schedule_post(post.id, schedule_dt)

    return RedirectResponse(url=f"/posts/{post.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/trash", response_class=HTMLResponse)
async def list_trash(request: Request, user=Depends(auth_service.require_auth)):
    """List soft-deleted posts."""
    posts, total = await posts_repo.list_posts(status="deleted", include_deleted=True)
    ctx = await get_base_context(request, active_page="trash")
    ctx.update({
        "posts": posts,
        "total": total,
    })
    tmpl = jinja_env.get_template("posts/trash.html")
    return HTMLResponse(tmpl.render(ctx))


@router.get("/{post_id}", response_class=HTMLResponse)
async def view_post(post_id: str, request: Request, user=Depends(auth_service.require_auth)):
    """View post details and full media preview."""
    post = await post_service.get_post_or_404(post_id)
    buyers_count = await purchases_repo.count_by_post(post.id)
    ctx = await get_base_context(request, active_page="posts")
    ctx.update({"post": post, "buyers_count": buyers_count})
    tmpl = jinja_env.get_template("posts/view.html")
    return HTMLResponse(tmpl.render(ctx))


@router.get("/{post_id}/edit", response_class=HTMLResponse)
async def edit_post_page(post_id: str, request: Request, user=Depends(auth_service.require_auth)):
    """Render post editor."""
    post = await post_service.get_post_or_404(post_id)
    ctx = await get_base_context(request, active_page="posts")
    ctx.update({"post": post})
    tmpl = jinja_env.get_template("posts/edit.html")
    return HTMLResponse(tmpl.render(ctx))


@router.post("/{post_id}/edit")
async def edit_post_action(
    post_id: str,
    title: str = Form(...),
    caption: str = Form(""),
    star_count: int = Form(50),
    scheduled_at: Optional[str] = Form(None),
    hidden_text: Optional[str] = Form(""),
    hidden_mode: Optional[str] = Form("off"),
    card_style: Optional[str] = Form("blurred photo background"),
    photos: List[UploadFile] = File(None),
    user=Depends(auth_service.require_auth),
):
    """Save updates to post (supports Mode B direct edits for published posts)."""
    post = await post_service.get_post_or_404(post_id)
    app_settings = await settings_repo.get_settings()
    user_tz = app_settings.timezone or "UTC"

    update_data = {
        "title": title.strip(),
        "caption": caption.strip(),
        "star_count": star_count,
        "hidden_text": (hidden_text or "").strip(),
        "hidden_mode": hidden_mode or "off",
        "card_style": card_style or "blurred photo background",
    }

    # In Bot Mode (Mode B), price and hidden text are editable anytime without reposting
    if post.is_published and post.hidden_mode == "bot":
        if caption.strip() != post.caption:
            await post_service.edit_post_caption(post_id, caption.strip())
        await posts_repo.update(post_id, update_data)
        return RedirectResponse(url=f"/posts/{post_id}", status_code=status.HTTP_303_SEE_OTHER)

    if scheduled_at:
        schedule_dt = parse_local_datetime_to_utc(scheduled_at, user_tz)
        if schedule_dt:
            update_data["scheduled_at"] = schedule_dt
            schedule_service.schedule_post(post_id, schedule_dt)

    # If new photos uploaded
    if photos and photos[0].filename:
        new_photos = []
        for f in photos:
            if f.filename:
                p = await media_service.save_and_process_upload(f)
                new_photos.append(p)
        if new_photos:
            update_data["photos"] = [p.model_dump() for p in new_photos]

    await posts_repo.update(post_id, update_data)
    return RedirectResponse(url=f"/posts/{post_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{post_id}/edit-caption")
async def edit_caption_action(
    post_id: str,
    caption: str = Form(...),
    user=Depends(auth_service.require_auth),
):
    """Edit caption of post and sync with Telegram via editMessageCaption."""
    await post_service.edit_post_caption(post_id, caption)
    return RedirectResponse(url=f"/posts/{post_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{post_id}/repost")
async def repost_action(
    post_id: str,
    star_count: int = Form(...),
    hidden_text: Optional[str] = Form(None),
    card_style: Optional[str] = Form(None),
    photos: List[UploadFile] = File(None),
    user=Depends(auth_service.require_auth),
):
    """Execute 'Delete & Repost' flow for published media."""
    new_photos = None
    if photos and photos[0].filename:
        new_photos = []
        for f in photos:
            if f.filename:
                p = await media_service.save_and_process_upload(f)
                new_photos.append(p)

    await post_service.delete_and_repost(
        post_id=post_id,
        new_star_count=star_count,
        new_photos=new_photos,
        new_hidden_text=hidden_text,
        new_card_style=card_style,
    )
    return RedirectResponse(url=f"/posts/{post_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{post_id}/publish")
async def publish_now_action(post_id: str, user=Depends(auth_service.require_auth)):
    """Publish a draft or scheduled post immediately."""
    await post_service.publish_post(post_id)
    return RedirectResponse(url=f"/posts/{post_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{post_id}/duplicate")
async def duplicate_post_action(post_id: str, user=Depends(auth_service.require_auth)):
    """Duplicate an existing post."""
    duplicated = await post_service.duplicate_post(post_id)
    return RedirectResponse(url=f"/posts/{duplicated.id}/edit", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{post_id}/delete")
async def soft_delete_post_action(post_id: str, user=Depends(auth_service.require_auth)):
    """Move post to trash and delete from Telegram channel."""
    schedule_service.cancel_post_schedule(post_id)
    await post_service.soft_delete_post(post_id)
    return RedirectResponse(url="/posts", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{post_id}/restore")
async def restore_post_action(post_id: str, user=Depends(auth_service.require_auth)):
    """Restore post from trash to drafts."""
    await post_service.restore_post(post_id)
    return RedirectResponse(url="/posts/trash", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{post_id}/permanent-delete")
async def permanent_delete_post_action(post_id: str, user=Depends(auth_service.require_auth)):
    """Permanently delete post doc and media files from disk."""
    schedule_service.cancel_post_schedule(post_id)
    await post_service.permanent_delete_post(post_id)
    return RedirectResponse(url="/posts/trash", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/bulk-price")
async def bulk_price_action(
    post_ids: List[str] = Form(...),
    new_star_count: int = Form(...),
    user=Depends(auth_service.require_auth),
):
    """Batch update star price across selected posts."""
    for pid in post_ids:
        post = await posts_repo.get_by_id(pid)
        if post and not post.is_published:
            await posts_repo.update(pid, {"star_count": new_star_count})
    return RedirectResponse(url="/posts", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/bulk-delete")
async def bulk_delete_action(
    post_ids: List[str] = Form(...),
    user=Depends(auth_service.require_auth),
):
    """Batch soft delete selected posts."""
    for pid in post_ids:
        schedule_service.cancel_post_schedule(pid)
        await post_service.soft_delete_post(pid)
    return RedirectResponse(url="/posts", status_code=status.HTTP_303_SEE_OTHER)
