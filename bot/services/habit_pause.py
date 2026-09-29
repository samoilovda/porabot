"""Step 13 (2026-09-26 audit remediation): pause a habit until a date.

While paused a habit sends nothing (scheduler), records no `missed` events
(habit_sweeper), is left out of fluid prompts (daily_briefs) and of the
calendar feed. Score and reports need no special casing: they are derived
from HabitEvent rows, and a paused habit produces none. When the pause ends
the schedule resumes at the next occurrence — cycles that fell inside the
pause are skipped, never replayed as a burst of catch-up notifications.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pytz

from bot.utils.time_ext import next_occurrence_utc

MAX_PAUSE_DAYS = 366


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def pause_end_utc_naive(tz_str: str, until: date) -> datetime:
    """Local midnight at the start of *until*, as naive UTC (DST-safe)."""
    try:
        tz = pytz.timezone(tz_str)
    except Exception:
        tz = pytz.UTC
    local = tz.localize(datetime(until.year, until.month, until.day))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def is_paused(reminder, now_utc_naive: Optional[datetime] = None) -> bool:
    paused_until = getattr(reminder, "paused_until", None)
    if paused_until is None:
        return False
    return (now_utc_naive or _now_naive()) < paused_until


def valid_pause_date(until: date, tz_str: str) -> bool:
    try:
        tz = pytz.timezone(tz_str)
    except Exception:
        tz = pytz.UTC
    today = datetime.now(tz).date()
    return today < until <= today + timedelta(days=MAX_PAUSE_DAYS)


def first_occurrence_at_or_after(reminder, tz_str: str, moment_utc_naive: datetime) -> datetime:
    """When a paused habit should next fire: its first scheduled occurrence
    at/after *moment*; *moment* itself if it has no usable recurrence."""
    if reminder.is_recurring and reminder.rrule_string:
        anchor = getattr(reminder, "rrule_dtstart", None) or reminder.execution_time
        try:
            nxt = next_occurrence_utc(
                reminder.rrule_string, anchor, tz_str, moment_utc_naive - timedelta(seconds=1)
            )
        except (ValueError, TypeError):
            nxt = None
        if nxt:
            return nxt
    return moment_utc_naive


async def apply_pause(reminder, user_tz: str, scheduler_service, session, until: Optional[date]) -> bool:
    """Pause *reminder* until local date *until*, or resume it now
    (until=None). Commits first and only touches scheduler jobs once that
    succeeded — returns False (and rolls back) if the commit fails.

    Resuming sets paused_until to "now" rather than NULL, so cycles that
    already fell inside the pause stay skipped by the sweeper instead of
    being recorded as misses the moment the pause is lifted.
    """
    now = _now_naive()
    if until is None:
        reminder.paused_until = now
    else:
        reminder.paused_until = pause_end_utc_naive(user_tz, until)

    resume_at = None
    if not reminder.is_fluid_habit:
        resume_at = first_occurrence_at_or_after(reminder, user_tz, max(reminder.paused_until, now))
        reminder.execution_time = resume_at

    try:
        await session.commit()
    except Exception:
        await session.rollback()
        return False

    if resume_at is not None:
        from bot.utils.time_ext import to_utc_aware

        scheduler_service.remove_nagging_job(reminder.id)
        scheduler_service.schedule_reminder(reminder.id, to_utc_aware(resume_at), is_nagging=reminder.is_nagging)
    return True
