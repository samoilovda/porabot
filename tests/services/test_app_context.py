"""Regression tests for bot/context.py (4.2).

get_context() must fail loudly — not return None for a caller to trip over
later — when nothing has called set_context() yet (startup order bug, or a
test that forgot to set one up).
"""

from unittest.mock import AsyncMock

import pytest

import bot.context as context_module
from bot.context import AppContext, clear_context, get_context, set_context


@pytest.fixture(autouse=True)
def _reset_context():
    clear_context()
    yield
    clear_context()


def test_get_context_raises_clearly_when_unset() -> None:
    with pytest.raises(RuntimeError, match="AppContext not set"):
        get_context()


def test_set_context_then_get_context_returns_the_same_object() -> None:
    ctx = AppContext(bot=AsyncMock(), session_pool=AsyncMock(), scheduler=AsyncMock())
    set_context(ctx)
    assert get_context() is ctx


def test_clear_context_makes_get_context_raise_again() -> None:
    set_context(AppContext(bot=AsyncMock(), session_pool=AsyncMock(), scheduler=AsyncMock()))
    assert get_context() is not None
    clear_context()
    with pytest.raises(RuntimeError):
        get_context()


def test_scheduler_service_init_sets_the_context(monkeypatch) -> None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from bot.services.scheduler import SchedulerService

    scheduler = AsyncIOScheduler()
    bot = AsyncMock()
    session_pool = AsyncMock()
    service = SchedulerService(scheduler, bot, session_pool)

    ctx = get_context()
    assert ctx.scheduler is service
    assert ctx.bot is bot
    assert ctx.session_pool is session_pool
    assert context_module._context is ctx
