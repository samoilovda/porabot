"""Inline keyboards for Porabot.

All dynamic callback data is built with typed CallbackData factories from
``bot.callbacks`` instead of raw f-strings, eliminating format drift bugs
and keeping every payload well under Telegram's 64-byte limit.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytz
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callbacks import (
    DelHabitCallback,
    DeleteTaskCallback,
    DoneNoteCallback,
    DoneSkipNextCallback,
    DoneTaskCallback,
    DoneUndoCallback,
    EditReminderCallback,
    FluidDoneCallback,
    FluidPickCustomCallback,
    FluidPickTimeCallback,
    HabitFluidModeCallback,
    HabitPresetCallback,
    SetLangCallback,
    SetTimezoneCallback,
    SnoozeActCallback,
    SnoozeShowCallback,
    TaskSettingsCallback,
    TimeDeltaCallback,
    TimeFixedCallback,
)
from bot.utils.time_ext import format_time, resolve_tz


def _format_utc_offset(tz_name: str) -> str:
    """Return current UTC offset label like UTC+03:00 for a timezone."""
    try:
        tz = pytz.timezone(tz_name)
        now_local = datetime.now(tz)
        raw = now_local.strftime("%z")  # +HHMM
        if raw and len(raw) == 5:
            return f"UTC{raw[:3]}:{raw[3:]}"
    except Exception:
        pass
    return "UTC+00:00"


# =============================================================================
# TIME SELECTION KEYBOARDS
# =============================================================================

def get_time_selection_keyboard(
    user_timezone: str,
    l10n: dict[str, Any],
    show_utc_offset: bool = False,
) -> InlineKeyboardMarkup:
    """Keyboard for choosing reminder time."""
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text=l10n["time_delta_15m"], callback_data=TimeDeltaCallback(minutes=15).pack()),
        InlineKeyboardButton(text=l10n["time_delta_30m"], callback_data=TimeDeltaCallback(minutes=30).pack()),
        InlineKeyboardButton(text=l10n["time_delta_1h"],  callback_data=TimeDeltaCallback(minutes=60).pack()),
        InlineKeyboardButton(text=l10n["time_delta_2h"],  callback_data=TimeDeltaCallback(minutes=120).pack()),
        InlineKeyboardButton(text=l10n["time_delta_3h"],  callback_data=TimeDeltaCallback(minutes=180).pack()),
    )

    tz = resolve_tz(user_timezone)
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
        ts = int(target_time.timestamp())
        time_str = format_time(target_time, user_timezone, show_utc_offset, "%H:%M")
        buttons.append(
            InlineKeyboardButton(
                text=f"{label} ({time_str})",
                callback_data=TimeFixedCallback(ts=ts).pack(),
            )
        )

    builder.row(*buttons[:2])
    builder.row(*buttons[2:])

    builder.row(
        InlineKeyboardButton(text=l10n["time_tomorrow"], callback_data="time_tomorrow"),
        InlineKeyboardButton(text=l10n["time_manual"],   callback_data="time_manual"),
    )

    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_cancel", "❌ Отмена"),
            callback_data="cancel_wizard",
        )
    )

    return builder.as_markup()


def get_timezone_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Keyboard for selecting timezone."""
    builder = InlineKeyboardBuilder()
    zones = [
        ("America/New_York",    l10n.get("tz_label_america_new_york",   "US Eastern (EST/EDT)")),
        ("America/Chicago",     l10n.get("tz_label_america_chicago",    "US Central (CST/CDT)")),
        ("America/Denver",      l10n.get("tz_label_america_denver",     "US Mountain (MST/MDT)")),
        ("America/Los_Angeles", l10n.get("tz_label_america_los_angeles","US Pacific (PST/PDT)")),
        ("Europe/London",       l10n.get("tz_label_europe_london",      "London (GMT/BST)")),
        ("Europe/Berlin",       l10n.get("tz_label_europe_berlin",      "Berlin (CET/CEST)")),
        ("Europe/Kyiv",         l10n.get("tz_label_europe_kyiv",        "Kyiv")),
        ("Europe/Moscow",       l10n.get("tz_label_europe_moscow",      "Moscow")),
        ("Asia/Dubai",          l10n.get("tz_label_asia_dubai",         "Dubai")),
        ("Asia/Almaty",         l10n.get("tz_label_asia_almaty",        "Almaty")),
        ("Asia/Tokyo",          l10n.get("tz_label_asia_tokyo",         "Tokyo")),
        ("Asia/Singapore",      l10n.get("tz_label_asia_singapore",     "Singapore")),
        ("UTC",                 l10n.get("tz_label_utc",                "UTC")),
    ]
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("tz_manual_button", "⌨️ Enter manually"),
            callback_data=SetTimezoneCallback(tz="manual").pack(),
        )
    )
    for tz, label in zones:
        offset = _format_utc_offset(tz)
        builder.row(
            InlineKeyboardButton(
                text=f"{label} ({offset})",
                callback_data=SetTimezoneCallback(tz=tz).pack(),
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
    nagging_max_repeats: int = 3,
    rrule_text: str = "Нет",
) -> InlineKeyboardMarkup:
    """Keyboard for editing an existing task."""
    builder = InlineKeyboardBuilder()

    if is_recurring:
        builder.row(
            InlineKeyboardButton(
                text=f"{l10n['btn_repeat_prefix']} {rrule_text}",
                callback_data=EditReminderCallback(action="toggle_repeat", reminder_id=reminder_id).pack(),
            )
        )

    nagging_status = l10n["status_on"] if is_nagging else l10n["status_off"]
    nagging_icon = "🔥" if is_nagging else "❄️"
    builder.row(
        InlineKeyboardButton(
            text=l10n["btn_nagging_prefix"].format(icon=nagging_icon) + f" {nagging_status}",
            callback_data=EditReminderCallback(action="toggle_nagging", reminder_id=reminder_id).pack(),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text=l10n["btn_nagging_repeats_prefix"].format(count=max(0, int(nagging_max_repeats))),
            callback_data=EditReminderCallback(action="set_nag_limit", reminder_id=reminder_id).pack(),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text=l10n["btn_delete"],
            callback_data=EditReminderCallback(action="delete", reminder_id=reminder_id).pack(),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_cancel", "❌ Отмена"),
            callback_data="cancel_wizard",
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
    cycle_due_ts: Optional[int] = None,
) -> InlineKeyboardMarkup:
    """Keyboard shown when a reminder fires."""
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=l10n["btn_done"],
            callback_data=DoneTaskCallback(reminder_id=reminder_id, cycle_due_ts=cycle_due_ts).pack(),
        )
    )

    builder.row(
        InlineKeyboardButton(text=l10n["snooze_15m"], callback_data=SnoozeActCallback(reminder_id=reminder_id, action="15m").pack()),
        InlineKeyboardButton(text=l10n["snooze_30m"], callback_data=SnoozeActCallback(reminder_id=reminder_id, action="30m").pack()),
        InlineKeyboardButton(text=l10n["snooze_1h"],  callback_data=SnoozeActCallback(reminder_id=reminder_id, action="1h").pack()),
        InlineKeyboardButton(text=l10n["snooze_2h"],  callback_data=SnoozeActCallback(reminder_id=reminder_id, action="2h").pack()),
    )

    if show_time_of_day_options:
        builder.row(
            InlineKeyboardButton(text="🌅", callback_data=SnoozeActCallback(reminder_id=reminder_id, action="morning").pack()),
            InlineKeyboardButton(text="🏙️", callback_data=SnoozeActCallback(reminder_id=reminder_id, action="day").pack()),
            InlineKeyboardButton(text="🌇", callback_data=SnoozeActCallback(reminder_id=reminder_id, action="evening").pack()),
            InlineKeyboardButton(text="🌃", callback_data=SnoozeActCallback(reminder_id=reminder_id, action="night").pack()),
        )

    builder.row(
        InlineKeyboardButton(text=l10n["snooze_1d"],    callback_data=SnoozeActCallback(reminder_id=reminder_id, action="1d").pack()),
        InlineKeyboardButton(text=l10n["snooze_custom"], callback_data=SnoozeActCallback(reminder_id=reminder_id, action="custom").pack()),
    )

    return builder.as_markup()


