from typing import Any
from datetime import datetime, timedelta

import pytz
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.utils.time_ext import format_time


def get_time_selection_keyboard(user_timezone: str, l10n: dict[str, Any], show_utc_offset: bool = False) -> InlineKeyboardMarkup:
    """
    Keyboard for choosing reminder time.
    If a fixed time-of-day has passed today, it rolls over to tomorrow.
    """
    builder = InlineKeyboardBuilder()

    # Row 1: deltas
    builder.row(
        InlineKeyboardButton(text="+15м", callback_data="time_delta_15"),
        InlineKeyboardButton(text="+30м", callback_data="time_delta_30"),
        InlineKeyboardButton(text="+1ч", callback_data="time_delta_60"),
        InlineKeyboardButton(text="+2ч", callback_data="time_delta_120"),
        InlineKeyboardButton(text="+3ч", callback_data="time_delta_180"),
    )

    # Row 2-3: time-of-day slots
    try:
        tz = pytz.timezone(user_timezone)
    except Exception:
        tz = pytz.UTC

    now = datetime.now(tz)

    times = [
        (l10n["time_morning"], 9),
        (l10n["time_day"], 14),
        (l10n["time_evening"], 19),
        (l10n["time_night"], 23),
    ]

    buttons = []
    for label, hour in times:
        target_time = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target_time <= now:
            target_time += timedelta(days=1)

        callback_val = target_time.isoformat()
        time_str = format_time(target_time, user_timezone, show_utc_offset, "%H:%M")
        buttons.append(
            InlineKeyboardButton(
                text=f"{label} ({time_str})",
                callback_data=f"time_fixed_{callback_val}",
            )
        )

    builder.row(*buttons[:2])
    builder.row(*buttons[2:])

    # Row 4: other options
    builder.row(
        InlineKeyboardButton(text=l10n["time_tomorrow"], callback_data="time_tomorrow"),
        InlineKeyboardButton(text=l10n["time_manual"], callback_data="time_manual"),
    )

    return builder.as_markup()


def get_edit_keyboard(
    reminder_id: int,
    l10n: dict[str, Any],
    is_recurring: bool = False,
    is_nagging: bool = False,
    rrule_text: str = "Нет",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=f"{l10n['btn_repeat_prefix']} {rrule_text}", callback_data=f"edit_toggle_repeat_{reminder_id}"
        )
    )

    nagging_status = l10n["status_on"] if is_nagging else l10n["status_off"]
    nagging_icon = "🔥" if is_nagging else "❄️"
    builder.row(
        InlineKeyboardButton(
            text=l10n["btn_nagging_prefix"].format(icon=nagging_icon) + f" {nagging_status}",
            callback_data=f"edit_toggle_nagging_{reminder_id}",
        )
    )

    builder.row(
        InlineKeyboardButton(text=l10n["btn_delete"], callback_data=f"edit_delete_{reminder_id}")
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
    builder.row(
        InlineKeyboardButton(
            text="⌨️ Ввести город вручную", callback_data="set_tz_manual"
        )
    )
    return builder.as_markup()


def get_task_done_keyboard(reminder_id: int, l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    # Row 1: Primary action
    builder.row(
        InlineKeyboardButton(
            text=l10n["btn_done"], callback_data=f"done_task_{reminder_id}"
        )
    )
    
    # Row 2: Short intervals
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_15m"], callback_data=f"snooze_act_{reminder_id}_15m"),
        InlineKeyboardButton(text=l10n["snooze_30m"], callback_data=f"snooze_act_{reminder_id}_30m"),
        InlineKeyboardButton(text=l10n["snooze_1h"], callback_data=f"snooze_act_{reminder_id}_1h"),
        InlineKeyboardButton(text=l10n["snooze_2h"], callback_data=f"snooze_act_{reminder_id}_2h"),
    )
    
    # Row 3: Time of day (Emojis only in scenery style)
    # 🌅=Morning, 🏙️=Day, 🌇=Evening, 🌃=Night
    builder.row(
        InlineKeyboardButton(text="🌅", callback_data=f"snooze_act_{reminder_id}_morning"),
        InlineKeyboardButton(text="🏙️", callback_data=f"snooze_act_{reminder_id}_day"),
        InlineKeyboardButton(text="🌇", callback_data=f"snooze_act_{reminder_id}_evening"),
        InlineKeyboardButton(text="🌃", callback_data=f"snooze_act_{reminder_id}_night"),
    )
    
    # Row 4: Long intervals and custom
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_1d"], callback_data=f"snooze_act_{reminder_id}_1d"),
        InlineKeyboardButton(text=l10n["snooze_custom"], callback_data=f"snooze_act_{reminder_id}_custom"),
    )
    
    return builder.as_markup()


