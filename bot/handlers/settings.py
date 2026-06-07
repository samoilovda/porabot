"""Settings handlers — timezone, language, and UTC offset preferences."""

import logging
import re
from datetime import datetime
from typing import Any

import pytz
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.callbacks import SetTimezoneCallback
from bot.database.dao.user import UserDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.keyboards.inline import (
    get_timezone_keyboard,
    get_settings_keyboard,
    get_language_selection_keyboard,
    get_clear_all_confirm_keyboard,
)
from bot.keyboards.reply import get_main_menu_keyboard
from bot.services.scheduler import SchedulerService
from bot.utils.time_ext import parse_hhmm

class SettingsState(StatesGroup):
    waiting_for_brief_time = State()
    waiting_for_timezone = State()
    waiting_for_quiet_time = State()

router = Router(name="settings")
logger = logging.getLogger(__name__)

def _resolve_timezone_candidate(raw: str) -> str:
    """
    Normalize manual timezone input from UTC offset to a canonical IANA timezone.

    Supported manual formats:
      - +5
      - -6
      - 0

    Returns:
      - UTC for 0
      - Etc/GMT-5 for +5
      - Etc/GMT+6 for -6
    """
    candidate = (raw or "").strip()
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


@router.message(F.text.in_(["⚙️ Настройки", "⚙️ Settings", "⚙️ Ajustes"]))
async def btn_settings(message: Message, state: FSMContext, user: User, l10n: dict[str, Any]) -> None:
    await state.clear()  # Reset FSM if user navigates here mid-wizard
    text = _render_settings_text(user, l10n)
    await message.answer(text, reply_markup=get_settings_keyboard(l10n, user.show_utc_offset), parse_mode="Markdown")


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
    scheduler_service: SchedulerService,
    l10n: dict[str, Any],
) -> None:
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


@router.callback_query(SetTimezoneCallback.filter())
async def callback_set_tz(
    callback: CallbackQuery,
    callback_data: SetTimezoneCallback,
    user_dao: UserDAO,
    user: User,
    l10n: dict[str, Any],
    state: FSMContext,
) -> None:
    state_data = await state.get_data()
    is_onboarding_tz = bool(state_data.get("onboarding_timezone"))
    if callback_data.tz == "manual":
        await state.set_state(SettingsState.waiting_for_timezone)
        await callback.message.edit_text(l10n["tz_manual_prompt"])
        await callback.answer()
        return
    tz = callback_data.tz
    await user_dao.update_timezone(user.id, tz)
    user.timezone = tz
    await callback.message.edit_text(
        l10n["tz_success"].format(tz=_format_tz_display_label(tz)),
        reply_markup=None,
    )
    if is_onboarding_tz:
        await state.clear()
        text = l10n["cmd_start"].format(name=callback.from_user.first_name)
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
        text = l10n["cmd_start"].format(name=message.from_user.first_name)
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


@router.callback_query(F.data == "settings_quiet_setup")
async def callback_quiet_setup(callback: CallbackQuery, user: User, l10n: dict[str, Any], state: FSMContext) -> None:
    await state.clear()
    from bot.keyboards.inline import get_quiet_hours_setup_keyboard

    enabled = bool(getattr(user, "quiet_hours_enabled", False))
    start = getattr(user, "quiet_hours_start", "23:00")
    end = getattr(user, "quiet_hours_end", "07:00")
    await callback.message.edit_reply_markup(
        reply_markup=get_quiet_hours_setup_keyboard(
            l10n,
            enabled=enabled,
            start_time=start,
            end_time=end,
        )
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
    start = getattr(user, "quiet_hours_start", "23:00")
    end = getattr(user, "quiet_hours_end", "07:00")
    await callback.message.edit_reply_markup(
        reply_markup=get_quiet_hours_setup_keyboard(
            l10n,
            enabled=enabled,
            start_time=start,
            end_time=end,
        )
    )
    await callback.answer()


@router.callback_query(F.data.in_(["quiet_edit_start", "quiet_edit_end"]))
async def callback_quiet_edit_time(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    target = "start" if callback.data.endswith("start") else "end"
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
    
    parsed = parse_hhmm(message.text)
    if parsed is None:
        await message.answer(l10n["brief_time_invalid_format"], parse_mode="Markdown")
        return

    h, m = parsed
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

    parsed = parse_hhmm(message.text or "")
    if parsed is None:
        await message.answer(l10n.get("quiet_time_invalid", "❌ Please enter time in HH:MM format."), parse_mode="Markdown")
        return

    h, m = parsed
    value = f"{h:02d}:{m:02d}"
    data = await state.get_data()
    target = data.get("quiet_target")
    if target not in {"start", "end"}:
        await state.clear()
        await message.answer(l10n.get("parse_error", "Error parsing text. Check the format."))
        return
    if target == "start":
        await user_dao.update_settings(user.id, quiet_hours_start=value)
        user.quiet_hours_start = value
    else:
        await user_dao.update_settings(user.id, quiet_hours_end=value)
        user.quiet_hours_end = value
    await state.clear()

    from bot.keyboards.inline import get_quiet_hours_setup_keyboard

    await message.answer(
        l10n.get("quiet_time_saved", "✅ Quiet hours updated: {time}").format(time=value),
        parse_mode="Markdown",
    )
    await message.answer(
        _render_settings_text(user, l10n),
        reply_markup=get_quiet_hours_setup_keyboard(
            l10n,
            enabled=bool(getattr(user, "quiet_hours_enabled", False)),
            start_time=getattr(user, "quiet_hours_start", "23:00"),
            end_time=getattr(user, "quiet_hours_end", "07:00"),
        ),
        parse_mode="Markdown",
    )
