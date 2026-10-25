import hmac
import bcrypt
from app.config import settings


class UserRepository:
    """Authentication and user verification repository."""

    @staticmethod
    def verify_password(plain_password: str, stored_credential: str) -> bool:
        """Verify plain password against bcrypt hash or plain text fallback."""
        if not plain_password or not stored_credential:
            return False

        stored = stored_credential.strip().strip("'\"")

        # 1. If stored credential is a valid bcrypt hash ($2a$, $2b$, $2y$)
        if stored.startswith(("$2a$", "$2b$", "$2y$")):
            try:
                return bcrypt.checkpw(
                    plain_password.encode("utf-8"),
                    stored.encode("utf-8")
                )
            except Exception:
                pass

        # 2. Plain text comparison fallback (safe against timing attacks)
        # Allows users who set plain password like "admin123@" directly in .env
        return hmac.compare_digest(plain_password, stored)

    async def authenticate(self, username: str, password: str) -> bool:
        """Authenticate user against settings configuration."""
        if not username or not password:
            return False

        configured_user = (settings.ADMIN_USERNAME or "admin").strip()
        if username.strip().lower() != configured_user.lower():
            return False

        # Check ADMIN_PASSWORD_HASH
        if settings.ADMIN_PASSWORD_HASH:
            if self.verify_password(password, settings.ADMIN_PASSWORD_HASH):
                return True

        # Check ADMIN_PASSWORD fallback
        admin_plain = getattr(settings, "ADMIN_PASSWORD", None)
        if admin_plain:
            if self.verify_password(password, admin_plain):
                return True

        return False


users_repo = UserRepository()
