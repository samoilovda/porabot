"""Inline keyboards for Porabot."""

from typing import Any, Optional
from datetime import datetime, timedelta

import pytz
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import config
from bot.utils.time_ext import format_time


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
        InlineKeyboardButton(text=l10n["time_delta_15m"], callback_data="time_delta_15"),
        InlineKeyboardButton(text=l10n["time_delta_30m"], callback_data="time_delta_30"),
        InlineKeyboardButton(text=l10n["time_delta_1h"], callback_data="time_delta_60"),
        InlineKeyboardButton(text=l10n["time_delta_2h"], callback_data="time_delta_120"),
        InlineKeyboardButton(text=l10n["time_delta_3h"], callback_data="time_delta_180"),
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


def get_timezone_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
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
        ("America/New_York", l10n.get("tz_label_america_new_york", "US Eastern (EST/EDT)")),
        ("America/Chicago", l10n.get("tz_label_america_chicago", "US Central (CST/CDT)")),
        ("America/Denver", l10n.get("tz_label_america_denver", "US Mountain (MST/MDT)")),
        ("America/Los_Angeles", l10n.get("tz_label_america_los_angeles", "US Pacific (PST/PDT)")),
        ("Europe/London", l10n.get("tz_label_europe_london", "London (GMT/BST)")),
        ("Europe/Berlin", l10n.get("tz_label_europe_berlin", "Berlin (CET/CEST)")),
        ("Europe/Kyiv", l10n.get("tz_label_europe_kyiv", "Kyiv")),
        ("Europe/Moscow", l10n.get("tz_label_europe_moscow", "Moscow")),
        ("Asia/Dubai", l10n.get("tz_label_asia_dubai", "Dubai")),
        ("Asia/Almaty", l10n.get("tz_label_asia_almaty", "Almaty")),
        ("Asia/Tokyo", l10n.get("tz_label_asia_tokyo", "Tokyo")),
        ("Asia/Singapore", l10n.get("tz_label_asia_singapore", "Singapore")),
        ("UTC", l10n.get("tz_label_utc", "UTC")),
    ]
    # Manual entry first, then popular presets.
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("tz_manual_button", "⌨️ Enter manually"),
            callback_data="set_tz_manual"
        )
    )
    for tz, label in zones:
        offset = _format_utc_offset(tz)
        builder.row(InlineKeyboardButton(text=f"{label} ({offset})", callback_data=f"set_tz_{tz}"))
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
    """
    Keyboard for editing an existing task.

    Args:
        reminder_id: Primary key of the reminder
        l10n: Localization dictionary
        is_recurring: Whether this is a recurring task
        is_nagging: Whether nagging is enabled
        nagging_max_repeats: Max number of nagging follow-up messages
        rrule_text: Recurrence rule text (e.g., "FREQ=DAILY")

    Returns:
        InlineKeyboardMarkup with edit options

    Example:
        >>> markup = get_edit_keyboard(123, ru, is_recurring=True)
        # Shows repeat toggle, nagging status, delete button
    """
    builder = InlineKeyboardBuilder()

    # Toggle recurrence. Shown for every task, not just already-recurring
    # ones — every new task is created with is_recurring=False, so gating
    # this button on is_recurring made it impossible to ever turn recurrence
    # on for a plain task through the UI. rrule_text already defaults to
    # "None" (via _rrule_text) when the task isn't recurring.
    builder.row(
        InlineKeyboardButton(
            text=f"{l10n['btn_repeat_prefix']} {rrule_text}",
            callback_data=f"edit_repeat_menu_{reminder_id}"
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

    # Per-task/habit nagging repeats limit.
    builder.row(
        InlineKeyboardButton(
            text=l10n["btn_nagging_repeats_prefix"].format(count=max(0, int(nagging_max_repeats))),
            callback_data=f"edit_set_nag_limit_{reminder_id}",
        )
    )

    # Change the scheduled time in place, without recreating the reminder.
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_edit_time", "🕒 Change time"),
            callback_data=f"edit_edit_{reminder_id}",
        )
    )

    # Snooze — swaps this keyboard for the compact snooze-picker layout.
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_snooze", "⏰ Snooze"),
            callback_data=f"snooze_show_{reminder_id}",
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
# REPEAT BUILDER KEYBOARDS (3.1: full RRULE construction UI)
# =============================================================================

