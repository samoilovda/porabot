"""Regression test for 1.2: an unhandled exception must not leave a
callback/message hanging silently for the user.

Before the fix, bot/__main__.py registered no dp.errors handler — an
exception raised inside any handler (a malformed callback_data, an
edit_text over the length limit, ...) propagated out of aiogram's own
try/except, got logged, and nothing else happened. For a CallbackQuery
that means Telegram shows the tapped button as "loading" until it times
out, since callback.answer() was never reached.

This drives a REAL Dispatcher (same construction as bot/__main__.py) with
one router registering a handler that always raises, feeds it a fake
Update, and asserts the dispatcher-level error handler answers the
callback/message instead of the update disappearing.
"""

import os
from datetime import datetime
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher, F, Router
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser

os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN-NOT-REAL-ABCDEFGHIJKLMNOPQRS")

from bot.__main__ import handle_dispatcher_error

_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _make_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.errors.register(handle_dispatcher_error)

    boom_router = Router(name="boom")

    @boom_router.message(F.text == "/boom")
    async def _boom_message(message: Message) -> None:
        raise RuntimeError("simulated handler failure")

    @boom_router.callback_query(F.data == "boom")
    async def _boom_callback(callback: CallbackQuery) -> None:
        raise RuntimeError("simulated handler failure")

    dp.include_router(boom_router)
    return dp


async def test_failing_message_handler_gets_a_reply() -> None:
    dp = _make_dispatcher()
    bot = Bot(token=_TOKEN)
    try:
        tg_user = TgUser(id=1, is_bot=False, first_name="Test")
        chat = Chat(id=1, type="private")
        msg = Message.model_construct(message_id=1, date=datetime.now(), chat=chat, from_user=tg_user, text="/boom")
        update = Update(update_id=1, message=msg)

        with patch.object(Bot, "__call__", AsyncMock(return_value=object())) as mock_call:
            await dp.feed_update(bot, update)

        assert mock_call.await_count >= 1
        sent = mock_call.await_args.args[0]
        assert type(sent).__name__ == "SendMessage"
    finally:
        await bot.session.close()


async def test_failing_callback_handler_gets_answered_with_alert() -> None:
    dp = _make_dispatcher()
    bot = Bot(token=_TOKEN)
    try:
        tg_user = TgUser(id=2, is_bot=False, first_name="Test")
        chat = Chat(id=2, type="private")
        msg = Message.model_construct(message_id=1, date=datetime.now(), chat=chat, from_user=tg_user, text="x")
        callback = CallbackQuery.model_construct(
            id="cbid1", from_user=tg_user, chat_instance="ci", data="boom", message=msg
        )
        update = Update(update_id=2, callback_query=callback)

        with patch.object(Bot, "__call__", AsyncMock(return_value=True)) as mock_call:
            await dp.feed_update(bot, update)

        assert mock_call.await_count >= 1
        sent = mock_call.await_args.args[0]
        assert type(sent).__name__ == "AnswerCallbackQuery"
        assert sent.show_alert is True
    finally:
        await bot.session.close()
