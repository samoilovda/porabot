"""P1-3 regression: a retryable send failure must not permanently consume
today's brief slot.

_claim_brief_slot commits the "already sent today" flag BEFORE the actual
Telegram call — necessary to make concurrent claims atomic (see
test_daily_briefs_window_dedup.py) — but if the send itself then fails with
a transient error, the claim must be released so a later tick the same day
retries, instead of the brief silently vanishing until tomorrow.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import bot.services.scheduler as scheduler_module
from bot.database.engine import Base
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import Reminder, User
from bot.services.daily_briefs import process_daily_briefs


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _seed_user_with_task(session_factory, *, morning_time: str) -> None:
    async with session_factory() as session:
        user = User(
            id=555,
            username="t",
            timezone="UTC",
            briefs_enabled=True,
            morning_brief_time=morning_time,
            evening_brief_time="23:59",
        )
        session.add(user)
        reminder = await ReminderDAO(session).create_reminder(
            user_id=555,
            text="Test task",
            execution_time=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        await session.commit()


async def test_network_error_releases_claim_so_next_tick_retries(session_factory) -> None:
    now = datetime.now(timezone.utc)
    morning_time = (now - timedelta(minutes=1)).strftime("%H:%M")
    await _seed_user_with_task(session_factory, morning_time=morning_time)

    failing_send = AsyncMock(side_effect=TimeoutError("network hiccup"))
    scheduler_module._instance = SimpleNamespace(
        bot=SimpleNamespace(send_message=failing_send),
        session_pool=session_factory,
    )
    try:
        await process_daily_briefs()
    finally:
        scheduler_module._instance = None

    assert failing_send.await_count == 1
    async with session_factory() as session:
        user = await session.get(User, 555)
        # The claim must have been released — NOT recorded as sent — so a
        # later tick today can retry.
        assert user.last_morning_brief_date is None


async def test_retry_after_release_succeeds_exactly_once(session_factory) -> None:
    now = datetime.now(timezone.utc)
    morning_time = (now - timedelta(minutes=1)).strftime("%H:%M")
    await _seed_user_with_task(session_factory, morning_time=morning_time)

    failing_send = AsyncMock(side_effect=TimeoutError("network hiccup"))
    scheduler_module._instance = SimpleNamespace(
        bot=SimpleNamespace(send_message=failing_send),
        session_pool=session_factory,
    )
    try:
        await process_daily_briefs()  # first tick: fails, releases claim
    finally:
        scheduler_module._instance = None

    ok_send = AsyncMock(return_value=SimpleNamespace(message_id=1))
    scheduler_module._instance = SimpleNamespace(
        bot=SimpleNamespace(send_message=ok_send),
        session_pool=session_factory,
    )
    try:
        await process_daily_briefs()  # second tick (retry): succeeds
        await process_daily_briefs()  # third tick: must not resend
    finally:
        scheduler_module._instance = None

    assert ok_send.await_count == 1
    async with session_factory() as session:
        user = await session.get(User, 555)
        today_str = now.date().isoformat()
        assert user.last_morning_brief_date == today_str
