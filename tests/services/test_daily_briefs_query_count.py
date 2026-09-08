"""Regression test for 2.3: process_daily_briefs must not open one DB
session per candidate user just to fetch their own User row a second time
— get_users_needing_brief_check already found the candidate ids in one
query; the old code then did a separate session-open-and-get_by_id per
candidate on top of that. It now does one batched
`select(User).where(User.id.in_(ids))` instead. Mirrors
test_missed_recovery_query_count.py / test_habit_sweeper_query_count.py /
test_habit_reports_job_query_count.py.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import bot.services.scheduler as scheduler_module
from bot.database import models  # noqa: F401
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.services.daily_briefs import process_daily_briefs


@pytest.fixture
async def session_pool():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker, engine
    await engine.dispose()


async def _seed_users_with_pending_task(session_pool, n_users: int) -> None:
    maker, _ = session_pool
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with maker() as session:
        reminder_dao = ReminderDAO(session)
        for i in range(n_users):
            uid = 6000 + i
            # Times chosen so nothing is actually due this tick — this
            # test only cares about the SELECT count on `users`, not
            # whether a brief gets sent.
            session.add(
                User(
                    id=uid, username="u", timezone="UTC", briefs_enabled=True,
                    morning_brief_time="23:58", evening_brief_time="23:59",
                )
            )
            await session.flush()
            await reminder_dao.create_reminder(
                user_id=uid, text="Task", execution_time=now + timedelta(hours=1),
            )
        await session.commit()


async def test_only_two_selects_on_users_regardless_of_candidate_count(session_pool, monkeypatch) -> None:
    """One SELECT for get_users_needing_brief_check's id list, one more
    for the batched full-row fetch — flat, not O(candidates)."""
    maker, engine = session_pool
    await _seed_users_with_pending_task(session_pool, n_users=12)

    fake_instance = type("FakeInstance", (), {})()
    fake_instance.bot = AsyncMock()
    fake_instance.session_pool = maker
    monkeypatch.setattr(scheduler_module, "_instance", fake_instance)

    select_count = 0

    def _count_user_selects(conn, cursor, statement, parameters, context, executemany):
        nonlocal select_count
        if "FROM users" in statement.replace('"', "") and statement.strip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count_user_selects)
    try:
        await process_daily_briefs()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count_user_selects)

    # Before the fix this was 1 (id list) + 12 (one get_by_id per
    # candidate) == 13.
    assert select_count == 2, f"expected exactly two SELECTs on users, got {select_count}"
