import io
import os
from pathlib import Path
import pytest
from PIL import Image
import mongomock_motor

import app.db as app_db
from app.config import settings, MEDIA_DIR
from app.main import create_app
from app.models.post import PostPhoto


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch, tmp_path):
    """Set up test environment with isolated mock DB and temp media directory."""
    # Isolated temp media directory
    test_media = tmp_path / "media"
    test_media.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.config.MEDIA_DIR", test_media)
    monkeypatch.setattr("app.services.media.MEDIA_DIR", test_media)

    # Mock MongoDB with in-memory mongomock_motor
    mock_client = mongomock_motor.AsyncMongoMockClient()
    mock_db = mock_client["test_starpost"]
    monkeypatch.setattr(app_db, "client", mock_client)
    monkeypatch.setattr(app_db, "db", mock_db)

    # Test settings
    settings.TELEGRAM_BOT_TOKEN = "123456:TEST_TOKEN_ABC_DEF_12345"
    settings.TELEGRAM_CHANNEL_ID = "-1001234567890"
    settings.ADMIN_USERNAME = "admin"
    # Bcrypt hash for password "admin"
    settings.ADMIN_PASSWORD_HASH = "$2b$12$Qhgqj/wjsY4u3L2ZCF7tz.zWkCyJRdU7SaMc4AgPNVJehQoO2tbiG"

    yield mock_db


@pytest.fixture
def sample_image_path(tmp_path):
    """Generate a clean JPEG test image file on disk inside media dir."""
    test_media = tmp_path / "media"
    test_media.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (800, 600), color=(73, 109, 137))
    file_path = test_media / "test_photo.jpg"
    img.save(file_path, "JPEG")
    return str(file_path)


@pytest.fixture
def sample_photos(sample_image_path):
    """Return a list with one PostPhoto model."""
    return [
        PostPhoto(
            path=sample_image_path,
            original_filename="test_photo.jpg",
            width=800,
            height=600,
            size_bytes=1024
        )
    ]
