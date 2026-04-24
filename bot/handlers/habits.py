"""Habits handler — true daily custom habits builder."""

import logging
from typing import Any

import pytz
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.database.models import User
from bot.database.dao.reminder import ReminderDAO
from bot.services.scheduler import SchedulerService
from bot.services.parser import InputParser
from bot.utils.time_ext import format_time

router = Router(name="habits")
logger = logging.getLogger(__name__)

class HabitState(StatesGroup):
    waiting_for_name = State()
    waiting_for_time = State()

def _preset_habit_texts(l10n: dict[str, Any]) -> dict[str, str]:
    return {
        "workout": l10n["habit_preset_workout"],
        "water": l10n["habit_preset_water"],
        "rest": l10n["habit_preset_rest"],
    }


def get_habits_keyboard(l10n: dict[str, Any]) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n["habit_preset_water"], callback_data="habit_preset_water"),
        InlineKeyboardButton(text=l10n["habit_preset_workout"], callback_data="habit_preset_workout")
    )
    builder.row(
        InlineKeyboardButton(text=l10n["habit_preset_rest"], callback_data="habit_preset_rest"),
        InlineKeyboardButton(text=l10n["habit_btn_custom"], callback_data="habit_custom")
    )
    builder.row(InlineKeyboardButton(text=l10n["habit_btn_list"], callback_data="habit_list"))
    builder.row(InlineKeyboardButton(text=l10n["habit_btn_cancel"], callback_data="habit_cancel"))
    return builder

@router.message(F.text.in_(["🫧 Привычки", "🫧 Habits"]))
async def btn_habits(
    message: Message,
    state: FSMContext,
    l10n: dict[str, Any],
    reminder_dao: ReminderDAO,
    user: User,
) -> None:
    """Show the habits dashboard."""
    await state.clear()
    stats = await reminder_dao.get_habit_motivation_stats(user.id, user.timezone, days=7)
    motivation = l10n.get("habit_motivation", "").format(
        weekly_done=stats.get("weekly_done", 0),
        active_count=stats.get("active_count", 0),
    )
    text = l10n["habits_dashboard"]
    if motivation.strip():
        text = f"{text}\n\n{motivation}"
    await message.answer(
        text,
        reply_markup=get_habits_keyboard(l10n).as_markup(),
        parse_mode="Markdown",
    )

