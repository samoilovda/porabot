"""2.3: RateLimitMiddleware and DatabaseMiddleware are both registered via
dp.update.middleware(...) (see bot/__main__.py). aiogram's outer-middleware
chain on the "update" observer always calls them with the raw
aiogram.types.Update, never the Message/CallbackQuery inside it — that's
true regardless of what kind of update arrived. Before this fix, both
middlewares checked `isinstance(event, Message)` / `hasattr(event,
"answer")` directly against `event`, which is consequently ALWAYS False/
always an Update — a rate-limited user's "slow down" notice and a DB-error
notice never actually sent, with no visible symptom other than the
message just silently vanishing.

Earlier tests (test_rate_limit_middleware.py) passed a bare Message
directly as `event`, which is NOT how aiogram actually calls this
middleware — that's why the bug went unnoticed. These tests wrap the
event in a real aiogram.types.Update, matching production.
"""

from datetime import datetime, timezone

from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser

from bot.middlewares.database import DatabaseMiddleware
from bot.middlewares.rate_limit import RateLimitMiddleware


def _real_update_with_message(user_id: int, text: str = "hi") -> Update:
    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=TgUser(id=user_id, is_bot=False, first_name="A"),
        text=text,
    )
    return Update(update_id=1, message=message)


def _real_update_with_callback_query(user_id: int) -> Update:
    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=TgUser(id=0, is_bot=True, first_name="Bot"),
        text="prompt",
    )
    cq = CallbackQuery(
        id="1",
        from_user=TgUser(id=user_id, is_bot=False, first_name="A"),
        chat_instance="1",
        data="something",
        message=message,
    )
    return Update(update_id=2, callback_query=cq)


async def test_rate_limited_message_wrapped_in_a_real_update_gets_the_slow_down_reply() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    middleware = RateLimitMiddleware(max_updates=1, window_seconds=10.0)
    handler = AsyncMock(return_value="ok")
    update = _real_update_with_message(user_id=1)
    data = {"event_from_user": SimpleNamespace(id=1)}

    await middleware(handler, update, data)  # consumes the allowance

    with patch.object(Message, "answer", new=AsyncMock()) as mock_answer:
        result = await middleware(handler, update, data)

    assert result is None
    mock_answer.assert_awaited_once()


async def test_rate_limited_callback_query_wrapped_in_a_real_update_gets_the_slow_down_toast() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    middleware = RateLimitMiddleware(max_updates=1, window_seconds=10.0)
    handler = AsyncMock(return_value="ok")
    update = _real_update_with_callback_query(user_id=1)
    data = {"event_from_user": SimpleNamespace(id=1)}

    await middleware(handler, update, data)

    with patch.object(CallbackQuery, "answer", new=AsyncMock()) as mock_answer:
        result = await middleware(handler, update, data)

    assert result is None
    mock_answer.assert_awaited_once()


async def test_database_middleware_notifies_the_real_message_on_user_bootstrap_failure() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    class _FailingUserDao:
        def __init__(self, session):
            pass

        async def get_or_create(self, **kwargs):
            raise RuntimeError("db exploded")

    class _SessionCtx:
        def __init__(self, s):
            self._s = s

        async def __aenter__(self):
            return self._s

        async def __aexit__(self, *exc):
            return False

    session = SimpleNamespace(rollback=AsyncMock())

    def session_pool():
        return _SessionCtx(session)

    with patch("bot.middlewares.database.UserDAO", _FailingUserDao):
        middleware = DatabaseMiddleware(session_pool=session_pool)
        update = _real_update_with_message(user_id=1)
        handler = AsyncMock()
        # DatabaseMiddleware itself reads data["event_from_user"] to decide
        # whether to bootstrap a User row — populated here the same way
        # aiogram's own UserContextMiddleware would (see
        # bot/utils/telegram.py's inner_event docstring).
        initial_data = {"event_from_user": update.message.from_user}

        with patch.object(Message, "answer", new=AsyncMock()) as mock_answer:
            result = await middleware(handler, update, initial_data)

    assert result is None
    mock_answer.assert_awaited_once()
    handler.assert_not_awaited()
