"""Time utilities for UTC normalization and display formatting."""

from datetime import datetime, time as dt_time, timezone
from typing import Optional

import pytz
from dateutil.rrule import rrulestr


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


def _time_in_window(current: dt_time, start: dt_time, end: dt_time) -> bool:
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def is_quiet_hours(user, now_local: datetime, *, is_habit: bool = False) -> bool:
    """Whether *now_local* (timezone-aware, already in the user's local zone)
    falls within the user's configured quiet hours window.

    3.5: two extensions on top of the single all-week start-end window:
      - a separate weekend window (Sat/Sun), used instead of the weekday one
        when quiet_hours_weekend_enabled is set;
      - quiet_hours_habits_exempt: habits can wake the user even during
        quiet hours while regular tasks stay silenced — set is_habit=True
        for a habit-like reminder to honor that.
    """
    if not bool(getattr(user, "quiet_hours_enabled", False)):
        return False
    if is_habit and bool(getattr(user, "quiet_hours_habits_exempt", False)):
        return False

    is_weekend = now_local.weekday() >= 5  # Sat=5, Sun=6
    if is_weekend and bool(getattr(user, "quiet_hours_weekend_enabled", False)):
        start = parse_hhmm(getattr(user, "quiet_hours_weekend_start", "23:00"), "23:00")
        end = parse_hhmm(getattr(user, "quiet_hours_weekend_end", "07:00"), "07:00")
    else:
        start = parse_hhmm(getattr(user, "quiet_hours_start", "23:00"), "23:00")
        end = parse_hhmm(getattr(user, "quiet_hours_end", "07:00"), "07:00")

    return _time_in_window(now_local.time(), start, end)


def next_occurrence_utc(
    rrule_string: str,
    dtstart_utc_naive: datetime,
    user_tz_str: str,
    after_utc_naive: datetime,
) -> Optional[datetime]:
    """Compute the next rrule occurrence after *after_utc_naive*, in the user's
    local timezone, and return it as naive UTC.

    execution_time is stored in UTC, but a daily/weekly rrule anchored to a
    UTC dtstart drifts by an hour across DST transitions (the wall-clock time
    a user sees shifts even though the recurrence rule didn't change). Doing
    the rrule math in local time keeps the local wall-clock time stable.
    """
    try:
        user_tz = pytz.timezone(user_tz_str)
    except Exception:
        user_tz = pytz.UTC

    dtstart_local_naive = to_utc_aware(dtstart_utc_naive).astimezone(user_tz).replace(tzinfo=None)
    after_local_naive = to_utc_aware(after_utc_naive).astimezone(user_tz).replace(tzinfo=None)

    rule = rrulestr(rrule_string, dtstart=dtstart_local_naive)
    next_local_naive = rule.after(after_local_naive)
    if next_local_naive is None:
        return None

    next_local_aware = user_tz.localize(next_local_naive)
    return to_utc_naive(next_local_aware)


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
