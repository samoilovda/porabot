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
    # FSM storage writes through a separate session to the same SQLite DB.
    # Release this transaction's write lock before onboarding updates FSM,
    # and make the preference durable before reporting success to Telegram.
    await user_dao.session.commit()
    new_l10n = get_l10n(lang_code)
    await callback.message.delete()
    await callback.message.answer(new_l10n["lang_set"], reply_markup=None if is_onboarding else get_main_menu_keyboard(new_l10n))

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
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text=l10n["help_create_label"], callback_data="help_topic_create")
    builder.button(text=l10n["help_edit_label"], callback_data="help_topic_edit")
    await message.answer(l10n["cmd_help"], parse_mode="Markdown", reply_markup=builder.adjust(1).as_markup())


async def _cancel_text(state: FSMContext, l10n: dict[str, Any]) -> str:
    data = await state.get_data()
    current = await state.get_state()
    if data.get("edit_reminder_id"):
        return l10n["cancel_edit"]
    if current in {"ReminderWizard:entering_text", "ReminderWizard:choosing_time", "ReminderWizard:confirming_parse"}:
        return l10n["cmd_cancel"]
    return l10n["action_closed"]


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, l10n: dict[str, Any], reminder_dao=None, user=None) -> None:
    data = await state.get_data()
    text = await _cancel_text(state, l10n)
    await state.clear()
    await message.answer(text, reply_markup=get_main_menu_keyboard(l10n))
    await _cancel_return(message,data,reminder_dao,user,l10n)


@router.callback_query(F.data == "cancel_wizard")
async def callback_cancel(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any], reminder_dao=None, user=None) -> None:
    data = await state.get_data()
    text = await _cancel_text(state, l10n)
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(text, reply_markup=get_main_menu_keyboard(l10n))
    await _cancel_return(callback.message,data,reminder_dao,user,l10n)

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


@router.callback_query(F.data.in_({"help_topic_create", "help_topic_edit"}))
async def callback_help_topic(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    await callback.message.answer(l10n["help_create"] if callback.data.endswith("create") else l10n["help_edit"], parse_mode=None)
    await callback.answer()


async def _cancel_return(message,data,reminder_dao,user,l10n):
    if reminder_dao is None or user is None:
        return
    if data.get("nav_token"):
        from bot.handlers.experience import return_to_list
        await return_to_list(message,data,reminder_dao,user,l10n)
    elif data.get("edit_reminder_id"):
        task=await reminder_dao.get_owned(data["edit_reminder_id"],user.id)
        if task:
            from bot.keyboards.inline import get_saved_task_keyboard
            from bot.utils.time_ext import format_time
            await message.answer(task.reminder_text+"\n"+format_time(task.execution_time,user.timezone,fmt="%d.%m.%Y %H:%M"),reply_markup=get_saved_task_keyboard(task.id,l10n),parse_mode=None)
