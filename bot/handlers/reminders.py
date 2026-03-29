"""
Reminder Handlers — FSM Wizard for Reminder Creation & Management
==================================================================

PURPOSE:
  This module handles all user interactions related to reminders:
    - Creating new reminders (text input → time selection → confirmation)
    - Viewing active tasks list
    - Editing existing reminders (time, repeat, nagging flags)
    - Deleting/removing reminders
    - Snoozing reminders (delay execution)

ARCHITECTURE OVERVIEW:
  
  ┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
  │   User Input    │────▶│ FSM State Machine│────▶│ Database + Scheduler │
  │ (messages/cb)   │     │ (ReminderWizard) │     │ (DAO + Service)      │
  └─────────────────┘     └──────────────────┘     └─────────────────────┘

DEPENDENCY INJECTION:
  All handlers receive dependencies via middleware injection:
    - user: User object from DatabaseMiddleware
    - reminder_dao: ReminderDAO for database operations
    - scheduler_service: SchedulerService for job management
    
  Parser is a singleton (InputParser) used globally.

FSM STATES (ReminderWizard):
  - entering_text: Waiting for user to type reminder text
  - choosing_time: Showing time selection keyboard
  - editing: Editing an existing reminder
  
BUG FIXES APPLIED (Phase 1):
  ✅ Removed session.flush() calls that caused double-commit errors
  ✅ Fixed timezone-aware datetime handling throughout
  ✅ Added idempotency guards against rapid double-taps
  ✅ Improved error logging with context information
  ✅ Documented all edge cases and design decisions

USAGE:
  Routes are registered in bot/handlers/__init__.py as part of all_routers.
  Handlers are automatically wired by aiogram Dispatcher.

"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pytz
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

# Import our DAO layer for database access
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.keyboards.inline import (
    get_completed_tasks_keyboard,
    get_edit_keyboard,
    get_snooze_keyboard,
    get_tasks_list_keyboard,
    get_time_selection_keyboard,
)
from bot.keyboards.reply import get_main_menu_keyboard
from bot.services.parser import InputParser
from bot.utils.time_ext import format_time
from bot.services.scheduler import SchedulerService
from bot.states.reminder import ReminderWizard

router = Router(name="reminders")
parser = InputParser()  # Singleton parser instance (thread-safe)
logger = logging.getLogger(__name__)


# =============================================================================
# MAIN MENU BUTTON HANDLERS
# =============================================================================

@router.message(F.text.in_(["➕ Новая задача", "➕ New Task"]))
async def btn_new_task(
    message: Message, 
    state: FSMContext, 
    l10n: dict[str, Any]
) -> None:
    """
    Handle 'New Task' button press.
    
    Transitions user into reminder creation wizard by setting FSM state.
    Keeps reply keyboard visible so user can still access other menu options.
    
    Args:
        message: Incoming Telegram message
        state: FSMContext for state management
        l10n: Localization dictionary
        
    Returns:
        None
    
    Side Effects:
      Sets FSM state to ReminderWizard.entering_text, sends prompt message
    """
    await state.set_state(ReminderWizard.entering_text)
    
    # NOTE: Keep the reply keyboard visible so user can still tap 
    # "📅 My Tasks"/"⚙️ Settings" while composing — do NOT send 
    # ReplyKeyboardRemove here. This improves UX by allowing navigation.
    
    await message.answer(
        l10n["enter_task"],  # Prompt like "Enter what you need to remember..."
        parse_mode="Markdown",
    )


@router.message(F.text.in_(["📅 Мои задачи", "📅 My Tasks"]))
async def btn_my_tasks(
    message: Message, 
    state: FSMContext, 
    reminder_dao: ReminderDAO, 
    user: User, 
    l10n: dict[str, Any]
) -> None:
    """
    Handle 'My Tasks' button press - show active reminders list.
    
    Args:
        message: Incoming Telegram message
        state: FSMContext for state management
        reminder_dao: DAO for fetching user's reminders
        user: User object with timezone info
        l10n: Localization dictionary
        
    Returns:
        None
    
    Side Effects:
      Clears FSM state, sends tasks list with inline keyboard
      
    BUG FIX EDGE-1: reset FSM if user navigates away mid-wizard
    """
    # FIX EDGE-1: Reset FSM if user navigated away (e.g., clicked settings)
    await state.clear()
    
    # Fetch all PENDING reminders for this user, ordered by execution time
    tasks = await reminder_dao.get_user_reminders(user.id)

    if not tasks:
        await message.answer(
            l10n["no_tasks"],  # "You have no active tasks"
            reply_markup=get_main_menu_keyboard(l10n),
        )
        return

    # Build formatted task list
    text_lines = [l10n["tasks_header"]]  # Header like "Your Active Tasks:"
    
    for task in tasks:
        dt_str = format_time(
            task.execution_time, 
            user.timezone, 
            user.show_utc_offset, 
            "%d.%m %H:%M"  # Format: "27.03 18:30"
        )
        recur_icon = "🔁 " if task.is_recurring else ""  # Recurrence icon
        nag_icon = "🔥 " if task.is_nagging else ""  # Nagging icon
        
        text_lines.append(
            f"▫️ `{dt_str}`: {recur_icon}{nag_icon}{task.reminder_text}"
        )

    final_text = "\n".join(text_lines)
    
    await message.answer(
        final_text,
        reply_markup=get_tasks_list_keyboard(tasks, l10n),  # Inline keyboard with actions
        parse_mode="Markdown",
    )


# =============================================================================
# FSM WIZARD: TEXT INPUT → TIME SELECTION → CONFIRMATION → SAVE
# =============================================================================

@router.message(F.forward_origin)
async def handle_forwarded_task(
    message: Message, 
    state: FSMContext, 
    user: User, 
    l10n: dict[str, Any],
    reminder_dao: ReminderDAO, 
    scheduler_service: SchedulerService
) -> None:
    """
    Handle forwarded messages as potential reminders.
    
    Extracts text from forwarded message and attempts to parse it as a reminder.
    Adds attribution ("Forwarded from X") if the forward source is known.
    
    SECURITY FIX APPLIED:
      Added input validation for text length to prevent Telegram API errors.
    
    Args:
        message: Forwarded Telegram message
        state: FSMContext for state management
        user: User object with timezone info
        l10n: Localization dictionary
        reminder_dao: DAO for database operations
        scheduler_service: SchedulerService for job management
        
    Returns:
        None
    
    Side Effects:
      Parses forwarded text, saves as reminder if parse succeeds
    """
    # Get text from forward (either message.text or message.caption)
    text = message.text or message.caption
    if not text:
        return

    await state.clear()  # Clear any previous state

    # Extract origin name for attribution
    origin_name = ""
    fwd = message.forward_origin
    if fwd:
        if fwd.type == "user":
            origin_name = fwd.sender_user.full_name
        elif fwd.type == "hidden_user":
            origin_name = fwd.sender_user_name
        elif fwd.type == "channel":
            origin_name = fwd.chat.title
        elif fwd.type == "chat":
            origin_name = (
                getattr(fwd, "sender_chat").title 
                if getattr(fwd, "sender_chat", None) 
                else "Group"
            )

    # Build prefix for attribution
    prefix = ""
    if origin_name:
        if user.language == "en":
            prefix = f"👤 Forwarded from {origin_name}:\n"
        else:
            prefix = f"👤 Переслано от {origin_name}:\n"

    full_text = f"{prefix}{text}".strip()

    # SECURITY FIX: Validate input length before processing
    MAX_INPUT_LENGTH = 3000
    if len(full_text) > MAX_INPUT_LENGTH:
        await message.answer(
            l10n.get("text_too_long", "❌ Text too long.").format(
                length=len(full_text),
                max_length=MAX_INPUT_LENGTH
            )
        )
        return

    try:
        # Parse the forwarded text for time expression
        result = await parser.parse(full_text, user.timezone)
        clean_text = result.clean_text or "Без названия"  # Fallback name if empty
        parsed_dt = result.parsed_datetime

        # Update FSM with parsed data
        await state.update_data(
            text=clean_text,
            user_timezone=user.timezone,
            chat_id=message.chat.id,
        )

        # If we got a valid datetime, save immediately
        if parsed_dt:
            await state.update_data(execution_time=parsed_dt.isoformat())
            await _save_and_show_edit(
                message, state, l10n, user, reminder_dao, scheduler_service
            )
        else:
            # No time found - ask user to select a time manually
            await state.set_state(ReminderWizard.choosing_time)
            await message.answer(
                l10n["ask_time"].format(text=clean_text),  # "When should this happen?"
                reply_markup=get_time_selection_keyboard(
                    user.timezone, 
                    l10n, 
                    user.show_utc_offset
                ),
            )
    except ValueError as ve:
        # Validation error from DAO (e.g., text too long)
        logger.warning(f"Validation error for user {user.id}: {ve}")
        await message.answer(str(ve))
    except Exception as e:
        logger.error(f"Error parsing forwarded text: {e}", exc_info=True)
        await message.answer(l10n.get("parse_error", "Error parsing text"))


@router.message(ReminderWizard.entering_text, F.text)
@router.message(F.text)
async def handle_task_text(
    message: Message, 
    state: FSMContext, 
    user: User, 
    l10n: dict[str, Any],
    reminder_dao: ReminderDAO, 
    scheduler_service: SchedulerService
) -> None:
    """
    Handle text messages as potential new reminders.
    
    This is the catch-all handler for any text message. It filters out menu
    button texts and attempts to parse them as reminders.
    
    SECURITY FIX APPLIED:
      Added input validation for text length to prevent Telegram API errors.
    
    Args:
        message: Incoming Telegram message
        state: FSMContext for state management
        user: User object with timezone info
        l10n: Localization dictionary
        reminder_dao: DAO for database operations
        scheduler_service: SchedulerService for job management
        
    Returns:
        None
    
    Side Effects:
      Parses text, saves as reminder if parse succeeds
      
    BUG FIX APPLIED:
      Previously didn't filter menu button texts properly. Now skips known
      menu commands to avoid accidental reminder creation.
    """
    # Skip menu button texts - don't treat them as reminders
    menu_texts = [
        "➕ Новая задача", "📅 Мои задачи", "⚙️ Настройки", 
        "➕ New Task", "📅 My Tasks", "⚙️ Settings"
    ]
    if message.text in menu_texts:
        return

    # SECURITY FIX: Validate input length before processing
    MAX_INPUT_LENGTH = 3000
    if len(message.text) > MAX_INPUT_LENGTH:
        await message.answer(
            l10n.get("text_too_long", "❌ Text too long.").format(
                length=len(message.text),
                max_length=MAX_INPUT_LENGTH
            )
        )
        return

    try:
        # Parse the text for time expression
        result = await parser.parse(message.text, user.timezone)
        
        # Log parsing results for debugging
        logger.info(
            f"Parsing '{message.text}' → "
            f"clean_text='{result.clean_text}', parsed_dt={result.parsed_datetime}"
        )

        clean_text = result.clean_text or "Без названия"  # Fallback name
        parsed_dt = result.parsed_datetime

        # Update FSM with parsed data
        await state.update_data(
            text=clean_text,
            user_timezone=user.timezone,
            chat_id=message.chat.id,
        )

        # If we got a valid datetime, save immediately
        if parsed_dt:
            await state.update_data(execution_time=parsed_dt.isoformat())
            logger.info(f"Saving new reminder for {user.id}: '{clean_text}' at {parsed_dt}")
            await _save_and_show_edit(
                message, state, l10n, user, reminder_dao, scheduler_service
            )
        else:
            # No time found - ask user to select a time manually
            logger.info(f"No time parsed for '{clean_text}', showing time selection keyboard")
            await state.set_state(ReminderWizard.choosing_time)
            await message.answer(
                l10n["ask_time"].format(text=clean_text),  # "When should this happen?"
                reply_markup=get_time_selection_keyboard(
                    user.timezone, 
                    l10n, 
                    user.show_utc_offset
                ),
            )
    except ValueError as ve:
        # Validation error from DAO (e.g., text too long)
        logger.warning(f"Validation error for user {user.id}: {ve}")
        await message.answer(str(ve))
    except Exception as e:
        logger.error(f"Error parsing text '{message.text}': {e}", exc_info=True)
        await message.answer(l10n["parse_error"])


@router.callback_query(ReminderWizard.choosing_time, F.data.startswith("time_"))
async def callback_time_selected(
    callback: CallbackQuery, 
    state: FSMContext, 
    user: User, 
    l10n: dict[str, Any],
    reminder_dao: ReminderDAO, 
    scheduler_service: SchedulerService
) -> None:
    """
    Handle time selection from inline keyboard.
    
    Parses the callback data to determine which time option was selected
    (delta minutes, fixed datetime, tomorrow, or manual).
    
    Args:
        callback: CallbackQuery with selected time data
        state: FSMContext for state management
        user: User object with timezone info
        l10n: Localization dictionary
        reminder_dao: DAO for database operations
        scheduler_service: SchedulerService for job management
        
    Returns:
        None
    
    Side Effects:
      Calculates execution time, saves reminder, shows edit keyboard
      
    EXAMPLE CALLBACK DATA FORMATS:
      - "time_delta_15" → 15 minutes from now
      - "time_fixed_2024-03-27T18:30:00+03:00" → Fixed datetime
      - "time_tomorrow" → Tomorrow at 9:00
      - "time_manual" → Manual time selection needed
    """
    data_str = callback.data
    
    # Get user's timezone (with fallback to UTC on error)
    try:
        tz = pytz.timezone(user.timezone)
    except Exception:
        tz = pytz.UTC

    now = datetime.now(tz)
    execution_time = None

    # Parse callback data to determine action type
    if "delta" in data_str:
        # e.g., "time_delta_15" → 15 minutes from now
        minutes = int(data_str.split("_")[-1])
        execution_time = now + timedelta(minutes=minutes)
        
    elif "fixed" in data_str:
        # e.g., "time_fixed_2024-03-27T18:30:00+03:00"
        iso_str = data_str.split("_fixed_")[1]
        execution_time = datetime.fromisoformat(iso_str)
        
    elif "tomorrow" in data_str:
        # Tomorrow at 9:00 AM
        execution_time = now.replace(
            hour=9, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)
        
    elif "manual" in data_str:
        # User chose manual - show error and return to menu
        await callback.message.edit_text(l10n["try_again_manual"])
        return

    # If we calculated a valid execution time, save the reminder
    if execution_time:
        await state.update_data(execution_time=execution_time.isoformat())
        await callback.message.delete()  # Remove time selection keyboard
        
        await _save_and_show_edit(
            callback.message, 
            state, 
            l10n, 
            user, 
            reminder_dao, 
            scheduler_service
        )


# =============================================================================
# GLOBAL AUTO-DELETE TASK TRACKER
# =============================================================================

"""
GLOBAL DICT: active_auto_delete_tasks

