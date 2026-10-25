from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from app.models.template import TemplateModel
from app.repositories.templates import templates_repo
from app.routers.dashboard import get_base_context, jinja_env
from app.services.auth import auth_service

router = APIRouter(prefix="/templates", tags=["Templates"])


@router.get("", response_class=HTMLResponse)
async def templates_index(request: Request, user=Depends(auth_service.require_auth)):
    """Render Templates Library and manager."""
    templates = await templates_repo.list_all()

    ctx = await get_base_context(request, active_page="templates")
    ctx.update({"templates": templates})

    tmpl = jinja_env.get_template("templates/index.html")
    return HTMLResponse(tmpl.render(ctx))


@router.post("/create")
async def create_template_action(
    title: str = Form(...),
    caption: str = Form(""),
    star_count: int = Form(50),
    user=Depends(auth_service.require_auth),
):
    """Create a new reusable caption and price template."""
    tmpl = TemplateModel(
        title=title.strip(),
        caption=caption.strip(),
        star_count=star_count
    )
    await templates_repo.create(tmpl)
    return RedirectResponse(url="/templates", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{template_id}/edit")
async def edit_template_action(
    template_id: str,
    title: str = Form(...),
    caption: str = Form(""),
    star_count: int = Form(50),
    user=Depends(auth_service.require_auth),
):
    """Edit existing template."""
    await templates_repo.update(
        template_id,
        {
            "title": title.strip(),
            "caption": caption.strip(),
            "star_count": star_count
        }
    )
    return RedirectResponse(url="/templates", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{template_id}/delete")
async def delete_template_action(template_id: str, user=Depends(auth_service.require_auth)):
    """Delete a template."""
    await templates_repo.delete(template_id)
    return RedirectResponse(url="/templates", status_code=status.HTTP_303_SEE_OTHER)
