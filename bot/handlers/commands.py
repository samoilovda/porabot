"""Handlers for base commands: /start, /help, /cancel."""

import logging
from typing import Any

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.database.dao.user import UserDAO
from bot.database.models import User
from bot.keyboards.reply import get_main_menu_keyboard
from bot.keyboards.inline import get_language_selection_keyboard
from bot.lexicon import get_l10n

router = Router(name="commands")
logger = logging.getLogger(__name__)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, user: User, l10n: dict[str, Any]) -> None:
    await state.clear()
    if user.language is None:
        await message.answer(l10n["choose_language"], reply_markup=get_language_selection_keyboard(l10n))
        return
    text = l10n["cmd_start"].format(name=message.from_user.first_name)
    await message.answer(text, reply_markup=get_main_menu_keyboard(l10n))


@router.callback_query(F.data.startswith("set_lang_"))
async def callback_set_lang(callback: CallbackQuery, user_dao: UserDAO, user: User) -> None:
    lang_code = callback.data.split("set_lang_")[1]
    await user_dao.update_language(user.id, lang_code)
    new_l10n = get_l10n(lang_code)
    await callback.message.delete()
    await callback.message.answer(new_l10n["lang_set"])
    text = new_l10n["cmd_start"].format(name=callback.from_user.first_name)
    await callback.message.answer(text, reply_markup=get_main_menu_keyboard(new_l10n))
    await callback.answer()


@router.message(F.text == "/cancel")
async def cmd_cancel(message: Message, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.clear()
    await message.answer(l10n.get("cmd_cancel", "Reminder creation cancelled."), reply_markup=get_main_menu_keyboard(l10n))


@router.callback_query(F.data == "cancel_wizard")
async def callback_cancel(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.clear()
    text = l10n.get("cmd_cancel", "Reminder creation cancelled.")
    await callback.message.edit_text(text, reply_markup=get_main_menu_keyboard(l10n))
    await callback.answer()