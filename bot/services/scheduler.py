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
from bot.database.models import Reminder, is_habit_like
from bot.keyboards.inline import get_task_done_keyboard
from bot.lexicon import get_l10n
from bot.utils.time_ext import is_quiet_hours, next_occurrence_utc, parse_hhmm, to_utc_aware, to_utc_naive

logger = logging.getLogger(__name__)
NAGGING_INTERVAL_MINUTES = 5
# After this many consecutive TelegramForbiddenError sends, stop rescheduling
# the reminder (both recurrence and nagging) instead of retrying forever
# against a user who blocked the bot.
FORBIDDEN_STRIKES_LIMIT = 3

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
        """Add (or replace) a one-shot date-trigger job for *reminder_id*.

        *is_nagging* only affects the log line below — it does not schedule a
        follow-up job. The actual nagging chain is started by
        _execute_reminder once the main reminder has fired and checks
        reminder.is_nagging from the DB at that point. Callers pass it here
        purely so the schedule log reflects current nagging state.
        """
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

    async def reconcile_jobs_with_db(self) -> None:
        """Recreate scheduler jobs for pending reminders missing from the jobstore.

        Run once at startup: a job can be missing from the jobstore after bot
        downtime spanning a misfire window, or after a jobstore reset. Without
        this, a dropped date-job means the reminder (and, for recurring ones,
        the whole future chain) silently never fires again.

        One-off reminders stay status='pending' after firing (until the user
        taps Done), so an overdue one-off with no job isn't necessarily
        undelivered — it may just be waiting on the user. Only create a
        catch-up job for it if last_fired_at shows this cycle was never sent.
        Reminders that already gave up after repeated TelegramForbiddenError
        (see FORBIDDEN_STRIKES_LIMIT) are skipped entirely.
        """
        now_utc = datetime.now(timezone.utc)
        restored = 0
        async with self.session_pool() as session:
            result = await session.execute(
                select(Reminder).where(
                    Reminder.status == "pending",
                    Reminder.is_fluid_habit.is_(False),
                    Reminder.forbidden_strikes < FORBIDDEN_STRIKES_LIMIT,
                )
            )
            reminders = result.scalars().all()

            for reminder in reminders:
                if self.scheduler.get_job(str(reminder.id)) is not None:
                    continue

                run_at_utc = to_utc_aware(reminder.execution_time)

                if run_at_utc <= now_utc:
                    if reminder.is_recurring and reminder.rrule_string:
                        try:
                            rule = rrulestr(reminder.rrule_string, dtstart=run_at_utc)
                            next_run = rule.after(now_utc)
                        except (ValueError, TypeError) as e:
                            logger.error(
                                "Invalid rrule for reminder %s during reconcile: %s", reminder.id, e
                            )
                            next_run = None
                        if not next_run:
                            continue
                        next_run_utc_naive = to_utc_naive(next_run)
                        reminder.execution_time = next_run_utc_naive
                        run_at_utc = to_utc_aware(next_run_utc_naive)
                    else:
                        last_fired = reminder.last_fired_at
                        already_delivered = (
                            last_fired is not None and last_fired >= reminder.execution_time
                        )
                        if already_delivered:
                            # Notification already reached the user; they just
                            # haven't tapped Done yet — don't resend it.
                            continue
                        run_at_utc = now_utc + timedelta(minutes=1)

                self.schedule_reminder(reminder.id, run_at_utc, is_nagging=reminder.is_nagging)
                restored += 1

            await session.commit()

        logger.info("Reconciled scheduler jobs with DB: restored %s job(s).", restored)

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

    def _is_quiet_hours_now(self, user, now_utc: datetime) -> bool:
        try:
            user_tz = pytz.timezone(user.timezone)
        except Exception:
            user_tz = pytz.UTC
        now_local = now_utc.astimezone(user_tz)
        return is_quiet_hours(user, now_local)

    def _next_quiet_end_utc(self, user, now_utc: datetime) -> datetime:
        try:
            user_tz = pytz.timezone(user.timezone)
        except Exception:
            user_tz = pytz.UTC
        now_local = now_utc.astimezone(user_tz)
        end = parse_hhmm(getattr(user, "quiet_hours_end", "07:00"), "07:00")
        candidate = now_local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
        if candidate <= now_local:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)

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
                if not is_nagging_execution:
                    reminder.last_fired_at = now_utc_naive
                cycle_due_ts = None
                if is_habit_like(reminder):
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
                        # Only adopt execution_time as the active cycle when it
                        # hasn't already been advanced to the NEXT cycle. A
                        # snooze re-fire runs after the original on-time fire
                        # already pushed execution_time forward (see "Recurring
                        # reschedule" below) — overwriting active_due here would
                        # credit the snoozed Done to tomorrow's cycle instead of
                        # today's, and leave today's cycle permanently unclaimed.
                        if current_due <= now_utc_naive + timedelta(minutes=5):
                            reminder.habit_active_due_at = current_due
                    active_due = reminder.habit_active_due_at or reminder.execution_time
                    if active_due.tzinfo is not None:
                        active_due = active_due.astimezone(timezone.utc).replace(tzinfo=None)
                    cycle_due_ts = int(active_due.replace(tzinfo=timezone.utc).timestamp())

                l10n = get_l10n(user.language)
                keyboard = get_task_done_keyboard(reminder.id, l10n, cycle_due_ts=cycle_due_ts)
                forbidden = await self._send_or_replace_nag_message(
                    reminder=reminder,
                    l10n=l10n,
                    keyboard=keyboard,
                    is_nagging_execution=is_nagging_execution,
                )
                reminder.forbidden_strikes = (
                    int(reminder.forbidden_strikes or 0) + 1 if forbidden else 0
                )
                gave_up = reminder.forbidden_strikes >= FORBIDDEN_STRIKES_LIMIT
                if gave_up:
                    logger.warning(
                        "Reminder %s: %s consecutive blocked sends — giving up on rescheduling.",
                        reminder_id,
                        reminder.forbidden_strikes,
                    )
                    self.remove_reminder_job(reminder_id)
                    self.remove_nagging_job(reminder_id)

                if is_nagging_execution:
                    reminder.nagging_sent_count = max(0, int(reminder.nagging_sent_count or 0)) + 1
                else:
                    # New reminder cycle starts with zero sent nagging follow-ups.
                    reminder.nagging_sent_count = 0

                # Recurring reschedule
                if not gave_up and not is_nagging_execution and reminder.is_recurring and reminder.rrule_string:
                    try:
                        next_run_utc_naive = next_occurrence_utc(
                            reminder.rrule_string,
                            reminder.execution_time,
                            user.timezone,
                            now_utc_naive,
                        )
                        if next_run_utc_naive:
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
                    except (ValueError, TypeError) as e:
                        logger.error("Invalid rrule for reminder %s: %s — disabling recurrence.", reminder_id, e)
                        reminder.is_recurring = False
                        reminder.rrule_string = None
                # Nagging reschedule with per-reminder max repeats.
                max_nag_repeats = max(0, int(reminder.nagging_max_repeats or 0))
                sent_nags = max(0, int(reminder.nagging_sent_count or 0))
                if not gave_up and reminder.is_nagging and reminder.status != "completed" and sent_nags < max_nag_repeats:
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

        Returns a (message, forbidden) tuple: message is the Telegram Message
        object on success (otherwise None); forbidden is True iff the send
        failed specifically because the user has blocked the bot.
        """
        try:
            message = await self.bot.send_message(
                chat_id=user_id,
                text=f"{l10n['reminder_prefix']}{text}",
                reply_markup=reply_markup,
                parse_mode=None,
            )
            return message, False
        except TelegramForbiddenError:
            logger.warning("User %s has blocked the bot.", user_id)
            return None, True
        except TelegramBadRequest as e:
            logger.error("Bad request sending to %s: %s", user_id, e)
        except Exception as e:
            logger.error("Failed to send message to %s: %s", user_id, e, exc_info=True)
        return None, False

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
    ) -> bool:
        """For nagging reminders, keep only one active reminder message visible.

        Returns True iff the send failed because the user has blocked the bot.
        """
        if (
            is_nagging_execution
            and reminder.last_nag_chat_id is not None
            and reminder.last_nag_message_id is not None
        ):
            await self._delete_telegram_message(
                chat_id=int(reminder.last_nag_chat_id),
                message_id=int(reminder.last_nag_message_id),
            )

        sent_message, forbidden = await self._send_telegram_message(
            reminder.user_id,
            reminder.reminder_text,
            l10n,
            keyboard,
        )

        if not reminder.is_nagging:
            self._clear_nag_tracking(reminder)
            return forbidden

        if sent_message is not None:
            reminder.last_nag_chat_id = int(sent_message.chat.id)
            reminder.last_nag_message_id = int(sent_message.message_id)

        return forbidden
