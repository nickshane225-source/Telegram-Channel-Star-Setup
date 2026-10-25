import os
import uuid
from pathlib import Path
from typing import List, Optional, Tuple
from fastapi import UploadFile
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from loguru import logger
from app.config import MEDIA_DIR, STATIC_DIR
from app.models.post import PostPhoto

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB per Telegram limits
MAX_DIMENSION = 2560  # Max width/height to avoid Telegram compression artifacts


class MediaService:
    """Service for validating, processing, compressing and storing media files."""

    def __init__(self, media_dir: Path = MEDIA_DIR):
        self.media_dir = media_dir
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def validate_file(self, filename: str, file_size: int) -> Tuple[bool, Optional[str]]:
        """Validate file extension and raw file size."""
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            return False, f"Unsupported file extension '{ext}'. Allowed: JPG, PNG, WEBP."
        if file_size > MAX_FILE_SIZE_BYTES:
            return False, f"File size ({file_size / (1024*1024):.1f}MB) exceeds maximum allowed 10MB."
        return True, None

    async def save_and_process_upload(
        self,
        upload_file: UploadFile,
        apply_watermark_hook: Optional[callable] = None
    ) -> PostPhoto:
        """Process an uploaded image: validate, optionally watermark, compress, and save to media/."""
        filename = upload_file.filename or "photo.jpg"
        content = await upload_file.read()
        file_size = len(content)

        valid, error_msg = self.validate_file(filename, file_size)
        if not valid:
            raise ValueError(error_msg)

        unique_id = uuid.uuid4().hex[:12]
        safe_name = f"{unique_id}.jpg"
        dest_path = self.media_dir / safe_name

        try:
            # Write temporary bytes into memory for Pillow
            from io import BytesIO
            img = Image.open(BytesIO(content))

            # Convert to RGB if RGBA or P to save as clean JPEG
            if img.mode in ("RGBA", "LA", "P"):
                # Fill transparent areas with black or white
                background = Image.new("RGB", img.size, (0, 0, 0))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")

            # Resize if dimensions exceed Telegram recommendation
            orig_w, orig_h = img.size
            if orig_w > MAX_DIMENSION or orig_h > MAX_DIMENSION:
                img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)

            # Optional watermark hook from plugin
            if apply_watermark_hook:
                try:
                    img = apply_watermark_hook(img)
                except Exception as w_err:
                    logger.warning(f"Watermark hook failed, continuing without watermark: {w_err}")

            # Save optimized JPEG
            img.save(dest_path, "JPEG", quality=88, optimize=True)
            final_size = os.path.getsize(dest_path)
            w, h = img.size

            logger.info(f"Saved processed photo to {dest_path} ({w}x{h}, {final_size} bytes)")

            return PostPhoto(
                path=str(dest_path),
                original_filename=filename,
                width=w,
                height=h,
                size_bytes=final_size,
            )
        except Exception as exc:
            logger.error(f"Image processing failed for {filename}: {exc}")
            raise ValueError(f"Invalid or corrupted image file: {exc}")

    def delete_photo(self, photo_path: str) -> bool:
        """Safely delete photo from disk."""
        try:
            path = Path(photo_path).resolve()
            if path.exists():
                path.unlink()
                logger.info(f"Deleted media file {path}")
                return True
            return False
        except Exception as exc:
            logger.warning(f"Failed to delete photo {photo_path}: {exc}")
            return False

    def _load_font(self, size: int) -> ImageFont.ImageFont:
        """Load bundled DejaVuSans or NotoSans TTF font, falling back gracefully to default."""
        font_candidates = [
            STATIC_DIR / "fonts" / "DejaVuSans.ttf",
            STATIC_DIR / "fonts" / "NotoSans-Regular.ttf",
        ]
        for fp in font_candidates:
            if fp.exists():
                try:
                    return ImageFont.truetype(str(fp), size)
                except Exception:
                    continue
        return ImageFont.load_default()

    def _wrap_text(self, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
        """Wrap text to fit within max_width pixels."""
        lines = []
        for paragraph in text.split("\n"):
            p_strip = paragraph.strip()
            if not p_strip:
                lines.append("")
                continue
            words = p_strip.split(" ")
            cur = []
            for word in words:
                candidate = " ".join(cur + [word])
                try:
                    bbox = font.getbbox(candidate)
                    w = bbox[2] - bbox[0]
                except Exception:
                    w = len(candidate) * getattr(font, "size", 16) * 0.55
                if w <= max_width:
                    cur.append(word)
                else:
                    if cur:
                        lines.append(" ".join(cur))
                        cur = [word]
                    else:
                        lines.append(word)
                        cur = []
            if cur:
                lines.append(" ".join(cur))
        return lines

    def render_text_card(
        self,
        text: str,
        reference_size: Tuple[int, int],
        style: str = "blurred photo background",
        background_photo_path: Optional[str] = None,
    ) -> str:
        """
        Draws hidden text, word-wrapped and auto-sized, on a card the same size as reference_size.
        Styles: 'blurred photo background', 'gradient', 'solid'.
        Returns: file path to rendered card JPEG.
        """
        ref_w, ref_h = reference_size
        width = max(600, ref_w)
        height = max(600, ref_h)

        # 1. Base card background
        style_normalized = (style or "blurred photo background").lower().strip()
        card_img = None
        if ("blur" in style_normalized or "photo" in style_normalized) and background_photo_path and Path(background_photo_path).exists():
            try:
                bg = Image.open(background_photo_path).convert("RGB")
                bg = bg.resize((width, height), Image.Resampling.LANCZOS)
                bg = bg.filter(ImageFilter.GaussianBlur(radius=35))
                darkener = Image.new("RGBA", (width, height), (15, 23, 42, 195))
                card_img = Image.alpha_composite(bg.convert("RGBA"), darkener).convert("RGB")
            except Exception as e:
                logger.warning(f"Could not blur background photo for text card: {e}")
                card_img = None

        if card_img is None:
            if "solid" in style_normalized:
                card_img = Image.new("RGB", (width, height), (15, 23, 42))
            else:
                # Gradient (from slate-900 to indigo-950)
                card_img = Image.new("RGB", (width, height))
                top_color = (15, 23, 42)
                bottom_color = (30, 27, 75)
                for y in range(height):
                    ratio = y / max(1, height - 1)
                    r = int(top_color[0] + (bottom_color[0] - top_color[0]) * ratio)
                    g = int(top_color[1] + (bottom_color[1] - top_color[1]) * ratio)
                    b = int(top_color[2] + (bottom_color[2] - top_color[2]) * ratio)
                    for x in range(width):
                        card_img.putpixel((x, y), (r, g, b))

        draw = ImageDraw.Draw(card_img)

        # Decorative inner border
        border_inset = int(min(width, height) * 0.04)
        draw.rounded_rectangle(
            [border_inset, border_inset, width - border_inset, height - border_inset],
            radius=20,
            outline=(255, 255, 255, 40),
            width=2,
        )

        margin = int(min(width, height) * 0.08)
        content_w = width - 2 * margin
        header_y = margin + 10
        footer_space = int(min(width, height) * 0.12)
        content_h = height - header_y - footer_space - 20

        # Header Badge
        header_font = self._load_font(max(14, int(min(width, height) * 0.028)))
        badge_text = "★ UNLOCKED BUYER CONTENT"
        draw.text((margin, header_y), badge_text, fill=(245, 158, 11), font=header_font)
        draw.line([margin, header_y + 35, width - margin, header_y + 35], fill=(255, 255, 255, 30), width=1)

        # 2. Auto-size and word-wrap hidden text
        text_content_y = header_y + 55
        target_size = min(max(20, int(min(width, height) * 0.038)), 46)
        min_size = 14
        chosen_font = self._load_font(target_size)
        wrapped_lines = self._wrap_text(text, chosen_font, content_w)

        while target_size > min_size:
            line_spacing = int(target_size * 1.45)
            total_h = len(wrapped_lines) * line_spacing
            if total_h <= content_h:
                break
            target_size -= 2
            chosen_font = self._load_font(target_size)
            wrapped_lines = self._wrap_text(text, chosen_font, content_w)

        line_spacing = int(target_size * 1.45)

        # Render lines
        curr_y = text_content_y
        for line in wrapped_lines:
            if curr_y + line_spacing > height - footer_space:
                draw.text((margin, curr_y), "...", fill=(248, 250, 252), font=chosen_font)
                break
            draw.text((margin, curr_y), line, fill=(248, 250, 252), font=chosen_font)
            curr_y += line_spacing

        # Footer Badge
        footer_font = self._load_font(max(12, int(min(width, height) * 0.022)))
        footer_text = "Delivered via Telegram Stars Paid Media"
        draw.text((margin, height - margin - 15), footer_text, fill=(148, 163, 184), font=footer_font)

        # Save card
        unique_id = uuid.uuid4().hex[:12]
        dest_path = self.media_dir / f"card_{unique_id}.jpg"
        card_img.save(dest_path, "JPEG", quality=90, optimize=True)
        logger.info(f"Generated hidden text card at {dest_path} ({width}x{height})")
        return str(dest_path)

    def generate_teaser(self, photo_path: str, star_count: int) -> str:
        """
        Create a blurred teaser image of photo 1 with an 'Unlock for N Stars' badge overlay.
        Returns: file path to teaser JPEG.
        """
        path = Path(photo_path)
        if not path.exists():
            raise FileNotFoundError(f"Media file not found: {photo_path}")

        img = Image.open(path).convert("RGB")
        w, h = img.size

        # Heavy Gaussian blur
        teaser = img.filter(ImageFilter.GaussianBlur(radius=30))

        # Darken overlay
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 95))
        teaser = Image.alpha_composite(teaser.convert("RGBA"), overlay).convert("RGB")

        # Center Unlock badge
        badge_w = int(w * 0.65)
        badge_h = int(h * 0.22)
        badge_x0 = (w - badge_w) // 2
        badge_y0 = (h - badge_h) // 2
        badge_x1 = badge_x0 + badge_w
        badge_y1 = badge_y0 + badge_h

        # Translucent pill background
        pill = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        pill_draw = ImageDraw.Draw(pill)
        pill_draw.rounded_rectangle(
            [badge_x0, badge_y0, badge_x1, badge_y1],
            radius=int(badge_h * 0.35),
            fill=(15, 23, 42, 210),
            outline=(245, 158, 11, 230),
            width=3,
        )
        teaser = Image.alpha_composite(teaser.convert("RGBA"), pill).convert("RGB")

        # Text inside badge
        draw = ImageDraw.Draw(teaser)
        title_font_size = max(18, int(badge_h * 0.30))
        title_font = self._load_font(title_font_size)
        sub_font_size = max(12, int(badge_h * 0.18))
        sub_font = self._load_font(sub_font_size)

        title_text = f"★ Unlock for {star_count} Stars"
        sub_text = "Locked content • Tap link below to purchase"

        try:
            tb = title_font.getbbox(title_text)
            tw = tb[2] - tb[0]
            th = tb[3] - tb[1]
        except Exception:
            tw, th = len(title_text) * 12, title_font_size

        try:
            sb = sub_font.getbbox(sub_text)
            sw = sb[2] - sb[0]
        except Exception:
            sw = len(sub_text) * 8

        tx = (w - tw) // 2
        ty = badge_y0 + int(badge_h * 0.22)
        draw.text((tx, ty), title_text, fill=(245, 158, 11), font=title_font)

        sx = (w - sw) // 2
        sy = ty + th + int(badge_h * 0.15)
        draw.text((sx, sy), sub_text, fill=(248, 250, 252), font=sub_font)

        # Save teaser
        unique_id = uuid.uuid4().hex[:12]
        dest_path = self.media_dir / f"teaser_{unique_id}.jpg"
        teaser.save(dest_path, "JPEG", quality=88, optimize=True)
        logger.info(f"Generated teaser image at {dest_path} ({w}x{h})")
        return str(dest_path)


media_service = MediaService()
