import io
from pathlib import Path
import pytest
from PIL import Image
from fastapi import UploadFile
from app.services.media import MediaService, MAX_DIMENSION


@pytest.mark.asyncio
async def test_process_valid_jpeg(tmp_path):
    """Test standard JPEG upload processing."""
    svc = MediaService(media_dir=tmp_path / "media")

    # Generate test image in memory
    buf = io.BytesIO()
    img = Image.new("RGB", (640, 480), color="blue")
    img.save(buf, format="JPEG")
    buf.seek(0)

    upload = UploadFile(filename="photo.jpg", file=buf)
    processed = await svc.save_and_process_upload(upload)

    assert Path(processed.path).exists()
    assert processed.width == 640
    assert processed.height == 480
    assert processed.size_bytes > 0


@pytest.mark.asyncio
async def test_process_png_with_transparency(tmp_path):
    """Test PNG with transparency converts to clean RGB JPEG."""
    svc = MediaService(media_dir=tmp_path / "media")

    buf = io.BytesIO()
    img = Image.new("RGBA", (400, 400), color=(255, 0, 0, 128))
    img.save(buf, format="PNG")
    buf.seek(0)

    upload = UploadFile(filename="transparent.png", file=buf)
    processed = await svc.save_and_process_upload(upload)

    assert Path(processed.path).exists()
    assert Path(processed.path).suffix.lower() == ".jpg"
    assert processed.width == 400


@pytest.mark.asyncio
async def test_reject_invalid_extension(tmp_path):
    """Verify non-image extensions are rejected with ValueError."""
    svc = MediaService(media_dir=tmp_path / "media")
    upload = UploadFile(filename="script.sh", file=io.BytesIO(b"echo hello"))

    with pytest.raises(ValueError, match="Unsupported file extension"):
        await svc.save_and_process_upload(upload)


@pytest.mark.asyncio
async def test_resize_oversized_image(tmp_path):
    """Verify images exceeding Telegram max dimension are scaled down."""
    svc = MediaService(media_dir=tmp_path / "media")

    buf = io.BytesIO()
    # Create image 3000 x 1500
    img = Image.new("RGB", (3000, 1500), color="green")
    img.save(buf, format="JPEG")
    buf.seek(0)

    upload = UploadFile(filename="giant.jpg", file=buf)
    processed = await svc.save_and_process_upload(upload)

    assert processed.width <= MAX_DIMENSION
    assert processed.height <= MAX_DIMENSION
