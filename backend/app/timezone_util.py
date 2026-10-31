import os
import zoneinfo
from datetime import datetime, timezone
from typing import Optional

def get_app_timezone(custom_tz: Optional[str] = None) -> zoneinfo.ZoneInfo:
    """
    Resolves target timezone:
    1. custom_tz parameter (e.g. from app_settings)
    2. TZ environment variable (e.g. set in docker-compose)
    3. Default 'Europe/Copenhagen' (automatic Danish CET/CEST DST)
    4. Host system local timezone
    """
    tz_str = (custom_tz or os.environ.get("TZ", "")).strip()
    if tz_str:
        try:
            return zoneinfo.ZoneInfo(tz_str)
        except Exception:
            pass
    try:
        return zoneinfo.ZoneInfo("Europe/Copenhagen")
    except Exception:
        return datetime.now().astimezone().tzinfo

def format_localtime(value: Optional[str], fmt: str = "%Y-%m-%d %H:%M", custom_tz: Optional[str] = None) -> str:
    """
    Converts a UTC SQLite timestamp (e.g. '2026-10-31 10:53:44') to the target timezone
    (with automatic daylight saving / winter / summer time transitions) and formats it.
    """
    if not value:
        return ""
    try:
        val_str = str(value).strip().replace("T", " ")
        if len(val_str) < 19:
            if len(val_str) == 10 and "-" in val_str:
                return val_str
            if len(val_str) == 16:
                val_str += ":00"
            else:
                return val_str[:16]
        else:
            val_str = val_str[:19]

        dt = datetime.strptime(val_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        target_tz = get_app_timezone(custom_tz)
        return dt.astimezone(target_tz).strftime(fmt)
    except Exception:
        return str(value)[:16]

def format_localdate(value: Optional[str], custom_tz: Optional[str] = None) -> str:
    """Converts a UTC SQLite timestamp to local date (YYYY-MM-DD)."""
    return format_localtime(value, fmt="%Y-%m-%d", custom_tz=custom_tz)
