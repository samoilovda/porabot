"""2.4: data retention for two tables that otherwise grow without bound.

`habit_events` gets one row per resolved habit cycle per day, forever — a
user with 50 habits accumulates ~18,000 rows a year, all of which
get_events_for_reminder(s) reads in full on every score computation
(bot/services/habit_reports.py's compute_habit_score). Completed one-off
reminders (`reminders` rows with status='completed', is_recurring=False)
are never touched again once completed_at passes, but nothing ever
removes them either.

Runs once a day, not minutely like the other cron jobs in this package —
this is housekeeping, not time-critical, and a daily cadence is plenty to
keep both tables bounded relative to their growth rate.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from bot.database.models import HabitEvent, Reminder

logger = logging.getLogger(__name__)

# compute_habit_score is an EMA with alpha=0.2: (1-0.2)**60 ~ 1e-6, so
# roughly 60 EMA STEPS (recorded events, not calendar days) after a
# pruned event, its contribution has decayed past floating-point
# precision. For a habit with roughly continuous recent activity — the
# normal case for one still being actively tracked — 400 days of history
# comfortably clears that, so pruning anything older leaves the score
# unchanged. This is NOT a guarantee for a habit with a long gap and only
# a handful of events recorded since: there, fewer EMA steps separate
# "now" from the pruned history, and the score can shift measurably. That
# is an accepted tradeoff of calendar-age-based retention — such a habit
# is effectively being scored fresh from its recent restart anyway. See
# bot/services/test_retention_cleanup.py for a test against the
# continuous-activity case this reasoning actually covers.
HABIT_EVENT_RETENTION_DAYS = 400

# A completed one-off reminder is never read again after completed_at —
# get_recent_completed_tasks only looks back _COMPLETED_HISTORY_DAYS (7)
# from bot/handlers/reminders.py, and get_today_tasks_by_status's
# 'completed' branch is bounded to the current local day. 180 days is a
# wide margin past both.
COMPLETED_REMINDER_RETENTION_DAYS = 180


async def process_retention_cleanup() -> None:
    """Prune habit_events and long-completed one-off reminders past their
    retention window. Recurring reminders (including every habit — both
    is_habit and is_fluid_habit rows are always is_recurring=True) are
    never touched here regardless of age; only a genuinely finished,
    never-revisited one-off task qualifies."""
    from bot.context import get_context
    try:
        ctx = get_context()
    except RuntimeError:
        logger.error("Failed to process retention cleanup: AppContext not set")
        return

    session_pool_factory = ctx.session_pool
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    habit_event_cutoff = now_utc_naive - timedelta(days=HABIT_EVENT_RETENTION_DAYS)
    reminder_cutoff = now_utc_naive - timedelta(days=COMPLETED_REMINDER_RETENTION_DAYS)

    try:
        async with session_pool_factory() as session:
            deleted_events = await session.execute(
                delete(HabitEvent).where(HabitEvent.created_at < habit_event_cutoff)
            )
            deleted_reminders = await session.execute(
                delete(Reminder).where(
                    Reminder.status == "completed",
                    Reminder.is_recurring.is_(False),
                    Reminder.completed_at.is_not(None),
                    Reminder.completed_at < reminder_cutoff,
                )
            )
            await session.commit()
            logger.info(
                "Retention cleanup: removed %s habit_events (>%sd old), %s completed one-off reminders (>%sd old).",
                deleted_events.rowcount,
                HABIT_EVENT_RETENTION_DAYS,
                deleted_reminders.rowcount,
                COMPLETED_REMINDER_RETENTION_DAYS,
            )
    except Exception as e:
        logger.error("Error in retention cleanup job: %s", e, exc_info=True)


def setup_retention_cleanup(scheduler) -> None:
    """Register the daily retention-cleanup cron job. Call once at startup."""
    logger.info("Registering daily retention cleanup cron job")
    scheduler.add_job(
        process_retention_cleanup,
        "cron",
        hour=3,
        minute=17,
        id="retention_cleanup",
        replace_existing=True,
    )
