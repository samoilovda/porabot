"""Time utilities for UTC normalization and display formatting."""

from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
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


def _safe_tz(tz_str: str) -> pytz.BaseTzInfo:
    try:
        return pytz.timezone(tz_str)
    except Exception:
        return pytz.UTC


def local_time_today_or_tomorrow(
    tz_str: str,
    hour: int,
    minute: int = 0,
    *,
    now_utc: Optional[datetime] = None,
) -> datetime:
    """The nearest local HH:MM — today if it's still ahead, tomorrow
    otherwise — returned as timezone-aware UTC (1.3).

    DST-safe by construction: the arithmetic (setting hour/minute, rolling
    to the next day) happens on a NAIVE local datetime, then the result is
    localized exactly once at the end. Doing this on an already-localized
    (pytz aware) datetime instead — `datetime.now(tz).replace(hour=...)`
    followed by `+ timedelta(days=1)` — reuses the UTC offset baked into
    `now`'s tzinfo even when the target instant falls on the other side of
    a DST transition, silently landing an hour off. Same pitfall
    `SchedulerService._next_quiet_end_utc` and `next_occurrence_utc` above
    already avoid the same way.
    """
    tz = _safe_tz(tz_str)
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    now_local_naive = to_utc_aware(now_utc).astimezone(tz).replace(tzinfo=None)
    candidate_naive = now_local_naive.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate_naive <= now_local_naive:
        candidate_naive += timedelta(days=1)
    return tz.localize(candidate_naive).astimezone(timezone.utc)


def local_time_today_strict(
    tz_str: str,
    hour: int,
    minute: int = 0,
    *,
    now_utc: Optional[datetime] = None,
) -> Optional[datetime]:
    """Same DST-safe construction as `local_time_today_or_tomorrow`, but
    returns None instead of rolling to tomorrow when HH:MM has already
    passed today — for flows where "already passed" is a user input error
    to reject (e.g. a fluid habit's manual pick-time-for-today prompt),
    not a reason to silently reschedule for a different day.
    """
    tz = _safe_tz(tz_str)
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    now_local_naive = to_utc_aware(now_utc).astimezone(tz).replace(tzinfo=None)
    candidate_naive = now_local_naive.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate_naive <= now_local_naive:
        return None
    return tz.localize(candidate_naive).astimezone(timezone.utc)


def local_time_tomorrow(
    tz_str: str,
    hour: int,
    minute: int = 0,
    *,
    now_utc: Optional[datetime] = None,
) -> datetime:
    """Tomorrow's local HH:MM — unconditionally, unlike
    `local_time_today_or_tomorrow` — returned as aware UTC (1.3). For a
    "tomorrow morning" style button where "tomorrow" is the whole point,
    not just what happens if today's slot already passed. Same DST-safe
    construction as the two helpers above.
    """
    tz = _safe_tz(tz_str)
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    now_local_naive = to_utc_aware(now_utc).astimezone(tz).replace(tzinfo=None)
    candidate_naive = (now_local_naive + timedelta(days=1)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return tz.localize(candidate_naive).astimezone(timezone.utc)


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
