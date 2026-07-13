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
from bot.keyboards.inline import (
    get_evening_wrapup_keyboard,
    get_fluid_completion_keyboard,
    get_fluid_pick_time_keyboard,
)
from bot.utils.markdown import escape_markdown
from bot.utils.time_ext import format_time, is_quiet_hours

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_morning_text(tasks, user, l10n: dict) -> str:
    lines = [l10n.get("brief_morning", "🌅 **Доброе утро! План на сегодня:**\n")]
    for t in tasks:
        time_str = format_time(t.execution_time, user.timezone, user.show_utc_offset, "%H:%M")
        lines.append(f"▫️ `{time_str}`: {escape_markdown(t.reminder_text)}")
    return "\n".join(lines)


def _build_evening_text(completed, pending, user, l10n: dict) -> str:
    lines = [
        l10n.get("brief_evening_title", "🌙 **Итоги дня:**"),
        l10n.get("brief_evening_done", "✅ Выполнено: {count}").format(count=len(completed)),
        l10n.get("brief_evening_pending", "⏳ Осталось/Пропущено: {count}\n").format(count=len(pending)),
    ]
    for t in completed:
        time_str = format_time(t.execution_time, user.timezone, user.show_utc_offset, "%H:%M")
        lines.append(f"✅ ~{escape_markdown(t.reminder_text)}~ ({time_str})")
    for t in pending:
        time_str = format_time(t.execution_time, user.timezone, user.show_utc_offset, "%H:%M")
        lines.append(f"❌ {escape_markdown(t.reminder_text)} ({time_str})")  # BUG-4 fixed: closing paren added
    return "\n".join(lines)


async def _send_safe(bot: Bot, user_id: int, text: str, reply_markup=None) -> None:
    """Send a message, silently suppressing bot-blocked and bad-request errors.

    Falls back to plain text if Markdown entities fail to parse, so a stray
    unescaped character never causes the whole brief to be silently dropped.
    """
    try:
        await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown", reply_markup=reply_markup)
    except TelegramForbiddenError:
        logger.warning("User %s has blocked the bot — skipping brief.", user_id)
    except TelegramBadRequest as e:
        logger.error("Bad request sending brief to %s: %s — retrying without Markdown.", user_id, e)
        try:
            await bot.send_message(chat_id=user_id, text=text, parse_mode=None, reply_markup=reply_markup)
        except Exception as retry_e:
            logger.error("Retry without Markdown also failed for %s: %s", user_id, retry_e, exc_info=True)
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

            # BUG-C1 FIX: Only fetch users who have PENDING (active) reminders
            # AND have briefs enabled. Previously also included "completed" which
            # caused every past user to be processed every minute indefinitely.
            result = await session.execute(
                select(User.id)
                .distinct()
                .join(Reminder)
                .where(
                    Reminder.status == "pending",
                    User.briefs_enabled.is_(True),
                )
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
                    
                local_time_str = datetime.now(tz).strftime("%H:%M")
                if is_quiet_hours(user, datetime.now(tz)):
                    continue
                reminder_dao = ReminderDAO(session)
                
                from bot.lexicon import get_l10n
                l10n = get_l10n(user.language)
                fluid_habits = await reminder_dao.get_active_fluid_habits(user.id)
                today_str = datetime.now(tz).date().isoformat()

                morning_due = (
                    local_time_str >= getattr(user, 'morning_brief_time', "09:00")
                    and getattr(user, 'last_morning_brief_date', None) != today_str
                )
                evening_due = (
                    local_time_str >= getattr(user, 'evening_brief_time', "23:00")
                    and getattr(user, 'last_evening_brief_date', None) != today_str
                )

                if morning_due:
                    user.last_morning_brief_date = today_str
                    tasks = await reminder_dao.get_today_pending_tasks(user.id, user.timezone)
                    if tasks:
                        text = _build_morning_text(tasks, user, l10n)
                        await _send_safe(bot, user.id, text)
                        logger.info("Morning brief sent to user %s", user.id)

                    fluid_brief_only = [h for h in fluid_habits if (h.fluid_mode or "brief_only") == "brief_only"]
                    if fluid_brief_only:
                        lines = [l10n.get("fluid_morning_title", "🌊 **Fluid habits for today:**")]
                        for h in fluid_brief_only:
                            lines.append(f"▫️ {escape_markdown(h.reminder_text)}")
                        await _send_safe(bot, user.id, "\n".join(lines))

                    for h in fluid_habits:
                        if (h.fluid_mode or "brief_only") != "ask_time":
                            continue
                        if h.fluid_planned_date == today_str:
                            continue
                        await _send_safe(
                            bot,
                            user.id,
                            l10n.get("fluid_pick_time_prompt", "🌊 **Plan your fluid habit:**\nPick today’s reminder time for: **{habit}**").format(habit=escape_markdown(h.reminder_text)),
                            reply_markup=get_fluid_pick_time_keyboard(h.id, l10n),
                        )
                        logger.info("Fluid pick-time prompt sent to user %s for habit %s", user.id, h.id)

                elif evening_due:
                    user.last_evening_brief_date = today_str
                    for h in fluid_habits:
                        await reminder_dao.reset_stale_fluid_streak_if_needed(h.id, user.timezone)

                    completed = await reminder_dao.get_today_completed_tasks(user.id, user.timezone)
                    pending = await reminder_dao.get_today_pending_tasks(user.id, user.timezone)
                    if completed or pending:
                        text = _build_evening_text(completed, pending, user, l10n)
                        await _send_safe(
                            bot,
                            user.id,
                            text,
                            reply_markup=get_evening_wrapup_keyboard(pending, l10n) if pending else None,
                        )
                        logger.info("Evening brief sent to user %s", user.id)

                    pending_fluid = [h for h in fluid_habits if h.fluid_last_completed_date != today_str]
                    if pending_fluid:
                        await _send_safe(
                            bot,
                            user.id,
                            l10n.get("fluid_evening_check", "🌙 **Before evening wrap-up:**\nMark completed fluid habits:"),
                            reply_markup=get_fluid_completion_keyboard(pending_fluid, l10n),
                        )

                await session.commit()

    except Exception as e:
        logger.error("Error in daily briefs job: %s", e, exc_info=True)


# ---------------------------------------------------------------------------
# Scheduler registration
# ---------------------------------------------------------------------------

def setup_daily_briefs(scheduler) -> None:
    """Register the minutely cron job for daily briefs. Call once at startup."""
    logger.info("Registering minutely daily briefs cron job")
    scheduler.add_job(
        process_daily_briefs,
        "cron",
        minute="*",
        id="daily_briefs_minutely",
        replace_existing=True,
    )
