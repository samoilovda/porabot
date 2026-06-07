"""Time utilities for UTC normalization, display formatting, and timezone resolution."""

import re
from datetime import datetime, timezone
from typing import Optional, Tuple
import pytz


def to_utc_aware(dt: datetime) -> datetime:
    """Return *dt* as a timezone-aware UTC datetime.

    Naive datetimes are treated as UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_utc_naive(dt: datetime) -> datetime:
    """Return *dt* normalized to UTC and stripped to naive form for DB storage."""
    return to_utc_aware(dt).replace(tzinfo=None)


def resolve_tz(tz_str: str) -> pytz.BaseTzInfo:
    """Parse *tz_str* into a pytz timezone, falling back to UTC on failure."""
    try:
        return pytz.timezone(tz_str)
    except pytz.UnknownTimeZoneError:
        return pytz.UTC


def parse_hhmm(raw: str) -> Optional[Tuple[int, int]]:
    """Parse a HH:MM string and return (hour, minute) or None if invalid.

    Accepts 1–2 digit hours (0–23) and exactly 2 digit minutes (0–59).
    Leading/trailing whitespace is ignored.

    Examples::

        parse_hhmm("09:30")  → (9, 30)
        parse_hhmm("9:5")    → None   # minutes must be 2 digits
        parse_hhmm("25:00")  → None   # hour out of range
    """
    match = re.match(r'^(\d{1,2}):(\d{2})$', raw.strip())
    if not match:
        return None
    h, m = int(match.group(1)), int(match.group(2))
    if 0 <= h <= 23 and 0 <= m <= 59:
        return h, m
    return None


def format_time(
    dt: datetime,
    tz_str: str,
    show_utc_offset: bool = False,
    fmt: str = "%H:%M",
) -> str:
    """Format *dt* in the user's timezone, optionally appending the UTC offset.

    Naive datetimes are treated as UTC. Invalid timezone strings fall back to UTC.

    Examples::

        format_time(utc_dt, "Europe/Moscow")               → '17:30'
        format_time(utc_dt, "Europe/Moscow", True)         → '17:30 (UTC+03:00)'
        format_time(utc_dt, "Europe/Moscow", fmt="%d.%m")  → '27.03'
    """
    try:
        user_tz = pytz.timezone(tz_str)
    except pytz.UnknownTimeZoneError:
        user_tz = pytz.UTC

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    dt_local = dt.astimezone(user_tz)
    base_str = dt_local.strftime(fmt)

    if not show_utc_offset:
        return base_str

    offset_seconds = dt_local.utcoffset().total_seconds()
    sign = "-" if offset_seconds < 0 else "+"
    hours, mins = divmod(int(abs(offset_seconds)), 3600)
    return f"{base_str} (UTC{sign}{hours:02d}:{mins // 60:02d})"
