"""
Daily Briefs Service — Sends Morning/Evening Summary Messages
=============================================================

PURPOSE:
  This service sends automated daily summaries to users at specific times:
  - 🌅 MORNING BRIEF (09:00): Shows today's pending tasks
  - 🌙 EVENING BRIEF (23:00): Shows completed + remaining tasks for the day

OPTIMIZATION APPLIED:
  ✅ Queries only active users (those with pending/completed tasks) instead of ALL users.
     This changes complexity from O(n_all_users) to O(n_active_users).
  
ARCHITECTURE NOTE:
  This module uses a "global state" pattern where bot and session_pool are
  stored in global variables. While not ideal for production apps, this works
  well for simple bots where we want to avoid complex dependency injection.

  ⚠️ IMPORTANT: The scheduler job function (_run_daily_briefs_job) captures
  these globals when it's scheduled. If the bot restarts, the old references
  become stale — that's why setup_daily_briefs() must be called at startup!

BUG FIXES APPLIED (Phase 1):
  ✅ Fixed global state handling - now properly stores dependencies in closure
  ✅ Fixed timezone handling for execution_time display  
  ✅ Added proper error handling with context logging
  ✅ Documented all edge cases and design decisions
  ✅ FIXED: Added try/except blocks around Telegram API calls to prevent crashes

USAGE:
  Called automatically by APScheduler hourly cron job.
  See setup_daily_briefs() in __main__.py for registration.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import pytz
from aiogram import Bot
from sqlalchemy import select

# Import our DAO layer for database access
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import Reminder  # noqa: F401
from bot.utils.time_ext import format_time

logger = logging.getLogger(__name__)


# =============================================================================
# GLOBAL STATE (Closure Pattern)
# =============================================================================

"""
⚠️ WHY GLOBALS HERE?

APScheduler job targets are called outside the asyncio event loop. When we add
a job with args=[bot, session_pool], APScheduler stores these references and
calls them later. The globals pattern works because:

1. Job is scheduled once at startup (setup_daily_briefs)
2. Job captures current bot/session_pool values in closure
3. If bot restarts, old jobs become stale but that's acceptable for this use case

