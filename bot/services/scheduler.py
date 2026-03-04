"""
SchedulerService — facade over APScheduler.

All scheduler interactions MUST go through this class.
Receives all dependencies (bot, scheduler, session_pool) via constructor —
no global imports.
"""

import logging
from datetime import datetime, timedelta

import pytz
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dateutil.rrule import rrulestr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.database.models import Reminder
from bot.database.dao.user import UserDAO
from bot.keyboards.inline import get_task_done_keyboard
from bot.lexicon import get_l10n

logger = logging.getLogger(__name__)

# Global delegate for APScheduler to avoid pickling `self` (which contains unpicklable Bot/Scheduler instances)
_instance = None

async def execute_reminder_job(reminder_id: int, is_nagging_execution: bool = False) -> None:
    global _instance
    if _instance:
        await _instance._execute_reminder(reminder_id, is_nagging_execution=is_nagging_execution)
    else:
        logger.error(f"Cannot execute reminder {reminder_id}: SchedulerService not initialized in this process.")


class SchedulerService:
    """
    Facade over APScheduler.
    Owns its own session_pool for job execution (outside request scope).
    """

    def __init__(
        self,
        scheduler: AsyncIOScheduler,
        bot: Bot,
        session_pool: async_sessionmaker,
    ) -> None:
        self.scheduler = scheduler
        self.bot = bot
        self.session_pool = session_pool
        
        global _instance
        _instance = self

    # ------------------------------------------------------------------ #
    #  Public API (called from handlers)                                  #
    # ------------------------------------------------------------------ #

    def schedule_reminder(
        self, reminder_id: int, run_date: datetime, *, is_nagging: bool = False
    ) -> None:
        """Register a one-shot 'date' trigger job for a reminder."""
        try:
            self.scheduler.add_job(
                execute_reminder_job,
                "date",
                run_date=run_date,
                args=[reminder_id, False],
                id=str(reminder_id),
                replace_existing=True,
            )
            logger.info(f"Scheduled reminder {reminder_id} for {run_date}")
        except Exception as e:
            logger.error(
                f"Failed to schedule reminder {reminder_id}: {e}", exc_info=True
            )

    def remove_reminder_job(self, reminder_id: int) -> None:
        """Remove the main job + any nagging job for a reminder."""
        try:
            self.scheduler.remove_job(str(reminder_id))
            logger.info(f"Removed job for reminder {reminder_id}")
        except Exception:
            pass
        self.remove_nagging_job(reminder_id)

    def remove_nagging_job(self, reminder_id: int) -> None:
        """Remove only the nagging follow-up job."""
        try:
            self.scheduler.remove_job(f"nag_{reminder_id}")
            logger.info(f"Removed nagging job for reminder {reminder_id}")
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Job target (called by APScheduler, outside request scope)          #
    # ------------------------------------------------------------------ #

    async def _execute_reminder(self, reminder_id: int, is_nagging_execution: bool = False) -> None:
        """
        APScheduler job target.  Opens its own session (no middleware),
        sends the Telegram message, handles recurring reschedule & nagging.

        Session lifecycle:
        - ``async with self.session_pool()`` auto-commits on clean exit and
          rolls back on exception.  We must NOT call ``session.commit()``
          manually inside the block — that causes a double-commit error.
        """
        logger.info(f"Executing reminder job for ID: {reminder_id} (nagging={is_nagging_execution})")

        async with self.session_pool() as session:
            try:
                result = await session.execute(
                    select(Reminder).where(Reminder.id == reminder_id)
                )
                reminder = result.scalar_one_or_none()

                if not reminder:
                    logger.warning(f"Reminder {reminder_id} not found in DB. Skipping.")
                    return

                # FIX: Do not fire for reminders that were already marked done
                # between the time the nag job was scheduled and now.
                if reminder.status == "completed":
                    logger.info(
                        f"Reminder {reminder_id} already completed — skipping nag execution."
                    )
                    return

                # Fetch user for language preferences
                user_dao = UserDAO(session)
                user = await user_dao.get_by_id(reminder.user_id)
                lang_code = user.language if user else None
                l10n = get_l10n(lang_code)

                # 1. Send the message
                keyboard = None
                if reminder.is_nagging:
                    keyboard = get_task_done_keyboard(reminder.id, l10n)

                await self._send_telegram_message(
                    reminder.user_id, reminder.reminder_text, l10n, reply_markup=keyboard
                )

                # 2. Handle RECURRING
                # FIX: Do NOT call session.commit() here — the session context
                # manager commits automatically on clean exit.  A manual commit
                # here raises 'This transaction is already committed' on the
                # auto-flush that follows.
                if not is_nagging_execution and reminder.is_recurring and reminder.rrule_string:
                    try:
                        start_dt = reminder.execution_time
                        if start_dt.tzinfo is None:
                            start_dt = start_dt.replace(tzinfo=pytz.UTC)

                        rule = rrulestr(reminder.rrule_string, dtstart=start_dt)
                        now = datetime.now(start_dt.tzinfo)
                        next_run = rule.after(now)

                        if next_run:
                            logger.info(
                                f"Rescheduling RECURRING reminder {reminder_id} to {next_run}"
                            )
                            reminder.execution_time = next_run
                            # No session.commit() here — CM handles it on exit
                            self.schedule_reminder(
                                reminder_id,
                                next_run,
                                is_nagging=reminder.is_nagging,
                            )
                        else:
                            logger.info(f"No next occurrence for reminder {reminder_id}.")
                    except Exception as e:
                        logger.error(
                            f"Error calculating next run for reminder {reminder_id}: {e}",
                            exc_info=True,
                        )

                # 3. Handle NAGGING
                if reminder.is_nagging:
                    tz = reminder.execution_time.tzinfo or pytz.UTC
                    next_nag = datetime.now(tz) + timedelta(minutes=5)
                    logger.info(f"Scheduling NAGGING for reminder {reminder_id} at {next_nag}")
                    self.scheduler.add_job(
                        execute_reminder_job,
                        "date",
                        run_date=next_nag,
                        args=[reminder_id, True],
                        id=f"nag_{reminder_id}",
                        replace_existing=True,
                    )

            except Exception as e:
                logger.error(
                    f"Generic error in reminder wrapper for {reminder_id}: {e}",
                    exc_info=True,
                )

    async def _send_telegram_message(
        self, user_id: int, text: str, l10n: dict, reply_markup=None
    ) -> None:
        """Send a message via self.bot, handling Forbidden/BadRequest."""
        try:
            await self.bot.send_message(
                chat_id=user_id,
                text=f"{l10n['reminder_prefix']}{text}",
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
        except TelegramForbiddenError:
            logger.warning(f"User {user_id} has blocked the bot.")
        except TelegramBadRequest as e:
            logger.error(f"Bad request when sending to {user_id}: {e}")
        except Exception as e:
            logger.error(
                f"Failed to send message to {user_id}: {e}", exc_info=True
            )
