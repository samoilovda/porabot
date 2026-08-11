"""Settings handlers — timezone, language, and UTC offset preferences."""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import pytz
from aiogram import Router, F
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.database.dao.user import UserDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.models import User
from bot.keyboards.inline import (
    get_timezone_keyboard,
    get_settings_keyboard,
    get_language_selection_keyboard,
    get_clear_all_confirm_keyboard,
)
from bot.keyboards.reply import get_main_menu_keyboard
from bot.services.scheduler import SchedulerService
from bot.utils.markdown import escape_markdown

class SettingsState(StatesGroup):
    waiting_for_brief_time = State()
    waiting_for_timezone = State()
    waiting_for_quiet_time = State()
    waiting_for_habit_report_time = State()
    waiting_for_missed_recovery_time = State()

router = Router(name="settings")
logger = logging.getLogger(__name__)

# Half/quarter-hour UTC offsets mapped to a real IANA zone that actually uses
# that offset today, so users in these regions get correct DST behavior
# instead of a frozen fixed offset. Not exhaustive — offsets with no matching
# well-known zone are rejected rather than silently approximated (W8).
_HALF_HOUR_TZ_MAP: dict[str, str] = {
    "+3:30": "Asia/Tehran",
    "+4:30": "Asia/Kabul",
    "+5:30": "Asia/Kolkata",
    "+5:45": "Asia/Kathmandu",
    "+6:30": "Asia/Yangon",
    "+8:45": "Australia/Eucla",
    "+9:30": "Australia/Darwin",
    "+10:30": "Australia/Lord_Howe",
    "+12:45": "Pacific/Chatham",
    "-3:30": "America/St_Johns",
    "-9:30": "Pacific/Marquesas",
}


def _resolve_timezone_candidate(raw: str) -> str:
    """
    Normalize manual timezone input from UTC offset to a canonical IANA timezone.

    Supported manual formats:
      - +5, -6, 0                    → whole-hour offset
      - +5:30, -3:30, +5:45, etc.    → known half/quarter-hour offset (see
                                        _HALF_HOUR_TZ_MAP)

    Whole-hour offsets resolve to Etc/GMT±N, a *fixed* offset with no DST —
    accurate today, but a user in a DST-observing region will drift by an
    hour when their local clocks change. The keyboard presets (real IANA
    city zones) don't have this problem; manual entry is a deliberate
    trade-off for regions not covered by the preset list.

    Returns:
      - UTC for 0
      - Etc/GMT-5 for +5, Etc/GMT+6 for -6 (note the reversed IANA sign)
      - a real IANA zone for known half/quarter-hour offsets
    """
    candidate = (raw or "").strip().replace(",", ".")
    # Accept "+5.5" / "-3.5" as an alternate spelling of "+5:30" / "-3:30".
    half_match = re.fullmatch(r"([+-]?\d{1,2})\.5", candidate)
    if half_match:
        candidate = f"{half_match.group(1)}:30"
    if not candidate.startswith(("+", "-")) and ":" in candidate:
        candidate = f"+{candidate}"

    if ":" in candidate:
        mapped = _HALF_HOUR_TZ_MAP.get(candidate)
        if mapped is None:
            raise pytz.UnknownTimeZoneError(raw)
        return mapped

    if not re.fullmatch(r"[+-]?\d{1,2}", candidate):
        raise pytz.UnknownTimeZoneError(raw)

    hours = int(candidate)
    if hours < -12 or hours > 14:
        raise pytz.UnknownTimeZoneError(raw)

    if hours == 0:
        return "UTC"

    # NOTE: IANA Etc/GMT has reversed sign semantics by convention.
    sign = "-" if hours > 0 else "+"
    return f"Etc/GMT{sign}{abs(hours)}"


def _format_tz_display_label(tz_name: str) -> str:
    """Friendly timezone label with current UTC offset."""
    try:
        tz = pytz.timezone(tz_name)
        now_local = datetime.now(tz)
        raw = now_local.strftime("%z")  # +HHMM
        if raw and len(raw) == 5:
            return f"{tz_name} (UTC{raw[:3]}:{raw[3:]})"
    except Exception:
        pass
    return tz_name


def _quiet_hours_label(user: User, l10n: dict[str, Any]) -> str:
    enabled = bool(getattr(user, "quiet_hours_enabled", False))
    start = getattr(user, "quiet_hours_start", "23:00")
    end = getattr(user, "quiet_hours_end", "07:00")
    status = l10n.get("status_on", "ON") if enabled else l10n.get("status_off", "OFF")
    return l10n.get("quiet_hours_summary", "{status} ({start}–{end})").format(
        status=status,
        start=start,
        end=end,
    )


