from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from app.config import TEMPLATES_DIR
from app.repositories.users import users_repo
from app.services.auth import auth_service
from jinja2 import Environment, FileSystemLoader

router = APIRouter(prefix="/auth", tags=["Authentication"])
jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    """Render the local admin login form."""
    user = await auth_service.get_current_user_optional(request)
    if user:
        return RedirectResponse(url=next or "/", status_code=status.HTTP_303_SEE_OTHER)

    tmpl = jinja_env.get_template("login.html")
    return HTMLResponse(tmpl.render(request=request, next=next, error=None))


@router.post("/login")
async def login_action(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/")
):
    """Verify credentials and issue signed session cookie."""
    is_valid = await users_repo.authenticate(username, password)
    if not is_valid:
        tmpl = jinja_env.get_template("login.html")
        return HTMLResponse(
            tmpl.render(request=request, next=next, error="Invalid username or password."),
            status_code=status.HTTP_400_BAD_REQUEST
        )

    redirect_url = next if next and not next.startswith("/auth") else "/"
    redirect_resp = RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
    auth_service.set_session_cookie(redirect_resp, username)
    return redirect_resp


@router.post("/logout")
async def logout_action(response: Response):
    """Log out admin and clear session cookie."""
    redirect_resp = RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    auth_service.clear_session_cookie(redirect_resp)
    return redirect_resp
