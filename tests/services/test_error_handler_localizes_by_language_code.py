"""2.4: handle_dispatcher_error always showed the generic error alert in
Russian (get_l10n(None)) regardless of the user's actual language.

Verified empirically (see this function's docstring) that aiogram's own
dependency injection does NOT carry data["l10n"] (set by DatabaseMiddleware
further down the chain) back to this error handler — ErrorsMiddleware's
own *data* snapshot predates the inner observer's middleware chain, and
TelegramEventObserver.trigger's **kwargs unpacking creates a fresh dict at
that boundary. The actual fix reads Telegram's own client-reported
language_code straight off the Update's embedded user instead.

Drives a REAL Dispatcher (same construction as bot/__main__.py) so the
language is threaded through exactly the way production does.
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
from bot.lexicon.en import EN
from bot.lexicon.ru import RU

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


async def test_error_alert_uses_the_clients_reported_language_for_a_message() -> None:
    assert RU["generic_error"] != EN["generic_error"]  # otherwise this test proves nothing

    dp = _make_dispatcher()
    bot = Bot(token=_TOKEN)
    try:
        tg_user = TgUser(id=1, is_bot=False, first_name="Test", language_code="en")
        chat = Chat(id=1, type="private")
        msg = Message.model_construct(message_id=1, date=datetime.now(), chat=chat, from_user=tg_user, text="/boom")
        update = Update(update_id=1, message=msg)

        with patch.object(Bot, "__call__", AsyncMock(return_value=object())) as mock_call:
            await dp.feed_update(bot, update)

        sent = mock_call.await_args.args[0]
        assert sent.text == EN["generic_error"]
        assert sent.text != RU["generic_error"]
    finally:
        await bot.session.close()


async def test_error_alert_uses_the_clients_reported_language_for_a_callback() -> None:
    dp = _make_dispatcher()
    bot = Bot(token=_TOKEN)
    try:
        tg_user = TgUser(id=2, is_bot=False, first_name="Test", language_code="en")
        chat = Chat(id=2, type="private")
        msg = Message.model_construct(message_id=1, date=datetime.now(), chat=chat, from_user=tg_user, text="x")
        callback = CallbackQuery.model_construct(
            id="cbid1", from_user=tg_user, chat_instance="ci", data="boom", message=msg
        )
        update = Update(update_id=2, callback_query=callback)

        with patch.object(Bot, "__call__", AsyncMock(return_value=True)) as mock_call:
            await dp.feed_update(bot, update)

        sent = mock_call.await_args.args[0]
        assert sent.text == EN["generic_error"]
    finally:
        await bot.session.close()


async def test_error_alert_falls_back_to_russian_for_an_unsupported_language_code() -> None:
    dp = _make_dispatcher()
    bot = Bot(token=_TOKEN)
    try:
        tg_user = TgUser(id=3, is_bot=False, first_name="Test", language_code="de")
        chat = Chat(id=3, type="private")
        msg = Message.model_construct(message_id=1, date=datetime.now(), chat=chat, from_user=tg_user, text="/boom")
        update = Update(update_id=3, message=msg)

        with patch.object(Bot, "__call__", AsyncMock(return_value=object())) as mock_call:
            await dp.feed_update(bot, update)

        sent = mock_call.await_args.args[0]
        assert sent.text == RU["generic_error"]
    finally:
        await bot.session.close()
