"""Handlers for base commands: /start, /help, /cancel."""

import logging
from typing import Any

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.database.dao.user import UserDAO
from bot.database.models import User
from bot.keyboards.reply import get_main_menu_keyboard
from bot.keyboards.inline import get_language_selection_keyboard, get_timezone_keyboard
from bot.lexicon import get_l10n
from bot.utils.markdown import escape_markdown

router = Router(name="commands")
logger = logging.getLogger(__name__)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, user: User, l10n: dict[str, Any]) -> None:
    await state.clear()
    if user.language is None:
        await message.answer(l10n["choose_language"], reply_markup=get_language_selection_keyboard(l10n))
        return
    # P2-4: first_name is Telegram user-controlled text sent under the
    # default parse_mode=Markdown — an unescaped "_"/"*"/"`"/"[" in it can
    # break formatting or trip a TelegramBadRequest.
    text = l10n["cmd_start"].format(name=escape_markdown(message.from_user.first_name))
    await message.answer(text, reply_markup=get_main_menu_keyboard(l10n))


@router.callback_query(F.data.startswith("set_lang_"))
async def callback_set_lang(callback: CallbackQuery, user_dao: UserDAO, user: User, state: FSMContext) -> None:
    is_onboarding = user.language is None
    lang_code = callback.data.split("set_lang_")[1]
    await user_dao.update_language(user.id, lang_code)
    user.language = lang_code
    new_l10n = get_l10n(lang_code)
    await callback.message.delete()
    await callback.message.answer(new_l10n["lang_set"])

    if is_onboarding:
        await state.update_data(onboarding_timezone=True)
        await callback.message.answer(new_l10n["choose_tz"], reply_markup=get_timezone_keyboard(new_l10n))
        await callback.answer()
        return

    text = new_l10n["cmd_start"].format(name=escape_markdown(callback.from_user.first_name))
    await callback.message.answer(text, reply_markup=get_main_menu_keyboard(new_l10n))
    await callback.answer()


@router.message(Command("help"))
async def cmd_help(message: Message, l10n: dict[str, Any]) -> None:
    await message.answer(l10n["cmd_help"], parse_mode="Markdown")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.clear()
    await message.answer(l10n.get("cmd_cancel", "Reminder creation cancelled."), reply_markup=get_main_menu_keyboard(l10n))


@router.callback_query(F.data == "cancel_wizard")
async def callback_cancel(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.clear()
    text = l10n.get("cmd_cancel", "Reminder creation cancelled.")
    await callback.message.delete()
    await callback.message.answer(text, reply_markup=get_main_menu_keyboard(l10n))
    await callback.answer()


@router.message(F.pinned_message)
async def cleanup_pin_service_message(message: Message) -> None:
    """Delete Telegram's auto-posted "message pinned" service notice.

    Pinning the morning brief (bot/services/daily_briefs.py) makes Telegram
    drop this notice into the chat. It's pure noise — the pinned content is
    already visible at the top of the chat — so remove it as soon as it
    arrives.
    """
    try:
        await message.delete()
    except Exception as e:
        logger.debug("Could not delete pin service message %s: %s", message.message_id, e)
