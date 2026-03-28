"""Inline keyboards for Porabot."""

from typing import Any, Optional
from datetime import datetime, timedelta

import pytz
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.utils.time_ext import format_time


# =============================================================================
# TIME SELECTION KEYBOARDS
# =============================================================================

def get_time_selection_keyboard(
    user_timezone: str,
    l10n: dict[str, Any],
    show_utc_offset: bool = False,
) -> InlineKeyboardMarkup:
    """
    Keyboard for choosing reminder time.
    
    If a fixed time-of-day has passed today, it rolls over to tomorrow.

    Args:
        user_timezone: User's timezone string (e.g., 'Europe/Moscow')
        l10n: Localization dictionary
        show_utc_offset: Whether to append UTC offset in parentheses

    Returns:
        InlineKeyboardMarkup with time selection buttons

    Example:
        >>> markup = get_time_selection_keyboard("Europe/Moscow", ru)
        # Shows +15m, +30m, +1ч, etc. plus time-of-day slots
    """
    builder = InlineKeyboardBuilder()

    # Row 1: Delta buttons (add X minutes/hours to now)
    builder.row(
        InlineKeyboardButton(text="+15м", callback_data="time_delta_15"),
        InlineKeyboardButton(text="+30м", callback_data="time_delta_30"),
        InlineKeyboardButton(text="+1ч", callback_data="time_delta_60"),
        InlineKeyboardButton(text="+2ч", callback_data="time_delta_120"),
        InlineKeyboardButton(text="+3ч", callback_data="time_delta_180"),
    )

    # Row 2-3: Time-of-day slots (morning, day, evening, night)
    try:
        tz = pytz.timezone(user_timezone)
    except pytz.UnknownTimeZoneError:
        tz = pytz.UTC

    now = datetime.now(tz)

    times = [
        (l10n["time_morning"], 9),
        (l10n["time_day"], 14),
        (l10n["time_evening"], 19),
        (l10n["time_night"], 23),
    ]

    buttons: list[InlineKeyboardButton] = []
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

    # Split into two rows for better layout
    builder.row(*buttons[:2])
    builder.row(*buttons[2:])

    # Row 4: Other options (tomorrow, manual entry)
    builder.row(
        InlineKeyboardButton(text=l10n["time_tomorrow"], callback_data="time_tomorrow"),
        InlineKeyboardButton(text=l10n["time_manual"], callback_data="time_manual"),
    )

    # Row 5: Cancel option (escape route for wizard)
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_cancel", "❌ Отмена"),
            callback_data="cancel_wizard"
        )
    )

    return builder.as_markup()


def get_timezone_keyboard() -> InlineKeyboardMarkup:
    """
    Keyboard for selecting timezone.

    Returns:
        InlineKeyboardMarkup with timezone options

    Example:
        >>> markup = get_timezone_keyboard()
        # Shows Москва, Киев, Минск, Алматы, etc.
    """
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
            text="⌨️ Ввести город вручную",
            callback_data="set_tz_manual"
        )
    )
    return builder.as_markup()


# =============================================================================
# EDIT KEYBOARD (for existing tasks)
# =============================================================================

def get_edit_keyboard(
    reminder_id: int,
    l10n: dict[str, Any],
    is_recurring: bool = False,
    is_nagging: bool = False,
    rrule_text: str = "Нет",
) -> InlineKeyboardMarkup:
    """
    Keyboard for editing an existing task.

    Args:
        reminder_id: Primary key of the reminder
        l10n: Localization dictionary
        is_recurring: Whether this is a recurring task
        is_nagging: Whether nagging is enabled
        rrule_text: Recurrence rule text (e.g., "FREQ=DAILY")

    Returns:
        InlineKeyboardMarkup with edit options

    Example:
        >>> markup = get_edit_keyboard(123, ru, is_recurring=True)
        # Shows repeat toggle, nagging status, delete button
    """
    builder = InlineKeyboardBuilder()

    # Toggle recurrence (only for recurring tasks)
    if is_recurring:
        builder.row(
            InlineKeyboardButton(
                text=f"{l10n['btn_repeat_prefix']} {rrule_text}",
                callback_data=f"edit_toggle_repeat_{reminder_id}"
            )
        )

    # Toggle nagging with icon
    nagging_status = l10n["status_on"] if is_nagging else l10n["status_off"]
    nagging_icon = "🔥" if is_nagging else "❄️"
    builder.row(
        InlineKeyboardButton(
            text=l10n["btn_nagging_prefix"].format(icon=nagging_icon) + f" {nagging_status}",
            callback_data=f"edit_toggle_nagging_{reminder_id}",
        )
    )

    # Delete button
    builder.row(
        InlineKeyboardButton(text=l10n["btn_delete"], callback_data=f"edit_delete_{reminder_id}")
    )

    # Cancel option (escape route)
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_cancel", "❌ Отмена"),
            callback_data="cancel_wizard"
        )
    )

    return builder.as_markup()


