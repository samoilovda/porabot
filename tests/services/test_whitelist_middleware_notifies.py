"""docs/audits/2026-09-22-audit.md#a-24 — WhitelistMiddleware is registered
via dp.update.middleware() (see bot/__main__.py's commented-out
registration point), so `event` is always the raw aiogram.types.Update,
never the Message/CallbackQuery inside it. isinstance(event, Message) and
isinstance(event, CallbackQuery) were consequently always False, and a
denied user's "closed beta" notice never actually sent — the exact
dead-code shape RateLimitMiddleware had before its own fix (see
bot/middlewares/rate_limit.py's 2.3 comment).
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import Chat, Message, Update

from bot.middlewares.whitelist import WhitelistMiddleware


async def test_denied_message_update_gets_notified() -> None:
    middleware = WhitelistMiddleware(allowed_users=[], admin_id=0)

    async def handler(event, data):
        raise AssertionError("handler must not run for a denied user")

    data = {"event_from_user": SimpleNamespace(id=999, full_name="Stranger")}
    # Drive the middleware with a real Update wrapping a real Message —
    # isinstance(event, Update) must hold for inner_event's unwrap path to
    # engage at all (this middleware is registered via
    # dp.update.middleware(), so `event` is always the raw Update in
    # production, never a bare Message/CallbackQuery). Message.answer is a
    # real bound API method that needs a live Bot to actually send
    # anything — patched at the class level so the middleware's own
    # isinstance(target, Message) check still sees a genuine Message.
    real_message = Message.model_construct(
        message_id=1, date=datetime.now(), chat=Chat(id=1, type="private")
    )
    real_update = Update.model_construct(update_id=1, message=real_message)

    with patch.object(Message, "answer", AsyncMock()) as mock_answer:
        result = await middleware(handler, real_update, data)

    assert result is None
    mock_answer.assert_awaited_once()
    assert "closed beta" in mock_answer.await_args.args[0]


async def test_allowed_user_reaches_the_handler() -> None:
    middleware = WhitelistMiddleware(allowed_users=[42], admin_id=0)
    handler_called = False

    async def handler(event, data):
        nonlocal handler_called
        handler_called = True
        return "ok"

    data = {"event_from_user": SimpleNamespace(id=42, full_name="Friend")}
    result = await middleware(handler, SimpleNamespace(), data)

    assert handler_called
    assert result == "ok"
