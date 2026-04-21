"""
SchedulerService — APScheduler facade for reminder jobs.

Job targets cannot hold unpicklable 'self' references, so a module-level
``_instance`` singleton lets the free function ``execute_reminder_job``
reach the service at call time. This is a known APScheduler tradeoff.
"""

import logging
from datetime import datetime, timedelta, timezone

import pytz
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError
from dateutil.rrule import rrulestr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.database.dao.user import UserDAO
from bot.database.models import Reminder
from bot.keyboards.inline import get_task_done_keyboard
from bot.lexicon import get_l10n

logger = logging.getLogger(__name__)

# Module-level singleton — set by SchedulerService.__init__
_instance = None


# ---------------------------------------------------------------------------
# APScheduler job target (must be a top-level function, not a bound method)
# ---------------------------------------------------------------------------

async def execute_reminder_job(reminder_id: int, is_nagging_execution: bool = False) -> None:
    """Called by APScheduler at the scheduled time to fire a reminder."""
    if not _instance:
        logger.error("Cannot execute reminder %s: SchedulerService not initialised.", reminder_id)
        return
    await _instance._execute_reminder(reminder_id, is_nagging_execution=is_nagging_execution)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SchedulerService:
    """Facade over APScheduler that handlers use for all scheduling operations.

    Args:
        scheduler:    AsyncIOScheduler instance.
        bot:          Telegram Bot instance.
        session_pool: Async SQLAlchemy session factory.
    """

    def __init__(self, scheduler: AsyncIOScheduler, bot: Bot, session_pool: async_sessionmaker) -> None:
        self.scheduler = scheduler
        self.bot = bot
        self.session_pool = session_pool
        global _instance
        _instance = self

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def schedule_reminder(self, reminder_id: int, run_date: datetime, *, is_nagging: bool = False) -> None:
        """Add (or replace) a one-shot date-trigger job for *reminder_id*."""
        if run_date.tzinfo is None:
            logger.warning("Reminder %s has naive run_date — assuming UTC.", reminder_id)
            run_date = run_date.replace(tzinfo=timezone.utc)
        try:
            self.scheduler.add_job(
                execute_reminder_job,
                "date",
                run_date=run_date,
                args=[reminder_id, False],
                id=str(reminder_id),
                replace_existing=True,
            )
            logger.info("Scheduled reminder %s for %s (nagging=%s).", reminder_id, run_date, is_nagging)
        except Exception as e:
            logger.error("Failed to schedule reminder %s: %s", reminder_id, e, exc_info=True)
            # Critical: propagate failure so callers can rollback DB/session changes
            # instead of committing reminders that were never actually scheduled.
            raise

    def remove_reminder_job(self, reminder_id: int) -> None:
        """Remove the main job and any nagging job for *reminder_id*."""
        for job_id in (str(reminder_id), f"nag_{reminder_id}"):
            try:
                self.scheduler.remove_job(job_id)
                logger.debug("Removed job %s.", job_id)
            except JobLookupError:
                logger.debug("Job %s not found (already removed).", job_id)

    def remove_nagging_job(self, reminder_id: int) -> None:
        """Remove only the nagging follow-up job for *reminder_id*."""
        try:
            self.scheduler.remove_job(f"nag_{reminder_id}")
        except JobLookupError:
            logger.debug("Nagging job nag_%s not found.", reminder_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_reminder(self, reminder_id: int, is_nagging_execution: bool = False) -> None:
        """Fetch reminder from DB, send notification, handle recurrence and nagging."""
        logger.info("Executing reminder %s (nagging=%s).", reminder_id, is_nagging_execution)

        async with self.session_pool() as session:
            scheduled_main_job = False
            scheduled_nagging_job = False
            try:
                result = await session.execute(select(Reminder).where(Reminder.id == reminder_id))
                reminder = result.scalar_one_or_none()

                if not reminder:
                    logger.warning("Reminder %s not found — skipping.", reminder_id)
                    return

                if reminder.status == "completed":
                    logger.info("Reminder %s already completed — skipping.", reminder_id)
                    return

                user_dao = UserDAO(session)
                user = await user_dao.get_by_id(reminder.user_id)
                if not user:
                    logger.warning("User %s not found for reminder %s.", reminder.user_id, reminder_id)
                    return

                l10n = get_l10n(user.language)
                keyboard = get_task_done_keyboard(reminder.id, l10n)
                await self._send_telegram_message(reminder.user_id, reminder.reminder_text, l10n, keyboard)

                # Recurring reschedule
                if not is_nagging_execution and reminder.is_recurring and reminder.rrule_string:
                    start_dt = reminder.execution_time
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=timezone.utc)
                    try:
                        rule = rrulestr(reminder.rrule_string, dtstart=start_dt)
                        next_run = rule.after(datetime.now(start_dt.tzinfo))
                        if next_run:
                            reminder.execution_time = next_run
                            self.schedule_reminder(reminder_id, next_run, is_nagging=reminder.is_nagging)
                            scheduled_main_job = True
                            logger.info("Rescheduled recurring reminder %s → %s.", reminder_id, next_run)
                        else:
                            logger.info("Recurring reminder %s has no future occurrences.", reminder_id)
                    except (ValueError, TypeError) as e:
                        logger.error("Invalid rrule for reminder %s: %s — disabling recurrence.", reminder_id, e)
                        reminder.is_recurring = False
                        reminder.rrule_string = None
                # Nagging reschedule
                if reminder.is_nagging and reminder.status != "completed":
                    tz = reminder.execution_time.tzinfo or timezone.utc
                    next_nag = datetime.now(tz) + timedelta(minutes=5)
                    self.scheduler.add_job(
                        execute_reminder_job,
                        "date",
                        run_date=next_nag,
                        args=[reminder_id, True],
                        id=f"nag_{reminder_id}",
                        replace_existing=True,
                    )
                    scheduled_nagging_job = True
                    logger.info("Scheduled nagging for reminder %s at %s.", reminder_id, next_nag)

                await session.commit()

            except Exception as e:
                await session.rollback()
                # Critical: if DB state was rolled back, also remove newly created jobs
                # to keep scheduler and DB state consistent.
                if scheduled_main_job:
                    self.remove_reminder_job(reminder_id)
                elif scheduled_nagging_job:
                    self.remove_nagging_job(reminder_id)
                logger.error("Error executing reminder %s: %s", reminder_id, e, exc_info=True)
                raise

    async def _send_telegram_message(
        self, user_id: int, text: str, l10n: dict, reply_markup=None
    ) -> None:
        """Send a reminder notification, suppressing bot-blocked and bad-request errors."""
        try:
            await self.bot.send_message(
                chat_id=user_id,
                text=f"{l10n['reminder_prefix']}{text}",
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
        except TelegramForbiddenError:
            logger.warning("User %s has blocked the bot.", user_id)
        except TelegramBadRequest as e:
            logger.error("Bad request sending to %s: %s", user_id, e)
        except Exception as e:
            logger.error("Failed to send message to %s: %s", user_id, e, exc_info=True)
