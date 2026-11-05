"""B-08 (2026-11-04 audit): streaks for habits on a non-daily rule. The
old fixed 12–36h "consecutive" window reset a Mon–Fri habit every Monday
and kept a weekly habit at 1 forever."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User

MONDAY = datetime(2026, 11, 2, 6, 0)


@pytest.fixture
async def dao():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        s.add(User(id=1, username="u", timezone="Europe/Moscow"))
        await s.flush()
        yield ReminderDAO(s)
    await engine.dispose()


async def _habit(dao, rule):
    return await dao.create_reminder(1, "h", MONDAY, is_recurring=True, rrule_string=rule, is_habit=True)


async def _complete(dao, habit, due, after=timedelta(minutes=5)):
    return await dao.apply_habit_streak_completion(habit.id, due_at_utc_naive=due, completed_at_utc_naive=due + after)


async def test_weekday_habit_streak_survives_the_weekend(dao) -> None:
    habit = await _habit(dao, "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR")
    streaks = []
    for day in range(14):
        due = MONDAY + timedelta(days=day)
        if due.weekday() < 5:
            await _complete(dao, habit, due)
            streaks.append(habit.habit_streak_current)
    assert streaks == list(range(1, 11))


async def test_weekly_habit_streak_grows(dao) -> None:
    habit = await _habit(dao, "FREQ=WEEKLY")
    for week in range(4):
        await _complete(dao, habit, MONDAY + timedelta(weeks=week))
    assert habit.habit_streak_current == 4
    assert habit.habit_streak_best == 4


async def test_skipped_weekday_resets_the_streak(dao) -> None:
    habit = await _habit(dao, "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR")
    await _complete(dao, habit, MONDAY)
    await _complete(dao, habit, MONDAY + timedelta(days=2))  # Tuesday skipped
    assert habit.habit_streak_current == 1


async def test_daily_habit_skip_still_resets(dao) -> None:
    habit = await _habit(dao, "FREQ=DAILY")
    await _complete(dao, habit, MONDAY)
    await _complete(dao, habit, MONDAY + timedelta(days=1))
    await _complete(dao, habit, MONDAY + timedelta(days=3))
    assert habit.habit_streak_current == 1


async def test_weekly_cycle_completed_three_days_later_still_counts(dao) -> None:
    habit = await _habit(dao, "FREQ=WEEKLY")
    result = await _complete(dao, habit, MONDAY, after=timedelta(days=3))
    assert result["counted"] and not result["late"]


async def test_daily_cycle_completed_after_a_day_is_late(dao) -> None:
    habit = await _habit(dao, "FREQ=DAILY")
    result = await _complete(dao, habit, MONDAY, after=timedelta(hours=25))
    assert result["late"]
