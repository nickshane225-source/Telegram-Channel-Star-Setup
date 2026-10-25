import io
import pytest
from PIL import Image
from fastapi import FastAPI
from app.plugins.manager import load_plugins, PluginContext
from app.plugins.example_watermark.plugin import apply_watermark
from app.repositories.feature_flags import feature_flags_repo


@pytest.mark.asyncio
async def test_watermark_processing():
    """Verify watermark stamps image and preserves dimensions."""
    img = Image.new("RGB", (600, 400), color=(100, 150, 200))
    watermarked = apply_watermark(img, text="★ Test Watermark", opacity=150)

    assert watermarked.size == (600, 400)
    assert watermarked.mode == "RGB"


@pytest.mark.asyncio
async def test_plugin_auto_load():
    """Verify plugin auto-loader discovers example_watermark and registers hooks."""
    app = FastAPI()
    ctx = await load_plugins(app)

    # Check nav item registered
    labels = [item["label"] for item in ctx.nav_items]
    assert "Watermark Tool" in labels

    # Check hook registered
    assert "apply_watermark" in ctx.hooks
    assert callable(ctx.hooks["apply_watermark"][0])

    # Check feature flag created in database
    flag = await feature_flags_repo.is_enabled("plugin_example_watermark", default=False)
    assert flag is True