def get_done_followup_keyboard(
    reminder_id: int,
    l10n: dict[str, Any],
    *,
    is_recurring: bool = False,
) -> InlineKeyboardMarkup:
    """Keyboard shown after marking a task as done."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_done_add_note", "📝 Add note"),
            callback_data=DoneNoteCallback(reminder_id=reminder_id).pack(),
        ),
        InlineKeyboardButton(
            text=l10n.get("btn_done_undo", "↩ Undo"),
            callback_data=DoneUndoCallback(reminder_id=reminder_id).pack(),
        ),
    )
    if is_recurring:
        builder.row(
            InlineKeyboardButton(
                text=l10n.get("btn_done_skip_next", "⏭ Skip next"),
                callback_data=DoneSkipNextCallback(reminder_id=reminder_id).pack(),
            )
        )
    builder.row(InlineKeyboardButton(text=l10n["btn_close"], callback_data="done_close"))
    return builder.as_markup()


def get_parse_confirmation_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Keyboard for low-confidence parser confirmations."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_parse_confirm_yes",  "✅ Yes"),       callback_data="parse_confirm_yes"),
        InlineKeyboardButton(text=l10n.get("btn_parse_confirm_time", "🕒 Pick time"), callback_data="parse_confirm_pick_time"),
    )
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_parse_confirm_cancel", "❌ Cancel"), callback_data="parse_confirm_cancel")
    )
    return builder.as_markup()


def get_missed_recovery_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Keyboard for missed-task recovery digest."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_recovery_done_all",   "✅ Done all"), callback_data="recovery_done_all"),
        InlineKeyboardButton(text=l10n.get("btn_recovery_snooze_all", "⏰ +1h all"),  callback_data="recovery_snooze_all"),
    )
    builder.row(InlineKeyboardButton(text=l10n["btn_close"], callback_data="close_tasks"))
    return builder.as_markup()


# =============================================================================
# SNOOZE KEYBOARD (text-label variant)
# =============================================================================

def get_snooze_keyboard(reminder_id: int, l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Alternative snooze layout with text labels."""
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text=l10n["snooze_15m"], callback_data=SnoozeActCallback(reminder_id=reminder_id, action="15m").pack()),
        InlineKeyboardButton(text=l10n["snooze_30m"], callback_data=SnoozeActCallback(reminder_id=reminder_id, action="30m").pack()),
    )
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_1h"], callback_data=SnoozeActCallback(reminder_id=reminder_id, action="1h").pack()),
        InlineKeyboardButton(text=l10n["snooze_2h"], callback_data=SnoozeActCallback(reminder_id=reminder_id, action="2h").pack()),
    )
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_morning"], callback_data=SnoozeActCallback(reminder_id=reminder_id, action="morning").pack()),
        InlineKeyboardButton(text=l10n["snooze_day"],     callback_data=SnoozeActCallback(reminder_id=reminder_id, action="day").pack()),
    )
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_evening"], callback_data=SnoozeActCallback(reminder_id=reminder_id, action="evening").pack()),
        InlineKeyboardButton(text=l10n["snooze_night"],   callback_data=SnoozeActCallback(reminder_id=reminder_id, action="night").pack()),
    )
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_1d"],     callback_data=SnoozeActCallback(reminder_id=reminder_id, action="1d").pack()),
        InlineKeyboardButton(text=l10n["snooze_custom"], callback_data=SnoozeActCallback(reminder_id=reminder_id, action="custom").pack()),
    )

    return builder.as_markup()


