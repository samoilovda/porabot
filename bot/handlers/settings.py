"""Settings handlers — timezone, language, and UTC offset preferences."""

import logging
from typing import Any

import pytz
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.database.dao.user import UserDAO
from bot.database.models import User
from bot.keyboards.inline import get_timezone_keyboard, get_settings_keyboard, get_language_selection_keyboard

class SettingsState(StatesGroup):
    waiting_for_brief_time = State()
    waiting_for_timezone = State()

router = Router(name="settings")
logger = logging.getLogger(__name__)


@router.message(F.text.in_(["⚙️ Настройки", "⚙️ Settings"]))
async def btn_settings(message: Message, state: FSMContext, user: User, l10n: dict[str, Any]) -> None:
    await state.clear()  # Reset FSM if user navigates here mid-wizard
    text = l10n["settings_text"].format(timezone=user.timezone)
    await message.answer(text, reply_markup=get_settings_keyboard(l10n, user.show_utc_offset), parse_mode="Markdown")


@router.callback_query(F.data == "settings_toggle_utc")
async def callback_toggle_utc(callback: CallbackQuery, user_dao: UserDAO, user: User, l10n: dict[str, Any]) -> None:
    new_val = not user.show_utc_offset
    await user_dao.update_show_utc_offset(user.id, new_val)
    text = l10n["settings_text"].format(timezone=user.timezone)
    await callback.message.edit_text(text, reply_markup=get_settings_keyboard(l10n, new_val), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "settings_change_tz")
async def callback_change_tz(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    await callback.message.edit_text(l10n["choose_tz"], reply_markup=get_timezone_keyboard())
    await callback.answer()


@router.callback_query(F.data == "settings_change_lang")
async def callback_change_lang(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    await callback.message.edit_text(l10n["choose_language"], reply_markup=get_language_selection_keyboard(l10n))
    await callback.answer()


@router.callback_query(F.data.startswith("set_tz_"))
async def callback_set_tz(
    callback: CallbackQuery, user_dao: UserDAO, user: User, l10n: dict[str, Any], state: FSMContext
) -> None:
    action = callback.data.split("set_tz_")[1]
    if action == "manual":
        await state.set_state(SettingsState.waiting_for_timezone)
        await callback.message.edit_text(l10n["tz_manual_prompt"])
        await callback.answer()
        return
    await user_dao.update_timezone(user.id, action)
    await callback.message.edit_text(l10n["tz_success"].format(tz=action), reply_markup=None)
    await callback.answer()


@router.message(SettingsState.waiting_for_timezone, F.text)
async def state_set_manual_timezone(
    message: Message, state: FSMContext, user: User, user_dao: UserDAO, l10n: dict[str, Any]
) -> None:
    tz_candidate = message.text.strip()
    try:
        pytz.timezone(tz_candidate)
    except pytz.UnknownTimeZoneError:
        await message.answer(
            l10n.get(
                "tz_invalid",
                "❌ Unknown timezone. Please use a valid IANA timezone, e.g. `Europe/London`.",
            ),
            parse_mode="Markdown",
        )
        return

    await user_dao.update_timezone(user.id, tz_candidate)
    user.timezone = tz_candidate
    await state.clear()

    await message.answer(l10n["tz_success"].format(tz=tz_candidate), parse_mode="Markdown")
    await message.answer(
        l10n["settings_text"].format(timezone=user.timezone),
        reply_markup=get_settings_keyboard(l10n, user.show_utc_offset),
        parse_mode="Markdown",
    )

@router.callback_query(F.data == "settings_back")
async def callback_settings_back(callback: CallbackQuery, user: User, l10n: dict[str, Any]) -> None:
    from bot.keyboards.inline import get_settings_keyboard
    text = l10n["settings_text"].format(timezone=user.timezone)
    await callback.message.edit_text(text, reply_markup=get_settings_keyboard(l10n, user.show_utc_offset), parse_mode="Markdown")
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
        await message.answer("❌ Please enter time in HH:MM format (e.g. `09:30` or `23:45`).", parse_mode="Markdown")
        return
    
    h, m = int(match.group(1)), int(match.group(2))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        await message.answer("❌ Invalid time. Hours must be 0–23, minutes 0–59.", parse_mode="Markdown")
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
    text = l10n["settings_text"].format(timezone=user.timezone)
    await message.answer(text, reply_markup=get_briefs_setup_keyboard(l10n, enabled, morning, evening), parse_mode="Markdown")