def _render_settings_text(user: User, l10n: dict[str, Any]) -> str:
    return l10n["settings_text"].format(
        timezone=_format_tz_display_label(user.timezone),
        quiet_hours=_quiet_hours_label(user, l10n),
    )


# NOTE: the "⚙️ Settings" main-menu button handler lives in
# bot/handlers/menu.py (registered on an earlier router) so it can't be
# swallowed by another router's stateful FSM handlers. _render_settings_text
# above is still used throughout this module and imported from here by
# menu.py's btn_settings.

@router.callback_query(F.data == "settings_toggle_utc")
async def callback_toggle_utc(callback: CallbackQuery, user_dao: UserDAO, user: User, l10n: dict[str, Any]) -> None:
    new_val = not user.show_utc_offset
    await user_dao.update_show_utc_offset(user.id, new_val)
    text = _render_settings_text(user, l10n)
    await callback.message.edit_text(text, reply_markup=get_settings_keyboard(l10n, new_val), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "settings_change_tz")
async def callback_change_tz(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    await callback.message.edit_text(l10n["choose_tz"], reply_markup=get_timezone_keyboard(l10n))
    await callback.answer()


@router.callback_query(F.data == "settings_change_lang")
async def callback_change_lang(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    await callback.message.edit_text(l10n["choose_language"], reply_markup=get_language_selection_keyboard(l10n))
    await callback.answer()


def _dt_iso(value) -> Any:
    return value.isoformat() if value else None


def build_data_export(user: User, reminders, habit_events) -> dict:
    """Build the JSON-serializable payload for the 3.3 data export: user
    settings + reminders + habit_events. Only excludes reminders currently
    inside the undo-delete window (pending_delete_at set) — those are about
    to be purged and aren't meaningfully "the user's data" any more."""
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user": {
            "id": user.id,
            "timezone": user.timezone,
            "language": user.language,
            "show_utc_offset": bool(getattr(user, "show_utc_offset", False)),
            "quiet_hours_enabled": bool(getattr(user, "quiet_hours_enabled", False)),
            "quiet_hours_start": getattr(user, "quiet_hours_start", None),
            "quiet_hours_end": getattr(user, "quiet_hours_end", None),
            "quiet_hours_weekend_enabled": bool(getattr(user, "quiet_hours_weekend_enabled", False)),
            "quiet_hours_weekend_start": getattr(user, "quiet_hours_weekend_start", None),
            "quiet_hours_weekend_end": getattr(user, "quiet_hours_weekend_end", None),
            "quiet_hours_habits_exempt": bool(getattr(user, "quiet_hours_habits_exempt", False)),
            "briefs_enabled": bool(getattr(user, "briefs_enabled", True)),
            "morning_brief_time": getattr(user, "morning_brief_time", None),
            "evening_brief_time": getattr(user, "evening_brief_time", None),
            "missed_recovery_enabled": bool(getattr(user, "missed_recovery_enabled", True)),
            "missed_recovery_time": getattr(user, "missed_recovery_time", None),
            "habit_reports_enabled": bool(getattr(user, "habit_reports_enabled", True)),
            "habit_report_weekday": getattr(user, "habit_report_weekday", None),
            "habit_report_time": getattr(user, "habit_report_time", None),
        },
        "reminders": [
            {
                "id": r.id,
                "text": r.reminder_text,
                "execution_time": _dt_iso(r.execution_time),
                "is_recurring": r.is_recurring,
                "rrule_string": r.rrule_string,
                "is_habit": r.is_habit,
                "is_fluid_habit": r.is_fluid_habit,
                "fluid_mode": r.fluid_mode,
                "status": r.status,
                "is_nagging": r.is_nagging,
                "nagging_max_repeats": r.nagging_max_repeats,
                "habit_streak_current": r.habit_streak_current,
                "habit_streak_best": r.habit_streak_best,
                "fluid_streak_current": getattr(r, "fluid_streak_current", 0),
                "fluid_streak_best": getattr(r, "fluid_streak_best", 0),
                "completed_at": _dt_iso(r.completed_at),
                "created_at": _dt_iso(r.created_at),
            }
            for r in reminders
            if getattr(r, "pending_delete_at", None) is None
        ],
        "habit_events": [
            {
                "reminder_id": e.reminder_id,
                "habit_text": e.habit_text,
                "local_date": e.local_date,
                "due_at": _dt_iso(e.due_at),
                "outcome": e.outcome,
                "source": e.source,
                "created_at": _dt_iso(e.created_at),
            }
            for e in habit_events
        ],
    }