@router.callback_query(F.data == "habit_cancel")
async def cb_habit_cancel(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.clear()
    await callback.message.delete()
    await callback.answer(l10n["habit_cancelled"])

@router.callback_query(F.data.startswith("habit_preset_"))
async def cb_habit_preset(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    preset_key = callback.data.split("_")[-1]
    habit_text = _preset_habit_texts(l10n).get(preset_key, l10n["habit_unknown"])
    
    await state.update_data(habit_text=habit_text)
    await state.set_state(HabitState.waiting_for_time)
    
    await callback.message.edit_text(
        l10n["habit_selected_prompt"].format(habit=habit_text),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data == "habit_custom")
async def cb_habit_custom(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.set_state(HabitState.waiting_for_name)
    await callback.message.edit_text(
        l10n["habit_custom_prompt"],
        parse_mode="Markdown"
    )
    await callback.answer()

@router.message(HabitState.waiting_for_name)
async def state_habit_name(message: Message, state: FSMContext, l10n: dict[str, Any]) -> None:
    if not message.text:
        return
    await state.update_data(habit_text=message.text.strip())
    await state.set_state(HabitState.waiting_for_time)
    await message.answer(l10n["habit_time_prompt"])

@router.message(HabitState.waiting_for_time)
async def state_habit_time(
    message: Message, 
    state: FSMContext, 
    user: User, 
    reminder_dao: ReminderDAO, 
    scheduler_service: SchedulerService,
    l10n: dict[str, Any],
) -> None:
    if not message.text:
        return
        
    data = await state.get_data()
    habit_text = data.get("habit_text", l10n["habit_default_name"])
    
    parser = InputParser()
    result = await parser.parse(message.text, user.timezone)
    
    if not result.parsed_datetime:
        await message.answer(l10n["habit_time_retry"])
        return
        
    # Convert local execution time to UTC before storing in database!
    execution_time_utc = result.parsed_datetime.astimezone(pytz.UTC)
    
    # Schedule it as a strict daily recurring task with nagging enabled
    try:
        reminder = await reminder_dao.create_reminder(
            user_id=user.id,
            text=habit_text,
            execution_time=execution_time_utc,
            is_recurring=True,
            rrule_string="FREQ=DAILY",
            is_nagging=True,
            nagging_max_repeats=3,
        )
        scheduler_service.schedule_reminder(
            reminder.id,
            execution_time_utc,
            is_nagging=reminder.is_nagging,
        )
        
        time_str = format_time(execution_time_utc, user.timezone, user.show_utc_offset, "%H:%M")
        await message.answer(
            l10n["habit_created"].format(habit=habit_text, time=time_str),
            parse_mode="Markdown"
        )
        await state.clear()
        
    except ValueError as ve:
        logger.error("Validation error: %s", ve)
        await message.answer(l10n["habit_create_failed_long"])
    except Exception as e:
        logger.error("Error creating habit: %s", e, exc_info=True)
        # Keep DB and scheduler state consistent if scheduling fails mid-flow.
        await reminder_dao.session.rollback()
        await message.answer(l10n["habit_create_failed_internal"])
        
@router.callback_query(F.data == "habit_list")
async def cb_habit_list(
    callback: CallbackQuery, user: User, reminder_dao: ReminderDAO, l10n: dict[str, Any]
) -> None:
    reminders = await reminder_dao.get_user_reminders(user.id)
    # Filter for active recurring tasks
    habits = [r for r in reminders if r.is_recurring and r.status == "pending"]
    
    if not habits:
        await callback.message.edit_text(
            l10n["habit_no_active"],
            reply_markup=get_habits_keyboard(l10n).as_markup(),
            parse_mode="Markdown",
        )
        await callback.answer()
        return
        
    # Build an inline keyboard with delete buttons for each habit
    builder = InlineKeyboardBuilder()
    text_lines = [l10n["habit_list_header"]]
    
    for i, h in enumerate(habits, start=1):
        time_str = format_time(h.execution_time, user.timezone, user.show_utc_offset, "%H:%M")
        text_lines.append(l10n["habit_list_item"].format(index=i, habit=h.reminder_text, time=time_str))
        # Add deletion button
        builder.row(
            InlineKeyboardButton(
                text=l10n.get("btn_task_settings", "⚙️"),
                callback_data=f"task_settings_{h.id}",
            ),
            InlineKeyboardButton(
                text=l10n["habit_btn_delete_n"].format(index=i),
                callback_data=f"del_habit_{h.id}",
            )
        )
    
    builder.row(InlineKeyboardButton(text=l10n["habit_btn_back_dashboard"], callback_data="habit_back_dash"))
    
    await callback.message.edit_text(
        "\n".join(text_lines), 
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data == "habit_back_dash")
async def cb_habit_back_dash(
    callback: CallbackQuery,
    state: FSMContext,
    l10n: dict[str, Any],
    reminder_dao: ReminderDAO,
    user: User,
) -> None:
    await state.clear()
    stats = await reminder_dao.get_habit_motivation_stats(user.id, user.timezone, days=7)
    motivation = l10n.get("habit_motivation", "").format(
        weekly_done=stats.get("weekly_done", 0),
        active_count=stats.get("active_count", 0),
    )
    text = l10n["habits_dashboard"]
    if motivation.strip():
        text = f"{text}\n\n{motivation}"
    await callback.message.edit_text(
        text,
        reply_markup=get_habits_keyboard(l10n).as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("del_habit_"))
async def cb_del_habit(
    callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService, l10n: dict[str, Any]
) -> None:
    task_id = int(callback.data.split("_")[-1])
    try:
        await reminder_dao.delete_by_id(task_id)
        scheduler_service.remove_reminder_job(task_id)
            
        await callback.answer(l10n["habit_deleted_alert"], show_alert=True)
        await callback.message.delete()
        await callback.message.answer(
            l10n["habit_deleted_followup"],
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error("Error deleting habit %s: %s", task_id, e)
        await callback.answer(l10n["habit_delete_error_alert"], show_alert=True)
