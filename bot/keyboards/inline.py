from typing import Any
from datetime import datetime, timedelta

import pytz
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_time_selection_keyboard(user_timezone: str, l10n: dict[str, Any]) -> InlineKeyboardMarkup:
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
        buttons.append(
            InlineKeyboardButton(
                text=f"{label} ({target_time.strftime('%H:%M')})",
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=l10n["btn_done"], callback_data=f"done_task_{reminder_id}"
                )
            ]
        ]
    )


def get_tasks_list_keyboard(tasks, l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Keyboard for task list with per-task delete buttons."""
    builder = InlineKeyboardBuilder()
    for task in tasks:
        text_preview = (
            task.reminder_text[:20] + "..."
            if len(task.reminder_text) > 20
            else task.reminder_text
        )
        builder.row(
            InlineKeyboardButton(
                text=f"{l10n['btn_delete_prefix']} {text_preview}",
                callback_data=f"del_task_{task.id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text=l10n["btn_refresh"], callback_data="refresh_tasks"),
        InlineKeyboardButton(text=l10n["btn_close"], callback_data="close_tasks"),
    )
    return builder.as_markup()

def get_settings_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=l10n["btn_change_tz"], callback_data="settings_change_tz"
        ),
        InlineKeyboardButton(
            text=l10n["btn_change_lang"], callback_data="settings_change_lang"
        ),
    )
    return builder.as_markup()

def get_language_selection_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n["lang_ru"], callback_data="set_lang_ru"),
        InlineKeyboardButton(text=l10n["lang_en"], callback_data="set_lang_en"),
    )
    return builder.as_markup()