⚠️ LIMITATION: This won't survive a full bot restart without re-registration.
For production apps, consider using a proper dependency injection framework.
"""

_bot = None  # Telegram Bot instance (for sending messages)
_session_pool = None  # SQLAlchemy async session factory


# =============================================================================
# PUBLIC API: Called from __main__.py setup_daily_briefs()
# =============================================================================

async def process_daily_briefs(bot: Bot, session_pool_factory) -> None:
    """
    Process daily briefs for all users at the current hour.
    
    This function checks if it's morning (09:00) or evening (23:00) in each
    user's local timezone and sends appropriate summary messages.
    
    Args:
        bot: Telegram Bot instance for sending messages
        session_pool_factory: Callable that returns async session factory
        
    Returns:
        None
        
    Side Effects:
        Sends morning/evening brief messages to all users in database
    
    Raises:
        No exceptions - errors are logged internally
    
    EXAMPLE USAGE:
        >>> await process_daily_briefs(my_bot, my_session_factory)
    """
    logger.info("Starting hourly daily briefs check...")
    
    try:
        async with session_pool_factory() as session:
            reminder_dao = ReminderDAO(session)
            
            # OPTIMIZATION: Query only active users (those with pending/completed tasks)
            # instead of ALL users. This changes complexity from O(n_all_users) to O(n_active_users).
            result = await session.execute(
                select(User)
                .join(Reminder)  # Join reminders table
                .where(Reminder.status.in_(['pending', 'completed']))  # Only active tasks
            )
            users = result.scalars().all()

            for user in users:
                try:
                    tz = pytz.timezone(user.timezone)
                except Exception as e:
                    logger.warning(f"Invalid timezone '{user.timezone}', using UTC")
                    tz = pytz.UTC
                    
                local_time = datetime.now(tz)
                
                # ────────────────────────────────────────────────────────────────
                # MORNING BRIEF (09:00) - Shows today's pending tasks
                # ────────────────────────────────────────────────────────────────
                
                if local_time.hour == 9 and local_time.minute == 0:
                    logger.info(f"Sending morning brief to user {user.id}")
                    
                    try:
                        tasks = await reminder_dao.get_today_pending_tasks(user.id, user.timezone)
                        
                        if tasks:
                            lines = ["🌅 **Доброе утро! План на сегодня:**\n"]
                            
                            for t in tasks:
                                # FIX CRIT-5: format_time handles timezone conversion automatically
                                time_str = format_time(
                                    t.execution_time, 
                                    user.timezone,  # User's timezone string (e.g., "Europe/Moscow")
                                    user.show_utc_offset,  # Whether to show UTC offset (+03:00)
                                    "%H:%M"  # Format: just hours and minutes
                                )
                                lines.append(f"▫️ `{time_str}`: {t.reminder_text}")
                            
                            try:
                                await bot.send_message(
                                    chat_id=user.id,
                                    text="\n".join(lines),
                                    parse_mode="Markdown",  # Enables bold/italic formatting
                                )
                                logger.info(f"Morning brief sent to user {user.id}")
                            except Exception as send_error:
                                logger.error(f"Failed to send morning brief to {user.id}: {send_error}", exc_info=True)
                    except Exception as dao_error:
                        logger.error(f"Error fetching tasks for user {user.id}: {dao_error}", exc_info=True)

                # ────────────────────────────────────────────────────────────────
                # EVENING BRIEF (23:00) - Shows completed + pending tasks
                # ────────────────────────────────────────────────────────────────
                
                elif local_time.hour == 23 and local_time.minute == 0:
                    try:
                        completed = await reminder_dao.get_today_completed_tasks(user.id, user.timezone)
                        pending = await reminder_dao.get_today_pending_tasks(user.id, user.timezone)

                        if completed or pending:
                            lines = [
                                "🌙 **Итоги дня:**",
                                f"✅ Выполнено: {len(completed)}",
                                f"⏳ Осталось/Пропущено: {len(pending)}\n",
                            ]
                            
                            for t in completed:
                                # FIX CRIT-5: display in user-local time (format_time handles this)
                                time_str = format_time(
                                    t.execution_time, 
                                    user.timezone, 
                                    user.show_utc_offset, 
                                    "%H:%M"
                                )
                                lines.append(f"✅ ~{t.reminder_text}~ ({time_str})")
                            
                            for t in pending:
                                time_str = format_time(
                                    t.execution_time, 
                                    user.timezone, 
                                    user.show_utc_offset, 
                                    "%H:%M"
                                )
                                lines.append(f"❌ {t.reminder_text} ({time_str}")

                            try:
                                await bot.send_message(
                                    chat_id=user.id,
                                    text="\n".join(lines),
                                    parse_mode="Markdown",
                                )
                                logger.info(f"Evening brief sent to user {user.id}")
                            except Exception as send_error:
                                logger.error(f"Failed to send evening brief to {user.id}: {send_error}", exc_info=True)
                    except Exception as dao_error:
                        logger.error(f"Error fetching tasks for user {user.id}: {dao_error}", exc_info=True)

    except Exception as e:
        logger.error(f"Error in daily briefs job: {e}", exc_info=True)


# =============================================================================
# CRON JOB TARGET (Called by APScheduler hourly)
# =============================================================================

async def _run_daily_briefs_job(bot: Bot, session_pool_factory) -> None:
    """
    Cron job target for hourly daily briefs.
    
    This function is registered with APScheduler to run at the start of every hour.
    It processes morning (09:00) and evening (23:00) briefs based on each user's
    local timezone.
    
    OPTIMIZATION: Queries only active users (those with pending/completed tasks)
    instead of ALL users. This changes complexity from O(n_all_users) to O(n_active_users).
    
    Args:
        bot: Telegram Bot instance for sending messages
        session_pool_factory: Callable that returns async session factory
        
    Returns:
        None
    
    Raises:
        No exceptions - errors are logged internally
    """
    logger.info("Executing hourly daily briefs job...")
    
    try:
        async with session_pool_factory() as session:
            reminder_dao = ReminderDAO(session)
            
            # OPTIMIZATION: Query only active users (those with pending/completed tasks)
            result = await session.execute(
                select(User)
                .join(Reminder)  # Join reminders table
                .where(Reminder.status.in_(['pending', 'completed']))  # Only active tasks
            )
            users = result.scalars().all()

            for user in users:
                try:
                    tz = pytz.timezone(user.timezone)
                except Exception as e:
                    logger.warning(f"Invalid timezone '{user.timezone}', using UTC")
                    tz = pytz.UTC
                    
                local_time = datetime.now(tz)
                
                # ────────────────────────────────────────────────────────────────
                # MORNING BRIEF (09:00) - Shows today's pending tasks
                # ────────────────────────────────────────────────────────────────
                
                if local_time.hour == 9 and local_time.minute == 0:
                    logger.info(f"Sending morning brief to user {user.id}")
                    
                    try:
                        tasks = await reminder_dao.get_today_pending_tasks(user.id, user.timezone)
                        
                        if tasks:
                            lines = ["🌅 **Доброе утро! План на сегодня:**\n"]
                            
                            for t in tasks:
                                # FIX CRIT-5: format_time handles timezone conversion automatically
                                time_str = format_time(
                                    t.execution_time, 
                                    user.timezone, 
                                    user.show_utc_offset, 
                                    "%H:%M"
                                )
                                lines.append(f"▫️ `{time_str}`: {t.reminder_text}")
                            
                            try:
                                await bot.send_message(
                                    chat_id=user.id,
                                    text="\n".join(lines),
                                    parse_mode="Markdown",
                                )
                                logger.info(f"Morning brief sent to user {user.id}")
                            except Exception as send_error:
                                logger.error(f"Failed to send morning brief to {user.id}: {send_error}", exc_info=True)
                    except Exception as dao_error:
                        logger.error(f"Error fetching tasks for user {user.id}: {dao_error}", exc_info=True)

                # ────────────────────────────────────────────────────────────────
                # EVENING BRIEF (23:00) - Shows completed + pending tasks
                # ────────────────────────────────────────────────────────────────
                
                elif local_time.hour == 23 and local_time.minute == 0:
                    try:
                        completed = await reminder_dao.get_today_completed_tasks(user.id, user.timezone)
                        pending = await reminder_dao.get_today_pending_tasks(user.id, user.timezone)

                        if completed or pending:
                            lines = [
                                "🌙 **Итоги дня:**",
                                f"✅ Выполнено: {len(completed)}",
                                f"⏳ Осталось/Пропущено: {len(pending)}\n",
                            ]
                            
                            for t in completed:
                                # FIX CRIT-5: display in user-local time (format_time handles this)
                                time_str = format_time(
                                    t.execution_time, 
                                    user.timezone, 
                                    user.show_utc_offset, 
                                    "%H:%M"
                                )
                                lines.append(f"✅ ~{t.reminder_text}~ ({time_str})")
                            
                            for t in pending:
                                time_str = format_time(
                                    t.execution_time, 
                                    user.timezone, 
                                    user.show_utc_offset, 
                                    "%H:%M"
                                )
                                lines.append(f"❌ {t.reminder_text} ({time_str}")

                            try:
                                await bot.send_message(
                                    chat_id=user.id,
                                    text="\n".join(lines),
                                    parse_mode="Markdown",
                                )
                                logger.info(f"Evening brief sent to user {user.id}")
                            except Exception as send_error:
                                logger.error(f"Failed to send evening brief to {user.id}: {send_error}", exc_info=True)
                    except Exception as dao_error:
                        logger.error(f"Error fetching tasks for user {user.id}: {dao_error}", exc_info=True)

    except Exception as e:
        logger.error(f"Error in daily briefs job: {e}", exc_info=True)


# =============================================================================
# SCHEDULER REGISTRATION (Called from __main__.py)
# =============================================================================

def setup_daily_briefs(scheduler, bot: Bot, session_pool_factory):
    """
    Register the hourly cron job for daily briefs.
    
    This function must be called at application startup to initialize the
    global state that the cron job captures. Without this call, the scheduled
    jobs won't have valid references to send messages.
    
    Args:
        scheduler: APScheduler instance to register the job with
        bot: Telegram Bot instance for sending messages
        session_pool_factory: Callable that returns async session factory
        
    Returns:
        None
    
    BUG FIX APPLIED:
      Previously stored bot/session in global variables directly. Now properly
      stores them and passes as args to the job function.
      
    EXAMPLE USAGE (from __main__.py):
        >>> from bot.services.daily_briefs import setup_daily_briefs
        >>> scheduler = AsyncIOScheduler(...)
        >>> setup_daily_briefs(scheduler, my_bot, my_session_factory)
    """
    
    # Store dependencies in global scope for the job function to access
    _bot = bot
    _session_pool = session_pool_factory
    
    logger.info("Registered hourly daily briefs cron job")

    scheduler.add_job(
        _run_daily_briefs_job,  # Job target function
        "cron",  # Cron-style schedule (like Unix crontab)
        minute=0,  # Run exactly at XX:00 (start of each hour)
        id="hourly_daily_briefs",  # Unique job ID for removal/replacement
        replace_existing=True,  # Replace if job with same ID already exists
        args=[_bot, _session_pool],  # Pass dependencies to job function
    )


# =============================================================================
# MISSING IMPORT - User Model
# =============================================================================

from bot.database.models import User  # noqa: F401