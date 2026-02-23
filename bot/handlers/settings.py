"""Settings handlers — timezone management and preferences."""

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot.database.dao.user import UserDAO
from bot.database.models import User
from bot.keyboards.inline import get_timezone_keyboard, get_settings_keyboard, get_language_selection_keyboard
from bot.keyboards.reply import get_main_menu_keyboard
from aiogram.types import Message
from typing import Any

router = Router(name="settings")
logger = logging.getLogger(__name__)


@router.message(F.text.in_(["⚙️ Настройки", "⚙️ Settings"]))
async def btn_settings(message: Message, user: User, l10n: dict[str, Any]) -> None:
    text = l10n["settings_text"].format(timezone=user.timezone)
    await message.answer(text, reply_markup=get_settings_keyboard(l10n), parse_mode="Markdown")


@router.callback_query(F.data == "settings_change_tz")
async def callback_change_tz(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    await callback.message.edit_text(
        l10n["choose_tz"], reply_markup=get_timezone_keyboard()
    )


@router.callback_query(F.data == "settings_change_lang")
async def callback_change_lang(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    await callback.message.edit_text(
        l10n["choose_language"], reply_markup=get_language_selection_keyboard(l10n)
    )


@router.callback_query(F.data.startswith("set_tz_"))
async def callback_set_tz(
    callback: CallbackQuery, user_dao: UserDAO, user: User, l10n: dict[str, Any]
) -> None:
    action = callback.data.split("set_tz_")[1]
    if action == "manual":
        await callback.message.edit_text(l10n["tz_manual_prompt"])
        return

    await user_dao.update_timezone(user.id, action)
    await callback.message.edit_text(
        l10n["tz_success"].format(tz=action), reply_markup=None
    )
    await callback.answer()
