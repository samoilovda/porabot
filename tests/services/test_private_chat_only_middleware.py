"""2.6: no handler in this codebase filters on chat.type — a group the bot
is added to (or one with Telegram's privacy mode off) reached the exact
same free-text-parses-as-task flow a private chat does, notifying a
chat_id (the tapping member's own private chat) they may never have
opened with the bot, three TelegramForbiddenError strikes and silently
given up on.

PrivateChatOnlyMiddleware rejects any Message/CallbackQuery from a
non-private chat before it reaches DatabaseMiddleware or a handler.
"""

from datetime import datetime
from unittest.mock import AsyncMock

from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser

from bot.lexicon import get_l10n
from bot.middlewares.private_chat_only import PrivateChatOnlyMiddleware


def _group_message_update(text: str = "hi") -> Update:
    message = Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=-100123, type="supergroup", title="Test Group"),
        from_user=TgUser(id=1, is_bot=False, first_name="A"),
        text=text,
    )
    return Update(update_id=1, message=message)


def _private_message_update(text: str = "hi") -> Update:
    message = Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=1, type="private"),
        from_user=TgUser(id=1, is_bot=False, first_name="A"),
        text=text,
    )
    return Update(update_id=1, message=message)


def _group_callback_update() -> Update:
    message = Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=-100123, type="supergroup", title="Test Group"),
        from_user=TgUser(id=0, is_bot=True, first_name="Bot"),
        text="prompt",
    )
    cq = CallbackQuery(
        id="1", from_user=TgUser(id=1, is_bot=False, first_name="A"), chat_instance="1", data="x", message=message
    )
    return Update(update_id=2, callback_query=cq)


async def test_group_message_is_rejected_before_the_handler() -> None:
    middleware = PrivateChatOnlyMiddleware()
    handler = AsyncMock(return_value="ok")
    update = _group_message_update()

    result = await middleware(handler, update, {})

    assert result is None
    handler.assert_not_awaited()


async def test_group_message_gets_the_private_only_notice() -> None:
    middleware = PrivateChatOnlyMiddleware()
    handler = AsyncMock(return_value="ok")
    update = _group_message_update()

    from unittest.mock import patch

    with patch.object(Message, "answer", new=AsyncMock()) as mock_answer:
        await middleware(handler, update, {})

    mock_answer.assert_awaited_once_with(get_l10n(None)["private_only"])


async def test_group_callback_query_gets_an_alert_and_is_rejected() -> None:
    middleware = PrivateChatOnlyMiddleware()
    handler = AsyncMock(return_value="ok")
    update = _group_callback_update()

    from unittest.mock import patch

    with patch.object(CallbackQuery, "answer", new=AsyncMock()) as mock_answer:
        result = await middleware(handler, update, {})

    assert result is None
    handler.assert_not_awaited()
    mock_answer.assert_awaited_once()
    assert mock_answer.call_args.kwargs.get("show_alert") is True


async def test_private_message_reaches_the_handler_unaffected() -> None:
    middleware = PrivateChatOnlyMiddleware()
    handler = AsyncMock(return_value="ok")
    update = _private_message_update()

    result = await middleware(handler, update, {})

    assert result == "ok"
    handler.assert_awaited_once()


async def test_repeated_group_messages_only_get_notified_once_within_the_cooldown() -> None:
    """docs/audits/2026-09-22-audit.md#a-25 — a group with Telegram's
    privacy mode off sees every single message; this used to reply with
    the "private only" notice on EVERY one of them, forever, despite the
    module's own docstring already claiming "answer once"."""
    middleware = PrivateChatOnlyMiddleware()
    handler = AsyncMock(return_value="ok")

    from unittest.mock import patch

    with patch.object(Message, "answer", new=AsyncMock()) as mock_answer:
        for _ in range(5):
            result = await middleware(handler, _group_message_update(), {})
            assert result is None

    mock_answer.assert_awaited_once()  # not five times
    handler.assert_not_awaited()


async def test_notice_is_resent_after_the_cooldown_elapses(monkeypatch) -> None:
    import time as time_module

    middleware = PrivateChatOnlyMiddleware()
    handler = AsyncMock(return_value="ok")

    from unittest.mock import patch

    fake_now = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: fake_now[0])

    with patch.object(Message, "answer", new=AsyncMock()) as mock_answer:
        await middleware(handler, _group_message_update(), {})
        fake_now[0] += 3601.0  # just past _RENOTIFY_AFTER_SECONDS
        await middleware(handler, _group_message_update(), {})

    assert mock_answer.await_count == 2


async def test_cleanup_expired_drops_chats_past_their_cooldown() -> None:
    import time as time_module
    from unittest.mock import patch

    middleware = PrivateChatOnlyMiddleware()
    handler = AsyncMock(return_value="ok")

    with patch.object(Message, "answer", new=AsyncMock()):
        await middleware(handler, _group_message_update(), {})

    assert -100123 in middleware._last_notified
    # Simulate the cooldown having already elapsed.
    middleware._last_notified[-100123] = time_module.monotonic() - 7200.0

    middleware.cleanup_expired()

    assert -100123 not in middleware._last_notified


async def test_my_chat_member_update_passes_through_untouched() -> None:
    """No handler acts on my_chat_member today — this must not crash or
    block it; inner_event returns None for it, so it's a pure pass-through."""
    from aiogram.types import ChatMemberBanned, ChatMemberMember, ChatMemberUpdated

    middleware = PrivateChatOnlyMiddleware()
    handler = AsyncMock(return_value="ok")
    chat = Chat(id=-100123, type="supergroup", title="Test Group")
    tg_user = TgUser(id=1, is_bot=False, first_name="A")
    old_member = ChatMemberMember(user=tg_user)
    new_member = ChatMemberBanned(user=tg_user, until_date=datetime.now())
    my_chat_member = ChatMemberUpdated(
        chat=chat, from_user=tg_user, date=datetime.now(), old_chat_member=old_member, new_chat_member=new_member
    )
    update = Update(update_id=3, my_chat_member=my_chat_member)

    result = await middleware(handler, update, {})

    assert result == "ok"
    handler.assert_awaited_once()
