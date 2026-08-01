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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.database.dao.user import UserDAO
from bot.database.models import Reminder, User, is_habit_like
from bot.keyboards.inline import get_task_done_keyboard
from bot.lexicon import get_l10n
from bot.utils.time_ext import is_quiet_hours, next_occurrence_utc, parse_hhmm, to_utc_aware

logger = logging.getLogger(__name__)
NAGGING_INTERVAL_MINUTES = 5
# After this many consecutive TelegramForbiddenError sends, stop rescheduling
# the reminder (both recurrence and nagging) instead of retrying forever
# against a user who blocked the bot.
FORBIDDEN_STRIKES_LIMIT = 3
# Backoff schedule (minutes) for retrying a reminder send that failed with a
# retryable/permanent error (NOT delivered). Capped — once exhausted, no
# further retry job is scheduled; reconcile_jobs_with_db (last_fired_at
# stays unset) catches up on the next restart instead.
SEND_RETRY_BACKOFF_MINUTES = [1, 5, 15, 60]

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
        now_utc_naive = now_utc.replace(tzinfo=None)
        restored = 0
        async with self.session_pool() as session:
            result = await session.execute(
                select(Reminder).where(
                    Reminder.status == "pending",
                    Reminder.is_fluid_habit.is_(False),
                    Reminder.forbidden_strikes < FORBIDDEN_STRIKES_LIMIT,
                    # A soft-deleted reminder (pending_delete_at set) has its
                    # job removed on purpose, awaiting either Undo or the
                    # cleanup sweep — reconcile must not resurrect it.
                    Reminder.pending_delete_at.is_(None),
                )
            )
            reminders = result.scalars().all()

            user_tz_result = await session.execute(select(User.id, User.timezone))
            user_tz_map = {uid: tz for uid, tz in user_tz_result.all()}

            for reminder in reminders:
                if self.scheduler.get_job(str(reminder.id)) is not None:
                    continue

                run_at_utc = to_utc_aware(reminder.execution_time)

                if run_at_utc <= now_utc:
                    last_fired = reminder.last_fired_at
                    already_delivered = (
                        last_fired is not None and last_fired >= reminder.execution_time
                    )
                    if reminder.is_recurring and reminder.rrule_string:
                        if already_delivered:
                            # This cycle was already sent (bot is just
                            # catching up on scheduling the next one, e.g.
                            # after a jobstore reset) — compute the true next
                            # occurrence in the user's local timezone (not raw
                            # UTC) so a restart across a DST transition
                            # doesn't shift the reminder's local wall-clock
                            # time, same reasoning as _execute_reminder.
                            user_tz = user_tz_map.get(reminder.user_id, "UTC")
                            try:
                                next_run_utc_naive = next_occurrence_utc(
                                    reminder.rrule_string, reminder.execution_time, user_tz, now_utc_naive
                                )
                            except (ValueError, TypeError) as e:
                                logger.error(
                                    "Invalid rrule for reminder %s during reconcile: %s", reminder.id, e
                                )
                                next_run_utc_naive = None
                            if not next_run_utc_naive:
                                continue
                            reminder.execution_time = next_run_utc_naive
                            run_at_utc = to_utc_aware(next_run_utc_naive)
                        else:
                            # P1-4: this cycle was NEVER delivered (the bot
                            # was down through it) — schedule a near-term
                            # catch-up for the CURRENT missed cycle instead of
                            # silently jumping straight to the next
                            # occurrence. Leaving execution_time untouched
                            # also means get_overdue_pending_tasks (missed
                            # recovery) can surface it until the catch-up
                            # fires. _execute_reminder's own recurring-
                            # reschedule logic computes the true next
                            # occurrence relative to "now" once this fires, so
                            # the chain self-corrects — only the single oldest
                            # missed cycle is (late) delivered, not a full
                            # backfill of every cycle downtime spanned.
                            run_at_utc = now_utc + timedelta(minutes=1)
                    else:
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

    def _schedule_send_retry(self, reminder_id: int, attempt_number: int, is_nagging_execution: bool) -> None:
        """Schedule a retry for a send that failed with a retryable/permanent error.

        *attempt_number* is 1-based (the count of consecutive failures
        including this one). Once it exceeds SEND_RETRY_BACKOFF_MINUTES, no
        further retry job is scheduled — see the module-level docstring on
        SEND_RETRY_BACKOFF_MINUTES for why that's not a lost notification.
        """
        backoff = SEND_RETRY_BACKOFF_MINUTES
        if attempt_number > len(backoff):
            logger.warning(
                "Reminder %s: %s consecutive send failures — exhausted retry backoff, "
                "leaving it for reconcile_jobs_with_db on next restart.",
                reminder_id,
                attempt_number,
            )
            return
        delay_minutes = backoff[attempt_number - 1]
        run_date = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
        job_id = f"nag_{reminder_id}" if is_nagging_execution else str(reminder_id)
        self.scheduler.add_job(
            execute_reminder_job,
            "date",
            run_date=run_date,
            args=[reminder_id, is_nagging_execution],
            id=job_id,
            replace_existing=True,
        )
        logger.info(
            "Reminder %s: send failed (attempt %s), retrying at %s.", reminder_id, attempt_number, run_date
        )

    def resume_nagging_if_stalled(self, reminder: Reminder) -> bool:
        """Recreate the nag job if raising the limit should revive a dead chain.

        _execute_reminder stops scheduling follow-ups once nagging_sent_count
        reaches nagging_max_repeats, without touching any job (there's none
        left to remove). If the user later raises the limit, that chain stays
        dead until the next main reminder fire — days away for a recurring
        reminder, never for a one-off. If the reminder is overdue and has no
        job pending, schedule one immediately instead of waiting.
        """
        if not reminder.is_nagging or reminder.status == "completed":
            return False
        if int(reminder.forbidden_strikes or 0) >= FORBIDDEN_STRIKES_LIMIT:
            return False
        max_nag_repeats = max(0, int(reminder.nagging_max_repeats or 0))
        sent_nags = max(0, int(reminder.nagging_sent_count or 0))
        if sent_nags >= max_nag_repeats:
            return False
        run_at_utc = to_utc_aware(reminder.execution_time)
        now_utc = datetime.now(timezone.utc)
        if run_at_utc > now_utc:
            return False
        if (
            self.scheduler.get_job(str(reminder.id)) is not None
            or self.scheduler.get_job(f"nag_{reminder.id}") is not None
        ):
            return False
        next_nag = now_utc + timedelta(minutes=NAGGING_INTERVAL_MINUTES)
        self.scheduler.add_job(
            execute_reminder_job,
            "date",
            run_date=next_nag,
            args=[reminder.id, True],
            id=f"nag_{reminder.id}",
            replace_existing=True,
        )
        logger.info("Resumed stalled nagging chain for reminder %s at %s.", reminder.id, next_nag)
        return True

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
                keyboard = get_task_done_keyboard(
                    reminder.id,
                    l10n,
                    cycle_due_ts=cycle_due_ts,
                    show_not_today=is_habit_like(reminder) or reminder.is_fluid_habit,
                )
                send_outcome = await self._send_or_replace_nag_message(
                    reminder=reminder,
                    l10n=l10n,
                    keyboard=keyboard,
                    is_nagging_execution=is_nagging_execution,
                )
                # Only a confirmed "user blocked the bot" counts toward the
                # strike limit. A transient network/bad-request error must
                # NOT reset an existing streak of forbidden strikes — a user
                # who has blocked the bot doesn't stop being blocked just
                # because one delivery attempt happened to hit an unrelated
                # error instead of TelegramForbiddenError.
                if send_outcome == "forbidden":
                    reminder.forbidden_strikes = int(reminder.forbidden_strikes or 0) + 1
                elif send_outcome == "ok":
                    reminder.forbidden_strikes = 0
                gave_up = reminder.forbidden_strikes >= FORBIDDEN_STRIKES_LIMIT
                if gave_up:
                    logger.warning(
                        "Reminder %s: %s consecutive blocked sends — giving up on rescheduling.",
                        reminder_id,
                        reminder.forbidden_strikes,
                    )
                    self.remove_reminder_job(reminder_id)
                    self.remove_nagging_job(reminder_id)

                if send_outcome in ("retryable_error", "permanent_error"):
                    # NOT delivered — do not advance any state as though it
                    # had been: last_fired_at stays unset (so a restart's
                    # reconcile_jobs_with_db still sees this cycle as
                    # undelivered), recurrence doesn't move to the next
                    # occurrence, and this doesn't count as a sent nag. Retry
                    # with backoff instead, unless the strike limit above
                    # already gave up on this reminder entirely.
                    reminder.send_retry_count = int(reminder.send_retry_count or 0) + 1
                    if not gave_up:
                        self._schedule_send_retry(reminder_id, reminder.send_retry_count, is_nagging_execution)
                    await session.commit()
                    return

                # Delivered (or the user has blocked the bot, which the
                # strike mechanism above already accounts for) — this cycle
                # is handled, reset the retry counter and advance state.
                reminder.send_retry_count = 0
                if not is_nagging_execution:
                    reminder.last_fired_at = now_utc_naive

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

        Returns a (message, outcome) tuple: message is the Telegram Message
        object on success (otherwise None); outcome is one of:
          "ok"               — delivered successfully.
          "forbidden"        — the user has blocked the bot.
          "permanent_error"  — Telegram rejected this specific payload (bad
                                request) even after retrying without the
                                keyboard — retrying the identical payload
                                again would fail the same way.
          "retryable_error"  — some other failure (timeout, network, etc.)
                                that may well succeed if retried.
        Neither error outcome is the user's fault, so callers must not treat
        them the same as "forbidden" (see forbidden_strikes), and must not
        treat them as delivered (see last_fired_at).
        """
        try:
            message = await self.bot.send_message(
                chat_id=user_id,
                text=f"{l10n['reminder_prefix']}{text}",
                reply_markup=reply_markup,
                parse_mode=None,
            )
            return message, "ok"
        except TelegramForbiddenError:
            logger.warning("User %s has blocked the bot.", user_id)
            return None, "forbidden"
        except TelegramBadRequest as e:
            logger.error("Bad request sending to %s: %s — retrying without keyboard.", user_id, e)
            try:
                message = await self.bot.send_message(
                    chat_id=user_id,
                    text=f"{l10n['reminder_prefix']}{text}",
                    parse_mode=None,
                )
                return message, "ok"
            except Exception as retry_e:
                logger.error("Retry without keyboard also failed for %s: %s", user_id, retry_e, exc_info=True)
                return None, "permanent_error"
        except Exception as e:
            logger.error("Failed to send message to %s: %s", user_id, e, exc_info=True)
            return None, "retryable_error"

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
    ) -> str:
        """For nagging reminders, keep only one active reminder message visible.

        Returns the send outcome — see _send_telegram_message.
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

        sent_message, outcome = await self._send_telegram_message(
            reminder.user_id,
            reminder.reminder_text,
            l10n,
            keyboard,
        )

        if not reminder.is_nagging:
            self._clear_nag_tracking(reminder)
            return outcome

        if sent_message is not None:
            reminder.last_nag_chat_id = int(sent_message.chat.id)
            reminder.last_nag_message_id = int(sent_message.message_id)

        return outcome
