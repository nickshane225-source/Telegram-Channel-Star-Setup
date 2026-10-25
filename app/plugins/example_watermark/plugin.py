import io
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from PIL import Image, ImageDraw, ImageFont
from app.services.auth import auth_service
from app.repositories.settings import settings_repo


def apply_watermark(
    img: Image.Image,
    text: str = "★ StarPost Exclusive",
    opacity: int = 140,
    font_size: int = 36
) -> Image.Image:
    """Apply a modern semi-transparent text watermark badge at the bottom right corner."""
    # Work on a copy with RGBA
    watermarked = img.convert("RGBA")
    overlay = Image.new("RGBA", watermarked.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)

    w, h = watermarked.size
    scaled_font_size = max(18, min(int(w * 0.035), 72))

    try:
        # Try loading default font or fallback
        font = ImageFont.load_default()
    except Exception:
        font = None

    # Calculate text bounding box
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # Position at bottom-right with padding
    padding_x = 30
    padding_y = 30
    x = max(10, w - text_w - padding_x - 20)
    y = max(10, h - text_h - padding_y - 14)

    # Draw rounded pill background
    pill_bbox = [x - 12, y - 6, x + text_w + 12, y + text_h + 6]
    draw.rounded_rectangle(pill_bbox, radius=8, fill=(0, 0, 0, opacity))
    # Draw text
    draw.text((x, y), text, fill=(255, 255, 255, min(255, opacity + 70)), font=font)

    # Composite overlay onto original image
    combined = Image.alpha_composite(watermarked, overlay)
    return combined.convert("RGB")


# Plugin APIRouter
watermark_router = APIRouter(prefix="/plugins/watermark", tags=["Watermark Plugin"])


@watermark_router.get("", response_class=HTMLResponse)
async def watermark_page(request: Request, user=Depends(auth_service.require_auth)):
    """Render the Watermark Plugin configuration & test lab."""
    from app.config import TEMPLATES_DIR
    from jinja2 import Environment, FileSystemLoader

    app_settings = await settings_repo.get_settings()
    theme = app_settings.theme or "midnight"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en" data-theme="{theme}">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Watermark Tool - StarPost Plugin</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link rel="stylesheet" href="/static/css/theme.css">
    </head>
    <body class="bg-base text-main min-h-screen p-6 md:p-10 flex flex-col items-center">
        <div class="max-w-2xl w-full bg-card border border-glass rounded-2xl p-8 backdrop-blur-xl shadow-2xl">
            <div class="flex items-center justify-between mb-6 pb-4 border-b border-glass">
                <div>
                    <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-primary/20 text-accent">
                        Plugin Extension
                    </span>
                    <h1 class="text-2xl font-bold text-white mt-1">Watermark Protection Lab</h1>
                </div>
                <a href="/" class="text-sm text-dim hover:text-white transition">← Back to Dashboard</a>
            </div>

            <p class="text-dim text-sm mb-6 leading-relaxed">
                This plugin demonstrator hooks into image processing to stamp an automatic semi-transparent
                authenticity badge on paid photos before they are uploaded to Telegram.
            </p>

            <form action="/plugins/watermark/test" method="post" enctype="multipart/form-data" class="space-y-5">
                <div>
                    <label class="block text-xs font-medium uppercase tracking-wider text-dim mb-2">Watermark Text</label>
                    <input type="text" name="text" value="★ StarPost Channel Exclusive"
                           class="w-full bg-base/60 border border-glass rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-accent">
                </div>

                <div>
                    <label class="block text-xs font-medium uppercase tracking-wider text-dim mb-2">Select Test Photo</label>
                    <input type="file" name="file" accept="image/*" required
                           class="w-full text-sm text-dim file:mr-4 file:py-2.5 file:px-4 file:rounded-xl file:border-0 file:text-xs file:font-semibold file:bg-primary file:text-white hover:file:bg-primary-hover file:cursor-pointer">
                </div>

                <button type="submit"
                        class="w-full py-3 px-6 rounded-xl font-medium text-sm bg-primary hover:bg-primary-hover text-white shadow-lg transition duration-200">
                    Generate Watermarked Preview
                </button>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@watermark_router.post("/test")
async def watermark_test(
    file: UploadFile = File(...),
    text: str = Form("★ StarPost Channel Exclusive"),
    user=Depends(auth_service.require_auth)
):
    """Generate watermarked image preview on the fly."""
    content = await file.read()
    img = Image.open(io.BytesIO(content))
    processed = apply_watermark(img, text=text)

    output = io.BytesIO()
    processed.save(output, format="JPEG", quality=90)
    return Response(content=output.getvalue(), media_type="image/jpeg")


def register(app, ctx):
    """Plugin entrypoint invoked by the Plugin System."""
    # 1. Register Watermark Hook for Image Processing
    ctx.add_hook("apply_watermark", apply_watermark)

    # 2. Add Sidebar Navigation Item
    ctx.add_nav_item(label="Watermark Tool", url="/plugins/watermark", icon="sparkles")

    # 3. Mount Plugin Router
    ctx.register_router(watermark_router)