# =============================================================================
# TASK LIST KEYBOARDS
# =============================================================================

def get_tasks_list_keyboard(
    tasks: list[Any],
    l10n: dict[str, Any],
) -> InlineKeyboardMarkup:
    """Per-task Done / Settings / Delete row plus navigation controls."""
    builder = InlineKeyboardBuilder()

    for task in tasks:
        task_text = task.reminder_text if hasattr(task, "reminder_text") else str(task.get("reminder_text", ""))
        task_id   = task.id           if hasattr(task, "id")            else task.get("id", 0)

        text_preview = (task_text[:18] + "…") if len(task_text) > 18 else task_text

        builder.row(
            InlineKeyboardButton(
                text=f"{l10n['btn_done_task_prefix']} {text_preview}",
                callback_data=DoneTaskCallback(reminder_id=task_id).pack(),
            ),
            InlineKeyboardButton(
                text=l10n.get("btn_task_settings", "⚙️"),
                callback_data=TaskSettingsCallback(reminder_id=task_id).pack(),
            ),
            InlineKeyboardButton(
                text=l10n["btn_delete"],
                callback_data=DeleteTaskCallback(reminder_id=task_id).pack(),
            ),
        )

    builder.row(
        InlineKeyboardButton(text=l10n["btn_refresh"],         callback_data="refresh_tasks"),
        InlineKeyboardButton(text=l10n["btn_completed_tasks"], callback_data="show_completed"),
        InlineKeyboardButton(text=l10n["btn_close"],           callback_data="close_tasks"),
    )

    return builder.as_markup()


def get_completed_tasks_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Close button for the completed-tasks view."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=l10n["btn_close"], callback_data="close_tasks"))
    return builder.as_markup()


