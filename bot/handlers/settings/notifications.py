""""Уведомления" settings group (step 9, 2026-09-26 audit remediation):
quiet hours, daily briefs, habit reports, and the missed-task recovery
digest — split out of the original bot/handlers/settings.py, see the
package's __init__.py docstring for the full module family and why.
"""

import logging
import re
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database.dao.user import UserDAO
from bot.database.models import User
from bot.handlers.settings._common import SettingsState, _render_settings_text
from bot.keyboards.inline import (
    get_briefs_setup_keyboard,
    get_habit_report_day_keyboard,
    get_habit_reports_setup_keyboard,
    get_missed_recovery_setup_keyboard,
    get_notifications_group_keyboard,
    get_quiet_hours_setup_keyboard,
)
from bot.keyboards.reply import get_main_menu_keyboard

router = Router(name="settings_notifications")
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "settings_group_notifications")
async def callback_settings_group_notifications(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    """Step 9: entry point into this group from the top-level Settings
    screen — same settings text, just the group's own keyboard overlaid."""
    await callback.message.edit_reply_markup(reply_markup=get_notifications_group_keyboard(l10n))
    await callback.answer()


# --- Quiet hours -------------------------------------------------------

def _quiet_hours_kwargs(user: User) -> dict[str, Any]:
    """Keyword args for get_quiet_hours_setup_keyboard, read from *user* —
    shared by every handler that (re)renders that keyboard (3.5)."""
    return dict(
        enabled=bool(getattr(user, "quiet_hours_enabled", False)),
        start_time=getattr(user, "quiet_hours_start", "23:00"),
        end_time=getattr(user, "quiet_hours_end", "07:00"),
        weekend_enabled=bool(getattr(user, "quiet_hours_weekend_enabled", False)),
        weekend_start_time=getattr(user, "quiet_hours_weekend_start", "23:00"),
        weekend_end_time=getattr(user, "quiet_hours_weekend_end", "10:00"),
        habits_exempt=bool(getattr(user, "quiet_hours_habits_exempt", False)),
    )


@router.callback_query(F.data == "settings_quiet_setup")
async def callback_quiet_setup(callback: CallbackQuery, user: User, l10n: dict[str, Any], state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(
        reply_markup=get_quiet_hours_setup_keyboard(l10n, **_quiet_hours_kwargs(user))
    )
    await callback.answer()


@router.callback_query(F.data == "quiet_toggle")
async def callback_quiet_toggle(
    callback: CallbackQuery, user: User, user_dao: UserDAO, l10n: dict[str, Any], state: FSMContext
) -> None:
    await state.clear()
    enabled = not bool(getattr(user, "quiet_hours_enabled", False))
    await user_dao.update_settings(user.id, quiet_hours_enabled=enabled)
    user.quiet_hours_enabled = enabled
    await callback.message.edit_reply_markup(
        reply_markup=get_quiet_hours_setup_keyboard(l10n, **_quiet_hours_kwargs(user))
    )
    await callback.answer()


@router.callback_query(F.data == "quiet_weekend_toggle")
async def callback_quiet_weekend_toggle(
    callback: CallbackQuery, user: User, user_dao: UserDAO, l10n: dict[str, Any], state: FSMContext
) -> None:
    await state.clear()
    enabled = not bool(getattr(user, "quiet_hours_weekend_enabled", False))
    await user_dao.update_settings(user.id, quiet_hours_weekend_enabled=enabled)
    user.quiet_hours_weekend_enabled = enabled
    await callback.message.edit_reply_markup(
        reply_markup=get_quiet_hours_setup_keyboard(l10n, **_quiet_hours_kwargs(user))
    )
    await callback.answer()


@router.callback_query(F.data == "quiet_habits_exempt_toggle")
async def callback_quiet_habits_exempt_toggle(
    callback: CallbackQuery, user: User, user_dao: UserDAO, l10n: dict[str, Any], state: FSMContext
) -> None:
    await state.clear()
    exempt = not bool(getattr(user, "quiet_hours_habits_exempt", False))
    await user_dao.update_settings(user.id, quiet_hours_habits_exempt=exempt)
    user.quiet_hours_habits_exempt = exempt
    await callback.message.edit_reply_markup(
        reply_markup=get_quiet_hours_setup_keyboard(l10n, **_quiet_hours_kwargs(user))
    )
    await callback.answer()


@router.callback_query(
    F.data.in_(["quiet_edit_start", "quiet_edit_end", "quiet_edit_weekend_start", "quiet_edit_weekend_end"])
)
async def callback_quiet_edit_time(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    target = callback.data.removeprefix("quiet_edit_")  # start | end | weekend_start | weekend_end
    await state.update_data(quiet_target=target)
    await state.set_state(SettingsState.waiting_for_quiet_time)
    await callback.message.edit_text(
        l10n.get("quiet_time_prompt", "Please type time in HH:MM format (e.g. `23:00`)."),
        reply_markup=None,
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(SettingsState.waiting_for_quiet_time)
async def state_set_quiet_time(
    message: Message, state: FSMContext, user: User, user_dao: UserDAO, l10n: dict[str, Any]
) -> None:
    if not message.text:
        return

    raw = message.text.strip()
    match = re.match(r'^(\d{1,2}):(\d{2})$', raw)
    if not match:
        await message.answer(l10n.get("quiet_time_invalid", "❌ Please enter time in HH:MM format."), parse_mode="Markdown")
        return

    h, m = int(match.group(1)), int(match.group(2))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        await message.answer(l10n.get("quiet_time_invalid", "❌ Please enter time in HH:MM format."), parse_mode="Markdown")
        return

    value = f"{h:02d}:{m:02d}"
    data = await state.get_data()
    target = data.get("quiet_target")
    field_by_target = {
        "start": "quiet_hours_start",
        "end": "quiet_hours_end",
        "weekend_start": "quiet_hours_weekend_start",
        "weekend_end": "quiet_hours_weekend_end",
    }
    field = field_by_target.get(target)
    if field is None:
        await state.clear()
        await message.answer(l10n.get("parse_error", "Error parsing text. Check the format."))
        return
    await user_dao.update_settings(user.id, **{field: value})
    setattr(user, field, value)
    await state.clear()

    # Re-attach the persistent bottom menu here — the user just typed free
    # text, the moment they're most likely to have collapsed it, and every
    # message after this one in this flow only carries an inline keyboard.
    await message.answer(
        l10n.get("quiet_time_saved", "✅ Quiet hours updated: {time}").format(time=value),
        reply_markup=get_main_menu_keyboard(l10n),
        parse_mode="Markdown",
    )
    await message.answer(
        _render_settings_text(user, l10n),
        reply_markup=get_quiet_hours_setup_keyboard(l10n, **_quiet_hours_kwargs(user)),
        parse_mode="Markdown",
    )


# --- Daily briefs --------------------------------------------------------

@router.callback_query(F.data == "settings_briefs_setup")
async def callback_briefs_setup(callback: CallbackQuery, user: User, l10n: dict[str, Any], state: FSMContext) -> None:
    await state.clear()
    enabled = getattr(user, 'briefs_enabled', True)
    morning = getattr(user, 'morning_brief_time', "09:00")
    evening = getattr(user, 'evening_brief_time', "23:00")
    await callback.message.edit_reply_markup(reply_markup=get_briefs_setup_keyboard(l10n, enabled, morning, evening))
    await callback.answer()


@router.callback_query(F.data == "briefs_toggle")
async def callback_briefs_toggle(callback: CallbackQuery, user: User, user_dao: UserDAO, l10n: dict[str, Any], state: FSMContext) -> None:
    await state.clear()  # clear any pending FSM state so the next message isn't swallowed
    enabled = not getattr(user, 'briefs_enabled', True)
    await user_dao.update_briefs_settings(user.id, briefs_enabled=enabled)
    morning = getattr(user, 'morning_brief_time', "09:00")
    evening = getattr(user, 'evening_brief_time', "23:00")
    await callback.message.edit_reply_markup(reply_markup=get_briefs_setup_keyboard(l10n, enabled, morning, evening))
    await callback.answer()


@router.callback_query(F.data.in_(["briefs_edit_morning", "briefs_edit_evening"]))
async def callback_briefs_edit_hour(callback: CallbackQuery, l10n: dict[str, Any], state: FSMContext) -> None:
    target = callback.data.split("_")[-1]  # 'morning' or 'evening'
    await state.update_data(brief_target=target)
    await state.set_state(SettingsState.waiting_for_brief_time)

    # Needs to remove inline keyboard while waiting for input
    await callback.message.edit_text(
        l10n.get("choose_hour", "Please type the time (e.g. 09:30 or 23:45):"),
        reply_markup=None,
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(SettingsState.waiting_for_brief_time)
async def state_briefs_set_time(message: Message, state: FSMContext, user: User, user_dao: UserDAO, l10n: dict[str, Any]) -> None:
    if not message.text:
        return

    # Strict HH:MM validation — the InputParser is designed for full
    # reminder phrases, not time-only config. Freeform inputs like "in 30 minutes"
    # would produce the wrong brief time with no feedback to the user.
    raw = message.text.strip()
    match = re.match(r'^(\d{1,2}):(\d{2})$', raw)
    if not match:
        await message.answer(l10n["brief_time_invalid_format"], parse_mode="Markdown")
        return

    h, m = int(match.group(1)), int(match.group(2))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        await message.answer(l10n["brief_time_invalid_value"], parse_mode="Markdown")
        return

    extracted_time_str = f"{h:02d}:{m:02d}"

    data = await state.get_data()
    target = data.get("brief_target")

    if target == "morning":
        await user_dao.update_briefs_settings(user.id, morning_brief_time=extracted_time_str)
        user.morning_brief_time = extracted_time_str
    elif target == "evening":
        await user_dao.update_briefs_settings(user.id, evening_brief_time=extracted_time_str)
        user.evening_brief_time = extracted_time_str

    await state.clear()

    enabled = getattr(user, 'briefs_enabled', True)
    morning = getattr(user, 'morning_brief_time', "09:00")
    evening = getattr(user, 'evening_brief_time', "23:00")

    text = _render_settings_text(user, l10n)
    await message.answer(text, reply_markup=get_briefs_setup_keyboard(l10n, enabled, morning, evening), parse_mode="Markdown")
    # Unlike its quiet-hours/habit-report/missed-recovery siblings, this
    # flow only ever sends the one inline-keyboard message above — nothing
    # in it carries the persistent bottom menu back after the user typed
    # free text, so resend it as a small separate message.
    await message.answer(l10n.get("main_menu_hint", "👇"), reply_markup=get_main_menu_keyboard(l10n))


# --- Habit reports ---------------------------------------------------------

@router.callback_query(F.data == "settings_habit_reports_setup")
async def callback_habit_reports_setup(
    callback: CallbackQuery, user: User, l10n: dict[str, Any], state: FSMContext
) -> None:
    await state.clear()
    enabled = bool(getattr(user, "habit_reports_enabled", True))
    weekday = int(getattr(user, "habit_report_weekday", 6))
    time_str = getattr(user, "habit_report_time", "23:50")
    await callback.message.edit_reply_markup(
        reply_markup=get_habit_reports_setup_keyboard(l10n, enabled, weekday, time_str)
    )
    await callback.answer()


@router.callback_query(F.data == "habit_reports_toggle")
async def callback_habit_reports_toggle(
    callback: CallbackQuery, user: User, user_dao: UserDAO, l10n: dict[str, Any], state: FSMContext
) -> None:
    await state.clear()
    enabled = not bool(getattr(user, "habit_reports_enabled", True))
    await user_dao.update_habit_report_settings(user.id, habit_reports_enabled=enabled)
    user.habit_reports_enabled = enabled
    weekday = int(getattr(user, "habit_report_weekday", 6))
    time_str = getattr(user, "habit_report_time", "23:50")
    await callback.message.edit_reply_markup(
        reply_markup=get_habit_reports_setup_keyboard(l10n, enabled, weekday, time_str)
    )
    await callback.answer()


@router.callback_query(F.data == "habit_report_edit_day")
async def callback_habit_report_edit_day(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    await callback.message.edit_text(
        l10n.get("habit_report_day_prompt", "Choose the day for your habit report:"),
        reply_markup=get_habit_report_day_keyboard(l10n),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("habit_report_day_"))
async def callback_habit_report_day_selected(
    callback: CallbackQuery, user: User, user_dao: UserDAO, l10n: dict[str, Any]
) -> None:
    try:
        weekday = int(callback.data.split("habit_report_day_")[1])
    except ValueError:
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return
    if not 0 <= weekday <= 6:
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return

    await user_dao.update_habit_report_settings(user.id, habit_report_weekday=weekday)
    user.habit_report_weekday = weekday
    enabled = bool(getattr(user, "habit_reports_enabled", True))
    time_str = getattr(user, "habit_report_time", "23:50")
    await callback.message.edit_text(
        _render_settings_text(user, l10n),
        reply_markup=get_habit_reports_setup_keyboard(l10n, enabled, weekday, time_str),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "habit_report_edit_time")
async def callback_habit_report_edit_time(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.set_state(SettingsState.waiting_for_habit_report_time)
    await callback.message.edit_text(
        l10n.get("habit_report_time_prompt", "Please type the report time in HH:MM format (e.g. `23:50`)."),
        reply_markup=None,
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(SettingsState.waiting_for_habit_report_time, F.text)
async def state_habit_report_set_time(
    message: Message, state: FSMContext, user: User, user_dao: UserDAO, l10n: dict[str, Any]
) -> None:
    # Same strict HH:MM validation as briefs/quiet-hours time input — InputParser
    # is for freeform reminder phrases, not config values.
    raw = message.text.strip()
    match = re.match(r'^(\d{1,2}):(\d{2})$', raw)
    if not match:
        await message.answer(l10n.get("quiet_time_invalid", "❌ Please enter time in HH:MM format (e.g. `23:00`)."), parse_mode="Markdown")
        return

    h, m = int(match.group(1)), int(match.group(2))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        await message.answer(l10n.get("quiet_time_invalid", "❌ Please enter time in HH:MM format (e.g. `23:00`)."), parse_mode="Markdown")
        return

    value = f"{h:02d}:{m:02d}"
    await user_dao.update_habit_report_settings(user.id, habit_report_time=value)
    user.habit_report_time = value
    await state.clear()

    await message.answer(
        l10n.get("habit_report_time_saved", "✅ Habit report time updated: {time}").format(time=value),
        reply_markup=get_main_menu_keyboard(l10n),
        parse_mode="Markdown",
    )
    await message.answer(
        _render_settings_text(user, l10n),
        reply_markup=get_habit_reports_setup_keyboard(
            l10n,
            enabled=bool(getattr(user, "habit_reports_enabled", True)),
            weekday=int(getattr(user, "habit_report_weekday", 6)),
            time_str=value,
        ),
        parse_mode="Markdown",
    )


# --- Missed-task recovery digest -----------------------------------------

@router.callback_query(F.data == "settings_missed_recovery_setup")
async def callback_missed_recovery_setup(
    callback: CallbackQuery, user: User, l10n: dict[str, Any], state: FSMContext
) -> None:
    await state.clear()
    enabled = bool(getattr(user, "missed_recovery_enabled", True))
    time_str = getattr(user, "missed_recovery_time", "10:00")
    await callback.message.edit_reply_markup(
        reply_markup=get_missed_recovery_setup_keyboard(l10n, enabled, time_str)
    )
    await callback.answer()


@router.callback_query(F.data == "missed_recovery_toggle")
async def callback_missed_recovery_toggle(
    callback: CallbackQuery, user: User, user_dao: UserDAO, l10n: dict[str, Any], state: FSMContext
) -> None:
    await state.clear()
    enabled = not bool(getattr(user, "missed_recovery_enabled", True))
    await user_dao.update_settings(user.id, missed_recovery_enabled=enabled)
    user.missed_recovery_enabled = enabled
    time_str = getattr(user, "missed_recovery_time", "10:00")
    await callback.message.edit_reply_markup(
        reply_markup=get_missed_recovery_setup_keyboard(l10n, enabled, time_str)
    )
    await callback.answer()


@router.callback_query(F.data == "missed_recovery_edit_time")
async def callback_missed_recovery_edit_time(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.set_state(SettingsState.waiting_for_missed_recovery_time)
    await callback.message.edit_text(
        l10n.get("missed_recovery_time_prompt", "Please type the digest time in HH:MM format (e.g. `10:00`)."),
        reply_markup=None,
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(SettingsState.waiting_for_missed_recovery_time, F.text)
async def state_missed_recovery_set_time(
    message: Message, state: FSMContext, user: User, user_dao: UserDAO, l10n: dict[str, Any]
) -> None:
    raw = message.text.strip()
    match = re.match(r'^(\d{1,2}):(\d{2})$', raw)
    if not match:
        await message.answer(l10n.get("quiet_time_invalid", "❌ Please enter time in HH:MM format (e.g. `23:00`)."), parse_mode="Markdown")
        return

    h, m = int(match.group(1)), int(match.group(2))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        await message.answer(l10n.get("quiet_time_invalid", "❌ Please enter time in HH:MM format (e.g. `23:00`)."), parse_mode="Markdown")
        return

    value = f"{h:02d}:{m:02d}"
    await user_dao.update_settings(user.id, missed_recovery_time=value)
    user.missed_recovery_time = value
    await state.clear()

    await message.answer(
        l10n.get("missed_recovery_time_saved", "✅ Missed-task digest time updated: {time}").format(time=value),
        reply_markup=get_main_menu_keyboard(l10n),
        parse_mode="Markdown",
    )
    await message.answer(
        _render_settings_text(user, l10n),
        reply_markup=get_missed_recovery_setup_keyboard(
            l10n,
            enabled=bool(getattr(user, "missed_recovery_enabled", True)),
            time_str=value,
        ),
        parse_mode="Markdown",
    )