# =============================================================================
# TASK DONE KEYBOARD (for reminder notifications)
# =============================================================================

def get_task_done_keyboard(
    reminder_id: int,
    l10n: dict[str, Any],
    show_time_of_day_options: bool = True,
) -> InlineKeyboardMarkup:
    """
    Keyboard for marking a task as done or snoozing.

    This is shown when a reminder fires and the user needs to acknowledge it.

    Args:
        reminder_id: Primary key of the reminder
        l10n: Localization dictionary
        show_time_of_day_options: Whether to include emoji time-of-day buttons

    Returns:
        InlineKeyboardMarkup with snooze options

    Example:
        >>> markup = get_task_done_keyboard(456, ru)
        # Shows Done! button plus snooze intervals and time slots
    """
    builder = InlineKeyboardBuilder()

    # Row 1: Primary action (mark as done)
    builder.row(
        InlineKeyboardButton(
            text=l10n["btn_done"],
            callback_data=f"done_task_{reminder_id}"
        )
    )

    # Row 2: Short intervals (15m, 30m, 1h, 2h)
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_15m"], callback_data=f"snooze_act_{reminder_id}_15m"),
        InlineKeyboardButton(text=l10n["snooze_30m"], callback_data=f"snooze_act_{reminder_id}_30m"),
        InlineKeyboardButton(text=l10n["snooze_1h"], callback_data=f"snooze_act_{reminder_id}_1h"),
        InlineKeyboardButton(text=l10n["snooze_2h"], callback_data=f"snooze_act_{reminder_id}_2h"),
    )

    # Row 3: Time of day (emoji-only style) - optional
    if show_time_of_day_options:
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


# =============================================================================
# SNOOZE KEYBOARD (alternative layout)
# =============================================================================

def get_snooze_keyboard(
    reminder_id: int,
    l10n: dict[str, Any],
) -> InlineKeyboardMarkup:
    """
    Alternative keyboard for snoozing tasks.

    Uses a more compact 2-column layout with text labels instead of emojis.
    Use this when you want clearer labels or need to fit more buttons.

    Args:
        reminder_id: Primary key of the reminder
        l10n: Localization dictionary

    Returns:
        InlineKeyboardMarkup with snooze options in compact layout

    Example:
        >>> markup = get_snooze_keyboard(456, ru)
        # Shows +15m, +30m, etc. plus text labels for time slots
    """
    builder = InlineKeyboardBuilder()

    # Row 1: Short intervals (2 columns per row)
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_15m"], callback_data=f"snooze_act_{reminder_id}_15m"),
        InlineKeyboardButton(text=l10n["snooze_30m"], callback_data=f"snooze_act_{reminder_id}_30m"),
    )
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_1h"], callback_data=f"snooze_act_{reminder_id}_1h"),
        InlineKeyboardButton(text=l10n["snooze_2h"], callback_data=f"snooze_act_{reminder_id}_2h"),
    )

    # Row 2: Morning/Day (text labels)
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_morning"], callback_data=f"snooze_act_{reminder_id}_morning"),
        InlineKeyboardButton(text=l10n["snooze_day"], callback_data=f"snooze_act_{reminder_id}_day"),
    )

    # Row 3: Evening/Night (text labels)
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_evening"], callback_data=f"snooze_act_{reminder_id}_evening"),
        InlineKeyboardButton(text=l10n["snooze_night"], callback_data=f"snooze_act_{reminder_id}_night"),
    )

    # Row 4: Long intervals (2 columns)
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_1d"], callback_data=f"snooze_act_{reminder_id}_1d"),
        InlineKeyboardButton(text=l10n["snooze_custom"], callback_data=f"snooze_act_{reminder_id}_custom"),
    )

    return builder.as_markup()


