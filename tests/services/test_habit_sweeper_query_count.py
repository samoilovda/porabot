"""Regression test for 2.3: sweep_habit_cycles must not re-fetch each
candidate's User row via a separate SELECT once it already has it from
the broad candidate query. _sweep_user used to take a bare user_id and
call UserDAO.get_by_id inside its own per-user session — an extra SELECT
on `users` per candidate on top of the one that found them in the first
place. It now takes the already-loaded User row directly.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import bot.context as context_module
from bot.database import models  # noqa: F401
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.services.habit_sweeper import sweep_habit_cycles


@pytest.fixture
async def session_pool():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker, engine
    await engine.dispose()


async def _seed_users_with_pending_habits(session_pool, n_users: int) -> None:
    maker, _ = session_pool
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with maker() as session:
        reminder_dao = ReminderDAO(session)
        for i in range(n_users):
            uid = 3000 + i
            session.add(User(id=uid, username="u", timezone="UTC"))
            await session.flush()
            await reminder_dao.create_reminder(
                user_id=uid, text="Habit", execution_time=now + timedelta(hours=1),
                is_habit=True, is_recurring=True, rrule_string="FREQ=DAILY",
            )
        await session.commit()


async def test_sweep_makes_one_select_on_users_regardless_of_candidate_count(
    session_pool, monkeypatch
) -> None:
    maker, engine = session_pool
    await _seed_users_with_pending_habits(session_pool, n_users=10)

    fake_instance = type("FakeInstance", (), {})()
    fake_instance.session_pool = maker
    monkeypatch.setattr(context_module, "_context", fake_instance)

    select_count = 0

    def _count_user_selects(conn, cursor, statement, parameters, context, executemany):
        nonlocal select_count
        upper = statement.strip().upper()
        if upper.startswith("SELECT") and "FROM users" in statement.replace('"', ""):
            select_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count_user_selects)
    try:
        await sweep_habit_cycles()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count_user_selects)

    # 1 for the broad candidate fetch. Before the fix this was 1 + n_users
    # (11) — one UserDAO.get_by_id SELECT per candidate inside _sweep_user.
    assert select_count == 1, f"expected exactly one SELECT on users, got {select_count}"
