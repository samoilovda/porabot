"""Habits handler — one-tap shortcuts for common recurring tasks."""

import logging
from datetime import datetime, timedelta

import pytz
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.models import User
from bot.database.dao.reminder import ReminderDAO
from bot.services.scheduler import SchedulerService
from bot.utils.time_ext import format_time

router = Router(name="habits")
logger = logging.getLogger(__name__)

_HABIT_TEXTS = {
    "water":   "💧 Выпить стакан воды",
    "vit":     "💊 Принять витамины",
    "stretch": "🧘 Размять спину",
}


@router.message(F.text.in_(["🫧 Привычки", "🫧 Habits"]))
async def btn_habits(message: Message) -> None:
    """Show one-tap habit shortcuts."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💧 Выпить воду (+1ч)",      callback_data="habit_water_1"))
    builder.row(InlineKeyboardButton(text="💊 Принять витамины (+2ч)", callback_data="habit_vit_2"))
    builder.row(InlineKeyboardButton(text="🧘 Размять спину (+3ч)",    callback_data="habit_stretch_3"))
    builder.row(InlineKeyboardButton(text="❌ Отмена",                  callback_data="habit_cancel"))
    await message.answer("🫧 Выберите быструю привычку:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("habit_"))
async def callback_habit_create(
    callback: CallbackQuery,
    user: User,
    reminder_dao: ReminderDAO,
    scheduler_service: SchedulerService,
) -> None:
    """Create a habit reminder from a one-tap button."""
    if callback.data == "habit_cancel":
        return await callback.message.delete()

    parts = callback.data.split("_")
    name = parts[1]
    hours = int(parts[2])
    text = _HABIT_TEXTS.get(name, "Привычка")

    try:
        user_tz = pytz.timezone(user.timezone)
    except Exception:
        user_tz = pytz.UTC

    # Keep timezone-aware datetime — APScheduler handles tz-aware run_date correctly.
    execution_time = datetime.now(user_tz) + timedelta(hours=hours)

    try:
        reminder = await reminder_dao.create_reminder(
            user_id=user.id,
            text=text,
            execution_time=execution_time,
            is_recurring=False,
            is_nagging=False,
        )
        scheduler_service.schedule_reminder(reminder.id, execution_time)
        time_str = format_time(execution_time, user.timezone, user.show_utc_offset, "%H:%M")
        await callback.message.edit_text(
            f"✅ Привычка добавлена!\nНапомню в `{time_str}`: {text}",
            parse_mode="Markdown",
            reply_markup=None,
        )
        await callback.answer("Привычка создана!")
    except ValueError as ve:
        logger.error("Validation error creating habit for user %s: %s", user.id, ve)
        await callback.answer("❌ Ошибка создания привычки", show_alert=True)
    except Exception as e:
        logger.error("Error creating habit for user %s: %s", user.id, e, exc_info=True)
        await callback.answer("❌ Ошибка создания привычки", show_alert=True)
