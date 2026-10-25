import pytest
from starlette.requests import Request
from app.config import settings
from app.repositories.users import users_repo
from app.services.auth import auth_service
from scripts.make_password_hash import generate_hash


def test_password_hashing():
    """Verify password hashing produces verifiable bcrypt hash."""
    raw = "MySuperSecret123"
    hashed = generate_hash(raw)
    assert hashed != raw
    assert users_repo.verify_password(raw, hashed) is True
    assert users_repo.verify_password("WrongPassword", hashed) is False


@pytest.mark.asyncio
async def test_user_authentication():
    """Verify authenticate method with admin credentials."""
    assert await users_repo.authenticate("admin", "admin") is True
    assert await users_repo.authenticate("ADMIN", "admin") is True
    assert await users_repo.authenticate("admin", "wrong") is False
    assert await users_repo.authenticate("hacker", "admin") is False

    # Test plain text fallback
    assert users_repo.verify_password("admin123@", "admin123@") is True
    assert users_repo.verify_password("wrong", "admin123@") is False


def test_session_token_roundtrip():
    """Verify signed session tokens create and decode properly."""
    token = auth_service.create_token("admin")
    assert isinstance(token, str)
    decoded_user = auth_service.verify_token(token)
    assert decoded_user == "admin"

    # Corrupted token
    bad_token = token + "tampered"
    assert auth_service.verify_token(bad_token) is None
