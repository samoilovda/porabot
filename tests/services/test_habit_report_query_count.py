"""Regression (fix 2.2): _process_user_reports (bot/services/habit_reports.py)
computed each report row's EMA score with a separate
get_events_for_reminder call — an N+1 that turned "N habits with report
activity this week" into N extra SELECTs on habit_events. Same shape as
fix(3.2) (Mini App scores) and fix(2.1) (My Habits list); this test
mirrors bot/services/test_miniapp_scores_query_count.py's verification
method — counting actual SELECTs on habit_events via a SQLAlchemy engine
event hook, not guessing at call counts.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.engine import Base
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.services.habit_reports import _process_user_reports


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s, engine
    await engine.dispose()


async def _seed_user_with_habits(session, n_habits: int = 6) -> None:
    user = User(id=1, username="u", timezone="UTC")
    session.add(user)
    await session.flush()
    reminder_dao = ReminderDAO(session)
    habit_event_dao = HabitEventDAO(session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for i in range(n_habits):
        habit = await reminder_dao.create_reminder(
            user_id=1, text=f"Habit {i}", execution_time=now + timedelta(hours=1),
            is_habit=True, is_recurring=True, rrule_string="FREQ=DAILY",
        )
        for day in range(3):
            await habit_event_dao.record(
                reminder=habit, user_tz="UTC", outcome="done", source="button",
                local_date=(now.date() - timedelta(days=day)).isoformat(),
            )
    await session.commit()


async def test_weekly_report_issues_one_select_on_habit_events_per_events_call(session) -> None:
    """get_events_in_range (the initial weekly-window fetch) is one SELECT
    by design — this asserts the SCORE computation adds exactly one more
    (the batched get_events_for_reminders call), not one per habit."""
    s, engine = session
    await _seed_user_with_habits(s, n_habits=6)

    select_count = 0

    def _count_habit_event_selects(conn, cursor, statement, parameters, context, executemany):
        nonlocal select_count
        if "habit_events" in statement and statement.strip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count_habit_event_selects)
    try:
        bot = AsyncMock()
        bot.send_message = AsyncMock()
        now_local = datetime.now(timezone.utc)
        delivered = await _process_user_reports(s, bot, User(id=1, username="u", timezone="UTC"), now_local)
        assert delivered is True
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count_habit_event_selects)

    # 1 for get_events_in_range (the weekly window) + 1 for the batched
    # get_events_for_reminders (scores) == 2, regardless of habit count.
    # Before the fix this was 1 + n_habits == 7.
    assert select_count == 2, f"expected exactly 2 SELECTs on habit_events, got {select_count}"
