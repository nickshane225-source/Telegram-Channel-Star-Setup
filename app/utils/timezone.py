from datetime import datetime, timezone
from typing import Optional
import zoneinfo


def get_zone_info(tz_name: Optional[str] = None) -> zoneinfo.ZoneInfo:
    """Safely obtain ZoneInfo, falling back to UTC on unknown or empty timezone."""
    if not tz_name or not str(tz_name).strip():
        return zoneinfo.ZoneInfo("UTC")
    try:
        return zoneinfo.ZoneInfo(str(tz_name).strip())
    except (zoneinfo.ZoneInfoNotFoundError, Exception):
        return zoneinfo.ZoneInfo("UTC")


def parse_local_datetime_to_utc(dt_str: Optional[str], tz_name: Optional[str] = "UTC") -> Optional[datetime]:
    """
    Parse an HTML datetime-local string (e.g. '2026-10-25T14:30') in the specified
    local timezone and convert it to a UTC naive datetime for storage and APScheduler.
    """
    if not dt_str or not str(dt_str).strip():
        return None

    cleaned = str(dt_str).strip()
    try:
        # datetime.fromisoformat handles 'YYYY-MM-DDTHH:MM' and 'YYYY-MM-DDTHH:MM:SS'
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None

    tz = get_zone_info(tz_name)

    if dt.tzinfo is None:
        # Interpret naive datetime in specified user timezone
        local_dt = dt.replace(tzinfo=tz)
    else:
        local_dt = dt.astimezone(tz)

    # Convert to UTC
    utc_dt = local_dt.astimezone(zoneinfo.ZoneInfo("UTC"))
    # Return as naive datetime in UTC (standard for MongoDB & APScheduler in this app)
    return utc_dt.replace(tzinfo=None)


def format_utc_to_local(
    dt: Optional[datetime],
    tz_name: Optional[str] = "UTC",
    fmt: str = "%Y-%m-%d %H:%M"
) -> str:
    """
    Convert a UTC datetime (from database) into local timezone representation.
    """
    if not dt:
        return ""

    tz = get_zone_info(tz_name)

    # If datetime is naive, treat as UTC
    if dt.tzinfo is None:
        utc_dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
    else:
        utc_dt = dt.astimezone(zoneinfo.ZoneInfo("UTC"))

    local_dt = utc_dt.astimezone(tz)
    return local_dt.strftime(fmt)
