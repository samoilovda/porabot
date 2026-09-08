"""Regression test for 2.3: process_habit_reports must not open one DB
session per candidate user just to find out whether their scheduled
weekly/monthly report slot is now — that used to cost a session-open plus
a SELECT on `users` per candidate, even for users this tick has nothing to
do for (wrong weekday, or time not yet reached). Mirrors
test_missed_recovery_query_count.py / test_habit_sweeper_query_count.py.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import bot.context as context_module
from bot.database import models  # noqa: F401
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.services.habit_reports import process_habit_reports


@pytest.fixture
async def session_pool():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker, engine
    await engine.dispose()


async def _seed_users(session_pool, *, n_wrong_weekday: int, n_due: int, today_weekday: int) -> None:
    maker, _ = session_pool
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    wrong_weekday = (today_weekday + 1) % 7
    async with maker() as session:
        reminder_dao = ReminderDAO(session)

        for i in range(n_wrong_weekday):
            uid = 4000 + i
            session.add(
                User(
                    id=uid, username="u", timezone="UTC",
                    habit_reports_enabled=True, habit_report_weekday=wrong_weekday, habit_report_time="00:00",
                )
            )
            await session.flush()
            await reminder_dao.create_reminder(
                user_id=uid, text="Habit", execution_time=now + timedelta(hours=1),
                is_habit=True, is_recurring=True, rrule_string="FREQ=DAILY",
            )

        for i in range(n_due):
            uid = 5000 + i
            session.add(
                User(
                    id=uid, username="u", timezone="UTC",
                    habit_reports_enabled=True, habit_report_weekday=today_weekday, habit_report_time="00:00",
                )
            )
            await session.flush()
            reminder = await reminder_dao.create_reminder(
                user_id=uid, text="Habit", execution_time=now + timedelta(hours=1),
                is_habit=True, is_recurring=True, rrule_string="FREQ=DAILY",
            )
            from bot.database.dao.habit_event import HabitEventDAO

            await HabitEventDAO(session).record(
                reminder=reminder, user_tz="UTC", outcome="done", source="button",
                local_date=now.date().isoformat(),
            )
        await session.commit()


async def test_only_one_select_on_users_regardless_of_candidate_count(session_pool, monkeypatch) -> None:
    maker, engine = session_pool
    today_weekday = datetime.now(timezone.utc).weekday()
    await _seed_users(session_pool, n_wrong_weekday=15, n_due=2, today_weekday=today_weekday)

    fake_bot = AsyncMock()
    fake_instance = type("FakeInstance", (), {})()
    fake_instance.bot = fake_bot
    fake_instance.session_pool = maker
    monkeypatch.setattr(context_module, "_context", fake_instance)

    select_count = 0

    def _count_user_selects(conn, cursor, statement, parameters, context, executemany):
        nonlocal select_count
        if "FROM users" in statement.replace('"', "") and statement.strip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count_user_selects)
    try:
        await process_habit_reports()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count_user_selects)

    # 1 for the broad candidate fetch. Before the fix this was 1 + n
    # candidates (17) — one UserDAO.get_by_id SELECT per candidate.
    assert select_count == 1, f"expected exactly one SELECT on users, got {select_count}"
    assert fake_bot.send_message.await_count == 2
