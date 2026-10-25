import csv
import io
from math import ceil
from typing import Optional
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from app.repositories.posts import posts_repo
from app.repositories.purchases import purchases_repo
from app.routers.dashboard import get_base_context, jinja_env
from app.services.auth import auth_service

router = APIRouter(prefix="/buyers", tags=["Buyers"])


@router.get("", response_class=HTMLResponse)
async def list_buyers(
    request: Request,
    post_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user=Depends(auth_service.require_auth),
):
    """Render global or per-post buyers directory with search, pagination and CSV export."""
    purchases, total = await purchases_repo.list_purchases(
        post_id=post_id,
        search=search,
        page=page,
        page_size=page_size
    )
    total_pages = max(1, ceil(total / page_size))

    # Fetch post title mapping for display
    posts, _ = await posts_repo.list_posts(page=1, page_size=200)
    post_map = {p.id: p.title for p in posts}

    # Selected post if filtering
    selected_post = None
    if post_id:
        selected_post = await posts_repo.get_by_id(post_id)

    ctx = await get_base_context(request, active_page="buyers")
    ctx.update({
        "purchases": purchases,
        "total": total,
        "current_page": page,
        "total_pages": total_pages,
        "search": search or "",
        "post_id": post_id or "",
        "selected_post": selected_post,
        "post_map": post_map,
        "all_posts": posts,
    })

    tmpl = jinja_env.get_template("buyers/index.html")
    return HTMLResponse(tmpl.render(ctx))


@router.get("/export")
async def export_buyers_csv(
    post_id: Optional[str] = Query(None),
    user=Depends(auth_service.require_auth),
):
    """Export buyers transaction log as CSV download."""
    records = await purchases_repo.get_all_for_export(post_id=post_id)
    output = io.StringIO()

    if not records:
        writer = csv.writer(output)
        writer.writerow(["Buyer User ID", "Username", "Post ID", "Stars Paid", "Purchase Date (UTC)"])
    else:
        fieldnames = ["Buyer User ID", "Username", "Post ID", "Stars Paid", "Purchase Date (UTC)"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow(r)

    filename = f"buyers_{post_id or 'all'}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
