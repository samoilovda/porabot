import logging
from datetime import datetime
import pytz
from aiogram import Bot
from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database.models import User
from bot.database.dao.reminder import ReminderDAO

logger = logging.getLogger(__name__)

async def process_daily_briefs(bot: Bot, session_pool) -> None:
    """Hourly check to send morning/evening briefs based on user local time."""
    logger.info("Starting hourly daily briefs check...")
    
    async with session_pool() as session:
        reminder_dao = ReminderDAO(session)
        
        result = await session.execute(select(User))
        users = result.scalars().all()

        for user in users:
            try:
                tz = pytz.timezone(user.timezone)
            except Exception:
                tz = pytz.UTC
                
            local_time = datetime.now(tz)
            
            # 1. Morning Brief (09:00)
            if local_time.hour == 9:
                tasks = await reminder_dao.get_today_pending_tasks(user.id, user.timezone)
                if tasks:
                    lines = ["🌅 **Доброе утро! План на сегодня:**\n"]
                    for t in tasks:
                        time_str = t.execution_time.strftime('%H:%M')
                        lines.append(f"▫️ `{time_str}`: {t.reminder_text}")
                    try:
                        await bot.send_message(user.id, "\n".join(lines), parse_mode="Markdown")
                    except Exception as e:
                        logger.error(f"Failed to send morning brief to {user.id}: {e}")

            # 2. Evening Brief (23:00)
            elif local_time.hour == 23:
                completed = await reminder_dao.get_today_completed_tasks(user.id, user.timezone)
                pending = await reminder_dao.get_today_pending_tasks(user.id, user.timezone)
                
                if completed or pending:
                    lines = [
                        "🌙 **Итоги дня:**",
                        f"✅ Выполнено: {len(completed)}",
                        f"⏳ Осталось/Пропущено: {len(pending)}\n"
                    ]
                    for t in completed:
                        lines.append(f"✅ ~{t.reminder_text}~")
                    for t in pending:
                        lines.append(f"❌ {t.reminder_text}")
                        
                    try:
                        await bot.send_message(user.id, "\n".join(lines), parse_mode="Markdown")
                    except Exception as e:
                        logger.error(f"Failed to send evening brief to {user.id}: {e}")

_bot = None
_session_pool = None

async def _run_daily_briefs_job() -> None:
    global _bot, _session_pool
    if _bot and _session_pool:
        await process_daily_briefs(_bot, _session_pool)
    else:
        logger.error("Cannot run daily briefs: globals not initialized.")

def setup_daily_briefs(scheduler: AsyncIOScheduler, bot: Bot, session_pool) -> None:
    """Register the cron job to run at the start of every hour."""
    global _bot, _session_pool
    _bot = bot
    _session_pool = session_pool
    
    scheduler.add_job(
        _run_daily_briefs_job,
        "cron",
        minute=0,  # Run exactly at XX:00
        id="hourly_daily_briefs",
        replace_existing=True
    )
