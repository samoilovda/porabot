"""Settings handlers — timezone, language, and UTC offset preferences."""

import logging
from typing import Any

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from bot.database.dao.user import UserDAO
from bot.database.models import User
from bot.keyboards.inline import get_timezone_keyboard, get_settings_keyboard, get_language_selection_keyboard

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


@router.callback_query(F.data == "settings_change_lang")
async def callback_change_lang(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    await callback.message.edit_text(l10n["choose_language"], reply_markup=get_language_selection_keyboard(l10n))


@router.callback_query(F.data.startswith("set_tz_"))
async def callback_set_tz(callback: CallbackQuery, user_dao: UserDAO, user: User, l10n: dict[str, Any]) -> None:
    action = callback.data.split("set_tz_")[1]
    if action == "manual":
        await callback.message.edit_text(l10n["tz_manual_prompt"])
        return
    await user_dao.update_timezone(user.id, action)
    await callback.message.edit_text(l10n["tz_success"].format(tz=action), reply_markup=None)
    await callback.answer()

@router.callback_query(F.data == "settings_back")
async def callback_settings_back(callback: CallbackQuery, user: User, l10n: dict[str, Any]) -> None:
    from bot.keyboards.inline import get_settings_keyboard
    text = l10n["settings_text"].format(timezone=user.timezone)
    await callback.message.edit_text(text, reply_markup=get_settings_keyboard(l10n, user.show_utc_offset), parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data == "settings_briefs_setup")
async def callback_briefs_setup(callback: CallbackQuery, user: User, l10n: dict[str, Any]) -> None:
    from bot.keyboards.inline import get_briefs_setup_keyboard
    enabled = getattr(user, 'briefs_enabled', True)
    morning = getattr(user, 'morning_brief_hour', 9)
    evening = getattr(user, 'evening_brief_hour', 23)
    await callback.message.edit_reply_markup(reply_markup=get_briefs_setup_keyboard(l10n, enabled, morning, evening))
    await callback.answer()

@router.callback_query(F.data == "briefs_toggle")
async def callback_briefs_toggle(callback: CallbackQuery, user: User, user_dao: UserDAO, l10n: dict[str, Any]) -> None:
    from bot.keyboards.inline import get_briefs_setup_keyboard
    enabled = not getattr(user, 'briefs_enabled', True)
    await user_dao.update_briefs_settings(user.id, briefs_enabled=enabled)
    morning = getattr(user, 'morning_brief_hour', 9)
    evening = getattr(user, 'evening_brief_hour', 23)
    await callback.message.edit_reply_markup(reply_markup=get_briefs_setup_keyboard(l10n, enabled, morning, evening))
    await callback.answer()

@router.callback_query(F.data.in_(["briefs_edit_morning", "briefs_edit_evening"]))
async def callback_briefs_edit_hour(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    from bot.keyboards.inline import get_hour_selection_keyboard
    target = callback.data.split("_")[-1]  # 'morning' or 'evening'
    await callback.message.edit_text(l10n.get("choose_hour", "Choose hour:"), reply_markup=get_hour_selection_keyboard(l10n, target))
    await callback.answer()

@router.callback_query(F.data.startswith("briefs_set_"))
async def callback_briefs_set_hour(callback: CallbackQuery, user: User, user_dao: UserDAO, l10n: dict[str, Any]) -> None:
    from bot.keyboards.inline import get_briefs_setup_keyboard
    parts = callback.data.split("_")
    target = parts[-2]
    hour = int(parts[-1])
    
    if target == "morning":
        await user_dao.update_briefs_settings(user.id, morning_brief_hour=hour)
        user.morning_brief_hour = hour
    elif target == "evening":
        await user_dao.update_briefs_settings(user.id, evening_brief_hour=hour)
        user.evening_brief_hour = hour
        
    enabled = getattr(user, 'briefs_enabled', True)
    morning = getattr(user, 'morning_brief_hour', 9)
    evening = getattr(user, 'evening_brief_hour', 23)
    
    # Needs to return to settings_text because previous message was "choose_hour"
    text = l10n["settings_text"].format(timezone=user.timezone)
    await callback.message.edit_text(text, reply_markup=get_briefs_setup_keyboard(l10n, enabled, morning, evening), parse_mode="Markdown")
    await callback.answer()