RRULE_WEEKDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def get_repeat_builder_keyboard(
    reminder_id: int,
    l10n: dict[str, Any],
    end_label: str,
) -> InlineKeyboardMarkup:
    """Main menu of the repeat (RRULE) builder — replaces the old 4-option
    cycling button with real construction options over the existing
    next_occurrence_utc/rrulestr engine."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_repeat_none", "🚫 No repeat"), callback_data=f"rrb_none_{reminder_id}")
    )
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_repeat_daily", "📆 Every day"), callback_data=f"rrb_daily_{reminder_id}")
    )
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_repeat_weekdays_opt", "💼 Weekdays"), callback_data=f"rrb_weekdays_{reminder_id}"),
        InlineKeyboardButton(text=l10n.get("btn_repeat_weekend_opt", "🏖 Weekend"), callback_data=f"rrb_weekend_{reminder_id}"),
    )
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_repeat_weekly", "🗓 Weekly (same day)"), callback_data=f"rrb_weekly_{reminder_id}"),
    )
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_repeat_interval", "🔢 Every N days"), callback_data=f"rrb_interval_{reminder_id}"),
        InlineKeyboardButton(text=l10n.get("btn_repeat_custom_days", "☑️ Pick weekdays"), callback_data=f"rrb_customdays_{reminder_id}"),
    )
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_repeat_monthly", "📅 Monthly on day"), callback_data=f"rrb_monthly_{reminder_id}"),
        InlineKeyboardButton(text=l10n.get("btn_repeat_last_weekday", "🏁 Last workday"), callback_data=f"rrb_lastwd_{reminder_id}"),
    )
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_repeat_end", "⏳ End: {end}").format(end=end_label),
            callback_data=f"rrb_end_{reminder_id}",
        )
    )
    builder.row(InlineKeyboardButton(text=l10n.get("btn_repeat_back", "🔙 Back"), callback_data=f"rrb_back_{reminder_id}"))
    return builder.as_markup()


def get_repeat_weekday_keyboard(
    reminder_id: int,
    l10n: dict[str, Any],
    selected: set[str],
) -> InlineKeyboardMarkup:
    """Checkbox picker for arbitrary weekday combinations (BYDAY=...)."""
    builder = InlineKeyboardBuilder()
    names = l10n.get("weekday_names") or ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    row: list[InlineKeyboardButton] = []
    for i, code in enumerate(RRULE_WEEKDAY_CODES):
        mark = "✅" if code in selected else "⬜"
        label = names[i] if i < len(names) else code
        row.append(InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"rrb_wd_{reminder_id}_{code}"))
        if len(row) == 4:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_repeat_done_days", "✅ Done"), callback_data=f"rrb_wddone_{reminder_id}")
    )
    builder.row(InlineKeyboardButton(text=l10n.get("btn_repeat_back", "🔙 Back"), callback_data=f"rrb_open_{reminder_id}"))
    return builder.as_markup()


def get_repeat_end_keyboard(reminder_id: int, l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """End-condition submenu: unlimited / COUNT= / UNTIL=."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_repeat_end_none", "♾ Unlimited"), callback_data=f"rrb_endnone_{reminder_id}")
    )
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_repeat_end_count", "🔢 After N times"), callback_data=f"rrb_endcount_{reminder_id}"),
        InlineKeyboardButton(text=l10n.get("btn_repeat_end_until", "📅 Until date"), callback_data=f"rrb_enduntil_{reminder_id}"),
    )
    builder.row(InlineKeyboardButton(text=l10n.get("btn_repeat_back", "🔙 Back"), callback_data=f"rrb_open_{reminder_id}"))
    return builder.as_markup()


def get_undo_delete_keyboard(reminder_id: int, l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Keyboard shown after deletion with a time-limited Undo button."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_delete_undo", "↩ Undo delete"),
            callback_data=f"undo_del_{reminder_id}",
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
    show_not_today: bool = False,
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
    done_callback = f"done_task_{reminder_id}"
    if cycle_due_ts is not None:
        done_callback = f"{done_callback}_{int(cycle_due_ts)}"
    builder.row(
        InlineKeyboardButton(
            text=l10n["btn_done"],
            callback_data=done_callback,
        )
    )

    # Row 2: "Not today" (habits only) — directly under Done, before snooze options.
    if show_not_today:
        not_today_callback = f"not_today_{reminder_id}"
        if cycle_due_ts is not None:
            not_today_callback = f"{not_today_callback}_{int(cycle_due_ts)}"
        builder.row(
            InlineKeyboardButton(
                text=l10n["btn_not_today"],
                callback_data=not_today_callback,
            )
        )

    # Row 3: Short intervals (15m, 30m, 1h, 2h)
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