def get_snooze_keyboard(reminder_id: int, l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    # Row 1: 15m, 30m, 1h, 2h
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_15m"], callback_data=f"snooze_act_{reminder_id}_15m"),
        InlineKeyboardButton(text=l10n["snooze_30m"], callback_data=f"snooze_act_{reminder_id}_30m"),
        InlineKeyboardButton(text=l10n["snooze_1h"], callback_data=f"snooze_act_{reminder_id}_1h"),
        InlineKeyboardButton(text=l10n["snooze_2h"], callback_data=f"snooze_act_{reminder_id}_2h"),
    )
    
    # Row 2: morning, day, evening, night
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_morning"], callback_data=f"snooze_act_{reminder_id}_morning"),
        InlineKeyboardButton(text=l10n["snooze_day"], callback_data=f"snooze_act_{reminder_id}_day"),
    )
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_evening"], callback_data=f"snooze_act_{reminder_id}_evening"),
        InlineKeyboardButton(text=l10n["snooze_night"], callback_data=f"snooze_act_{reminder_id}_night"),
    )
    
    # Row 3: 1 day, custom
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_1d"], callback_data=f"snooze_act_{reminder_id}_1d"),
        InlineKeyboardButton(text=l10n["snooze_custom"], callback_data=f"snooze_act_{reminder_id}_custom"),
    )
    
    return builder.as_markup()


def get_tasks_list_keyboard(tasks, l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Keyboard for task list: Done! + Delete per task, Refresh/Close/Completed controls."""
    builder = InlineKeyboardBuilder()
    for task in tasks:
        text_preview = (
            task.reminder_text[:18] + "…"
            if len(task.reminder_text) > 18
            else task.reminder_text
        )
        builder.row(
            InlineKeyboardButton(
                text=f"{l10n['btn_done_task_prefix']} {text_preview}",
                callback_data=f"done_task_{task.id}",
            ),
            InlineKeyboardButton(
                text=l10n["btn_delete"],
                callback_data=f"del_task_{task.id}",
            ),
        )
    builder.row(
        InlineKeyboardButton(text=l10n["btn_refresh"], callback_data="refresh_tasks"),
        InlineKeyboardButton(text=l10n["btn_completed_tasks"], callback_data="show_completed"),
        InlineKeyboardButton(text=l10n["btn_close"], callback_data="close_tasks"),
    )
    return builder.as_markup()


def get_completed_tasks_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Simple close keyboard for the completed tasks view."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n["btn_close"], callback_data="close_tasks"),
    )
    return builder.as_markup()


def get_settings_keyboard(l10n: dict[str, Any], show_utc_offset: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=l10n["btn_change_tz"], callback_data="settings_change_tz"
        ),
        InlineKeyboardButton(
            text=l10n["btn_change_lang"], callback_data="settings_change_lang"
        ),
    )
    
    utc_btn_text = l10n["btn_toggle_utc_on"] if show_utc_offset else l10n["btn_toggle_utc_off"]
    builder.row(
        InlineKeyboardButton(
            text=utc_btn_text, callback_data="settings_toggle_utc"
        )
    )
    return builder.as_markup()

def get_language_selection_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n["lang_ru"], callback_data="set_lang_ru"),
        InlineKeyboardButton(text=l10n["lang_en"], callback_data="set_lang_en"),
    )
    return builder.as_markup()