def get_fluid_pick_time_keyboard(reminder_id: int, l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Time-of-day presets for scheduling a fluid habit today."""
    builder = InlineKeyboardBuilder()
    for label in ("09:00", "12:00", "15:00", "18:00", "21:00"):
        hhmm = label.replace(":", "")
        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=FluidPickTimeCallback(reminder_id=reminder_id, hhmm=hhmm).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("fluid_time_custom", "⌨️ Custom time"),
            callback_data=FluidPickCustomCallback(reminder_id=reminder_id).pack(),
        )
    )
    return builder.as_markup()


def get_fluid_completion_keyboard(tasks: list[Any], l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Evening check-in: one Done button per active fluid habit."""
    builder = InlineKeyboardBuilder()
    for task in tasks:
        task_id = task.id if hasattr(task, "id") else task.get("id")
        text    = task.reminder_text if hasattr(task, "reminder_text") else str(task.get("reminder_text", "Habit"))
        preview = (text[:24] + "…") if len(text) > 24 else text
        builder.row(
            InlineKeyboardButton(
                text=l10n.get("fluid_done_btn_prefix", "✅ Done: ") + preview,
                callback_data=FluidDoneCallback(reminder_id=task_id).pack(),
            )
        )
    return builder.as_markup()


# =============================================================================
# SETTINGS KEYBOARDS
# =============================================================================

def get_settings_keyboard(
    l10n: dict[str, Any],
    show_utc_offset: bool = False,
) -> InlineKeyboardMarkup:
    """Main settings menu."""
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text=l10n["btn_change_tz"],   callback_data="settings_change_tz"),
        InlineKeyboardButton(text=l10n["btn_change_lang"], callback_data="settings_change_lang"),
    )

    utc_btn_text = l10n["btn_toggle_utc_on"] if show_utc_offset else l10n["btn_toggle_utc_off"]
    builder.row(InlineKeyboardButton(text=utc_btn_text, callback_data="settings_toggle_utc"))

    builder.row(InlineKeyboardButton(text=l10n.get("btn_briefs_setup", "📋 Briefs setup"), callback_data="settings_briefs_setup"))
    builder.row(InlineKeyboardButton(text=l10n.get("btn_quiet_hours_setup", "😴 Quiet hours"), callback_data="settings_quiet_setup"))
    builder.row(InlineKeyboardButton(text=l10n.get("btn_clear_all", "🗑 Clear all"), callback_data="settings_clear_all"))

    return builder.as_markup()


def get_clear_all_confirm_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Confirmation keyboard for full account reset."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_clear_all_confirm", "✅ Yes, clear all"), callback_data="settings_clear_all_confirm"),
        InlineKeyboardButton(text=l10n.get("btn_clear_all_cancel",  "↩ Cancel"),          callback_data="settings_back"),
    )
    return builder.as_markup()


# =============================================================================
# LANGUAGE SELECTION
# =============================================================================

def get_language_selection_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Keyboard for selecting interface language."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n["lang_ru"], callback_data=SetLangCallback(lang="ru").pack()),
        InlineKeyboardButton(text=l10n["lang_en"], callback_data=SetLangCallback(lang="en").pack()),
    )
    builder.row(
        InlineKeyboardButton(text=l10n["lang_es"], callback_data=SetLangCallback(lang="es").pack()),
    )
    return builder.as_markup()


# =============================================================================
# BRIEFS / QUIET HOURS SETUP
# =============================================================================

def get_briefs_setup_keyboard(
    l10n: dict[str, Any],
    enabled: bool,
    morning_str: str,
    evening_str: str,
) -> InlineKeyboardMarkup:
    """Keyboard for Daily Briefs configuration."""
    builder = InlineKeyboardBuilder()
    toggle_text = l10n.get("btn_briefs_on") if enabled else l10n.get("btn_briefs_off")
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data="briefs_toggle"))
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_morning_brief").format(time=morning_str), callback_data="briefs_edit_morning"),
        InlineKeyboardButton(text=l10n.get("btn_evening_brief").format(time=evening_str), callback_data="briefs_edit_evening"),
    )
    builder.row(InlineKeyboardButton(text=l10n.get("btn_back_settings"), callback_data="settings_back"))
    return builder.as_markup()


def get_quiet_hours_setup_keyboard(
    l10n: dict[str, Any],
    *,
    enabled: bool,
    start_time: str,
    end_time: str,
) -> InlineKeyboardMarkup:
    """Keyboard for Quiet Hours configuration."""
    builder = InlineKeyboardBuilder()
    toggle_text = l10n.get("btn_quiet_on") if enabled else l10n.get("btn_quiet_off")
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data="quiet_toggle"))
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_quiet_start").format(time=start_time), callback_data="quiet_edit_start"),
        InlineKeyboardButton(text=l10n.get("btn_quiet_end").format(time=end_time),   callback_data="quiet_edit_end"),
    )
    builder.row(InlineKeyboardButton(text=l10n.get("btn_back_settings"), callback_data="settings_back"))
    return builder.as_markup()


# =============================================================================
# HABITS MANAGEMENT (used by habits.py for the list view)
# =============================================================================

def get_habit_list_row(
    reminder_id: int,
    index: int,
    l10n: dict[str, Any],
) -> list[InlineKeyboardButton]:
    """Return the (settings, delete) button pair for one habit list row."""
    return [
        InlineKeyboardButton(
            text=l10n.get("btn_task_settings", "⚙️"),
            callback_data=TaskSettingsCallback(reminder_id=reminder_id).pack(),
        ),
        InlineKeyboardButton(
            text=l10n["habit_btn_delete_n"].format(index=index),
            callback_data=DelHabitCallback(reminder_id=reminder_id).pack(),
        ),
    ]