def get_snooze_keyboard(
    reminder_id: int,
    l10n: dict[str, Any],
) -> InlineKeyboardMarkup:
    """
    Compact snooze-only keyboard (2-column layout with text labels instead of
    emojis), swapped in for the task-settings edit keyboard via "⏰ Snooze".

    Args:
        reminder_id: Primary key of the reminder
        l10n: Localization dictionary

    Returns:
        InlineKeyboardMarkup with snooze options in compact layout
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text=l10n["snooze_15m"], callback_data=f"snooze_act_{reminder_id}_15m"),
        InlineKeyboardButton(text=l10n["snooze_30m"], callback_data=f"snooze_act_{reminder_id}_30m"),
    )
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_1h"], callback_data=f"snooze_act_{reminder_id}_1h"),
        InlineKeyboardButton(text=l10n["snooze_2h"], callback_data=f"snooze_act_{reminder_id}_2h"),
    )
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_morning"], callback_data=f"snooze_act_{reminder_id}_morning"),
        InlineKeyboardButton(text=l10n["snooze_day"], callback_data=f"snooze_act_{reminder_id}_day"),
    )
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_evening"], callback_data=f"snooze_act_{reminder_id}_evening"),
        InlineKeyboardButton(text=l10n["snooze_night"], callback_data=f"snooze_act_{reminder_id}_night"),
    )
    builder.row(
        InlineKeyboardButton(text=l10n["snooze_1d"], callback_data=f"snooze_act_{reminder_id}_1d"),
        InlineKeyboardButton(text=l10n["snooze_custom"], callback_data=f"snooze_act_{reminder_id}_custom"),
    )

    return builder.as_markup()


def get_done_followup_keyboard(
    reminder_id: int,
    l10n: dict[str, Any],
    *,
    is_recurring: bool = False,
    cycle_due_ts: Optional[int] = None,
) -> InlineKeyboardMarkup:
    """Keyboard shown after marking a task as done.

    cycle_due_ts (3.4): the habit cycle Done credited, embedded in Undo's
    callback_data the same way get_task_done_keyboard already embeds it for
    Done/Not-today — without it, Undo fell back to reminder.habit_active_due_at,
    which can have moved on to the NEXT cycle by the time Undo is tapped
    (e.g. Done was tapped on an old/snoozed notification), reverting the
    wrong cycle's streak and event.
    """
    builder = InlineKeyboardBuilder()
    undo_callback = f"done_undo_{reminder_id}"
    if cycle_due_ts is not None:
        undo_callback = f"{undo_callback}_{int(cycle_due_ts)}"
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_done_add_note", "📝 Add note"), callback_data=f"done_note_{reminder_id}"),
        InlineKeyboardButton(text=l10n.get("btn_done_undo", "↩ Undo"), callback_data=undo_callback),
    )
    if is_recurring:
        builder.row(
            InlineKeyboardButton(
                text=l10n.get("btn_done_skip_next", "⏭ Skip next"),
                callback_data=f"done_skip_next_{reminder_id}",
            )
        )
    builder.row(InlineKeyboardButton(text=l10n["btn_close"], callback_data="done_close"))
    return builder.as_markup()


def get_parse_confirmation_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Keyboard for low-confidence parser confirmations."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_parse_confirm_yes", "✅ Yes"), callback_data="parse_confirm_yes"),
        InlineKeyboardButton(text=l10n.get("btn_parse_confirm_time", "🕒 Pick time"), callback_data="parse_confirm_pick_time"),
    )
    builder.row(InlineKeyboardButton(text=l10n.get("btn_parse_confirm_cancel", "❌ Cancel"), callback_data="parse_confirm_cancel"))
    return builder.as_markup()


def get_missed_recovery_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Keyboard for missed-task recovery digest."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_recovery_done_all", "✅ Done all"), callback_data="recovery_done_all"),
        InlineKeyboardButton(text=l10n.get("btn_recovery_snooze_all", "⏰ +1h all"), callback_data="recovery_snooze_all"),
    )
    builder.row(InlineKeyboardButton(text=l10n["btn_close"], callback_data="close_tasks"))
    return builder.as_markup()


def get_evening_wrapup_keyboard(tasks: list[Any], l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Keyboard for evening task check-in rows."""
    builder = InlineKeyboardBuilder()
    for task in tasks:
        task_id = task.id if hasattr(task, "id") else task.get("id")
        text = task.reminder_text if hasattr(task, "reminder_text") else str(task.get("reminder_text", "Task"))
        preview = (text[:24] + "…") if len(text) > 24 else text
        builder.row(
            InlineKeyboardButton(
                text=preview,
                callback_data=f"wrap_task_{task_id}",
            ),
            InlineKeyboardButton(
                text=l10n.get("btn_done_short", "Done"),
                callback_data=f"wrap_done_{task_id}",
            ),
            InlineKeyboardButton(
                text=l10n.get("btn_not_done_short", "Not done"),
                callback_data=f"wrap_not_done_{task_id}",
            ),
        )
    return builder.as_markup()


