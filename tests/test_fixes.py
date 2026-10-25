from datetime import datetime
import pytest

from app.config import settings
from app.models.post import PostPhoto
from app.repositories.posts import posts_repo
from app.services.posts import PostService
from app.utils.sanitizer import sanitize_telegram_html
from app.utils.security import get_security_warnings
from app.utils.timezone import format_utc_to_local, parse_local_datetime_to_utc


def test_timezone_conversion():
    """
    Verify parsing form datetime-local with local timezone and converting to UTC,
    and converting UTC datetimes back to local timezone for display.
    """
    # 1. New York in Standard Time vs Daylight Time
    # 2026-01-15 14:00 in America/New_York (UTC-5) -> 19:00 UTC
    dt_utc = parse_local_datetime_to_utc("2026-01-15T14:00", tz_name="America/New_York")
    assert dt_utc is not None
    assert dt_utc.year == 2026
    assert dt_utc.month == 1
    assert dt_utc.day == 15
    assert dt_utc.hour == 19
    assert dt_utc.minute == 0

    # 2. Dhaka in Bangladesh Time (UTC+6)
    # 2026-01-15 14:00 in Asia/Dhaka -> 08:00 UTC
    dt_dhaka = parse_local_datetime_to_utc("2026-01-15T14:00", tz_name="Asia/Dhaka")
    assert dt_dhaka is not None
    assert dt_dhaka.hour == 8
    assert dt_dhaka.minute == 0

    # 3. Format back to local timezone
    # Convert UTC 19:00 back to America/New_York -> 14:00
    formatted_ny = format_utc_to_local(dt_utc, tz_name="America/New_York", fmt="%Y-%m-%d %H:%M")
    assert formatted_ny == "2026-01-15 14:00"

    # Fallback to UTC on unknown timezone
    dt_fallback = parse_local_datetime_to_utc("2026-01-15T14:00", tz_name="Invalid/Timezone")
    assert dt_fallback.hour == 14


@pytest.mark.asyncio
async def test_bulk_prefix(sample_photos):
    """Verify bulk creation uses the specified bulk_prefix for post titles."""
    svc = PostService(repo=posts_repo)

    start = datetime.utcnow()
    posts = await svc.bulk_create_posts(
        photos=sample_photos,
        star_count=40,
        base_caption="Exclusive Drop",
        start_time=start,
        interval_hours=1.5,
        bulk_prefix="Sunset Batch",
    )

    assert len(posts) == 1
    assert posts[0].title == "Sunset Batch #1"


def test_caption_sanitization():
    """Verify Telegram caption sanitization preserves allowed tags and strips dangerous tags."""
    # Allowed tags: b, i, u, s, code, pre, a, tg-spoiler, blockquote
    sample_allowed = (
        "<b>Bold</b> <i>Italic</i> <u>Underline</u> <s>Strike</s> "
        "<code>print('hi')</code> <pre>code block</pre> "
        "<a href=\"https://t.me/channel\">Telegram</a> <tg-spoiler>Secret</tg-spoiler> "
        "<blockquote>Quote text</blockquote>"
    )
    sanitized = sanitize_telegram_html(sample_allowed)
    assert "<b>Bold</b>" in sanitized
    assert "<i>Italic</i>" in sanitized
    assert "<u>Underline</u>" in sanitized
    assert "<s>Strike</s>" in sanitized
    assert "<code>print('hi')</code>" in sanitized
    assert "<pre>code block</pre>" in sanitized
    assert "<a href=\"https://t.me/channel\">Telegram</a>" in sanitized
    assert "<tg-spoiler>Secret</tg-spoiler>" in sanitized
    assert "<blockquote>Quote text</blockquote>" in sanitized

    # Disallowed tags must be stripped (XSS / invalid Telegram tags)
    sample_malicious = "<script>alert('pwned')</script><img src=\"x\" onerror=\"alert(1)\"><b>Hello</b>"
    clean = sanitize_telegram_html(sample_malicious)
    assert "<script>" not in clean
    assert "<img>" not in clean
    assert "<img" not in clean
    assert "onerror" not in clean
    assert "<b>Hello</b>" in clean


def test_security_warnings():
    """Verify security warning is generated when default SECRET_KEY or password is used."""
    # Default secret key
    settings.SECRET_KEY = "starpost-secret-key-change-this-in-production-random-string"
    settings.ADMIN_PASSWORD = "admin"
    settings.ADMIN_PASSWORD_HASH = ""

    warnings = get_security_warnings()
    assert len(warnings) >= 2
    assert any("SECRET_KEY" in w for w in warnings)
    assert any("Admin password" in w for w in warnings)

    # Secure configuration
    settings.SECRET_KEY = "super-secure-production-random-token-998877665544332211"
    settings.ADMIN_PASSWORD = ""
    settings.ADMIN_PASSWORD_HASH = "$2b$12$CustomSecureHashThatIsNotAdmin123456789012345"

    clean_warnings = get_security_warnings()
    assert len(clean_warnings) == 0
