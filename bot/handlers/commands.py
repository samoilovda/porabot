"""Handlers for base commands: /start, /help, /cancel."""

import logging
from datetime import datetime, timezone
from typing import Any

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from bot.database.dao.user import UserDAO
from bot.database.models import User
from bot.keyboards.inline import get_language_selection_keyboard, get_timezone_keyboard
from bot.keyboards.reply import get_main_menu_keyboard
from bot.lexicon import SUPPORTED_LANGUAGES, get_l10n
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
async def callback_set_lang(
    callback: CallbackQuery, user_dao: UserDAO, user: User, state: FSMContext, l10n: dict[str, Any]
) -> None:
    # A-23: callback_data is client-controlled — Telegram doesn't
    # cryptographically bind it to the keyboard actually shown, so a
    # forged "set_lang_<garbage>" must not persist an unsupported language
    # code. A stored one falls back to Russian on every future get_l10n()
    # call anyway (per DEFAULT_LANG), but silently — the user would see
    # their language "change" to something that then never actually
    # matches what they picked, with no error at all. Same defense-in-
    # depth already applied to set_tz_<zone> in settings.py.
    is_onboarding = user.language is None
    lang_code = callback.data.split("set_lang_")[1]
    if lang_code not in SUPPORTED_LANGUAGES:
        return await callback.answer(l10n["invalid_action"], show_alert=True)
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


@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, user_dao: UserDAO, user: User) -> None:
    """2.7: track whether this user has blocked the bot in their private
    chat, so the per-minute cron jobs (daily briefs, missed-task recovery,
    habit sweeper, habit reports) and reconcile_jobs_with_db can exclude
    them entirely instead of checking/scheduling for a user who will only
    ever get TelegramForbiddenError, forever.

    Telegram reuses "kicked" as the ChatMember status for BOTH "removed
    from a group" and "blocked a private chat" — with PrivateChatOnlyMiddleware
    (2.6) this update can in principle still arrive for a group (e.g. the
    bot being removed from one it was briefly added to before that
    middleware's own reply went out), so this only acts on private chats;
    a group my_chat_member update is a no-op here regardless of status.

    DatabaseMiddleware already resolved *user* from event.my_chat_member's
    own from_user (UserContextMiddleware treats it the same as any other
    update's user) — a fresh User row for a chat this bot has never
    exchanged messages with (only ever received this one status update)
    is created there exactly like for any other update.
    """
    if event.chat.type != "private":
        return

    blocked = event.new_chat_member.status == ChatMemberStatus.KICKED
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None) if blocked else None
    if blocked and user.bot_blocked_at is None:
        await user_dao.update_settings(user.id, bot_blocked_at=now_utc_naive)
        user.bot_blocked_at = now_utc_naive
        logger.info("User %s blocked the bot.", user.id)
    elif not blocked and user.bot_blocked_at is not None:
        await user_dao.update_settings(user.id, bot_blocked_at=None)
        user.bot_blocked_at = None
        logger.info("User %s unblocked the bot.", user.id)


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