# =============================================================================
# TASK LIST KEYBOARDS
# =============================================================================

def _build_task_action_rows(tasks: list[Any], l10n: dict[str, Any]) -> list[list[InlineKeyboardButton]]:
    """Per-task Done/Settings/Delete button row — shared by the plain task
    list and the 3.4 search/filter results view."""
    rows: list[list[InlineKeyboardButton]] = []
    for task in tasks:
        if hasattr(task, 'reminder_text'):
            task_text = task.reminder_text
            task_id = task.id
        else:
            task_text = str(task.get('reminder_text', ''))
            task_id = task.get('id', '')

        text_preview = (task_text[:18] + "…") if len(task_text) > 18 else task_text

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{l10n['btn_done_task_prefix']} {text_preview}",
                    callback_data=f"done_task_{task_id}",
                ),
                InlineKeyboardButton(
                    text=l10n.get("btn_task_settings", "⚙️"),
                    callback_data=f"task_settings_{task_id}",
                ),
                InlineKeyboardButton(
                    text=l10n["btn_delete"],
                    callback_data=f"del_task_{task_id}",
                ),
            ]
        )
    return rows


def get_filtered_tasks_keyboard(tasks: list[Any], l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """3.4: results view for /find and the quick filters (today / this week
    / overdue / recurring) — per-task actions plus a way back to the full
    unfiltered list, deliberately without the paging/refresh machinery of
    get_tasks_list_keyboard since a filtered set isn't a "page" of anything."""
    builder = InlineKeyboardBuilder()
    for row in _build_task_action_rows(tasks[:25], l10n):
        builder.row(*row)
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_filter_back", "🔙 All tasks"), callback_data="tasks_page_0"),
        InlineKeyboardButton(text=l10n["btn_close"], callback_data="close_tasks"),
    )
    return builder.as_markup()


# fix(1.3): Telegram rejects an ENTIRE keyboard if any one button's
# callback_data exceeds 64 bytes — a single over-long tag would silently
# break the whole tags menu, not just its own button. bot/utils/tags.py
# caps new tags at 24 chars going forward, but tags written before that
# cap existed can still be longer, so this stays defensive independently.
_MAX_CALLBACK_DATA_BYTES = 64
# Telegram also rejects a keyboard with too many buttons/rows; keep the
# tags menu well under that so it always renders.
_MAX_TAG_BUTTONS = 30


def get_tags_menu_keyboard(tags: list[str], l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """4.3: one button per distinct tag, two per row, tapping filters the
    task list to that tag (tasks_tag:<tag> callback)."""
    builder = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    shown = 0
    for tag in tags:
        if shown >= _MAX_TAG_BUTTONS:
            break
        callback_data = f"tasks_tag:{tag}"
        if len(callback_data.encode("utf-8")) > _MAX_CALLBACK_DATA_BYTES:
            # Can't safely address this tag by value — skip its button
            # rather than let it poison the whole keyboard.
            continue
        row.append(InlineKeyboardButton(text=f"#{tag}", callback_data=callback_data))
        shown += 1
        if len(row) == 2:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_filter_back", "🔙 All tasks"), callback_data="tasks_page_0"),
        InlineKeyboardButton(text=l10n["btn_close"], callback_data="close_tasks"),
    )
    return builder.as_markup()


