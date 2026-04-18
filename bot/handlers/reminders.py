"""
Reminder Handlers — FSM wizard for reminder creation and management.

FSM states (ReminderWizard):
  entering_text  → waiting for reminder text
  choosing_time  → showing time-selection keyboard

All handlers receive dependencies via DatabaseMiddleware injection:
  user, reminder_dao, scheduler_service, l10n, state
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
from bot.services.scheduler import SchedulerService
from bot.states.reminder import ReminderWizard
from bot.utils.time_ext import format_time

router = Router(name="reminders")
parser = InputParser()
logger = logging.getLogger(__name__)

# asyncio.Task registry for auto-removing inline keyboards after 5 s
active_auto_delete_tasks: dict[int, asyncio.Task] = {}

_MENU_TEXTS = frozenset([
    "➕ Новая задача", "📅 Мои задачи", "⚙️ Настройки",
    "➕ New Task", "📅 My Tasks", "⚙️ Settings",
])
_MAX_INPUT = 3000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rrule_text(reminder, l10n: dict[str, Any]) -> str:
    """Return a human-readable recurrence label for *reminder*."""
    if not reminder.is_recurring or not reminder.rrule_string:
        return l10n["repeat_none"]
    if "DAILY" in reminder.rrule_string:
        return l10n["repeat_day"]
    if "BYDAY" in reminder.rrule_string:
        return l10n["repeat_weekdays"]
    if "WEEKLY" in reminder.rrule_string:
        return l10n["repeat_week"]
    return l10n["repeat_none"]


async def _remove_keyboard_after_delay(message: Message, delay: int = 5) -> None:
    """Background task: remove an inline keyboard after *delay* seconds."""
    try:
        await asyncio.sleep(delay)
        await message.edit_reply_markup(reply_markup=None)
    except asyncio.CancelledError:
        pass
    except TelegramBadRequest:
        pass
    finally:
        active_auto_delete_tasks.pop(message.message_id, None)


def _reset_auto_delete(message: Message) -> None:
    """Cancel the pending keyboard-removal task for *message* (if any)."""
    task = active_auto_delete_tasks.get(message.message_id)
    if task and not task.done():
        task.cancel()


async def _save_and_show_edit(
    source_message: Message,
    state: FSMContext,
    l10n: dict[str, Any],
    user: User,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
) -> None:
    """Persist reminder to DB, schedule it, send confirmation with edit keyboard."""
    data = await state.get_data()
    text = data.get("text")
    execution_time = datetime.fromisoformat(data["execution_time"])
    edit_reminder_id = data.get("edit_reminder_id")

    if edit_reminder_id:
        new_reminder = await reminder_dao.get_by_id(edit_reminder_id)
        if new_reminder:
            new_reminder.reminder_text = text
            new_reminder.execution_time = execution_time
            scheduler_service.remove_reminder_job(new_reminder.id)
        else:
            logger.warning("Reminder %s not found during edit.", edit_reminder_id)
            return
    else:
        try:
            new_reminder = await reminder_dao.create_reminder(
                user_id=user.id,
                text=text,
                execution_time=execution_time,
                is_recurring=False,
                rrule_string=None,
                is_nagging=False,
            )
        except ValueError as ve:
            logger.warning("Validation error for user %s: %s", user.id, ve)
            await source_message.answer(str(ve))
            await state.clear()
            return

    try:
        scheduler_service.schedule_reminder(new_reminder.id, new_reminder.execution_time, is_nagging=new_reminder.is_nagging)
    except Exception as e:
        logger.error("Failed to schedule reminder %s: %s", new_reminder.id, e, exc_info=True)
        await reminder_dao.delete_by_id(new_reminder.id)
        await source_message.answer(l10n.get("schedule_error", "❌ Failed to schedule. Please try again."))
        await state.clear()
        return

    await state.clear()

    date_str = format_time(execution_time, user.timezone, user.show_utc_offset, "%d.%m.%Y %H:%M")
    preview = l10n["preview"].format(text=new_reminder.reminder_text, time=date_str)
    keyboard = get_edit_keyboard(
        reminder_id=new_reminder.id,
        l10n=l10n,
        is_recurring=new_reminder.is_recurring,
        is_nagging=new_reminder.is_nagging,
        rrule_text=_rrule_text(new_reminder, l10n),
    )
    sent_msg = await source_message.answer(preview, reply_markup=keyboard, parse_mode="Markdown")

    task = asyncio.create_task(_remove_keyboard_after_delay(sent_msg, 5))
    active_auto_delete_tasks[sent_msg.message_id] = task


# ---------------------------------------------------------------------------
# Main menu buttons
# ---------------------------------------------------------------------------

@router.message(F.text.in_(["➕ Новая задача", "➕ New Task"]))
async def btn_new_task(message: Message, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.set_state(ReminderWizard.entering_text)
    await message.answer(l10n["enter_task"], parse_mode="Markdown")


@router.message(F.text.in_(["📅 Мои задачи", "📅 My Tasks"]))
async def btn_my_tasks(
    message: Message, state: FSMContext, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    await state.clear()
    tasks = await reminder_dao.get_user_reminders(user.id)
    if not tasks:
        await message.answer(l10n["no_tasks"], reply_markup=get_main_menu_keyboard(l10n))
        return

    lines = [l10n["tasks_header"]]
    for task in tasks:
        dt_str = format_time(task.execution_time, user.timezone, user.show_utc_offset, "%d.%m %H:%M")
        lines.append(f"▫️ `{dt_str}`: {'🔁 ' if task.is_recurring else ''}{'🔥 ' if task.is_nagging else ''}{task.reminder_text}")

    await message.answer("\n".join(lines), reply_markup=get_tasks_list_keyboard(tasks, l10n), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# FSM: text input
# ---------------------------------------------------------------------------

@router.message(F.forward_origin)
async def handle_forwarded_task(
    message: Message, state: FSMContext, user: User, l10n: dict[str, Any],
    reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
) -> None:
    """Extract text from a forwarded message and route it through the wizard."""
    text = message.text or message.caption
    if not text:
        return

    await state.clear()

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
            origin_name = getattr(fwd, "sender_chat").title if getattr(fwd, "sender_chat", None) else "Group"

    if origin_name:
        prefix = f"👤 {'Forwarded from' if user.language == 'en' else 'Переслано от'} {origin_name}:\n"
    else:
        prefix = ""

    full_text = f"{prefix}{text}".strip()

    if len(full_text) > _MAX_INPUT:
        await message.answer(l10n.get("text_too_long", "❌ Text too long.").format(length=len(full_text), max_length=_MAX_INPUT))
        return

    try:
        result = await parser.parse(full_text, user.timezone)
        clean_text = result.clean_text or "Без названия"
        await state.update_data(text=clean_text, user_timezone=user.timezone, chat_id=message.chat.id)

        if result.parsed_datetime:
            await state.update_data(execution_time=result.parsed_datetime.isoformat())
            await _save_and_show_edit(message, state, l10n, user, reminder_dao, scheduler_service)
        else:
            await state.set_state(ReminderWizard.choosing_time)
            await message.answer(l10n["ask_time"].format(text=clean_text), reply_markup=get_time_selection_keyboard(user.timezone, l10n, user.show_utc_offset))
    except ValueError as ve:
        await message.answer(str(ve))
    except Exception as e:
        logger.error("Error parsing forwarded text: %s", e, exc_info=True)
        await message.answer(l10n.get("parse_error", "Error parsing text"))
    
    # HIGH-3 FIX: Clear FSM state after error to prevent stuck wizard
    try:
        await state.clear()
    except Exception:
        pass  # Ignore if already cleared


@router.message(ReminderWizard.entering_text, F.text)
@router.message(F.text)
async def handle_task_text(
    message: Message, state: FSMContext, user: User, l10n: dict[str, Any],
    reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
) -> None:
    """Catch-all: parse any text message as a potential reminder."""
    if message.text in _MENU_TEXTS:
        return
    if len(message.text) > _MAX_INPUT:
        await message.answer(l10n.get("text_too_long", "❌ Text too long.").format(length=len(message.text), max_length=_MAX_INPUT))
        return

    try:
        result = await parser.parse(message.text, user.timezone)
        logger.info("Parsed '%s' → clean='%s' dt=%s", message.text, result.clean_text, result.parsed_datetime)
        clean_text = result.clean_text or "Без названия"
        await state.update_data(text=clean_text, user_timezone=user.timezone, chat_id=message.chat.id)

        if result.parsed_datetime:
            await state.update_data(execution_time=result.parsed_datetime.isoformat())
            await _save_and_show_edit(message, state, l10n, user, reminder_dao, scheduler_service)
        else:
            await state.set_state(ReminderWizard.choosing_time)
            await message.answer(l10n["ask_time"].format(text=clean_text), reply_markup=get_time_selection_keyboard(user.timezone, l10n, user.show_utc_offset))
    except ValueError as ve:
        await message.answer(str(ve))
    except Exception as e:
        logger.error("Error parsing text '%s': %s", message.text, e, exc_info=True)
        await message.answer(l10n["parse_error"])


# ---------------------------------------------------------------------------
# FSM: time selection
# ---------------------------------------------------------------------------

@router.callback_query(ReminderWizard.choosing_time, F.data.startswith("time_"))
async def callback_time_selected(
    callback: CallbackQuery, state: FSMContext, user: User, l10n: dict[str, Any],
    reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
) -> None:
    """Resolve the chosen time option and persist the reminder."""
    data_str = callback.data
    try:
        tz = pytz.timezone(user.timezone)
    except Exception:
        tz = pytz.UTC
    now = datetime.now(tz)
    execution_time = None

    if "delta" in data_str:
        minutes = int(data_str.split("_")[-1])
        execution_time = now + timedelta(minutes=minutes)
    elif "fixed" in data_str:
        execution_time = datetime.fromisoformat(data_str.split("_fixed_")[1])
    elif "tomorrow" in data_str:
        execution_time = now.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
    elif "manual" in data_str:
        await callback.answer()
        await callback.message.edit_text(l10n["try_again_manual"])
        return

    if execution_time:
        await state.update_data(execution_time=execution_time.isoformat())
        await callback.message.delete()
        await _save_and_show_edit(callback.message, state, l10n, user, reminder_dao, scheduler_service)
    else:
        await callback.answer(l10n.get("parse_error", "❌ Unknown option"), show_alert=True)
        await state.clear()


# ---------------------------------------------------------------------------
# Edit keyboard callbacks
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("edit_edit_"))
async def callback_edit_edit(
    callback: CallbackQuery, reminder_dao: ReminderDAO, state: FSMContext, l10n: dict[str, Any], user: User
) -> None:
    _reset_auto_delete(callback.message)
    reminder_id = int(callback.data.split("edit_edit_")[1])
    reminder = await reminder_dao.get_by_id(reminder_id)
    if not reminder:
        return await callback.answer("Not found", show_alert=True)
    await state.set_state(ReminderWizard.choosing_time)
    await state.update_data(edit_reminder_id=reminder.id, text=reminder.reminder_text)
    await callback.message.edit_text(
        l10n["ask_time"].format(text=reminder.reminder_text),
        reply_markup=get_time_selection_keyboard(user.timezone, l10n),
    )


@router.callback_query(F.data.startswith("edit_toggle_repeat_"))
async def callback_edit_repeat(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, l10n: dict[str, Any]
) -> None:
    _reset_auto_delete(callback.message)
    reminder_id = int(callback.data.split("edit_toggle_repeat_")[1])
    reminder = await reminder_dao.get_by_id(reminder_id)
    if not reminder:
        return await callback.answer("Not found", show_alert=True)

    options = {
        l10n["repeat_none"]:     (False, None),
        l10n["repeat_day"]:      (True, "FREQ=DAILY"),
        l10n["repeat_weekdays"]: (True, "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        l10n["repeat_week"]:     (True, "FREQ=WEEKLY"),
    }
    current_key = next((k for k, v in options.items() if reminder.is_recurring and reminder.rrule_string == v[1]), l10n["repeat_none"])
    keys = list(options)
    next_key = keys[(keys.index(current_key) + 1) % len(keys)]
    is_rec, rrule = options[next_key]

    reminder.is_recurring = is_rec
    reminder.rrule_string = rrule
    await reminder_dao.session.flush()

    scheduler_service.remove_reminder_job(reminder.id)
    scheduler_service.schedule_reminder(reminder.id, reminder.execution_time, is_nagging=reminder.is_nagging)

    await callback.message.edit_reply_markup(
        reply_markup=get_edit_keyboard(reminder.id, l10n, is_rec, reminder.is_nagging, next_key)
    )


@router.callback_query(F.data.startswith("edit_toggle_nagging_"))
async def callback_edit_nagging(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, l10n: dict[str, Any]
) -> None:
    _reset_auto_delete(callback.message)
    reminder_id = int(callback.data.split("edit_toggle_nagging_")[1])
    reminder = await reminder_dao.get_by_id(reminder_id)
    if not reminder:
        return await callback.answer("Not found", show_alert=True)

    reminder.is_nagging = not reminder.is_nagging
    scheduler_service.schedule_reminder(reminder.id, reminder.execution_time, is_nagging=reminder.is_nagging)

    await callback.message.edit_reply_markup(
        reply_markup=get_edit_keyboard(reminder.id, l10n, reminder.is_recurring, reminder.is_nagging, _rrule_text(reminder, l10n))
    )


@router.callback_query(F.data.startswith("edit_delete_"))
async def callback_edit_delete(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, l10n: dict[str, Any]
) -> None:
    _reset_auto_delete(callback.message)
    reminder_id = int(callback.data.split("edit_delete_")[1])
    await reminder_dao.delete_by_id(reminder_id)
    scheduler_service.remove_reminder_job(reminder_id)
    await callback.message.edit_text(l10n["task_deleted"], reply_markup=None)


# ---------------------------------------------------------------------------
# Task list callbacks
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("del_task_"))
async def callback_delete_task(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, l10n: dict[str, Any]
) -> None:
    task_id = int(callback.data.split("del_task_")[1])
    await reminder_dao.delete_by_id(task_id)
    scheduler_service.remove_reminder_job(task_id)
    await callback.answer(l10n["task_deleted"])
    await callback.message.edit_text(l10n["task_deleted"], reply_markup=None)


@router.callback_query(F.data == "close_tasks")
async def callback_close_tasks(callback: CallbackQuery) -> None:
    await callback.message.delete()


@router.callback_query(F.data == "refresh_tasks")
async def callback_refresh_tasks(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    tasks = await reminder_dao.get_user_reminders(user.id)
    if not tasks:
        await callback.message.edit_text(l10n["no_tasks"], reply_markup=None)
        return

    lines = [l10n["tasks_header"]]
    for task in tasks:
        dt_str = format_time(task.execution_time, user.timezone, user.show_utc_offset, "%d.%m %H:%M")
        lines.append(f"▫️ `{dt_str}`: {'🔁 ' if task.is_recurring else ''}{'🔥 ' if task.is_nagging else ''}{task.reminder_text}")

    await callback.message.edit_text("\n".join(lines), reply_markup=get_tasks_list_keyboard(tasks, l10n), parse_mode="Markdown")


@router.callback_query(F.data == "show_completed")
async def callback_show_completed(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    completed = await reminder_dao.get_today_completed_tasks(user.id, user.timezone)
    if not completed:
        await callback.answer(l10n["no_completed_tasks"], show_alert=True)
        return

    lines = [l10n["completed_header"]]
    for task in completed:
        dt_str = format_time(task.execution_time, user.timezone, user.show_utc_offset, "%d.%m %H:%M")
        lines.append(f"✅ `{dt_str}`: ~{task.reminder_text}~")

    await callback.message.edit_text("\n".join(lines), reply_markup=get_completed_tasks_keyboard(l10n), parse_mode="Markdown")
    await callback.answer()


# ---------------------------------------------------------------------------
# Mark done
# ---------------------------------------------------------------------------

def _cleanup_stale_timers() -> None:
    """
    Clean up any tasks in active_auto_delete_tasks that have already completed.
    
    This prevents memory leaks from orphaned task references after 5 seconds
    when the keyboard removal completes successfully.
    
    Called periodically via APScheduler cleanup job.
    """
    if not active_auto_delete_tasks:
        return
    
    # Remove any tasks that are still pending (tasks should have completed)
    # This handles edge cases where task wasn't properly removed from dict
    for msg_id, task in list(active_auto_delete_tasks.items()):
        if task.done():  # Task has completed (success or error)
            try:
                active_auto_delete_tasks.pop(msg_id, None)
            except Exception:
                pass

@router.callback_query(F.data.startswith("done_task_"))
async def callback_task_done(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, l10n: dict[str, Any]
) -> None:
    reminder_id = int(callback.data.split("done_task_")[1])
    reminder = await reminder_dao.get_by_id(reminder_id)

    # Idempotency: ignore rapid double-taps
    if not reminder or reminder.status == "completed":
        await callback.answer(l10n.get("already_done", "Already done ✅"))
        return

    await reminder_dao.mark_done(reminder_id)
    if not reminder.is_recurring:
        scheduler_service.remove_reminder_job(reminder_id)
    scheduler_service.remove_nagging_job(reminder_id)

    try:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n{l10n['task_done_reply']}",
            reply_markup=None,
            parse_mode="Markdown",
        )
    except TelegramBadRequest:
        pass  # Concurrent tap — safe to ignore

    await callback.answer(l10n["btn_done"])


# ---------------------------------------------------------------------------
# Snooze
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("snooze_show_"))
async def callback_snooze_show(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    reminder_id = int(callback.data.split("snooze_show_")[1])
    await callback.message.edit_reply_markup(reply_markup=get_snooze_keyboard(reminder_id, l10n))


@router.callback_query(F.data.startswith("snooze_act_"))
async def callback_snooze_act(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService,
    state: FSMContext, user: User, l10n: dict[str, Any],
) -> None:
    try:
        parts = callback.data.split("_", 3)
        if len(parts) < 4:
            raise ValueError("Too few parts in callback data")
        reminder_id = int(parts[2])
        action = parts[3]
    except (IndexError, ValueError) as e:
        logger.error("Malformed snooze callback data %r: %s", callback.data, e)
        return await callback.answer("❌ Invalid action", show_alert=True)

    reminder = await reminder_dao.get_by_id(reminder_id)
    if not reminder:
        return await callback.answer("Task not found.", show_alert=True)

    if action == "custom":
        await state.set_state(ReminderWizard.choosing_time)
        await state.update_data(edit_reminder_id=reminder.id, text=reminder.reminder_text)
        await callback.message.edit_text(
            l10n["ask_time"].format(text=reminder.reminder_text),
            reply_markup=get_time_selection_keyboard(user.timezone, l10n),
        )
        return

    try:
        user_tz = pytz.timezone(user.timezone)
    except Exception:
        user_tz = pytz.UTC
    now = datetime.now(user_tz)

    delta_map = {"15m": timedelta(minutes=15), "30m": timedelta(minutes=30), "1h": timedelta(hours=1), "2h": timedelta(hours=2), "1d": timedelta(days=1)}
    hour_map = {"morning": 9, "day": 13, "evening": 19, "night": 23}

    if action in delta_map:
        new_time = now + delta_map[action]
    elif action in hour_map:
        new_time = now.replace(hour=hour_map[action], minute=0, second=0, microsecond=0)
        if new_time <= now:
            new_time += timedelta(days=1)
    else:
        await callback.answer(l10n.get("unknown_snooze", "❌ Unknown action"), show_alert=True)
        return

    reminder.execution_time = new_time
    scheduler_service.schedule_reminder(reminder.id, new_time, is_nagging=reminder.is_nagging)

    friendly_time = format_time(new_time, user.timezone, user.show_utc_offset, "%d.%m %H:%M")
    await callback.message.edit_text(
        f"{callback.message.text}\n\n{l10n['snoozed_until'].format(time=friendly_time)}",
        reply_markup=None,
        parse_mode="Markdown",
    )
    await callback.answer("Snoozed!")