"""2.7: on_my_chat_member (bot/handlers/commands.py) tracks whether a user
has blocked the bot in their private chat, so cron jobs can stop checking
them and reconcile_jobs_with_db can stop scheduling for their reminders —
see test_bot_blocked_excludes_from_cron_and_reconcile.py for that side.

Telegram reuses ChatMemberStatus.KICKED for both "removed from a group"
and "blocked a private chat" — this handler must only act on the latter.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.enums import ChatMemberStatus
from aiogram.types import Chat, ChatMemberBanned, ChatMemberMember, ChatMemberUpdated
from aiogram.types import User as TgUser

from bot.handlers.commands import on_my_chat_member


def _event(chat_type: str, new_status: ChatMemberStatus, user_id: int = 1) -> ChatMemberUpdated:
    tg_user = TgUser(id=user_id, is_bot=False, first_name="A")
    chat = Chat(id=user_id if chat_type == "private" else -100999, type=chat_type)
    old = ChatMemberMember(user=tg_user)
    if new_status == ChatMemberStatus.KICKED:
        new = ChatMemberBanned(user=tg_user, until_date=datetime.now())
    else:
        new = ChatMemberMember(user=tg_user)
    return ChatMemberUpdated(chat=chat, from_user=tg_user, date=datetime.now(), old_chat_member=old, new_chat_member=new)


async def test_blocking_in_a_private_chat_sets_bot_blocked_at() -> None:
    user = SimpleNamespace(id=1, bot_blocked_at=None)
    user_dao = SimpleNamespace(update_settings=AsyncMock())
    event = _event("private", ChatMemberStatus.KICKED)

    await on_my_chat_member(event, user_dao, user)

    assert user.bot_blocked_at is not None
    user_dao.update_settings.assert_awaited_once()
    assert user_dao.update_settings.await_args.kwargs["bot_blocked_at"] is not None


async def test_unblocking_in_a_private_chat_clears_bot_blocked_at() -> None:
    user = SimpleNamespace(id=1, bot_blocked_at=datetime.now())
    user_dao = SimpleNamespace(update_settings=AsyncMock())
    event = _event("private", ChatMemberStatus.MEMBER)

    await on_my_chat_member(event, user_dao, user)

    assert user.bot_blocked_at is None
    user_dao.update_settings.assert_awaited_once_with(1, bot_blocked_at=None)


async def test_group_kicked_status_is_not_treated_as_blocking() -> None:
    """Telegram reuses "kicked" for both concepts — a group my_chat_member
    update must be a complete no-op here."""
    user = SimpleNamespace(id=1, bot_blocked_at=None)
    user_dao = SimpleNamespace(update_settings=AsyncMock())
    event = _event("supergroup", ChatMemberStatus.KICKED)

    await on_my_chat_member(event, user_dao, user)

    assert user.bot_blocked_at is None
    user_dao.update_settings.assert_not_awaited()


async def test_already_blocked_is_idempotent_no_redundant_write() -> None:
    user = SimpleNamespace(id=1, bot_blocked_at=datetime.now())
    user_dao = SimpleNamespace(update_settings=AsyncMock())
    event = _event("private", ChatMemberStatus.KICKED)

    await on_my_chat_member(event, user_dao, user)

    user_dao.update_settings.assert_not_awaited()