def get_tasks_list_keyboard(
    tasks: list[Any],  # type: ignore
    l10n: dict[str, Any],
    *,
    page: int = 0,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    """
    Keyboard for task list view.

    Shows Done, Settings, and Delete buttons for each task, plus navigation controls.

    Args:
        tasks: List of Reminder objects (or dicts with 'id' and 'reminder_text') — one page's worth
        l10n: Localization dictionary
        page: 0-based index of the page currently shown
        total_pages: Total number of pages across the full task list

    Returns:
        InlineKeyboardMarkup with per-task actions and navigation

    Example:
        >>> markup = get_tasks_list_keyboard(my_tasks, ru)
        # Shows each task with Done! and Delete buttons
    """
    builder = InlineKeyboardBuilder()

    for row in _build_task_action_rows(tasks, l10n):
        builder.row(*row)

    # 3.4: search/filter entry points — a full-text search plus quick
    # filters over the same active task set get_user_reminders queries.
    if total_pages <= 1:
        builder.row(
            InlineKeyboardButton(text=l10n.get("btn_filter_today", "📅 Today"), callback_data="tasks_filter_today"),
            InlineKeyboardButton(text=l10n.get("btn_filter_week", "🗓 This week"), callback_data="tasks_filter_week"),
        )
        builder.row(
            InlineKeyboardButton(text=l10n.get("btn_filter_overdue", "⏰ Overdue"), callback_data="tasks_filter_overdue"),
            InlineKeyboardButton(text=l10n.get("btn_filter_recurring", "🔁 Recurring"), callback_data="tasks_filter_recurring"),
        )
        builder.row(
            InlineKeyboardButton(text=l10n.get("btn_filter_tags", "🏷 Tags"), callback_data="tasks_tags_menu"),
        )

    # Page navigation row — only shown when there's more than one page.
    if total_pages > 1:
        page_row = []
        if page > 0:
            page_row.append(
                InlineKeyboardButton(text=l10n.get("btn_prev_page", "◀️"), callback_data=f"tasks_page_{page - 1}")
            )
        page_row.append(
            InlineKeyboardButton(
                text=l10n.get("tasks_page_indicator", "📄 {page}/{total}").format(page=page + 1, total=total_pages),
                callback_data="noop",
            )
        )
        if page < total_pages - 1:
            page_row.append(
                InlineKeyboardButton(text=l10n.get("btn_next_page", "▶️"), callback_data=f"tasks_page_{page + 1}")
            )
        builder.row(*page_row)

    # Navigation row — Refresh re-renders the CURRENT page, not page 0, so
    # an action taken from here and a manual refresh both keep the user
    # where they were instead of bouncing back to the start of the list.
    builder.row(
        InlineKeyboardButton(text=l10n["btn_refresh"], callback_data=f"tasks_page_{page}"),
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


def get_fluid_pick_time_keyboard(reminder_id: int, l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Keyboard for selecting today's reminder time for fluid habit."""
    builder = InlineKeyboardBuilder()
    for label in ("09:00", "12:00", "15:00", "18:00", "21:00"):
        callback_time = label.replace(":", "")
        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=f"fluid_pick_{reminder_id}_{callback_time}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("fluid_time_custom", "⌨️ Custom time"),
            callback_data=f"fluid_pick_custom_{reminder_id}",
        )
    )
    return builder.as_markup()


def get_fluid_completion_keyboard(tasks: list[Any], l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Keyboard asking completion for fluid habits in evening."""
    builder = InlineKeyboardBuilder()
    for task in tasks:
        task_id = task.id if hasattr(task, "id") else task.get("id")
        text = task.reminder_text if hasattr(task, "reminder_text") else str(task.get("reminder_text", "Habit"))
        preview = (text[:24] + "…") if len(text) > 24 else text
        builder.row(
            InlineKeyboardButton(
                text=l10n.get("fluid_done_btn_prefix", "✅ Done: ") + preview,
                callback_data=f"fluid_done_{task_id}",
            )
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
    
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_briefs_setup", "📋 Briefs setup"),
            callback_data="settings_briefs_setup"
        )
    )

    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_quiet_hours_setup", "😴 Quiet hours"),
            callback_data="settings_quiet_setup",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_habit_reports_setup", "📊 Habit reports"),
            callback_data="settings_habit_reports_setup",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_missed_recovery_setup", "📎 Missed tasks"),
            callback_data="settings_missed_recovery_setup",
        )
    )

    # 3.3: export before delete — psychologically easier to clear an
    # account when you can grab your data first.
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_export_data", "📤 Export data"),
            callback_data="settings_export_data",
        )
    )

    # 4.4: read-only .ics calendar feed link.
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_ics_feed", "📅 Calendar feed"),
            callback_data="settings_ics_feed",
        )
    )

    # 4.6: Mini App entry point — only shown once a real MINI_APP_URL is
    # configured (see bot/config.py). web_app buttons require an https://
    # URL Telegram's client can actually load; there is no meaningful
    # fallback for an unset one, so the button is simply absent instead of
    # shipping a button that can never work.
    if config.MINI_APP_URL:
        builder.row(
            InlineKeyboardButton(
                text=l10n.get("btn_open_mini_app", "📊 Open progress view"),
                web_app=WebAppInfo(url=config.MINI_APP_URL),
            )
        )

    # 5.1: voluntary Telegram Stars tip jar — not gating anything.
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_donate", "☕ Support Porabot"),
            callback_data="donate_open",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_clear_all", "🗑 Clear all"),
            callback_data="settings_clear_all",
        )
    )

    return builder.as_markup()


def get_ics_feed_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """4.4: keyboard for the calendar-feed screen — regenerate (revoke the
    old URL) or go back to Settings."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_ics_regenerate", "🔄 Generate new link"),
            callback_data="settings_ics_feed_regenerate",
        )
    )
    builder.row(InlineKeyboardButton(text=l10n.get("btn_back_settings"), callback_data="settings_back"))
    return builder.as_markup()