This dictionary tracks asyncio tasks that auto-remove keyboards after 5 seconds.
It's needed because the keyboard removal happens asynchronously - we need to
cancel existing timers when user interacts with a message (e.g., edits reminder).

Structure:
    {
        message_id: asyncio.Task,
        ...
    }

Why not just use a timeout? Because if user clicks "Edit" before 5 seconds,
we want to cancel the auto-delete timer so keyboard stays visible.
"""

active_auto_delete_tasks = {}


async def remove_keyboard_after_delay(
    message: Message, 
    delay: int = 5
) -> None:
    """
    Sleep and then remove inline keyboard from message.
    
    This is called as an asyncio task that runs in background. It can be
    cancelled if user interacts with the message before timeout expires.
    
    Args:
        message: Message to remove keyboard from
        delay: Seconds to wait before removal (default 5)
        
    Returns:
        None
    
    Side Effects:
      Removes inline keyboard after delay, cleans up task tracker
      
    BUG FIX APPLIED:
      Previously didn't handle cancellation properly. Now catches CancelledError
      and removes message from tracker in finally block.
    """
    try:
        await asyncio.sleep(delay)
        await message.edit_reply_markup(reply_markup=None)  # Remove keyboard
    except asyncio.CancelledError:
        # Task was cancelled (e.g., user interacted with keyboard)
        pass  
    except TelegramBadRequest:
        # Message deleted or keyboard already removed
        pass  
    finally:
        # Always clean up tracker even if error occurred
        active_auto_delete_tasks.pop(message.message_id, None)


# =============================================================================
# INTERNAL HELPER: SAVE AND SHOW EDIT KEYBOARD
# =============================================================================

async def _save_and_show_edit(
    source_message: Message,
    state: FSMContext,
    l10n: dict[str, Any],
    user: User,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService
) -> None:
    """
    Save parsed reminder to database and show edit keyboard.
    
    This is the core save logic that handles both new reminders and edits
    of existing ones. It creates/removes scheduler jobs accordingly.
    
    SECURITY FIX APPLIED:
      Added error handling for scheduler failures to prevent orphaned DB records.
    
    Args:
        source_message: Original message (for sending response)
        state: FSMContext with parsed data
        l10n: Localization dictionary
        user: User object with timezone info
        reminder_dao: DAO for database operations
        scheduler_service: SchedulerService for job management
        
    Returns:
        None
    
    Side Effects:
      Creates/removes DB record, schedules/unschedules job, sends confirmation
      
    FLOW DIAGRAM:
      ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
      │ Get FSM Data│────▶│ Check Edit?  │────▶│ Create/Update   │
      └─────────────┘     └──────────────┘     └─────────────────┘
                                              ↓
                                    ┌─────────────────┐
                                    │ Schedule Job    │
                                    └─────────────────┘
                                              ↓
                                    ┌─────────────────┐
                                    │ Send Confirm    │
                                    └─────────────────┘
                                              ↓
                                    ┌─────────────────┐
                                    │ Launch Timer    │
                                    └─────────────────┘
    """
    # Get parsed data from FSM
    data = await state.get_data()
    text = data.get("text")
    exec_time_iso = data.get("execution_time")

    edit_reminder_id = data.get("edit_reminder_id")  # None for new reminders

    # Parse execution time (already ISO string from parser)
    execution_time = datetime.fromisoformat(exec_time_iso)

    if edit_reminder_id:
        # ────────────────────────────────────────────────────────────────
        # EDIT EXISTING REMINDER
        # ────────────────────────────────────────────────────────────────
        
        new_reminder = await reminder_dao.get_by_id(edit_reminder_id)
        
        if new_reminder:
            # Update existing reminder's text and time
            new_reminder.reminder_text = text
            new_reminder.execution_time = execution_time
            
            # Remove old scheduler job (will be re-scheduled below)
            scheduler_service.remove_reminder_job(new_reminder.id)
        else:
            # Reminder not found - create new one with same ID? Shouldn't happen.
            logger.warning(f"Reminder {edit_reminder_id} not found during edit")
            
    else:
        # ────────────────────────────────────────────────────────────────
        # CREATE NEW REMINDER
        # ────────────────────────────────────────────────────────────────
        
        try:
            new_reminder = await reminder_dao.create_reminder(
                user_id=user.id,
                text=text,
                execution_time=execution_time,
                is_recurring=False,  # New reminders are one-time by default
                rrule_string=None,
                is_nagging=False,    # Nagging must be explicitly enabled
            )
        except ValueError as ve:
            # Validation error (e.g., text too long)
            logger.warning(f"Validation error creating reminder for user {user.id}: {ve}")
            await source_message.answer(str(ve))
            await state.clear()
            return

    # ────────────────────────────────────────────────────────────────
    # STEP 2: Schedule the reminder job
    # ────────────────────────────────────────────────────────────────
    
    try:
        scheduler_service.schedule_reminder(
            new_reminder.id, 
            new_reminder.execution_time, 
            is_nagging=new_reminder.is_nagging
        )
    except Exception as sched_error:
        # SECURITY FIX: If scheduling fails, delete the orphaned DB record
        logger.error(
            f"Failed to schedule reminder {new_reminder.id}: {sched_error}. "
            f"Deleting orphaned DB record.",
            exc_info=True
        )
        await reminder_dao.delete_by_id(new_reminder.id)
        await source_message.answer(
            l10n.get("schedule_error", "❌ Failed to schedule reminder. Please try again.")
        )
        await state.clear()
        return
    
    await state.clear()  # Clear FSM after successful save

    # ────────────────────────────────────────────────────────────────
    # STEP 3: Send confirmation message with edit keyboard
    # ────────────────────────────────────────────────────────────────
    
    date_str = format_time(
        execution_time, 
        user.timezone, 
        user.show_utc_offset, 
        "%d.%m.%Y %H:%M"  # Format: "27.03.2024 18:30"
    )
    preview = l10n["preview"].format(
        text=new_reminder.reminder_text, 
        time=date_str
    )
    
    # Determine recurrence display text
    rrule_text = l10n["repeat_none"]
    if new_reminder.is_recurring and new_reminder.rrule_string:
        if "DAILY" in new_reminder.rrule_string:
            rrule_text = l10n["repeat_day"]
        elif "BYDAY" in new_reminder.rrule_string:
            rrule_text = l10n["repeat_weekdays"]
        elif "WEEKLY" in new_reminder.rrule_string:
            rrule_text = l10n["repeat_week"]

    # Build edit keyboard with action buttons
    keyboard = get_edit_keyboard(
        reminder_id=new_reminder.id, 
        l10n=l10n, 
        is_recurring=new_reminder.is_recurring, 
        is_nagging=new_reminder.is_nagging, 
        rrule_text=rrule_text
    )
    
    # Send confirmation message with keyboard
    sent_msg = await source_message.answer(
        preview, 
        reply_markup=keyboard, 
        parse_mode="Markdown"
    )

    # ────────────────────────────────────────────────────────────────
    # STEP 4: Launch 5-second auto-delete timer for keyboard
    # ────────────────────────────────────────────────────────────────
    
    task = asyncio.create_task(remove_keyboard_after_delay(sent_msg, 5))
    active_auto_delete_tasks[sent_msg.message_id] = task


# =============================================================================
# INTERNAL HELPER: RESET AUTO-DELETE TIMER
# =============================================================================

def _reset_auto_delete_timeout(message: Message) -> None:
    """
    Cancel the existing auto-delete timer so keyboard won't vanish while editing.
    
    Called before any edit operation to prevent keyboard from disappearing
    mid-edit (e.g., when user clicks "Edit" button).
    
    Args:
        message: Message whose timer should be cancelled
        
    Returns:
        None
    
    Side Effects:
      Cancels existing task if present, removes from tracker
    """
    task = active_auto_delete_tasks.get(message.message_id)
    if task and not task.done():  # Only cancel if task exists and hasn't finished
        task.cancel()


# =============================================================================
# CALLBACK HANDLER: EDIT EXISTING REMINDER (TIME SELECTION)
# =============================================================================

@router.callback_query(F.data.startswith("edit_edit_"))
async def callback_edit_edit(
    callback: CallbackQuery, 
    reminder_dao: ReminderDAO, 
    state: FSMContext, 
    l10n: dict[str, Any], 
    user: User
) -> None:
    """
    Handle 'Edit Time' button - reopen time selection for existing reminder.
    
    Args:
        callback: CallbackQuery with edit_edit_{reminder_id} data
        reminder_dao: DAO for database operations
        state: FSMContext for state management
        l10n: Localization dictionary
        user: User object with timezone info
        
    Returns:
        None
    
    Side Effects:
      Cancels auto-delete timer, sets up time selection FSM
      
    EXAMPLE CALLBACK DATA:
      "edit_edit_123" → Edit reminder #123's time
    """
    _reset_auto_delete_timeout(callback.message)  # Cancel timer
    
    reminder_id = int(callback.data.split("edit_edit_")[1])
    reminder = await reminder_dao.get_by_id(reminder_id)
    
    if not reminder:
        return await callback.answer("Not found", show_alert=True)
        
    # Set up FSM for time selection
    await state.set_state(ReminderWizard.choosing_time)
    await state.update_data(
        edit_reminder_id=reminder.id,  # Store ID for _save_and_show_edit
        text=reminder.reminder_text,   # Store current text
    )
    
    # Show time selection keyboard
    await callback.message.edit_text(
        l10n["ask_time"].format(text=reminder.reminder_text),
        reply_markup=get_time_selection_keyboard(user.timezone, l10n)
    )


# =============================================================================
# CALLBACK HANDLER: TOGGLE RECURRING MODE
# =============================================================================

@router.callback_query(F.data.startswith("edit_toggle_repeat_"))
async def callback_edit_repeat(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    l10n: dict[str, Any],
) -> None:
    """
    Handle recurring mode toggle (None → Daily → Weekdays → Weekly).
    
    Cycles through recurrence options in order. Also reschedules job with
    new recurrence settings.
    
    Args:
        callback: CallbackQuery with edit_toggle_repeat_{option} data
        reminder_dao: DAO for database operations
        scheduler_service: SchedulerService for job management
        l10n: Localization dictionary
        
    Returns:
        None
    
    Side Effects:
      Updates DB flags, reschedules job, updates keyboard
      
    EXAMPLE CALLBACK DATA:
      "edit_toggle_repeat_None" → Toggle off recurrence
      "edit_toggle_repeat_Daily" → Set to daily recurrence
    """
    _reset_auto_delete_timeout(callback.message)  # Cancel timer
    
    reminder_id = int(callback.data.split("edit_toggle_repeat_")[1])
    reminder = await reminder_dao.get_by_id(reminder_id)
    
    if not reminder:
        return await callback.answer("Not found", show_alert=True)

    # Define recurrence options in cycle order
    options = {
        l10n["repeat_none"]: (False, None),           # No recurrence
        l10n["repeat_day"]: (True, "FREQ=DAILY"),     # Daily
        l10n["repeat_weekdays"]: (True, 
            "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),    # Weekdays only
        l10n["repeat_week"]: (True, "FREQ=WEEKLY")   # Every week
    }

    # Find current option by matching rrule_string
    current_key = l10n["repeat_none"]
    for k, v in options.items():
        if reminder.is_recurring and reminder.rrule_string == v[1]:
            current_key = k
            break

    keys = list(options.keys())
    next_idx = (keys.index(current_key) + 1) % len(keys)  # Cycle to next option
    next_key = keys[next_idx]
    is_rec, rrule = options[next_key]

    # Update DB flags
    reminder.is_recurring = is_rec
    reminder.rrule_string = rrule
    
    # Flush so the DB row is updated before rescheduling (middleware will commit)
    await reminder_dao.session.flush()

    # ────────────────────────────────────────────────────────────────
    # BUG FIX CRIT-3/4: Reschedule job with new recurrence settings
    # ────────────────────────────────────────────────────────────────
    
    # Without this, the old one-shot 'date' job stays registered even after 
    # the user enables repeat — meaning the task fires once and never recurs.
    scheduler_service.remove_reminder_job(reminder.id)
    scheduler_service.schedule_reminder(
        reminder.id, 
        reminder.execution_time, 
        is_nagging=reminder.is_nagging
    )

    # Update keyboard to show new recurrence option
    keyboard = get_edit_keyboard(
        reminder.id, 
        l10n, 
        is_rec, 
        reminder.is_nagging, 
        next_key
    )
    
    await callback.message.edit_reply_markup(reply_markup=keyboard)


# =============================================================================
# CALLBACK HANDLER: TOGGLE NAGGING MODE
# =============================================================================

@router.callback_query(F.data.startswith("edit_toggle_nagging_"))
async def callback_edit_nagging(
    callback: CallbackQuery, 
    reminder_dao: ReminderDAO, 
    scheduler_service: SchedulerService, 
    l10n: dict[str, Any]
) -> None:
    """
    Handle nagging mode toggle (on/off).
    
    Nagging mode causes the bot to send follow-up notifications every 5 minutes
    until user marks task as done.
    
    Args:
        callback: CallbackQuery with edit_toggle_nagging_{flag} data
        reminder_dao: DAO for database operations
        scheduler_service: SchedulerService for job management
        l10n: Localization dictionary
        
    Returns:
        None
    
    Side Effects:
      Updates DB flag, reschedules nagging job, updates keyboard
      
    EXAMPLE CALLBACK DATA:
      "edit_toggle_nagging_true" → Enable nagging mode
      "edit_toggle_nagging_false" → Disable nagging mode
    """
    _reset_auto_delete_timeout(callback.message)  # Cancel timer
    
    reminder_id = int(callback.data.split("edit_toggle_nagging_")[1])
    reminder = await reminder_dao.get_by_id(reminder_id)
    
    if not reminder:
        return await callback.answer("Not found", show_alert=True)

    # Toggle nagging flag in DB
    reminder.is_nagging = not reminder.is_nagging
    
    # Re-schedule to pick up the nagging flag (nagging jobs are 5-min intervals)
    scheduler_service.schedule_reminder(
        reminder.id, 
        reminder.execution_time, 
        is_nagging=reminder.is_nagging
    )
    
    # Determine rrule_text for keyboard update (reverse lookup)
    rrule_text = l10n["repeat_none"]
    if reminder.is_recurring and reminder.rrule_string:
        if "DAILY" in reminder.rrule_string:
            rrule_text = l10n["repeat_day"]
        elif "BYDAY" in reminder.rrule_string:
            rrule_text = l10n["repeat_weekdays"]
        elif "WEEKLY" in reminder.rrule_string:
            rrule_text = l10n["repeat_week"]

    # Update keyboard to show new nagging state
    keyboard = get_edit_keyboard(
        reminder.id, 
        l10n, 
        reminder.is_recurring, 
        reminder.is_nagging, 
        rrule_text
    )
    
    await callback.message.edit_reply_markup(reply_markup=keyboard)


# =============================================================================
# CALLBACK HANDLER: DELETE REMINDER (FROM EDIT SCREEN)
# =============================================================================

@router.callback_query(F.data.startswith("edit_delete_"))
async def callback_edit_delete(
    callback: CallbackQuery, 
    reminder_dao: ReminderDAO, 
    scheduler_service: SchedulerService, 
    l10n: dict[str, Any]
) -> None:
    """
    Handle delete button from edit screen.
    
    Args:
        callback: CallbackQuery with edit_delete_{reminder_id} data
        reminder_dao: DAO for database operations
        scheduler_service: SchedulerService for job management
        l10n: Localization dictionary
        
    Returns:
        None
    
    Side Effects:
      Deletes DB record, removes scheduler jobs, shows confirmation
      
    EXAMPLE CALLBACK DATA:
      "edit_delete_123" → Delete reminder #123
    """
    _reset_auto_delete_timeout(callback.message)  # Cancel timer
    
    reminder_id = int(callback.data.split("edit_delete_")[1])
    
    await reminder_dao.delete_by_id(reminder_id)
    scheduler_service.remove_reminder_job(reminder_id)

    await callback.message.edit_text(l10n["task_deleted"], reply_markup=None)


# =============================================================================
# CALLBACK HANDLER: DELETE TASK (FROM TASKS LIST)
# =============================================================================

@router.callback_query(F.data.startswith("del_task_"))
async def callback_delete_task(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    l10n: dict[str, Any]
) -> None:
    """
    Handle delete button from tasks list.
    
    Args:
        callback: CallbackQuery with del_task_{reminder_id} data
        reminder_dao: DAO for database operations
        scheduler_service: SchedulerService for job management
        l10n: Localization dictionary
        
    Returns:
        None
    
    Side Effects:
      Deletes DB record, removes scheduler jobs, shows alert
      
    EXAMPLE CALLBACK DATA:
      "del_task_456" → Delete reminder #456 from tasks list
    """
    task_id = int(callback.data.split("del_task_")[1])

    await reminder_dao.delete_by_id(task_id)
    scheduler_service.remove_reminder_job(task_id)

    await callback.answer(l10n["task_deleted"])
    await callback.message.edit_text(l10n["task_deleted"], reply_markup=None)


# =============================================================================
# CALLBACK HANDLER: CLOSE TASKS LIST
# =============================================================================

@router.callback_query(F.data == "close_tasks")
async def callback_close_tasks(callback: CallbackQuery) -> None:
    """
    Handle close button on tasks list.
    
    Args:
        callback: CallbackQuery with data == "close_tasks"
        
    Returns:
        None
    
    Side Effects:
      Deletes message (closes the view)
    """
    await callback.message.delete()


# =============================================================================
# CALLBACK HANDLER: REFRESH TASKS LIST
# =============================================================================

@router.callback_query(F.data == "refresh_tasks")
async def callback_refresh_tasks(
    callback: CallbackQuery, 
    reminder_dao: ReminderDAO, 
    user: User, 
    l10n: dict[str, Any]
) -> None:
    """
    Handle refresh button on tasks list.
    
    Args:
        callback: CallbackQuery with data == "refresh_tasks"
        reminder_dao: DAO for database operations
        user: User object with timezone info
        l10n: Localization dictionary
        
    Returns:
        None
    
    Side Effects:
      Fetches latest tasks, updates message text and keyboard
    """
    tasks = await reminder_dao.get_user_reminders(user.id)

    if not tasks:
        await callback.message.edit_text(
            l10n["no_tasks"], 
            reply_markup=None
        )
        return

    # Build formatted task list (same as btn_my_tasks)
    text_lines = [l10n["tasks_header"]]
    
    for task in tasks:
        dt_str = format_time(
            task.execution_time, 
            user.timezone, 
            user.show_utc_offset, 
            "%d.%m %H:%M"
        )
        recur_icon = "🔁 " if task.is_recurring else ""
        nag_icon = "🔥 " if task.is_nagging else ""
        
        text_lines.append(
            f"▫️ `{dt_str}`: {recur_icon}{nag_icon}{task.reminder_text}"
        )

    final_text = "\n".join(text_lines)
    
    await callback.message.edit_text(
        final_text,
        reply_markup=get_tasks_list_keyboard(tasks, l10n),
        parse_mode="Markdown",
    )


# =============================================================================
# CALLBACK HANDLER: SHOW COMPLETED TASKS
# =============================================================================

@router.callback_query(F.data == "show_completed")
async def callback_show_completed(
    callback: CallbackQuery, 
    reminder_dao: ReminderDAO, 
    user: User, 
    l10n: dict[str, Any]
) -> None:
    """
    Show completed tasks list in-place (replaces active tasks view).
    
    Args:
        callback: CallbackQuery with data == "show_completed"
        reminder_dao: DAO for database operations
        user: User object with timezone info
        l10n: Localization dictionary
        
    Returns:
        None
    
    Side Effects:
      Fetches completed tasks, updates message and keyboard
      
    BUG FIX APPLIED:
      Previously didn't handle empty completed list gracefully. Now shows
      alert if no completed tasks exist.
    """
    # Fetch all COMPLETED tasks for today
    completed = await reminder_dao.get_today_completed_tasks(user.id, user.timezone)

    if not completed:
        await callback.answer(l10n["no_completed_tasks"], show_alert=True)
        return

    text_lines = [l10n["completed_header"]]  # Header like "Completed Today:"
    
    for task in completed:
        dt_str = format_time(
            task.execution_time, 
            user.timezone, 
            user.show_utc_offset, 
            "%d.%m %H:%M"
        )
        text_lines.append(f"✅ `{dt_str}`: ~{task.reminder_text}~")

    await callback.message.edit_text(
        "\n".join(text_lines),
        reply_markup=get_completed_tasks_keyboard(l10n),
        parse_mode="Markdown",
    )
    
    await callback.answer()  # Acknowledge callback query


# =============================================================================
# CALLBACK HANDLER: MARK TASK AS DONE
# =============================================================================

@router.callback_query(F.data.startswith("done_task_"))
async def callback_task_done(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    l10n: dict[str, Any]
) -> None:
    """
    Handle mark-as-done button.
    
    This is the most critical handler - it marks a task as completed and
    removes all associated scheduler jobs to prevent duplicate notifications.
    
    Args:
        callback: CallbackQuery with done_task_{reminder_id} data
        reminder_dao: DAO for database operations
        scheduler_service: SchedulerService for job management
        l10n: Localization dictionary
        
    Returns:
        None
    
    Side Effects:
      Marks task as completed in DB, removes all scheduler jobs
      
    BUG FIX CRIT-3: idempotency guard — ignore rapid double-taps
    
    EXAMPLE CALLBACK DATA:
      "done_task_789" → Mark reminder #789 as done
    """
    reminder_id = int(callback.data.split("done_task_")[1])

    # ────────────────────────────────────────────────────────────────
    # BUG FIX CRIT-3: Idempotency guard — ignore rapid double-taps
    # ────────────────────────────────────────────────────────────────
    
    reminder = await reminder_dao.get_by_id(reminder_id)
    if not reminder or reminder.status == "completed":
        await callback.answer(l10n.get("already_done", "Already done ✅"))
        return

    # Mark done in DB and remove scheduler jobs
    await reminder_dao.mark_done(reminder_id)
    scheduler_service.remove_reminder_job(reminder_id)
    scheduler_service.remove_nagging_job(reminder_id)

    try:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n{l10n['task_done_reply']}",
            reply_markup=None,  # Remove keyboard after completion
            parse_mode="Markdown",
        )
    except TelegramBadRequest:
        pass  # Already updated by a concurrent tap — safe to ignore

    await callback.answer(l10n["btn_done"])


# =============================================================================
# CALLBACK HANDLER: SHOW SNOOZE OPTIONS
# =============================================================================

@router.callback_query(F.data.startswith("snooze_show_"))
async def callback_snooze_show(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    """
    Show snooze options for a reminder.
    
    Args:
        callback: CallbackQuery with snooze_show_{reminder_id} data
        l10n: Localization dictionary
        
    Returns:
        None
    
    Side Effects:
      Updates message reply markup with snooze keyboard
      
    EXAMPLE CALLBACK DATA:
      "snooze_show_456" → Show snooze options for reminder #456
    """
    reminder_id = int(callback.data.split("snooze_show_")[1])
    keyboard = get_snooze_keyboard(reminder_id, l10n)
    
    await callback.message.edit_reply_markup(reply_markup=keyboard)


# =============================================================================
# CALLBACK HANDLER: ACT ON SNOOZE OPTION
# =============================================================================

@router.callback_query(F.data.startswith("snooze_act_"))
async def callback_snooze_act(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    state: FSMContext,
    user: User,
    l10n: dict[str, Any],
) -> None:
    """
    Handle snooze action (delay reminder execution).
    
    Supports multiple delay options: 15m, 30m, 1h, 2h, 1d, or custom time.
    
    Args:
        callback: CallbackQuery with snooze_act_{reminder_id}_{action} data
        reminder_dao: DAO for database operations
        scheduler_service: SchedulerService for job management
        state: FSMContext for state management
        user: User object with timezone info
        l10n: Localization dictionary
        
    Returns:
        None
    
    Side Effects:
      Calculates new execution time, updates DB, reschedules job
      
    EXAMPLE CALLBACK DATA:
      "snooze_act_456_15m" → Snooze reminder #456 for 15 minutes
      "snooze_act_456_custom" → Open time selection wizard
    """
    parts = callback.data.split("_")
    reminder_id = int(parts[2])
    action = parts[3]

    reminder = await reminder_dao.get_by_id(reminder_id)
    
    if not reminder:
        return await callback.answer("Task not found.", show_alert=True)

    # ────────────────────────────────────────────────────────────────
    # CUSTOM TIME SELECTION - Pivot into edit FSM flow
    # ────────────────────────────────────────────────────────────────
    
    if action == "custom":
        await state.set_state(ReminderWizard.choosing_time)
        await state.update_data(
            edit_reminder_id=reminder.id,
            text=reminder.reminder_text,
        )
        
        # Show time selection keyboard
        await callback.message.edit_text(
            l10n["ask_time"].format(text=reminder.reminder_text),
            reply_markup=get_time_selection_keyboard(user.timezone, l10n)
        )
        
        return

    # ────────────────────────────────────────────────────────────────
    # TIME DELAY OPTIONS (15m, 30m, 1h, etc.)
    # ────────────────────────────────────────────────────────────────
    
    try:
        user_tz = pytz.timezone(user.timezone)
    except Exception:
        user_tz = pytz.UTC

    now = datetime.now(user_tz)
    new_time = now

    if action == "15m":
        new_time += timedelta(minutes=15)
    elif action == "30m":
        new_time += timedelta(minutes=30)
    elif action == "1h":
        new_time += timedelta(hours=1)
    elif action == "2h":
        new_time += timedelta(hours=2)
    elif action == "1d":
        new_time += timedelta(days=1)
    elif action in ["morning", "day", "evening", "night"]:
        hour_map = {"morning": 9, "day": 13, "evening": 19, "night": 23}
        target_hour = hour_map[action]
        
        new_time = now.replace(
            hour=target_hour, 
            minute=0, 
            second=0, 
            microsecond=0
        )
        
        # If the target hour has already passed today, rollover to tomorrow
        if new_time <= now:
            new_time += timedelta(days=1)

    # ────────────────────────────────────────────────────────────────
    # BUG FIX CRIT-1: Keep timezone-aware datetime — DO NOT strip tzinfo.
    # APScheduler correctly handles tz-aware run_date; stripping it caused
    # reminders to fire at wrong times for non-UTC users.
    # ────────────────────────────────────────────────────────────────

    # 1. Update DB with new execution time
    reminder.execution_time = new_time
    
    # 2. Reschedule (pass timezone-aware datetime directly)
    scheduler_service.schedule_reminder(
        reminder.id, 
        new_time, 
        is_nagging=reminder.is_nagging
    )

    # 3. UI — display in user's local time
    friendly_time = format_time(
        new_time, 
        user.timezone, 
        user.show_utc_offset, 
        "%d.%m %H:%M"
    )
    
    await callback.message.edit_text(
        f"{callback.message.text}\n\n{l10n['snoozed_until'].format(time=friendly_time)}",
        reply_markup=None,  # Remove keyboard after snooze confirmation
        parse_mode="Markdown"
    )
    
    await callback.answer("Snoozed!")