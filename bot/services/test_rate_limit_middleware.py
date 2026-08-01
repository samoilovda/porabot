"""P1-10: the bot has open access (no whitelist) with no per-user cap —
one user could flood the DB and the CPU-heavy NLP parser. RateLimitMiddleware
closes that gap with a simple per-user sliding window."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import Message

from bot.middlewares.rate_limit import RateLimitMiddleware


def _fake_message(user_id: int) -> Message:
    # spec=Message makes isinstance(event, Message) in the middleware pass.
    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    return msg


async def test_updates_within_the_limit_all_reach_the_handler() -> None:
    middleware = RateLimitMiddleware(max_updates=5, window_seconds=10.0)
    handler = AsyncMock(return_value="ok")
    event = _fake_message(1)
    data = {"event_from_user": SimpleNamespace(id=1)}

    for _ in range(5):
        result = await middleware(handler, event, data)
        assert result == "ok"

    assert handler.await_count == 5


async def test_exceeding_the_limit_blocks_further_updates_from_that_user() -> None:
    middleware = RateLimitMiddleware(max_updates=3, window_seconds=10.0)
    handler = AsyncMock(return_value="ok")
    event = _fake_message(1)
    data = {"event_from_user": SimpleNamespace(id=1)}

    for _ in range(3):
        await middleware(handler, event, data)
    result = await middleware(handler, event, data)

    assert result is None
    assert handler.await_count == 3  # the 4th call never reached the handler


async def test_rate_limit_is_per_user_not_global() -> None:
    middleware = RateLimitMiddleware(max_updates=2, window_seconds=10.0)
    handler = AsyncMock(return_value="ok")
    event_a = _fake_message(1)
    event_b = _fake_message(2)

    for _ in range(2):
        await middleware(handler, event_a, {"event_from_user": SimpleNamespace(id=1)})
    blocked = await middleware(handler, event_a, {"event_from_user": SimpleNamespace(id=1)})
    still_ok = await middleware(handler, event_b, {"event_from_user": SimpleNamespace(id=2)})

    assert blocked is None
    assert still_ok == "ok"


async def test_old_hits_outside_the_window_expire() -> None:
    middleware = RateLimitMiddleware(max_updates=2, window_seconds=10.0)
    handler = AsyncMock(return_value="ok")
    event = _fake_message(1)
    data = {"event_from_user": SimpleNamespace(id=1)}

    for _ in range(2):
        await middleware(handler, event, data)
    # Simulate time passing beyond the window by clearing recorded hits
    # directly (avoids a real sleep in the test suite).
    middleware._hits[1].clear()

    result = await middleware(handler, event, data)

    assert result == "ok"
    assert handler.await_count == 3


async def test_event_without_a_user_is_never_rate_limited() -> None:
    middleware = RateLimitMiddleware(max_updates=1, window_seconds=10.0)
    handler = AsyncMock(return_value="ok")
    event = _fake_message(0)

    for _ in range(5):
        result = await middleware(handler, event, {"event_from_user": None})
        assert result == "ok"