# 5.1: preset Telegram Stars donation amounts — a tip jar, not a paywall
# (see bot/handlers/donate.py's module docstring). Lives here rather than
# in the handler module so get_donate_keyboard has no reason to import
# handlers (this module is imported BY handlers, never the other way).
DONATION_PRESETS = [25, 50, 100, 200]


def get_donate_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Amount-picker for the 5.1 Telegram Stars donation flow."""
    builder = InlineKeyboardBuilder()
    buttons = [
        InlineKeyboardButton(text=f"⭐ {amount}", callback_data=f"donate_amount_{amount}")
        for amount in DONATION_PRESETS
    ]
    builder.row(*buttons[:2])
    if len(buttons) > 2:
        builder.row(*buttons[2:])
    builder.row(InlineKeyboardButton(text=l10n.get("btn_back_settings"), callback_data="settings_back"))
    return builder.as_markup()


def get_clear_all_confirm_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Confirmation keyboard for full account reset."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_clear_all_confirm", "✅ Yes, clear all"),
            callback_data="settings_clear_all_confirm",
        ),
        InlineKeyboardButton(
            text=l10n.get("btn_clear_all_cancel", "↩ Cancel"),
            callback_data="settings_back",
        ),
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
    Keyboard for selecting interface language.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=l10n["lang_ru"], callback_data="set_lang_ru"),
        InlineKeyboardButton(text=l10n["lang_en"], callback_data="set_lang_en"),
    )
    builder.row(
        InlineKeyboardButton(text=l10n["lang_es"], callback_data="set_lang_es"),
    )
    return builder.as_markup()

# =============================================================================
# BRIEFS SETUP KEYBOARDS
# =============================================================================

def get_briefs_setup_keyboard(l10n: dict[str, Any], enabled: bool, morning_str: str, evening_str: str) -> InlineKeyboardMarkup:
    """Keyboard for Custom Daily Briefs."""
    builder = InlineKeyboardBuilder()
    
    toggle_text = l10n.get("btn_briefs_on") if enabled else l10n.get("btn_briefs_off")
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data="briefs_toggle"))
    
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_morning_brief").format(time=morning_str), callback_data="briefs_edit_morning"),
        InlineKeyboardButton(text=l10n.get("btn_evening_brief").format(time=evening_str), callback_data="briefs_edit_evening")
    )
    
    builder.row(InlineKeyboardButton(text=l10n.get("btn_back_settings"), callback_data="settings_back"))
    return builder.as_markup()


def get_habit_reports_setup_keyboard(
    l10n: dict[str, Any],
    enabled: bool,
    weekday: int,
    time_str: str,
) -> InlineKeyboardMarkup:
    """Keyboard for weekly/monthly habit reports setup."""
    builder = InlineKeyboardBuilder()

    toggle_text = l10n.get("btn_habit_reports_on") if enabled else l10n.get("btn_habit_reports_off")
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data="habit_reports_toggle"))

    weekday_names = l10n.get("weekday_names") or ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_label = weekday_names[weekday] if 0 <= weekday < len(weekday_names) else str(weekday)
    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_habit_report_day", "📅 Day: {day}").format(day=day_label),
            callback_data="habit_report_edit_day",
        ),
        InlineKeyboardButton(
            text=l10n.get("btn_habit_report_time", "🕒 Time: {time}").format(time=time_str),
            callback_data="habit_report_edit_time",
        ),
    )

    builder.row(InlineKeyboardButton(text=l10n.get("btn_back_settings"), callback_data="settings_back"))
    return builder.as_markup()


def get_habit_report_day_keyboard(l10n: dict[str, Any]) -> InlineKeyboardMarkup:
    """Keyboard for picking the habit report weekday (0=Mon .. 6=Sun)."""
    builder = InlineKeyboardBuilder()
    weekday_names = l10n.get("weekday_names") or ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for i, name in enumerate(weekday_names):
        builder.row(InlineKeyboardButton(text=name, callback_data=f"habit_report_day_{i}"))
    return builder.as_markup()


def get_missed_recovery_setup_keyboard(
    l10n: dict[str, Any],
    enabled: bool,
    time_str: str,
) -> InlineKeyboardMarkup:
    """Keyboard for missed-task recovery digest setup."""
    builder = InlineKeyboardBuilder()

    toggle_text = l10n.get("btn_missed_recovery_on") if enabled else l10n.get("btn_missed_recovery_off")
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data="missed_recovery_toggle"))

    builder.row(
        InlineKeyboardButton(
            text=l10n.get("btn_missed_recovery_time", "🕒 Time: {time}").format(time=time_str),
            callback_data="missed_recovery_edit_time",
        )
    )

    builder.row(InlineKeyboardButton(text=l10n.get("btn_back_settings"), callback_data="settings_back"))
    return builder.as_markup()


def get_quiet_hours_setup_keyboard(
    l10n: dict[str, Any],
    *,
    enabled: bool,
    start_time: str,
    end_time: str,
    weekend_enabled: bool = False,
    weekend_start_time: str = "23:00",
    weekend_end_time: str = "10:00",
    habits_exempt: bool = False,
) -> InlineKeyboardMarkup:
    """Keyboard for Quiet Hours setup.

    3.5: adds a separate weekend window (Sat/Sun) and a "habits can wake,
    regular tasks can't" flag on top of the original single all-week window.
    """
    builder = InlineKeyboardBuilder()
    toggle_text = l10n.get("btn_quiet_on") if enabled else l10n.get("btn_quiet_off")
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data="quiet_toggle"))
    builder.row(
        InlineKeyboardButton(text=l10n.get("btn_quiet_start").format(time=start_time), callback_data="quiet_edit_start"),
        InlineKeyboardButton(text=l10n.get("btn_quiet_end").format(time=end_time), callback_data="quiet_edit_end"),
    )

    weekend_toggle_text = (
        l10n.get("btn_quiet_weekend_on", "🏖 Weekend window: ON")
        if weekend_enabled
        else l10n.get("btn_quiet_weekend_off", "🏖 Weekend window: OFF")
    )
    builder.row(InlineKeyboardButton(text=weekend_toggle_text, callback_data="quiet_weekend_toggle"))
    if weekend_enabled:
        builder.row(
            InlineKeyboardButton(
                text=l10n.get("btn_quiet_weekend_start", "🌙 Weekend start: {time}").format(time=weekend_start_time),
                callback_data="quiet_edit_weekend_start",
            ),
            InlineKeyboardButton(
                text=l10n.get("btn_quiet_weekend_end", "🌅 Weekend end: {time}").format(time=weekend_end_time),
                callback_data="quiet_edit_weekend_end",
            ),
        )

    habits_exempt_text = (
        l10n.get("btn_quiet_habits_exempt_on", "🔔 Habits can wake me: ON")
        if habits_exempt
        else l10n.get("btn_quiet_habits_exempt_off", "🔕 Habits can wake me: OFF")
    )
    builder.row(InlineKeyboardButton(text=habits_exempt_text, callback_data="quiet_habits_exempt_toggle"))

    builder.row(InlineKeyboardButton(text=l10n.get("btn_back_settings"), callback_data="settings_back"))
    return builder.as_markup()
