"""
SchedulerService — APScheduler facade for reminder jobs.

Job targets cannot hold unpicklable 'self' references, so a module-level
``_instance`` singleton lets the free function ``execute_reminder_job``
reach the service at call time. This is a known APScheduler tradeoff.
"""

import logging
from datetime import datetime, time as dt_time, timedelta, timezone

import pytz
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.database.dao.user import UserDAO
from bot.database.models import Reminder
from bot.keyboards.inline import get_task_done_keyboard
from bot.lexicon import get_l10n
from bot.utils.habits_utils import is_habit_like as _is_habit_like_util
from bot.utils.recurrence import next_rrule_occurrence
from bot.utils.time_ext import resolve_tz, to_utc_aware, to_utc_naive

logger = logging.getLogger(__name__)
NAGGING_INTERVAL_MINUTES = 5

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
        run_date_utc = to_utc_aware(run_date)
        try:
            self.scheduler.add_job(
                execute_reminder_job,
                "date",
                run_date=run_date_utc,
                args=[reminder_id, False],
                id=str(reminder_id),
                replace_existing=True,
            )
            logger.info("Scheduled reminder %s for %s (nagging=%s).", reminder_id, run_date_utc, is_nagging)
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

    @staticmethod
    def _parse_hhmm(raw: str, fallback: str) -> dt_time:
        val = (raw or fallback).strip()
        try:
            hour_str, min_str = val.split(":", 1)
            h = int(hour_str)
            m = int(min_str)
            if 0 <= h <= 23 and 0 <= m <= 59:
                return dt_time(hour=h, minute=m)
        except Exception:
            pass
        fh, fm = fallback.split(":")
        return dt_time(hour=int(fh), minute=int(fm))

    def _is_quiet_hours_now(self, user, now_utc: datetime) -> bool:
        if not bool(getattr(user, "quiet_hours_enabled", False)):
            return False
        user_tz = resolve_tz(user.timezone)
        now_local = now_utc.astimezone(user_tz)
        start = self._parse_hhmm(getattr(user, "quiet_hours_start", "23:00"), "23:00")
        end = self._parse_hhmm(getattr(user, "quiet_hours_end", "07:00"), "07:00")
        current = now_local.time()
        if start == end:
            return True
        if start < end:
            return start <= current < end
        return current >= start or current < end

    def _next_quiet_end_utc(self, user, now_utc: datetime) -> datetime:
        user_tz = resolve_tz(user.timezone)
        now_local = now_utc.astimezone(user_tz)
        end = self._parse_hhmm(getattr(user, "quiet_hours_end", "07:00"), "07:00")
        candidate = now_local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
        if candidate <= now_local:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    @staticmethod
    def _is_habit_like(reminder: Reminder) -> bool:
        return _is_habit_like_util(reminder)

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
                if is_nagging_execution and not reminder.is_nagging:
                    logger.info("Skipping stale nagging execution for reminder %s (nagging disabled).", reminder_id)
                    return

                user_dao = UserDAO(session)
                user = await user_dao.get_by_id(reminder.user_id)
                if not user:
                    logger.warning("User %s not found for reminder %s.", reminder.user_id, reminder_id)
                    return

                now_utc = datetime.now(timezone.utc)
                if self._is_quiet_hours_now(user, now_utc):
                    job_id = f"nag_{reminder_id}" if is_nagging_execution else str(reminder_id)
                    resume_utc = self._next_quiet_end_utc(user, now_utc)
                    self.scheduler.add_job(
                        execute_reminder_job,
                        "date",
                        run_date=resume_utc,
                        args=[reminder_id, is_nagging_execution],
                        id=job_id,
                        replace_existing=True,
                    )
                    logger.info(
                        "Reminder %s deferred due to quiet hours. New run at %s (nagging=%s).",
                        reminder_id,
                        resume_utc,
                        is_nagging_execution,
                    )
                    return

                now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
                cycle_due_ts = None
                if self._is_habit_like(reminder):
                    if not is_nagging_execution:
                        prev_due = reminder.habit_active_due_at
                        if (
                            prev_due is not None
                            and reminder.habit_last_completed_due_at != prev_due
                            and now_utc_naive >= (prev_due + timedelta(hours=24))
                        ):
                            reminder.habit_streak_current = 0

                        current_due = reminder.execution_time
                        if current_due.tzinfo is not None:
                            current_due = current_due.astimezone(timezone.utc).replace(tzinfo=None)
                        reminder.habit_active_due_at = current_due
                    active_due = reminder.habit_active_due_at or reminder.execution_time
                    if active_due.tzinfo is not None:
                        active_due = active_due.astimezone(timezone.utc).replace(tzinfo=None)
                    cycle_due_ts = int(active_due.replace(tzinfo=timezone.utc).timestamp())

                l10n = get_l10n(user.language)
                keyboard = get_task_done_keyboard(reminder.id, l10n, cycle_due_ts=cycle_due_ts)
                await self._send_or_replace_nag_message(
                    reminder=reminder,
                    l10n=l10n,
                    keyboard=keyboard,
                    is_nagging_execution=is_nagging_execution,
                )

                if is_nagging_execution:
                    reminder.nagging_sent_count = max(0, int(reminder.nagging_sent_count or 0)) + 1
                else:
                    # New reminder cycle starts with zero sent nagging follow-ups.
                    reminder.nagging_sent_count = 0

                # Recurring reschedule
                if not is_nagging_execution and reminder.is_recurring and reminder.rrule_string:
                    start_dt = reminder.execution_time
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=timezone.utc)
                    next_run = next_rrule_occurrence(
                        reminder.rrule_string, start_dt, datetime.now(start_dt.tzinfo)
                    )
                    if next_run:
                        next_run_utc_naive = to_utc_naive(next_run)
                        reminder.execution_time = next_run_utc_naive
                        self.schedule_reminder(
                            reminder_id,
                            to_utc_aware(next_run_utc_naive),
                            is_nagging=reminder.is_nagging,
                        )
                        scheduled_main_job = True
                        logger.info("Rescheduled recurring reminder %s → %s.", reminder_id, next_run_utc_naive)
                    else:
                        logger.info("Recurring reminder %s has no future occurrences.", reminder_id)
                # Nagging reschedule with per-reminder max repeats.
                max_nag_repeats = max(0, int(reminder.nagging_max_repeats or 0))
                sent_nags = max(0, int(reminder.nagging_sent_count or 0))
                if reminder.is_nagging and reminder.status != "completed" and sent_nags < max_nag_repeats:
                    tz = reminder.execution_time.tzinfo or timezone.utc
                    next_nag = datetime.now(tz) + timedelta(minutes=NAGGING_INTERVAL_MINUTES)
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
                elif reminder.is_nagging and reminder.status != "completed":
                    logger.info(
                        "Nagging limit reached for reminder %s (%s/%s); not scheduling more follow-ups.",
                        reminder_id,
                        sent_nags,
                        max_nag_repeats,
                    )

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
    ):
        """Send a reminder notification, suppressing bot-blocked and bad-request errors.

        Returns the Telegram Message object on success, otherwise None.
        """
        try:
            return await self.bot.send_message(
                chat_id=user_id,
                text=f"{l10n['reminder_prefix']}{text}",
                reply_markup=reply_markup,
                parse_mode=None,
            )
        except TelegramForbiddenError:
            logger.warning("User %s has blocked the bot.", user_id)
        except TelegramBadRequest as e:
            logger.error("Bad request sending to %s: %s", user_id, e)
        except Exception as e:
            logger.error("Failed to send message to %s: %s", user_id, e, exc_info=True)
        return None

    async def _delete_telegram_message(self, chat_id: int, message_id: int) -> None:
        """Best-effort Telegram message deletion helper."""
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramForbiddenError:
            logger.warning("Cannot delete nag message %s in chat %s: bot blocked/forbidden.", message_id, chat_id)
        except TelegramBadRequest as e:
            logger.debug("Cannot delete nag message %s in chat %s: %s", message_id, chat_id, e)
        except Exception as e:
            logger.error(
                "Unexpected error deleting nag message %s in chat %s: %s",
                message_id,
                chat_id,
                e,
                exc_info=True,
            )

    @staticmethod
    def _clear_nag_tracking(reminder: Reminder) -> None:
        reminder.last_nag_chat_id = None
        reminder.last_nag_message_id = None

    async def _send_or_replace_nag_message(
        self,
        *,
        reminder: Reminder,
        l10n: dict,
        keyboard,
        is_nagging_execution: bool,
    ) -> None:
        """For nagging reminders, keep only one active reminder message visible."""
        if (
            is_nagging_execution
            and reminder.last_nag_chat_id is not None
            and reminder.last_nag_message_id is not None
        ):
            await self._delete_telegram_message(
                chat_id=int(reminder.last_nag_chat_id),
                message_id=int(reminder.last_nag_message_id),
            )

        sent_message = await self._send_telegram_message(
            reminder.user_id,
            reminder.reminder_text,
            l10n,
            keyboard,
        )

        if not reminder.is_nagging:
            self._clear_nag_tracking(reminder)
            return

        if sent_message is not None:
            reminder.last_nag_chat_id = int(sent_message.chat.id)
            reminder.last_nag_message_id = int(sent_message.message_id)
