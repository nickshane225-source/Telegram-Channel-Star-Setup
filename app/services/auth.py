from typing import Optional
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from loguru import logger
from app.config import settings
from app.models.user import UserSession
from app.repositories.users import users_repo

SESSION_COOKIE_NAME = "starpost_session"
MAX_AGE_SECONDS = 7 * 24 * 3600  # 7 days


class AuthService:
    """Service handling local authentication, session signing and route guards."""

    def __init__(self, secret_key: str = settings.SECRET_KEY):
        self.serializer = URLSafeTimedSerializer(secret_key)

    def create_token(self, username: str) -> str:
        """Create signed session token."""
        return self.serializer.dumps({"sub": username})

    def verify_token(self, token: str) -> Optional[str]:
        """Verify signed session token and return username."""
        try:
            data = self.serializer.loads(token, max_age=MAX_AGE_SECONDS)
            return data.get("sub")
        except (BadSignature, SignatureExpired, Exception) as exc:
            logger.debug(f"Session token verification failed: {exc}")
            return None

    def set_session_cookie(self, response: Response, username: str) -> None:
        """Set HTTP-only session cookie on response."""
        token = self.create_token(username)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=MAX_AGE_SECONDS,
            httponly=True,
            samesite="lax",
            secure=False  # Allow local network LAN access
        )

    def clear_session_cookie(self, response: Response) -> None:
        """Clear session cookie on logout."""
        response.delete_cookie(key=SESSION_COOKIE_NAME)

    async def get_current_user_optional(self, request: Request) -> Optional[UserSession]:
        """Return UserSession if authenticated, None otherwise."""
        cookie_val = request.cookies.get(SESSION_COOKIE_NAME)
        if not cookie_val:
            return None
        username = self.verify_token(cookie_val)
        if not username:
            return None
        return UserSession(username=username, is_authenticated=True)

    async def require_auth(self, request: Request) -> UserSession:
        """Route dependency requiring authentication. Redirects to login on browser requests."""
        user = await self.get_current_user_optional(request)
        if not user:
            # Check if this is an HTMX request or AJAX
            if request.headers.get("HX-Request"):
                # Send HX-Redirect header to tell HTMX to redirect the whole page
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                    headers={"HX-Redirect": f"/auth/login?next={request.url.path}"}
                )
            # Regular browser page navigation: redirect to login
            raise HTTPException(
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers={"Location": f"/auth/login?next={request.url.path}"}
            )
        return user


auth_service = AuthService()
