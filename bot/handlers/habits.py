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

# The static predefined habits in English, as requested.
_HABIT_TEXTS = {
    "workout": "🧘 Workout",
    "water":   "💧 Drink water",
    "rest":    "🛌 Rest",
}

class HabitState(StatesGroup):
    waiting_for_name = State()
    waiting_for_time = State()

def get_habits_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💧 Water", callback_data="habit_preset_water"),
        InlineKeyboardButton(text="🧘 Workout", callback_data="habit_preset_workout")
    )
    builder.row(
        InlineKeyboardButton(text="🛌 Rest", callback_data="habit_preset_rest"),
        InlineKeyboardButton(text="➕ Custom Habit", callback_data="habit_custom")
    )
    builder.row(InlineKeyboardButton(text="📋 My Active Habits", callback_data="habit_list"))
    builder.row(InlineKeyboardButton(text="❌ Cancel", callback_data="habit_cancel"))
    return builder

@router.message(F.text.in_(["🫧 Привычки", "🫧 Habits"]))
async def btn_habits(message: Message, state: FSMContext) -> None:
    """Show the habits dashboard."""
    await state.clear()
    await message.answer("🫧 **Habits Dashboard**:\nChoose a preset or create your own custom daily habit.", 
                         reply_markup=get_habits_keyboard().as_markup(),
                         parse_mode="Markdown")

@router.callback_query(F.data == "habit_cancel")
async def cb_habit_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.delete()
    await callback.answer("Cancelled.")

@router.callback_query(F.data.startswith("habit_preset_"))
async def cb_habit_preset(callback: CallbackQuery, state: FSMContext) -> None:
    preset_key = callback.data.split("_")[-1]
    text_en = _HABIT_TEXTS.get(preset_key, "❓ Habit")
    
    await state.update_data(habit_text=text_en)
    await state.set_state(HabitState.waiting_for_time)
    
    await callback.message.edit_text(
        f"You selected: **{text_en}**\n\nWhat time every day should I remind you? (e.g. '10:00' or 'at 8pm')",
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data == "habit_custom")
async def cb_habit_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(HabitState.waiting_for_name)
    await callback.message.edit_text(
        "What custom habit do you want to build?\n*(e.g. 'Read 20 pages' or 'Study Python')*",
        parse_mode="Markdown"
    )
    await callback.answer()

@router.message(HabitState.waiting_for_name)
async def state_habit_name(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    await state.update_data(habit_text=message.text.strip())
    await state.set_state(HabitState.waiting_for_time)
    await message.answer("Great! What time every day should I remind you? (e.g. '10:00' or 'at 8pm')")

@router.message(HabitState.waiting_for_time)
async def state_habit_time(
    message: Message, 
    state: FSMContext, 
    user: User, 
    reminder_dao: ReminderDAO, 
    scheduler_service: SchedulerService
) -> None:
    if not message.text:
        return
        
    data = await state.get_data()
    habit_text = data.get("habit_text", "My Habit")
    
    parser = InputParser()
    result = await parser.parse(message.text, user.timezone)
    
    if not result.parsed_datetime:
        await message.answer("❌ I couldn't understand the time. Please try again (e.g. '10:00' or 'at 8pm'):")
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
        )
        scheduler_service.schedule_reminder(reminder.id, execution_time_utc)
        
        time_str = format_time(execution_time_utc, user.timezone, user.show_utc_offset, "%H:%M")
        await message.answer(
            f"✅ **Daily Habit Created!**\nI will remind you to **{habit_text}** every day at `{time_str}`.", 
            parse_mode="Markdown"
        )
        await state.clear()
        
    except ValueError as ve:
        logger.error("Validation error: %s", ve)
        await message.answer("❌ Failed to create habit (text too long).")
    except Exception as e:
        logger.error("Error creating habit: %s", e, exc_info=True)
        await message.answer("❌ Internal error creating habit.")
        
@router.callback_query(F.data == "habit_list")
async def cb_habit_list(callback: CallbackQuery, user: User, reminder_dao: ReminderDAO) -> None:
    reminders = await reminder_dao.get_user_reminders(user.id)
    # Filter for active recurring tasks
    habits = [r for r in reminders if r.is_recurring and r.status == "pending"]
    
    if not habits:
        await callback.message.edit_text(
            "📋 You have no active daily habits. Click '➕ Custom Habit' or choose a preset to create one!",
            reply_markup=get_habits_keyboard().as_markup()
        )
        await callback.answer()
        return
        
    # Build an inline keyboard with delete buttons for each habit
    builder = InlineKeyboardBuilder()
    text_lines = ["📋 **Your Active Daily Habits:**\n"]
    
    for i, h in enumerate(habits, start=1):
        time_str = format_time(h.execution_time, user.timezone, user.show_utc_offset, "%H:%M")
        text_lines.append(f"{i}. **{h.reminder_text}** (Daily at {time_str})")
        # Add deletion button
        builder.row(InlineKeyboardButton(text=f"❌ Delete {i}", callback_data=f"del_habit_{h.id}"))
    
    builder.row(InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="habit_back_dash"))
    
    await callback.message.edit_text(
        "\n".join(text_lines), 
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data == "habit_back_dash")
async def cb_habit_back_dash(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "🫧 **Habits Dashboard**:\nChoose a preset or create your own custom daily habit.", 
        reply_markup=get_habits_keyboard().as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("del_habit_"))
async def cb_del_habit(callback: CallbackQuery, reminder_dao: ReminderDAO, scheduler_service: SchedulerService) -> None:
    task_id = int(callback.data.split("_")[-1])
    try:
        await reminder_dao.delete_by_id(task_id)
        # Attempt to remove from scheduler if it exists. If it already fired or is missing, ignore.
        try:
            scheduler_service.scheduler.remove_job(str(task_id))
        except Exception:
            pass
            
        await callback.answer("✅ Habit successfully deleted!", show_alert=True)
        await callback.message.delete()
        await callback.message.answer(
            "Habit deleted. Click 🫧 Habits to view your updated dashboard.", 
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error("Error deleting habit %s: %s", task_id, e)
        await callback.answer("❌ Error deleting habit.", show_alert=True)