# =============================================================================
# TASK LIST KEYBOARDS
# =============================================================================

def get_tasks_list_keyboard(
    tasks: list[Any],  # type: ignore
    l10n: dict[str, Any],
) -> InlineKeyboardMarkup:
    """
    Keyboard for task list view.

    Shows Done! and Delete buttons for each task, plus navigation controls.

    Args:
        tasks: List of Reminder objects (or dicts with 'id' and 'reminder_text')
        l10n: Localization dictionary

    Returns:
        InlineKeyboardMarkup with per-task actions and navigation

    Example:
        >>> markup = get_tasks_list_keyboard(my_tasks, ru)
        # Shows each task with Done! and Delete buttons
    """
    builder = InlineKeyboardBuilder()
    
    for task in tasks:
        # Extract text safely (works with both Reminder objects and dicts)
        if hasattr(task, 'reminder_text'):
            task_text = task.reminder_text
        else:
            task_text = str(task.get('reminder_text', ''))
        
        # Truncate long text for display
        text_preview = (
            task_text[:18] + "…"
            if len(task_text) > 18
            else task_text
        )
        
        builder.row(
            InlineKeyboardButton(
                text=f"{l10n['btn_done_task_prefix']} {text_preview}",
                callback_data=f"done_task_{task.id}" if hasattr(task, 'id') else f"done_task_{task.get('id', '')}",
            ),
            InlineKeyboardButton(
                text=l10n["btn_delete"],
                callback_data=f"del_task_{task.id}" if hasattr(task, 'id') else f"del_task_{task.get('id', '')}",
            ),
        )

    # Navigation row
    builder.row(
        InlineKeyboardButton(text=l10n["btn_refresh"], callback_data="refresh_tasks"),
        InlineKeyboardButton(text=l10n["btn_completed_tasks"], callback_data="show_completed"),
        InlineKeyboardButton(text=l10n["btn_close"], callback_data="close_tasks"),
    )

    return builder.as_markup()


def get_completed_tasks_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """
    Simple close keyboard for the completed tasks view.

    Args:
        l10n: Localization dictionary

    Returns:
        InlineKeyboardMarkup with Close button

    Example:
        >>> markup = get_completed_tasks_keyboard(ru)
        # Shows single Close button
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n["btn_close"], callback_data="close_tasks"),
    )
    return builder.as_markup()


# =============================================================================
# SETTINGS KEYBOARD
# =============================================================================

def get_settings_keyboard(
    l10n: dict[str, Any],
    show_utc_offset: bool = False,
) -> InlineKeyboardMarkup:
    """
    Keyboard for settings view.

    Shows options to change timezone, language, and UTC offset display.

    Args:
        l10n: Localization dictionary
        show_utc_offset: Whether UTC offset is currently enabled

    Returns:
        InlineKeyboardMarkup with settings buttons

    Example:
        >>> markup = get_settings_keyboard(ru, show_utc_offset=True)
        # Shows Change Timezone, Change Language, Toggle UTC Offset buttons
    """
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(
            text=l10n["btn_change_tz"],
            callback_data="settings_change_tz"
        ),
        InlineKeyboardButton(
            text=l10n["btn_change_lang"],
            callback_data="settings_change_lang"
        ),
    )

    utc_btn_text = l10n["btn_toggle_utc_on"] if show_utc_offset else l10n["btn_toggle_utc_off"]
    builder.row(
        InlineKeyboardButton(
            text=utc_btn_text,
            callback_data="settings_toggle_utc"
        )
    )

    return builder.as_markup()


# =============================================================================
# LANGUAGE SELECTION KEYBOARD
# =============================================================================

def get_language_selection_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """
    Keyboard for selecting bot language.

    Args:
        l10n: Localization dictionary

    Returns:
        InlineKeyboardMarkup with Russian and English options

    Example:
        >>> markup = get_language_selection_keyboard(ru)
        # Shows 🇷🇺 Русский and 🇬🇧 English buttons
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n["lang_ru"], callback_data="set_lang_ru"),
        InlineKeyboardButton(text=l10n["lang_en"], callback_data="set_lang_en"),
    )
    return builder.as_markup()