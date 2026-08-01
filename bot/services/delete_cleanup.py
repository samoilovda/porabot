"""Soft-delete cleanup — hard-deletes reminders past their undo window.

Runs as a minutely APScheduler cron job. Deleting a reminder sets
Reminder.pending_delete_at (see bot/handlers/reminders.py's delete/undo
callbacks) instead of removing the row immediately, so an Undo tap can
restore it. This sweep finishes the job once that deadline has passed —
restart-safe by construction, since it re-derives its work from persisted
DB state instead of an in-memory timer.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import Reminder

logger = logging.getLogger(__name__)


async def process_deferred_deletes() -> None:
    """Hard-delete every reminder whose undo window has elapsed."""
    from bot.services.scheduler import _instance
    if not _instance:
        logger.error("Failed to process deferred deletes: SchedulerService not initialized")
        return

    session_pool_factory = _instance.session_pool
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async with session_pool_factory() as session:
            result = await session.execute(
                select(Reminder.id).where(
                    Reminder.pending_delete_at.is_not(None),
                    Reminder.pending_delete_at <= now_utc_naive,
                )
            )
            reminder_ids = result.scalars().all()

        for reminder_id in reminder_ids:
            async with session_pool_factory() as session:
                try:
                    # Re-check under a fresh read: an Undo tap may have
                    # cleared pending_delete_at between the select above and
                    # this row's turn — the WHERE clause below is the source
                    # of truth for whether the delete is still due.
                    reminder_dao = ReminderDAO(session)
                    reminder = await reminder_dao.get_by_id(reminder_id)
                    if (
                        not reminder
                        or reminder.pending_delete_at is None
                        or reminder.pending_delete_at > datetime.now(timezone.utc).replace(tzinfo=None)
                    ):
                        continue
                    await HabitEventDAO(session).delete_for_reminder(reminder_id)
                    await reminder_dao.delete_by_id(reminder_id)
                    await session.commit()
                    logger.info("Hard-deleted reminder %s after undo window elapsed.", reminder_id)
                except Exception as e:
                    await session.rollback()
                    logger.error("Error hard-deleting reminder %s: %s", reminder_id, e, exc_info=True)

    except Exception as e:
        logger.error("Error in deferred-delete cleanup job: %s", e, exc_info=True)


def setup_delete_cleanup(scheduler) -> None:
    """Register the minutely cron job for deferred-delete cleanup."""
    logger.info("Registering minutely deferred-delete cleanup cron job")
    scheduler.add_job(
        process_deferred_deletes,
        "cron",
        minute="*",
        id="delete_cleanup",
        replace_existing=True,
    )