@router.callback_query(F.data == "settings_export_data")
async def callback_export_data(
    callback: CallbackQuery,
    user: User,
    reminder_dao: ReminderDAO,
    habit_event_dao: HabitEventDAO,
    l10n: dict[str, Any],
) -> None:
    try:
        reminders = await reminder_dao.get_all(user_id=user.id)
        habit_events = await habit_event_dao.get_all(user_id=user.id)
        payload = build_data_export(user, reminders, habit_events)
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        await callback.message.answer_document(
            BufferedInputFile(data, filename=f"porabot_export_{user.id}.json"),
            caption=l10n.get("export_data_caption", "📤 Here is your data export."),
        )
    except Exception as e:
        logger.error("Data export failed for user %s: %s", user.id, e, exc_info=True)
        await callback.answer(l10n.get("export_data_error", "❌ Failed to export data."), show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data == "settings_clear_all")
async def callback_clear_all_prompt(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    await callback.message.edit_text(
        l10n.get(
            "clear_all_confirm_text",
            "⚠️ This will delete all your tasks, habits, and settings permanently.\n\nAre you sure?",
        ),
        reply_markup=get_clear_all_confirm_keyboard(l10n),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "settings_clear_all_confirm")
async def callback_clear_all_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    user: User,
    user_dao: UserDAO,
    reminder_dao: ReminderDAO,
    habit_event_dao: HabitEventDAO,
    scheduler_service: SchedulerService,
    l10n: dict[str, Any],
) -> None:
    # Events reference both reminders and users, so they must go first.
    await habit_event_dao.delete_for_user(user.id)

    reminders = await reminder_dao.get_all(user_id=user.id)
    for task in reminders:
        scheduler_service.remove_reminder_job(task.id)
        await reminder_dao.delete_by_id(task.id)

    await user_dao.delete_by_id(user.id)
    await state.clear()

    await callback.message.edit_text(
        l10n.get("clear_all_done", "✅ All data deleted. Starting from scratch."),
        reply_markup=None,
    )
    await callback.message.answer(
        l10n["choose_language"],
        reply_markup=get_language_selection_keyboard(l10n),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_tz_"))
async def callback_set_tz(
    callback: CallbackQuery, user_dao: UserDAO, user: User, l10n: dict[str, Any], state: FSMContext
) -> None:
    state_data = await state.get_data()
    is_onboarding_tz = bool(state_data.get("onboarding_timezone"))
    action = callback.data.split("set_tz_")[1]
    if action == "manual":
        await state.set_state(SettingsState.waiting_for_timezone)
        await callback.message.edit_text(l10n["tz_manual_prompt"])
        await callback.answer()
        return
    await user_dao.update_timezone(user.id, action)
    user.timezone = action
    await callback.message.edit_text(
        l10n["tz_success"].format(tz=_format_tz_display_label(action)),
        reply_markup=None,
    )
    if is_onboarding_tz:
        await state.clear()
        text = l10n["cmd_start"].format(name=escape_markdown(callback.from_user.first_name))
        await callback.message.answer(text, reply_markup=get_main_menu_keyboard(l10n))
    await callback.answer()


@router.message(SettingsState.waiting_for_timezone, F.text)
async def state_set_manual_timezone(
    message: Message, state: FSMContext, user: User, user_dao: UserDAO, l10n: dict[str, Any]
) -> None:
    tz_candidate = message.text.strip()
    try:
        resolved_tz = _resolve_timezone_candidate(tz_candidate)
    except pytz.UnknownTimeZoneError:
        await message.answer(
            l10n.get(
                "tz_invalid",
                "❌ Invalid timezone offset. Use `+5`, `0`, or `-6`.",
            ),
            parse_mode="Markdown",
        )
        return

    state_data = await state.get_data()
    is_onboarding_tz = bool(state_data.get("onboarding_timezone"))

    await user_dao.update_timezone(user.id, resolved_tz)
    user.timezone = resolved_tz
    await state.clear()

    await message.answer(
        l10n["tz_success"].format(tz=_format_tz_display_label(resolved_tz)),
        parse_mode="Markdown",
    )
    if is_onboarding_tz:
        text = l10n["cmd_start"].format(name=escape_markdown(message.from_user.first_name))
        await message.answer(text, reply_markup=get_main_menu_keyboard(l10n))
    else:
        await message.answer(
            _render_settings_text(user, l10n),
            reply_markup=get_settings_keyboard(l10n, user.show_utc_offset),
            parse_mode="Markdown",
        )

@router.callback_query(F.data == "settings_back")
async def callback_settings_back(callback: CallbackQuery, user: User, l10n: dict[str, Any]) -> None:
    from bot.keyboards.inline import get_settings_keyboard
    text = _render_settings_text(user, l10n)
    await callback.message.edit_text(text, reply_markup=get_settings_keyboard(l10n, user.show_utc_offset), parse_mode="Markdown")
    await callback.answer()


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
    from bot.keyboards.inline import get_quiet_hours_setup_keyboard

    await callback.message.edit_reply_markup(
        reply_markup=get_quiet_hours_setup_keyboard(l10n, **_quiet_hours_kwargs(user))
    )
    await callback.answer()


@router.callback_query(F.data == "quiet_toggle")
async def callback_quiet_toggle(
    callback: CallbackQuery, user: User, user_dao: UserDAO, l10n: dict[str, Any], state: FSMContext
) -> None:
    await state.clear()
    from bot.keyboards.inline import get_quiet_hours_setup_keyboard

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
    from bot.keyboards.inline import get_quiet_hours_setup_keyboard

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
    from bot.keyboards.inline import get_quiet_hours_setup_keyboard

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

@router.callback_query(F.data == "settings_briefs_setup")
async def callback_briefs_setup(callback: CallbackQuery, user: User, l10n: dict[str, Any], state: FSMContext) -> None:
    await state.clear()
    from bot.keyboards.inline import get_briefs_setup_keyboard
    enabled = getattr(user, 'briefs_enabled', True)
    morning = getattr(user, 'morning_brief_time', "09:00")
    evening = getattr(user, 'evening_brief_time', "23:00")
    await callback.message.edit_reply_markup(reply_markup=get_briefs_setup_keyboard(l10n, enabled, morning, evening))
    await callback.answer()

@router.callback_query(F.data == "briefs_toggle")
async def callback_briefs_toggle(callback: CallbackQuery, user: User, user_dao: UserDAO, l10n: dict[str, Any], state: FSMContext) -> None:
    await state.clear()  # BUG-H3 FIX: clear any pending FSM state so next message isn't swallowed
    from bot.keyboards.inline import get_briefs_setup_keyboard
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
    
    # BUG-H4 FIX: Strict HH:MM validation — the InputParser is designed for full
    # reminder phrases, not time-only config. Freeform inputs like "in 30 minutes"
    # would produce the wrong brief time with no feedback to the user.
    import re
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
    
    from bot.keyboards.inline import get_briefs_setup_keyboard
    text = _render_settings_text(user, l10n)
    await message.answer(text, reply_markup=get_briefs_setup_keyboard(l10n, enabled, morning, evening), parse_mode="Markdown")


@router.message(SettingsState.waiting_for_quiet_time)
async def state_set_quiet_time(
    message: Message, state: FSMContext, user: User, user_dao: UserDAO, l10n: dict[str, Any]
) -> None:
    if not message.text:
        return

    import re

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

    from bot.keyboards.inline import get_quiet_hours_setup_keyboard

    await message.answer(
        l10n.get("quiet_time_saved", "✅ Quiet hours updated: {time}").format(time=value),
        parse_mode="Markdown",
    )
    await message.answer(
        _render_settings_text(user, l10n),
        reply_markup=get_quiet_hours_setup_keyboard(l10n, **_quiet_hours_kwargs(user)),
        parse_mode="Markdown",
    )


@router.callback_query(F.data == "settings_habit_reports_setup")
async def callback_habit_reports_setup(
    callback: CallbackQuery, user: User, l10n: dict[str, Any], state: FSMContext
) -> None:
    await state.clear()
    from bot.keyboards.inline import get_habit_reports_setup_keyboard

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
    from bot.keyboards.inline import get_habit_reports_setup_keyboard

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
    from bot.keyboards.inline import get_habit_report_day_keyboard

    await callback.message.edit_text(
        l10n.get("habit_report_day_prompt", "Choose the day for your habit report:"),
        reply_markup=get_habit_report_day_keyboard(l10n),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("habit_report_day_"))
async def callback_habit_report_day_selected(
    callback: CallbackQuery, user: User, user_dao: UserDAO, l10n: dict[str, Any]
) -> None:
    from bot.keyboards.inline import get_habit_reports_setup_keyboard

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
    # is for freeform reminder phrases, not config values (BUG-H4).
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

    from bot.keyboards.inline import get_habit_reports_setup_keyboard

    await message.answer(
        l10n.get("habit_report_time_saved", "✅ Habit report time updated: {time}").format(time=value),
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


@router.callback_query(F.data == "settings_missed_recovery_setup")
async def callback_missed_recovery_setup(
    callback: CallbackQuery, user: User, l10n: dict[str, Any], state: FSMContext
) -> None:
    await state.clear()
    from bot.keyboards.inline import get_missed_recovery_setup_keyboard

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
    from bot.keyboards.inline import get_missed_recovery_setup_keyboard

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

    from bot.keyboards.inline import get_missed_recovery_setup_keyboard

    await message.answer(
        l10n.get("missed_recovery_time_saved", "✅ Missed-task digest time updated: {time}").format(time=value),
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
