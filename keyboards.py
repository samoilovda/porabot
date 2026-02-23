from datetime import datetime
import pytz
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# --- Reply Keyboards (Main Menu) ---

def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="➕ Новая задача"),
        KeyboardButton(text="📅 Мои задачи")
    )
    builder.row(
        KeyboardButton(text="⚙️ Настройки")
    )
    return builder.as_markup(resize_keyboard=True)


# --- Inline Keyboards ---

def get_time_selection_keyboard(user_timezone: str) -> InlineKeyboardMarkup:
    """
    Клавиатура для выбора времени напоминания.
    Логика: если фиксированное время (9, 14, 19, 23) сегодня прошло -> ставим на завтра.
    """
    builder = InlineKeyboardBuilder()
    
    # Ряд 1: Дельты (через X)
    builder.row(
        InlineKeyboardButton(text="+15м", callback_data="time_delta_15"),
        InlineKeyboardButton(text="+30м", callback_data="time_delta_30"),
        InlineKeyboardButton(text="+1ч", callback_data="time_delta_60"),
        InlineKeyboardButton(text="+2ч", callback_data="time_delta_120"),
        InlineKeyboardButton(text="+3ч", callback_data="time_delta_180"),
    )
    
    # Ряд 2: Время суток
    try:
        tz = pytz.timezone(user_timezone)
    except Exception:
        tz = pytz.UTC
        
    now = datetime.now(tz)
    
    times = [
        ("🌅 Утро", 9),
        ("☀️ День", 14),
        ("🌙 Вечер", 19),
        ("🌌 Ночь", 23),
    ]
    
    buttons = []
    for label, hour in times:
        # Создаем datetime на сегодня
        target_time = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        
        # Если время уже прошло, переносим на завтра
        if target_time <= now:
             from datetime import timedelta
             target_time += timedelta(days=1)
             
        # Форматируем для callback_data (isoformat)
        callback_val = target_time.isoformat()
        
        buttons.append(
            InlineKeyboardButton(text=f"{label} ({target_time.strftime('%H:%M')})", callback_data=f"time_fixed_{callback_val}")
        )
    
    # Разбиваем кнопки времени суток на 2 ряда по 2
    builder.row(*buttons[:2])
    builder.row(*buttons[2:])

    # Ряд 3: Другое
    builder.row(
        InlineKeyboardButton(text="🗓 Завтра (09:00)", callback_data="time_tomorrow"),
        InlineKeyboardButton(text="⌨️ Ввести вручную", callback_data="time_manual"),
    )
    
    return builder.as_markup()


def get_confirmation_keyboard(is_recurring: bool = False, is_nagging: bool = False, rrule_text: str = "Нет") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    # Кнопка повтора
    builder.row(
        InlineKeyboardButton(text=f"🔁 Повтор: {rrule_text}", callback_data="conf_toggle_repeat")
    )
    
    # Кнопка назойливости
    nagging_status = "ВКЛ" if is_nagging else "ВЫКЛ"
    nagging_icon = "🔥" if is_nagging else "❄️"
    builder.row(
        InlineKeyboardButton(text=f"{nagging_icon} Зуд (Nagging): {nagging_status}", callback_data="conf_toggle_nagging")
    )
    
    # Кнопка сохранения
    builder.row(
        InlineKeyboardButton(text="🚀 ПОРА! (Сохранить)", callback_data="conf_save")
    )
    
    # Кнопка отмены
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="conf_cancel")
    )
    
    return builder.as_markup()


def get_timezone_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    zones = [
        ("Europe/Moscow", "Москва"),
        ("Europe/Kiev", "Киев"),
        ("Europe/Minsk", "Минск"),
        ("Asia/Almaty", "Алматы"),
        ("Asia/Tashkent", "Ташкент"),
        ("Asia/Yekaterinburg", "Екатеринбург"),
        ("UTC", "UTC"),
    ]
    
    for tz, label in zones:
        builder.row(InlineKeyboardButton(text=label, callback_data=f"set_tz_{tz}"))
        
    builder.row(InlineKeyboardButton(text="⌨️ Ввести город вручную", callback_data="set_tz_manual"))
    return builder.as_markup()


def get_task_done_keyboard(reminder_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сделано", callback_data=f"done_task_{reminder_id}")]
    ])


def get_tasks_list_keyboard(tasks) -> InlineKeyboardMarkup:
    """
    Клавиатура для списка задач с кнопками удаления.
    tasks: список объектов Reminder (или кортежей с id и text)
    """
    builder = InlineKeyboardBuilder()
    
    for task in tasks:
        # Ограничиваем длину текста на кнопке
        text_preview = task.reminder_text[:20] + "..." if len(task.reminder_text) > 20 else task.reminder_text
        builder.row(
            InlineKeyboardButton(text=f"🗑 Удалить: {text_preview}", callback_data=f"del_task_{task.id}")
        )
        
    # Кнопка обновить/закрыть?
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_tasks"),
        InlineKeyboardButton(text="❌ Закрыть", callback_data="close_tasks")
    )
    
    return builder.as_markup()


def get_settings_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🌍 Сменить часовой пояс", callback_data="settings_change_tz"))
    return builder.as_markup()
