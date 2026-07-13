"""Missed-task recovery digests for overdue pending tasks."""

import logging
from datetime import datetime

import pytz
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select

from bot.database.dao.reminder import ReminderDAO
from bot.database.dao.user import UserDAO
from bot.database.models import User
from bot.keyboards.inline import get_missed_recovery_keyboard
from bot.lexicon import get_l10n
from bot.utils.markdown import escape_markdown
from bot.utils.time_ext import format_time, is_quiet_hours

logger = logging.getLogger(__name__)

RECOVERY_LOCAL_TIME = "10:00"


async def _send_safe(bot: Bot, user_id: int, text: str, l10n: dict) -> None:
    try:
        await bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=get_missed_recovery_keyboard(l10n),
        )
    except TelegramForbiddenError:
        logger.warning("User %s has blocked the bot — skipping missed recovery.", user_id)
    except TelegramBadRequest as e:
        logger.error("Bad request sending missed recovery to %s: %s — retrying without Markdown.", user_id, e)
        try:
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=None,
                reply_markup=get_missed_recovery_keyboard(l10n),
            )
        except Exception as retry_e:
            logger.error("Retry without Markdown also failed for %s: %s", user_id, retry_e, exc_info=True)
    except Exception as e:
        logger.error("Failed to send missed recovery to %s: %s", user_id, e, exc_info=True)


async def process_missed_task_recovery() -> None:
    """Send a daily catch-up digest for overdue pending tasks (once per local day)."""
    from bot.services.scheduler import _instance
    if not _instance:
        logger.error("Failed to process missed-task recovery: SchedulerService not initialized")
        return

    bot = _instance.bot
    session_pool_factory = _instance.session_pool

    try:
        async with session_pool_factory() as session:
            result = await session.execute(
                select(User.id).where(User.missed_recovery_enabled.is_(True))
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
                    tz = pytz.UTC
                now_local = datetime.now(tz)
                if now_local.strftime("%H:%M") < RECOVERY_LOCAL_TIME:
                    continue
                if is_quiet_hours(user, now_local):
                    continue

                today_key = now_local.strftime("%Y-%m-%d")
                if getattr(user, "last_missed_recovery_date", None) == today_key:
                    continue

                reminder_dao = ReminderDAO(session)
                overdue = await reminder_dao.get_overdue_pending_tasks(user.id, min_minutes_overdue=30, limit=5)
                if not overdue:
                    continue

                l10n = get_l10n(user.language)
                lines = [l10n.get("missed_recovery_title", "📎 Missed tasks: {count}").format(count=len(overdue))]
                for task in overdue:
                    dt_str = format_time(task.execution_time, user.timezone, user.show_utc_offset, "%d.%m %H:%M")
                    lines.append(f"▫️ `{dt_str}`: {escape_markdown(task.reminder_text)}")
                text = "\n".join(lines)

                await _send_safe(bot, user.id, text, l10n)
                user.last_missed_recovery_date = today_key
                await session.commit()

    except Exception as e:
        logger.error("Error in missed-task recovery job: %s", e, exc_info=True)


def setup_missed_task_recovery(scheduler) -> None:
    """Register minutely check for daily missed-task recovery digests."""
    logger.info("Registering missed-task recovery cron job")
    scheduler.add_job(
        process_missed_task_recovery,
        "cron",
        minute="*",
        id="missed_task_recovery",
        replace_existing=True,
    )

