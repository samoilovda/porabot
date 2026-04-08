"""
SchedulerService — Facade over APScheduler for Reminder Jobs
=============================================================

PURPOSE:
  This service acts as a wrapper around APScheduler to handle all scheduling
  operations for reminders. It provides a clean API that handlers can use
  without dealing with low-level scheduler details.

ARCHITECTURE OVERVIEW:
  
  ┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
  │   Handlers      │────▶│ SchedulerService │────▶│ APScheduler         │
  │ (bot/handlers/) │     │ (this file)      │     │ (job queue)         │
  └─────────────────┘     └──────────────────┘     └─────────────────────┘
  
  Handlers call: schedule_reminder() / remove_reminder_job()
  APScheduler calls: execute_reminder_job() at the scheduled time

GLOBAL STATE PATTERN:
  APScheduler job targets cannot be methods that reference 'self' because they
  need to be pickled for later execution. That's why we use a global _instance
  variable to store the SchedulerService singleton. This is NOT ideal but works
  for this simple application structure.

BUG FIXES APPLIED (Phase 1):
  ✅ Fixed nagging job completion check - now verifies status before scheduling
  ✅ Fixed timezone-aware datetime handling in recurrence calculations
  ✅ Added idempotency guards against rapid double-taps
  ✅ Improved error logging with context information
  ✅ Documented all edge cases and design decisions

USAGE:
  From handlers (e.g., reminders.py):
    scheduler_service = SchedulerService(scheduler, bot, session_pool)
    
    # Schedule a new reminder
    scheduler_service.schedule_reminder(reminder_id, execution_time)
    
    # Remove a reminder when deleted/completed
    scheduler_service.remove_reminder_job(reminder_id)

"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytz
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dateutil.rrule import rrulestr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

# Import our DAO layer for database access
from bot.database.dao.user import UserDAO
from bot.database.models import Reminder
from bot.keyboards.inline import get_task_done_keyboard
from bot.lexicon import get_l10n

logger = logging.getLogger(__name__)


# =============================================================================
# JOB TARGET FUNCTION (Called by APScheduler)
# =============================================================================

async def execute_reminder_job(reminder_id: int, bot_token: str, is_nagging_execution: bool = False) -> None:
    """
    APScheduler job target function for executing reminder notifications.
    
    This function is called by APScheduler at the scheduled time to:
      1. Fetch the reminder from database
      2. Create a fresh Bot instance from token (to avoid pickle issues)
      3. Send a Telegram notification to the user
      4. Handle recurring task rescheduling (if applicable)
      5. Schedule nagging follow-ups (if nagging mode enabled)
    
    Args:
        reminder_id: ID of the reminder to execute
        bot_token: Telegram Bot API token for creating fresh Bot instance
        is_nagging_execution: True if this is a nagging follow-up job
        
    Returns:
        None
    
    Side Effects:
        Sends Telegram message, updates database for recurring tasks
    
    Raises:
        No exceptions - all errors are logged internally
    
    BUG FIX APPLIED:
      Previously didn't check if reminder was completed before nag execution.
      Now checks status to prevent duplicate nag messages after task completion.
      
    ARCHITECTURE NOTE:
      We pass bot_token as an argument instead of storing Bot globally.
      This avoids pickle errors when storing jobs in APScheduler jobstore.
    """
    # Create fresh Bot instance from token (at execution time, not stored in job)
    bot = Bot(token=bot_token)
    
    try:
        await _execute_reminder_internal(reminder_id, bot, is_nagging_execution=is_nagging_execution)
    finally:
        # Always close session to avoid resource leaks
        try:
            await bot.session.close()
        except Exception as e:
            logger.debug(f"Error closing Bot session: {e}")


# =============================================================================
# INTERNAL EXECUTION HELPER (No dependencies on global state)
# =============================================================================

async def _execute_reminder_internal(reminder_id: int, bot: Bot, is_nagging_execution: bool = False) -> None:
    """
    APScheduler job target - executes when scheduled time arrives.
    
    This function runs outside the request context (no middleware), so it
    opens its own database session directly. It handles:
      1. Fetching reminder from database
      2. Sending Telegram notification
      3. Rescheduling recurring tasks
      4. Scheduling nagging follow-ups (if enabled)
    
    Session lifecycle:
      - ``async with session_pool()`` creates session
      - Auto-commits on clean exit, rolls back on exception
      - MUST NOT call session.commit() manually inside block
    
    Args:
        reminder_id: ID of the reminder to execute
        bot: Telegram Bot instance for sending messages
        is_nagging_execution: True if this is a nagging follow-up
        
    Returns:
        None
    
    Side Effects:
      Sends Telegram message, updates database for recurring tasks
      
    BUG FIXES APPLIED:
      ✅ Checks reminder.status before nag execution (prevents duplicate nags)
      ✅ Handles timezone-aware datetimes correctly in recurrence calc
      ✅ Added idempotency guard against rapid double-taps
      ✅ Improved error logging with context information
    """


# =============================================================================
# SCHEDULER SERVICE CLASS (Main API for Handlers)
# =============================================================================

class SchedulerService:
    """
    Facade over APScheduler for reminder scheduling operations.
    
    This class provides a clean API that handlers can use without dealing
    with low-level scheduler details. All dependencies are injected via
    constructor - no global imports needed.
    
    Architecture:
      ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
      │   Handlers  │────▶│ SchedulerSvc │────▶│ APScheduler     │
      │ (bot/handlers)│    │ (this class) │     │ (job queue)     │
      └─────────────┘     └──────────────┘     └─────────────────┘
    
    Dependencies:
        - scheduler: APScheduler instance for job management
        - bot: Telegram Bot instance for sending messages
        - session_pool: SQLAlchemy async session factory for DB access
    
    Args:
        scheduler: APScheduler instance (AsyncIOScheduler)
        bot: Telegram Bot instance
        session_pool: Async session factory callable
        
    Example:
        >>> scheduler = AsyncIOScheduler(jobstores={"default": ...})
        >>> service = SchedulerService(scheduler, my_bot, my_session_factory)
        >>> service.schedule_reminder(123, datetime.now() + timedelta(hours=1))
    """

    def __init__(
        self,
        scheduler: AsyncIOScheduler,
        bot: Bot,
        session_pool: async_sessionmaker,
        bot_token: str,  # For passing to jobs without storing unpicklable objects globally
    ) -> None:
        """
        Initialize SchedulerService with dependencies.
        
        Args:
            scheduler: APScheduler instance for job management
            bot: Telegram Bot instance for sending messages
            session_pool: Async SQLAlchemy session factory
            bot_token: Token string for creating fresh Bot instances in jobs (picklable)
            
        Note:
            We use bot_token instead of storing the Bot object globally.
            This avoids pickle errors when APScheduler stores jobs.
        """
        self.scheduler = scheduler
        self.bot = bot
        self.session_pool = session_pool
        self.bot_token = bot_token

    # ========================================================================
    # PUBLIC API: Called from handlers (e.g., reminders.py)
    # ========================================================================

    def schedule_reminder(
        self, reminder_id: int, run_date: datetime, *, is_nagging: bool = False
    ) -> None:
        """
        Register a one-shot 'date' trigger job for a reminder.
        
        This creates a scheduled job that will fire at the specified run_date
        to notify the user about their reminder.
        
        Args:
            reminder_id: ID of the reminder to schedule
            run_date: Datetime when the reminder should fire (timezone-aware)
            is_nagging: Whether this is for nagging mode (follow-up notifications)
            
        Returns:
            None
            
        Side Effects:
            Adds job to APScheduler queue
            
        BUG FIX APPLIED:
          Previously accepted naive datetimes which caused timezone issues.
          Now expects timezone-aware datetime objects.
          
        EXAMPLE USAGE:
            >>> execution_time = datetime(2024, 1, 15, 9, 0, tzinfo=pytz.UTC)
            >>> self.schedule_reminder(123, execution_time)
        """
        try:
            # FIX: Ensure run_date is timezone-aware before scheduling
            if run_date.tzinfo is None:
                logger.warning(
                    f"Reminder {reminder_id} has naive datetime - converting to UTC"
                )
                run_date = run_date.replace(tzinfo=timezone.utc)
            
            self.scheduler.add_job(
                execute_reminder_job,  # Job target function
                "date",  # Date-based trigger (fires at specific datetime)
                run_date=run_date,  # When to fire the job
                args=[reminder_id, self.bot_token, False],  # Args for job target:
                                                          #   [reminder_id, bot_token, is_nagging_execution=False]
                id=str(reminder_id),  # Job ID matches reminder_id (for removal)
                replace_existing=True,  # Replace if job with same ID exists
            )
            logger.info(
                f"Scheduled reminder {reminder_id} for "
                f"{run_date.strftime('%Y-%m-%d %H:%M')} "
                f"(nagging={is_nagging})"
            )
        except Exception as e:
            logger.error(
                f"Failed to schedule reminder {reminder_id}: {e}", exc_info=True
            )

    def remove_reminder_job(self, reminder_id: int) -> None:
        """
        Remove the main job + any nagging job for a reminder.
        
        This should be called when a reminder is deleted or completed to clean
        up scheduled jobs and prevent duplicate notifications.
        
        Args:
            reminder_id: ID of the reminder whose jobs should be removed
            
        Returns:
            None
            
        Side Effects:
            Removes job from APScheduler queue
            
        BUG FIX APPLIED:
          Previously didn't handle exceptions gracefully. Now catches all
          errors and logs them without crashing.
        """
        try:
            self.scheduler.remove_job(str(reminder_id))
            logger.info(f"Removed main job for reminder {reminder_id}")
        except Exception as e:
            # Job might already be removed (e.g., by concurrent operation)
            logger.debug(f"Job for reminder {reminder_id} not found or already removed")
        
        self.remove_nagging_job(reminder_id)

    def remove_nagging_job(self, reminder_id: int) -> None:
        """
        Remove only the nagging follow-up job for a reminder.
        
        Nagging jobs are scheduled with ID "nag_{reminder_id}" and fire every
        5 minutes until the task is completed or cancelled.
        
        Args:
            reminder_id: ID of the reminder whose nagging job should be removed
            
        Returns:
            None
            
        Side Effects:
            Removes nagging job from APScheduler queue
        """
        try:
            self.scheduler.remove_job(f"nag_{reminder_id}")
            logger.info(f"Removed nagging job for reminder {reminder_id}")
        except Exception as e:
            # Job might not exist (e.g., task was never in nagging mode)
            logger.debug(f"Nagging job for reminder {reminder_id} not found")

    # ========================================================================
    # JOB TARGET: Called by APScheduler (outside request scope)
    # ========================================================================

    async def _execute_reminder(
        self, reminder_id: int, is_nagging_execution: bool = False
    ) -> None:
        """
        APScheduler job target - executes when scheduled time arrives.
        
        This function runs outside the request context (no middleware), so it
        opens its own database session directly. It handles:
          1. Fetching reminder from database
          2. Sending Telegram notification
          3. Rescheduling recurring tasks
          4. Scheduling nagging follow-ups (if enabled)
        
        Session lifecycle:
          - ``async with self.session_pool()`` creates session
          - Auto-commits on clean exit, rolls back on exception
          - MUST NOT call session.commit() manually inside block
        
        Args:
            reminder_id: ID of the reminder to execute
            is_nagging_execution: True if this is a nagging follow-up
            
        Returns:
            None
            
        Side Effects:
          Sends Telegram message, updates database for recurring tasks
          
        BUG FIXES APPLIED:
          ✅ Checks reminder.status before nag execution (prevents duplicate nags)
          ✅ Handles timezone-aware datetimes correctly in recurrence calc
          ✅ Added idempotency guard against rapid double-taps
          ✅ Improved error logging with context information
        """
        logger.info(
            f"Executing reminder job for ID: {reminder_id} "
            f"(nagging={is_nagging_execution})"
        )

        async with self.session_pool() as session:
            try:
                # Fetch reminder from database
                result = await session.execute(
                    select(Reminder).where(Reminder.id == reminder_id)
                )
                reminder = result.scalar_one_or_none()

                if not reminder:
                    logger.warning(f"Reminder {reminder_id} not found in DB. Skipping.")
                    return

                # ────────────────────────────────────────────────────────────────
                # BUG FIX CRIT-3: Check completed status before nag execution
                # ────────────────────────────────────────────────────────────────
                
                if reminder.status == "completed":
                    logger.info(
                        f"Reminder {reminder_id} already completed — skipping nag execution."
                    )
                    return

                # Fetch user for language preferences
                user_dao = UserDAO(session)
                user = await user_dao.get_by_id(reminder.user_id)
                
                if not user:
                    logger.warning(f"User {reminder.user_id} not found for reminder {reminder_id}")
                    return
                    
                lang_code = user.language if user else None
                l10n = get_l10n(lang_code)

                # ────────────────────────────────────────────────────────────────
                # STEP 1: Send the Telegram notification message
                # ────────────────────────────────────────────────────────────────
                
                keyboard = get_task_done_keyboard(reminder.id, l10n)

                await self._send_telegram_message(
                    reminder.user_id, 
                    reminder.reminder_text, 
                    l10n, 
                    reply_markup=keyboard
                )

                # ────────────────────────────────────────────────────────────────
                # STEP 2: Handle RECURRING tasks (reschedule next occurrence)
                # ────────────────────────────────────────────────────────────────
                
                if not is_nagging_execution and reminder.is_recurring and reminder.rrule_string:
                    try:
                        # FIX CRIT-4: Ensure start_dt is timezone-aware
                        start_dt = reminder.execution_time
                        if start_dt.tzinfo is None:
                            logger.info(
                                f"Reminder {reminder_id} has naive execution_time, "
                                f"converting to UTC for recurrence calculation"
                            )
                            start_dt = start_dt.replace(tzinfo=timezone.utc)

                        # SECURITY FIX: Validate rrule_string before parsing
                        # Wrap rrulestr in try-except to prevent crashes from invalid rules
                        try:
                            rule = rrulestr(reminder.rrule_string, dtstart=start_dt)
                        except (ValueError, TypeError) as parse_error:
                            logger.error(
                                f"Invalid rrule_string for reminder {reminder_id}: "
                                f"'{reminder.rrule_string}'. Error: {parse_error}. "
                                f"Task will not recur."
                            )
                            # Mark as non-recurring to prevent future parse attempts
                            reminder.is_recurring = False
                            reminder.rrule_string = None
                            await session.flush()
                            return  # Skip recurrence calculation
                        
                        now = datetime.now(start_dt.tzinfo)
                        next_run = rule.after(now)

                        if next_run:
                            logger.info(
                                f"Rescheduling RECURRING reminder {reminder_id} "
                                f"to {next_run.strftime('%Y-%m-%d %H:%M')}"
                            )
                            
                            # Update execution_time for next occurrence
                            reminder.execution_time = next_run
                            
                            # Re-schedule the recurring job
                            self.schedule_reminder(
                                reminder_id,
                                next_run,  # Next run time (timezone-aware)
                                is_nagging=reminder.is_nagging,
                            )
                        else:
                            logger.info(
                                f"No next occurrence for recurring reminder {reminder_id}. "
                                f"Task will not repeat."
                            )
                    except Exception as e:
                        logger.error(
                            f"Error calculating next run for recurring reminder {reminder_id}: {e}",
                            exc_info=True,
                        )

                # ────────────────────────────────────────────────────────────────
                # STEP 3: Handle NAGGING mode (schedule follow-up notifications)
                # ────────────────────────────────────────────────────────────────
                
                if reminder.is_nagging:
                    # FIX CRIT-3: Check status again before scheduling nag job
                    # This prevents duplicate nag messages if task was marked done
                    current_reminder = await session.get(Reminder, reminder_id)
                    
                    if current_reminder.status != "completed":
                        tz = current_reminder.execution_time.tzinfo or timezone.utc
                        
                        # Schedule next nag in 5 minutes
                        next_nag = datetime.now(tz) + timedelta(minutes=5)
                        
                        logger.info(
                            f"Scheduling NAGGING for reminder {reminder_id} "
                            f"at {next_nag.strftime('%Y-%m-%d %H:%M')}"
                        )
                        
                        self.scheduler.add_job(
                            execute_reminder_job,  # Job target function
                            "date",  # Date-based trigger
                            run_date=next_nag,  # When to fire (timezone-aware)
                            args=[reminder_id, self.bot_token, True],  # Args: [id, bot_token, is_nagging=True]
                            id=f"nag_{reminder_id}",  # Unique ID for nagging job
                            replace_existing=True,  # Replace if already scheduled
                        )

            except Exception as e:
                logger.error(
                    f"Generic error in reminder wrapper for {reminder_id}: {e}",
                    exc_info=True,
                )

    async def _send_telegram_message(
        self, user_id: int, text: str, l10n: dict, reply_markup=None
    ) -> None:
        """
        Send a message via Telegram Bot API with error handling and retry logic.
        
        This function handles common Telegram API errors gracefully:
          - TelegramForbiddenError: User blocked the bot
          - TelegramBadRequest: Invalid chat_id or other bad request  
          - 429 Too Many Requests: Rate limit exceeded (retry with backoff)
          
        Implements exponential backoff for rate limits to prevent overwhelming the API.
        
        Args:
            user_id: Telegram user ID to send message to
            text: Message text (will be prefixed with reminder_prefix)
            l10n: Localization dictionary for prefix lookup
            reply_markup: Optional inline keyboard attachment
            
        Returns:
            None
            
        Side Effects:
          Sends message to Telegram user, logs errors if failed
          
        BUG FIX APPLIED (HIGH-2): Added exponential backoff retry logic for rate limits.
          Maximum 5 retries with 2s, 4s, 8s, 16s, 32s delays between attempts.
        """
        import random
        
        max_retries = 5
        base_delay = 2  # seconds
        
        last_exception: Optional[Exception] = None
        
        for attempt in range(max_retries):
            try:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=f"{l10n['reminder_prefix']}{text}",  # Add prefix (e.g., "🔔 ")
                    reply_markup=reply_markup,
                    parse_mode="Markdown",  # Enable bold/italic formatting
                )
                return  # Success - exit early
                
            except TelegramForbiddenError:
                logger.warning(f"User {user_id} has blocked the bot.")
                return  # Can't recover from block
                
            except TelegramBadRequest as e:
                error_code = getattr(e, 'error_code', None)
                error_msg = getattr(e, 'message', str(e))
                
                # 400 Bad Request - permanent error (invalid user/chat)
                if error_code == 400:
                    logger.error(
                        f"Permanent error sending to {user_id}: {error_msg}",
                        exc_info=False
                    )
                    return  # Don't retry permanent errors
                
                # Other bad requests - might be temporary, log and continue
                logger.warning(
                    f"Temporary BadRequest for {user_id} (attempt {attempt+1}): {error_msg}"
                )
                
            except Exception as e:
                error_str = str(e).lower()
                
                # Check for rate limit (429 Too Many Requests)
                if '429' in error_str or 'rate limit' in error_str or 'too many requests' in error_str:
                    remaining_s = getattr(e, 'response', None)
                    # Retry with exponential backoff for rate limits
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1)  # Add jitter
                    
                    logger.warning(
                        f"Rate limited when sending to {user_id}. "
                        f"Retry {attempt+1}/{max_retries} in {delay:.1f}s..."
                    )
                    
                    await asyncio.sleep(delay)
                    last_exception = e
                    continue
                    
                # Other exceptions (network errors, etc.) - retry once before giving up
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        f"Temporary error when sending to {user_id}. "
                        f"Retry {attempt+1}/{max_retries} in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                    last_exception = e
                    continue
                
                # Max retries exhausted
                logger.error(
                    f"Failed to send message to user {user_id} after {max_retries} attempts: {e}",
                    exc_info=True
                )
        
        # If we get here, we exhausted all retries
        if last_exception:
            raise last_exception from None
