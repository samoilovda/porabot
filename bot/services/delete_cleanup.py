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

from sqlalchemy import delete, select

from bot.database.models import HabitEvent, Reminder

logger = logging.getLogger(__name__)


async def process_deferred_deletes() -> None:
    """Hard-delete every reminder whose undo window has elapsed."""
    from bot.context import get_context
    try:
        ctx = get_context()
    except RuntimeError:
        logger.error("Failed to process deferred deletes: AppContext not set")
        return

    session_pool_factory = ctx.session_pool
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
                    # A-13: a separate SELECT-then-DELETE (the previous
                    # shape of this re-check) still has a TOCTOU window —
                    # an Undo tap can commit its own transaction in the gap
                    # between our read and our write, and this delete would
                    # go ahead anyway, since nothing here re-reads that
                    # write. Folding the exact same guard directly into the
                    # DELETE's own WHERE closes that window: the guard is
                    # then evaluated by SQLite atomically, against
                    # whatever is truly committed at the instant this
                    # statement runs, not a snapshot read moments earlier.
                    # A concurrent Undo either commits before this
                    # transaction starts (pending_delete_at is already
                    # NULL, rowcount 0 below, nothing deleted) or has to
                    # wait for it (SQLite serializes writers) and simply
                    # arrives "too late" — the same outcome
                    # callback_undo_delete already shows the user for a tap
                    # past the deadline.
                    now_at_delete = datetime.now(timezone.utc).replace(tzinfo=None)
                    still_due = (
                        Reminder.id == reminder_id,
                        Reminder.pending_delete_at.is_not(None),
                        Reminder.pending_delete_at <= now_at_delete,
                    )
                    # habit_events (FK child) first, scoped through the
                    # SAME guard via subquery so it only ever deletes
                    # events for a reminder this transaction is ALSO about
                    # to delete below — never a bare "regardless of
                    # pending_delete_at" delete_for_reminder.
                    await session.execute(
                        delete(HabitEvent).where(
                            HabitEvent.reminder_id == reminder_id,
                            HabitEvent.reminder_id.in_(select(Reminder.id).where(*still_due)),
                        )
                    )
                    result = await session.execute(delete(Reminder).where(*still_due))
                    await session.commit()
                    if result.rowcount:
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
        # 1.4: memory jobstore, not the default SQLAlchemyJobStore — this
        # job is re-registered with replace_existing=True on every single
        # startup anyway, so there is no benefit to persisting it, and the
        # persistent store is reserved for reminder jobs (see bot/__main__.py).
        jobstore="memory",
    )
