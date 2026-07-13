"""Time utilities for UTC normalization and display formatting."""

from datetime import datetime, time as dt_time, timezone
import pytz


def parse_hhmm(raw: str, fallback: str) -> dt_time:
    """Parse an "HH:MM" string, falling back to *fallback* (also "HH:MM") on any error."""
    value = (raw or fallback).strip()
    try:
        hh, mm = value.split(":", 1)
        h = int(hh)
        m = int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return dt_time(hour=h, minute=m)
    except Exception:
        pass
    fh, fm = fallback.split(":")
    return dt_time(hour=int(fh), minute=int(fm))


def is_quiet_hours(user, now_local: datetime) -> bool:
    """Whether *now_local* (timezone-aware, already in the user's local zone)
    falls within the user's configured quiet hours window."""
    if not bool(getattr(user, "quiet_hours_enabled", False)):
        return False
    start = parse_hhmm(getattr(user, "quiet_hours_start", "23:00"), "23:00")
    end = parse_hhmm(getattr(user, "quiet_hours_end", "07:00"), "07:00")
    current = now_local.time()
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


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
