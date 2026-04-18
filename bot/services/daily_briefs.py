"""
Daily Briefs Service — Sends Morning/Evening Summary Messages.

Runs as an hourly APScheduler cron job. For each user with active tasks,
checks if it is 09:00 (morning) or 23:00 (evening) in their local timezone
and sends the appropriate summary.
"""

import logging
from datetime import datetime

import pytz
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from sqlalchemy import select

from bot.database.dao.reminder import ReminderDAO
from bot.database.dao.user import UserDAO
from bot.database.models import Reminder, User
from bot.utils.time_ext import format_time

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_morning_text(tasks, user, l10n: dict) -> str:
    lines = [l10n.get("brief_morning", "🌅 **Доброе утро! План на сегодня:**\n")]
    for t in tasks:
        time_str = format_time(t.execution_time, user.timezone, user.show_utc_offset, "%H:%M")
        lines.append(f"▫️ `{time_str}`: {t.reminder_text}")
    return "\n".join(lines)


def _build_evening_text(completed, pending, user, l10n: dict) -> str:
    lines = [
        l10n.get("brief_evening_title", "🌙 **Итоги дня:**"),
        l10n.get("brief_evening_done", "✅ Выполнено: {count}").format(count=len(completed)),
        l10n.get("brief_evening_pending", "⏳ Осталось/Пропущено: {count}\n").format(count=len(pending)),
    ]
    for t in completed:
        time_str = format_time(t.execution_time, user.timezone, user.show_utc_offset, "%H:%M")
        lines.append(f"✅ ~{t.reminder_text}~ ({time_str})")
    for t in pending:
        time_str = format_time(t.execution_time, user.timezone, user.show_utc_offset, "%H:%M")
        lines.append(f"❌ {t.reminder_text} ({time_str})")  # BUG-4 fixed: closing paren added
    return "\n".join(lines)


async def _send_safe(bot: Bot, user_id: int, text: str) -> None:
    """Send a message, silently suppressing bot-blocked and bad-request errors."""
    try:
        await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
    except TelegramForbiddenError:
        logger.warning("User %s has blocked the bot — skipping brief.", user_id)
    except TelegramBadRequest as e:
        logger.error("Bad request sending brief to %s: %s", user_id, e)
    except Exception as e:
        logger.error("Failed to send brief to %s: %s", user_id, e, exc_info=True)


# ---------------------------------------------------------------------------
# Main job function (canonical — registered by setup_daily_briefs)
# ---------------------------------------------------------------------------

async def process_daily_briefs() -> None:
    """Process morning/evening briefs for all users with active tasks."""
    from bot.services.scheduler import _instance
    if not _instance:
        logger.error("Failed to process daily briefs: SchedulerService not initialized")
        return
    
    bot = _instance.bot
    session_pool_factory = _instance.session_pool
    logger.info("Starting hourly daily briefs check...")

    try:
        async with session_pool_factory() as session:

            # Query only user IDs who have at least one active reminder
            result = await session.execute(
                select(User.id)
                .distinct()
                .join(Reminder)
                .where(Reminder.status.in_(["pending", "completed"]))
            )
            user_ids = result.scalars().all()

        for uid in user_ids:
            async with session_pool_factory() as session:
                user_dao = UserDAO(session)
                user = await user_dao.get_by_id(uid)
                if not user:
                    continue
                
                try:
                    tz = pytz.timezone(user.timezone)
                except Exception:
                    logger.warning("Invalid timezone '%s' for user %s, using UTC", user.timezone, user.id)
                    tz = pytz.UTC

                if getattr(user, 'briefs_enabled', True) is False:
                    continue
                    
                local_hour = datetime.now(tz).hour
                reminder_dao = ReminderDAO(session)
                
                from bot.lexicon import get_l10n
                l10n = get_l10n(user.language)

                if local_hour == getattr(user, 'morning_brief_hour', 9):
                    tasks = await reminder_dao.get_today_pending_tasks(user.id, user.timezone)
                    if tasks:
                        text = _build_morning_text(tasks, user, l10n)
                        await _send_safe(bot, user.id, text)
                        logger.info("Morning brief sent to user %s", user.id)

                elif local_hour == getattr(user, 'evening_brief_hour', 23):
                    completed = await reminder_dao.get_today_completed_tasks(user.id, user.timezone)
                    pending = await reminder_dao.get_today_pending_tasks(user.id, user.timezone)
                    if completed or pending:
                        text = _build_evening_text(completed, pending, user, l10n)
                        await _send_safe(bot, user.id, text)
                        logger.info("Evening brief sent to user %s", user.id)

    except Exception as e:
        logger.error("Error in daily briefs job: %s", e, exc_info=True)


# ---------------------------------------------------------------------------
# Scheduler registration
# ---------------------------------------------------------------------------

def setup_daily_briefs(scheduler) -> None:
    """Register the hourly cron job for daily briefs. Call once at startup."""
    logger.info("Registering hourly daily briefs cron job")
    scheduler.add_job(
        process_daily_briefs,
        "cron",
        minute=0,
        id="hourly_daily_briefs",
        replace_existing=True,
    )