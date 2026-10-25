from typing import List
from app.config import settings
from app.repositories.users import users_repo

DEFAULT_SECRET_KEYS = {
    "starpost-secret-key-change-this-in-production-random-string",
    "change-this-to-a-very-secure-random-secret-key-in-production",
}


def get_security_warnings() -> List[str]:
    """Inspect environment configuration and return security warnings if defaults are in use."""
    warnings: List[str] = []

    # Check SECRET_KEY
    current_secret = (settings.SECRET_KEY or "").strip()
    if (
        not current_secret
        or current_secret in DEFAULT_SECRET_KEYS
        or "change-this" in current_secret.lower()
        or len(current_secret) < 16
    ):
        warnings.append(
            "SECRET_KEY is set to a default or insecure value. Generate a strong random key in your .env file."
        )

    # Check Admin Password
    is_default_password = False
    if settings.ADMIN_PASSWORD and settings.ADMIN_PASSWORD.strip() == "admin":
        is_default_password = True
    elif settings.ADMIN_PASSWORD_HASH:
        if users_repo.verify_password("admin", settings.ADMIN_PASSWORD_HASH):
            is_default_password = True
    elif not settings.ADMIN_PASSWORD and not settings.ADMIN_PASSWORD_HASH:
        is_default_password = True

    if is_default_password:
        warnings.append(
            "Admin password is set to the default value ('admin'). Update ADMIN_PASSWORD_HASH in .env immediately."
        )

    return warnings
