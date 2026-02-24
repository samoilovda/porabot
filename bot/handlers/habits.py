from typing import Any
from datetime import datetime, timedelta
import pytz
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.models import User
from bot.database.dao.reminder import ReminderDAO
from bot.services.scheduler import SchedulerService

router = Router(name="habits")

@router.message(F.text.in_(["🫧 Привычки", "🫧 Habits"]))
async def btn_habits(message: Message) -> None:
    """Show shortcuts for common tasks (1 click creation)."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💧 Выпить воду (+1ч)",     callback_data="habit_water_1"))
    builder.row(InlineKeyboardButton(text="💊 Принять витамины (+2ч)", callback_data="habit_vit_2"))
    builder.row(InlineKeyboardButton(text="🧘 Размять спину (+3ч)",   callback_data="habit_stretch_3"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="habit_cancel"))
    
    await message.answer("🫧 Выберите быструю привычку:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("habit_"))
async def callback_habit_create(
    callback: CallbackQuery,
    user: User,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService
) -> None:
    if callback.data == "habit_cancel":
        return await callback.message.delete()

    # Parse callback (format: habit_{name}_{hours})
    parts = callback.data.split("_")
    name = parts[1]
    hours = int(parts[2])
    
    habit_texts = {
        "water": "💧 Выпить стакан воды",
        "vit": "💊 Принять витамины",
        "stretch": "🧘 Размять спину"
    }
    text = habit_texts.get(name, "Привычка")
    
    try:
        user_tz = pytz.timezone(user.timezone)
    except Exception:
        user_tz = pytz.UTC
        
    execution_time = datetime.now(user_tz) + timedelta(hours=hours)
    naive_time = execution_time.replace(tzinfo=None)
    
    # Save directly to DB, skipping FSM
    reminder = await reminder_dao.create_reminder(
        user_id=user.id,
        text=text,
        execution_time=naive_time,
        is_recurring=False,
        is_nagging=False
    )
    
    # Schedule
    scheduler_service.schedule_reminder(reminder.id, naive_time)
    
    time_str = execution_time.strftime('%H:%M')
    await callback.message.edit_text(
        f"✅ Привычка добавлена!\nНапомню в `{time_str}`: {text}",
        parse_mode="Markdown",
        reply_markup=None
    )
    await callback.answer("Привычка создана!")
