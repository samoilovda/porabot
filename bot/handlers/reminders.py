"""
Reminder handlers — FSM wizard for creation, task list, deletion, nagging.

All database access goes through ReminderDAO (injected by middleware).
Scheduler interactions go through SchedulerService (injected via workflow_data).
"""

import asyncio
import logging
from datetime import datetime, timedelta

import pytz
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext

from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User, Reminder
from bot.keyboards.inline import (
    get_edit_keyboard,
    get_task_done_keyboard,
    get_snooze_keyboard,
    get_tasks_list_keyboard,
    get_time_selection_keyboard,
)
from bot.keyboards.reply import get_main_menu_keyboard
from bot.services.parser import InputParser
from bot.services.scheduler import SchedulerService
from bot.states.reminder import ReminderWizard
from typing import Any

router = Router(name="reminders")
parser = InputParser()
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Main menu buttons                                                  #
# ------------------------------------------------------------------ #


@router.message(F.text.in_(["➕ Новая задача", "➕ New Task"]))
async def btn_new_task(message: Message, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.set_state(ReminderWizard.entering_text)
    await message.answer(
        l10n["enter_task"],
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown",
    )


@router.message(F.text.in_(["📅 Мои задачи", "📅 My Tasks"]))
async def btn_my_tasks(
    message: Message, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    tasks = await reminder_dao.get_user_reminders(user.id)

    if not tasks:
        await message.answer(
            l10n["no_tasks"],
            reply_markup=get_main_menu_keyboard(l10n),
        )
        return

    text_lines = [l10n["tasks_header"]]
    for task in tasks:
        dt_display = task.execution_time
        try:
            if user.timezone:
                user_tz = pytz.timezone(user.timezone)
                if dt_display.tzinfo:
                    dt_display = dt_display.astimezone(user_tz)
        except Exception:
            pass

        dt_str = dt_display.strftime("%d.%m %H:%M")
        recur_icon = "🔁 " if task.is_recurring else ""
        nag_icon = "🔥 " if task.is_nagging else ""
        text_lines.append(f"▫️ `{dt_str}`: {recur_icon}{nag_icon}{task.reminder_text}")

    final_text = "\n".join(text_lines)
    await message.answer(
        final_text,
        reply_markup=get_tasks_list_keyboard(tasks, l10n),
        parse_mode="Markdown",
    )


# ------------------------------------------------------------------ #
#  FSM Wizard: text input → time selection → confirmation → save      #
# ------------------------------------------------------------------ #


@router.message(ReminderWizard.entering_text, F.text)
@router.message(F.text)
async def handle_task_text(
    message: Message, state: FSMContext, user: User, l10n: dict[str, Any],
    reminder_dao: ReminderDAO, scheduler_service: SchedulerService
) -> None:
    # Skip menu button texts
    if message.text in ["➕ Новая задача", "📅 Мои задачи", "⚙️ Настройки", "➕ New Task", "📅 My Tasks", "⚙️ Settings"]:
        return

    try:
        result = await parser.parse(message.text, user.timezone)
        clean_text = result.clean_text or "Без названия"
        parsed_dt = result.parsed_datetime

        await state.update_data(
            text=clean_text,
            user_timezone=user.timezone,
            chat_id=message.chat.id,
        )

        if parsed_dt:
            await state.update_data(execution_time=parsed_dt.isoformat())
            await _save_and_show_edit(
                message, state, l10n, user.id, reminder_dao, scheduler_service
            )
        else:
            await state.set_state(ReminderWizard.choosing_time)
            await message.answer(
                l10n["ask_time"].format(text=clean_text),
                reply_markup=get_time_selection_keyboard(user.timezone, l10n),
            )
    except Exception as e:
        logger.error(f"Error parsing text: {e}")
        await message.answer(l10n["parse_error"])


@router.callback_query(ReminderWizard.choosing_time, F.data.startswith("time_"))
async def callback_time_selected(
    callback: CallbackQuery, state: FSMContext, user: User, l10n: dict[str, Any],
    reminder_dao: ReminderDAO, scheduler_service: SchedulerService
) -> None:
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
        iso_str = data_str.split("_fixed_")[1]
        execution_time = datetime.fromisoformat(iso_str)
    elif "tomorrow" in data_str:
        execution_time = now.replace(
            hour=9, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)
    elif "manual" in data_str:
        await callback.message.edit_text(l10n["try_again_manual"])
        return

    if execution_time:
        await state.update_data(execution_time=execution_time.isoformat())
        await callback.message.delete()
        await _save_and_show_edit(
            callback.message, state, l10n, user.id, reminder_dao, scheduler_service
        )

# Global dict to track active sleep tasks by message_id
active_auto_delete_tasks = {}

async def remove_keyboard_after_delay(message: Message, delay: int = 5) -> None:
    """Sleeps and then removes the inline keyboard from the message."""
    try:
        await asyncio.sleep(delay)
        await message.edit_reply_markup(reply_markup=None)
    except asyncio.CancelledError:
        pass  # Task was cancelled (e.g. user interacted with keyboard)
    except TelegramBadRequest:
        pass  # Message deleted or keyboard already removed
    finally:
        active_auto_delete_tasks.pop(message.message_id, None)


async def _save_and_show_edit(
    source_message: Message,
    state: FSMContext,
    l10n: dict[str, Any],
    user_id: int,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService
) -> None:
    data = await state.get_data()
    text = data.get("text")
    exec_time_iso = data.get("execution_time")

    edit_reminder_id = data.get("edit_reminder_id")

    execution_time = datetime.fromisoformat(exec_time_iso)

    if edit_reminder_id:
        new_reminder = await reminder_dao.get_by_id(edit_reminder_id)
        if new_reminder:
            new_reminder.reminder_text = text
            new_reminder.execution_time = execution_time
            scheduler_service.remove_reminder_job(new_reminder.id)
        else:
            new_reminder = await reminder_dao.create_reminder(
                user_id=user_id,
                text=text,
                execution_time=execution_time,
                is_recurring=False,
                rrule_string=None,
                is_nagging=False,
            )
    else:
        # 1. Immediate save to DB
        new_reminder = await reminder_dao.create_reminder(
            user_id=user_id,
            text=text,
            execution_time=execution_time,
            is_recurring=False,
            rrule_string=None,
            is_nagging=False,
        )

    # 2. Schedule
    scheduler_service.schedule_reminder(
        new_reminder.id, new_reminder.execution_time, is_nagging=new_reminder.is_nagging
    )
    
    await state.clear()

    # 3. Send confirmation with Edit keyboard
    date_str = execution_time.strftime("%d.%m.%Y %H:%M")
    preview = l10n["preview"].format(text=new_reminder.reminder_text, time=date_str)
    
    rrule_text = l10n["repeat_none"]
    if new_reminder.is_recurring and new_reminder.rrule_string:
        if "DAILY" in new_reminder.rrule_string:
            rrule_text = l10n["repeat_day"]
        elif "BYDAY" in new_reminder.rrule_string:
            rrule_text = l10n["repeat_weekdays"]
        elif "WEEKLY" in new_reminder.rrule_string:
            rrule_text = l10n["repeat_week"]

    keyboard = get_edit_keyboard(
        reminder_id=new_reminder.id, 
        l10n=l10n, 
        is_recurring=new_reminder.is_recurring, 
        is_nagging=new_reminder.is_nagging, 
        rrule_text=rrule_text
    )
    
    sent_msg = await source_message.answer(
        preview, reply_markup=keyboard, parse_mode="Markdown"
    )

    # 4. Launch 5-second auto-delete trigger
    task = asyncio.create_task(remove_keyboard_after_delay(sent_msg, 5))
    active_auto_delete_tasks[sent_msg.message_id] = task


def _reset_auto_delete_timeout(message: Message) -> None:
    """Cancels the existing auto-delete timer so the keyboard won't vanish while editing."""
    task = active_auto_delete_tasks.get(message.message_id)
    if task and not task.done():
        task.cancel()


@router.callback_query(F.data.startswith("edit_edit_"))
async def callback_edit_edit(
    callback: CallbackQuery, reminder_dao: ReminderDAO, state: FSMContext, l10n: dict[str, Any], user: User
) -> None:
    _reset_auto_delete_timeout(callback.message)
    reminder_id = int(callback.data.split("edit_edit_")[1])
    reminder = await reminder_dao.get_by_id(reminder_id)
    
    if not reminder:
        return await callback.answer("Not found", show_alert=True)
        
    await state.set_state(ReminderWizard.choosing_time)
    await state.update_data(
        edit_reminder_id=reminder.id,
        text=reminder.reminder_text,
    )
    
    await callback.message.edit_text(
        l10n["ask_time"].format(text=reminder.reminder_text),
        reply_markup=get_time_selection_keyboard(user.timezone, l10n)
    )


@router.callback_query(F.data.startswith("edit_toggle_repeat_"))
async def callback_edit_repeat(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, l10n: dict[str, Any]
) -> None:
    _reset_auto_delete_timeout(callback.message)
    reminder_id = int(callback.data.split("edit_toggle_repeat_")[1])
    reminder = await reminder_dao.get_by_id(reminder_id)
    if not reminder:
        return await callback.answer("Not found", show_alert=True)

    options = {
        l10n["repeat_none"]: (False, None),
        l10n["repeat_day"]: (True, "FREQ=DAILY"),
        l10n["repeat_weekdays"]: (True, "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        l10n["repeat_week"]: (True, "FREQ=WEEKLY")
    }
    
    # Reverse lookup current state
    current_key = l10n["repeat_none"]
    for k, v in options.items():
        if reminder.is_recurring and reminder.rrule_string == v[1]:
            current_key = k
            break

    keys = list(options.keys())
    next_idx = (keys.index(current_key) + 1) % len(keys)
    next_key = keys[next_idx]
    is_rec, rrule = options[next_key]

    reminder.is_recurring = is_rec
    reminder.rrule_string = rrule
    # Uses session.flush(), middleware will commit
    
    keyboard = get_edit_keyboard(
        reminder.id, l10n, is_rec, reminder.is_nagging, next_key
    )
    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(F.data.startswith("edit_toggle_nagging_"))
async def callback_edit_nagging(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, l10n: dict[str, Any]
) -> None:
    _reset_auto_delete_timeout(callback.message)
    reminder_id = int(callback.data.split("edit_toggle_nagging_")[1])
    reminder = await reminder_dao.get_by_id(reminder_id)
    if not reminder:
        return await callback.answer("Not found", show_alert=True)

    # Toggle DB
    reminder.is_nagging = not reminder.is_nagging
    
    # Re-schedule to pick up the nagging flag
    scheduler_service.schedule_reminder(
        reminder.id, reminder.execution_time, is_nagging=reminder.is_nagging
    )
    
    # Determine rrule_text for the keyboard update (reverse lookup)
    rrule_text = l10n["repeat_none"]
    if reminder.is_recurring and reminder.rrule_string:
        if "DAILY" in reminder.rrule_string:
            rrule_text = l10n["repeat_day"]
        elif "BYDAY" in reminder.rrule_string:
            rrule_text = l10n["repeat_weekdays"]
        elif "WEEKLY" in reminder.rrule_string:
            rrule_text = l10n["repeat_week"]

    keyboard = get_edit_keyboard(
        reminder.id, l10n, reminder.is_recurring, reminder.is_nagging, rrule_text
    )
    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(F.data.startswith("edit_delete_"))
async def callback_edit_delete(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, l10n: dict[str, Any]
) -> None:
    _reset_auto_delete_timeout(callback.message)
    reminder_id = int(callback.data.split("edit_delete_")[1])
    
    await reminder_dao.delete_by_id(reminder_id)
    scheduler_service.remove_reminder_job(reminder_id)

    await callback.message.edit_text(l10n["task_deleted"], reply_markup=None)


# ------------------------------------------------------------------ #
#  Task list actions                                                  #
# ------------------------------------------------------------------ #


@router.callback_query(F.data.startswith("del_task_"))
async def callback_delete_task(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    l10n: dict[str, Any]
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
        await callback.message.edit_text(
            l10n["no_tasks"], reply_markup=None
        )
        return

    text_lines = [l10n["tasks_header"]]
    for task in tasks:
        dt_str = task.execution_time.strftime("%d.%m %H:%M")
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


@router.callback_query(F.data.startswith("done_task_"))
async def callback_task_done(
    callback: CallbackQuery, 
    reminder_dao: ReminderDAO, 
    scheduler_service: SchedulerService, 
    l10n: dict[str, Any]
) -> None:
    reminder_id = int(callback.data.split("done_task_")[1])
    
    # Soft delete
    await reminder_dao.mark_done(reminder_id)
    
    # Clean up jobs
    scheduler_service.remove_nagging_job(reminder_id)

    await callback.message.edit_text(
        f"{callback.message.text}\n\n{l10n['task_done_reply']}",
        parse_mode="Markdown",
    )
    await callback.answer(l10n["btn_done"])


# ------------------------------------------------------------------ #
#  Snooze actions                                                     #
# ------------------------------------------------------------------ #

@router.callback_query(F.data.startswith("snooze_show_"))
async def callback_snooze_show(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    reminder_id = int(callback.data.split("snooze_show_")[1])
    keyboard = get_snooze_keyboard(reminder_id, l10n)
    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(F.data.startswith("snooze_act_"))
async def callback_snooze_act(
    callback: CallbackQuery,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
    state: FSMContext,
    user: User,
    l10n: dict[str, Any],
) -> None:
    parts = callback.data.split("_")
    reminder_id = int(parts[2])
    action = parts[3]

    reminder = await reminder_dao.get_by_id(reminder_id)
    if not reminder:
        return await callback.answer("Task not found.", show_alert=True)

    # If user selected custom time, pivot into the edit FSM flow
    if action == "custom":
        await state.set_state(ReminderWizard.choosing_time)
        await state.update_data(
            edit_reminder_id=reminder.id,
            text=reminder.reminder_text,
        )
        await callback.message.edit_text(
            l10n["ask_time"].format(text=reminder.reminder_text),
            reply_markup=get_time_selection_keyboard(user.timezone, l10n)
        )
        return

    # Process time delta / fixed time
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
        new_time = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        # If the target hour has already passed today, rollover to tomorrow
        if new_time <= now:
            new_time += timedelta(days=1)

    # Discard timezone info if saving as naive (or handle properly depending on model setup)
    if new_time.tzinfo:
        new_time = new_time.replace(tzinfo=None)

    # 1. Update DB
    reminder.execution_time = new_time
    # 2. Reschedule
    scheduler_service.schedule_reminder(reminder.id, new_time, is_nagging=reminder.is_nagging)

    # 3. UI
    friendly_time = new_time.strftime("%d.%m %H:%M")
    await callback.message.edit_text(
        f"{callback.message.text}\n\n{l10n['snoozed_until'].format(time=friendly_time)}",
        reply_markup=None,
        parse_mode="Markdown"
    )
    await callback.answer("Snoozed!")
