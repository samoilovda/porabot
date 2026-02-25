"""Admin handlers — monitoring and system management."""

import logging
from datetime import datetime

from aiogram import Router, filters
from aiogram.types import Message
from sqlalchemy import text

from bot.config import config
from bot.database.dao.user import UserDAO
from bot.services.scheduler import SchedulerService

router = Router(name="admin")
logger = logging.getLogger(__name__)


@router.message(filters.Command("debug"))
async def cmd_debug(
    message: Message, 
    user_dao: UserDAO, 
    scheduler_service: SchedulerService
) -> None:
    """Admin-only command to show system status."""
    if message.from_user.id != config.ADMIN_ID:
        return  # Silently ignore or send permission error

    logger.info(f"Admin {message.from_user.id} requested debug summary")

    # 1. DB Status (Select 1)
    db_status = "✅ OK"
    try:
        await user_dao.session.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"❌ Error: {e}"

    # 2. User count
    user_count = await user_dao.count()

    # 3. Scheduler status
    job_count = len(scheduler_service.scheduler.get_jobs())

    # 4. Server time
    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    text_msg = (
        "🔍 **System Debug Summary**\n\n"
        f"🖥 **Server Time:** `{now_utc}`\n"
        f"🗄 **DB Status:** {db_status}\n"
        f"👤 **Total Users:** `{user_count}`\n"
        f"⏰ **Active Jobs:** `{job_count}`"
    )

    await message.answer(text_msg, parse_mode="Markdown")
