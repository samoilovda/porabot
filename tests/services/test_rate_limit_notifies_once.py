"""B-10 (2026-11-04 audit): a throttled user gets ONE notice per throttled
period, not one reply per dropped update."""

from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import CallbackQuery, Message

from bot.middlewares.rate_limit import RateLimitMiddleware


class _U:
    id = 1
    language_code = "ru"


async def _flood(mw, target, n, clock):
    handler = AsyncMock(return_value=None)
    with patch("bot.middlewares.rate_limit.time.monotonic", side_effect=lambda: clock[0]):
        for _ in range(n):
            await mw(handler, target, {"event_from_user": _U()})
    return handler


async def test_message_flood_gets_a_single_notice() -> None:
    mw = RateLimitMiddleware(max_updates=3, window_seconds=60)
    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    clock = [1000.0]
    handler = await _flood(mw, msg, 8, clock)
    assert handler.await_count == 3
    assert msg.answer.await_count == 1


async def test_notice_repeats_after_the_window_drains() -> None:
    mw = RateLimitMiddleware(max_updates=2, window_seconds=10)
    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    clock = [1000.0]
    await _flood(mw, msg, 4, clock)
    clock[0] = 1011.0  # window drained, user may send again
    await _flood(mw, msg, 4, clock)
    assert msg.answer.await_count == 2


async def test_dropped_callbacks_are_always_answered() -> None:
    mw = RateLimitMiddleware(max_updates=1, window_seconds=60)
    cb = MagicMock(spec=CallbackQuery)
    cb.answer = AsyncMock()
    clock = [1000.0]
    await _flood(mw, cb, 4, clock)
    # 3 dropped callbacks: first with the notice, the rest silently — but
    # every one answered so the button spinner stops.
    assert cb.answer.await_count == 3
    assert cb.answer.await_args_list[0].args
    assert not cb.answer.await_args_list[-1].args


async def test_cleanup_drops_expired_notice_marks() -> None:
    mw = RateLimitMiddleware(max_updates=1, window_seconds=10)
    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    clock = [1000.0]
    await _flood(mw, msg, 3, clock)
    assert mw._notified_until
    with patch("bot.middlewares.rate_limit.time.monotonic", return_value=2000.0):
        mw.cleanup_expired()
    assert not mw._notified_until
