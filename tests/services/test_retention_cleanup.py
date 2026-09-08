"""Regression tests for 2.4: data retention cleanup.

Verifies:
  - habit_events older than HABIT_EVENT_RETENTION_DAYS are removed, newer
    ones survive.
  - pruning old events does not change compute_habit_score's output for a
    habit with roughly continuous recent activity — enough EMA steps
    separate "now" from the pruned history that its contribution has
    decayed past floating-point precision.
  - completed one-off reminders older than COMPLETED_REMINDER_RETENTION_DAYS
    are removed.
  - recurring reminders (including every habit) are never touched, no
    matter how old their completed_at/created_at is.
  - a still-pending reminder is never touched.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import bot.services.scheduler as scheduler_module
from bot.database import models  # noqa: F401
from bot.database.engine import Base
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import HabitEvent, Reminder, User
from bot.services.habit_reports import compute_habit_score
from bot.services.retention_cleanup import (
    COMPLETED_REMINDER_RETENTION_DAYS,
    HABIT_EVENT_RETENTION_DAYS,
    process_retention_cleanup,
)


@pytest.fixture
async def session_pool():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _run_cleanup(maker) -> None:
    fake_instance = type("FakeInstance", (), {})()
    fake_instance.session_pool = maker
    import unittest.mock as mock

    with mock.patch.object(scheduler_module, "_instance", fake_instance):
        await process_retention_cleanup()


async def test_old_habit_events_are_removed_recent_ones_kept(session_pool) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with session_pool() as session:
        session.add(User(id=1, username="u", timezone="UTC"))
        await session.flush()
        reminder_dao = ReminderDAO(session)
        habit = await reminder_dao.create_reminder(
            user_id=1, text="Habit", execution_time=now + timedelta(hours=1),
            is_habit=True, is_recurring=True, rrule_string="FREQ=DAILY",
        )

        old_event = HabitEvent(
            user_id=1, reminder_id=habit.id, habit_text="Habit",
            cycle_key="day:old", local_date="2020-01-01", outcome="done", source="button",
        )
        old_event.created_at = now - timedelta(days=HABIT_EVENT_RETENTION_DAYS + 10)
        session.add(old_event)

        recent_event = HabitEvent(
            user_id=1, reminder_id=habit.id, habit_text="Habit",
            cycle_key="day:recent", local_date=now.date().isoformat(), outcome="done", source="button",
        )
        session.add(recent_event)
        await session.commit()

    await _run_cleanup(session_pool)

    async with session_pool() as session:
        result = await session.execute(select(HabitEvent.cycle_key))
        remaining = {row[0] for row in result.all()}
    assert remaining == {"day:recent"}


async def test_pruning_old_events_does_not_change_the_score(session_pool) -> None:
    """The EMA score, computed from full remaining history, must read the
    same before and after old events (beyond the retention window) are
    pruned — for a habit with roughly continuous recent activity (the
    normal case for one still being actively tracked), enough EMA steps
    happen between the ancient events and "now" that their contribution
    has decayed past floating-point significance ((1-alpha)**60 ~ 1e-6).

    This is NOT a guarantee for a habit with a long gap and only a
    handful of events since — there, fewer EMA steps separate "now" from
    the pruned history, and the score CAN shift measurably. That's an
    accepted tradeoff of calendar-age-based retention, not a target this
    test claims to cover.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with session_pool() as session:
        session.add(User(id=1, username="u", timezone="UTC"))
        await session.flush()
        reminder_dao = ReminderDAO(session)
        habit_event_dao = HabitEventDAO(session)
        habit = await reminder_dao.create_reminder(
            user_id=1, text="Habit", execution_time=now + timedelta(hours=1),
            is_habit=True, is_recurring=True, rrule_string="FREQ=DAILY",
        )

        # Ancient, mixed-outcome history — will be pruned.
        for i in range(20):
            outcome = "done" if i % 2 == 0 else "missed"
            await habit_event_dao.record(
                reminder=habit, user_tz="UTC", outcome=outcome, source="auto",
                local_date=(now.date() - timedelta(days=HABIT_EVENT_RETENTION_DAYS + 100 + i)).isoformat(),
            )
        # Roughly continuous recent history (mixed outcomes, not a clean
        # streak) — survives pruning; 60 events is enough EMA steps
        # ((1-0.2)**60 ~ 1e-6) for the ancient contribution to vanish.
        for i in range(60):
            outcome = "done" if i % 3 != 0 else "missed"
            await habit_event_dao.record(
                reminder=habit, user_tz="UTC", outcome=outcome, source="button",
                local_date=(now.date() - timedelta(days=i)).isoformat(),
            )
        await session.commit()

        # Backdate created_at on the ancient rows directly — record()
        # always stamps "now", same as the previous test needed.
        from sqlalchemy import update

        await session.execute(
            update(HabitEvent)
            .where(HabitEvent.local_date < (now.date() - timedelta(days=HABIT_EVENT_RETENTION_DAYS)).isoformat())
            .values(created_at=now - timedelta(days=HABIT_EVENT_RETENTION_DAYS + 60))
        )
        await session.commit()

        events_before = await habit_event_dao.get_events_for_reminder(habit.id)
        score_before = compute_habit_score(events_before)
        assert len(events_before) == 80

    await _run_cleanup(session_pool)

    async with session_pool() as session:
        habit_event_dao = HabitEventDAO(session)
        events_after = await habit_event_dao.get_events_for_reminder(habit.id)
        score_after = compute_habit_score(events_after)

    assert len(events_after) == 60  # the 20 ancient rows are gone
    assert score_after == score_before


async def test_old_completed_one_off_reminder_is_removed(session_pool) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with session_pool() as session:
        session.add(User(id=1, username="u", timezone="UTC"))
        await session.flush()
        reminder_dao = ReminderDAO(session)
        task = await reminder_dao.create_reminder(
            user_id=1, text="Old task", execution_time=now - timedelta(days=200),
        )
        task.status = "completed"
        task.completed_at = now - timedelta(days=COMPLETED_REMINDER_RETENTION_DAYS + 5)
        await session.commit()
        task_id = task.id

    await _run_cleanup(session_pool)

    async with session_pool() as session:
        reminder_dao = ReminderDAO(session)
        assert await reminder_dao.get_by_id(task_id) is None


async def test_recent_completed_one_off_reminder_survives(session_pool) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with session_pool() as session:
        session.add(User(id=1, username="u", timezone="UTC"))
        await session.flush()
        reminder_dao = ReminderDAO(session)
        task = await reminder_dao.create_reminder(
            user_id=1, text="Recent task", execution_time=now - timedelta(days=1),
        )
        task.status = "completed"
        task.completed_at = now - timedelta(days=1)
        await session.commit()
        task_id = task.id

    await _run_cleanup(session_pool)

    async with session_pool() as session:
        reminder_dao = ReminderDAO(session)
        assert await reminder_dao.get_by_id(task_id) is not None


async def test_recurring_habit_survives_regardless_of_age() -> None:
    """A habit is always is_recurring=True — even an ancient one must
    never be pruned by the one-off-reminder cleanup."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with maker() as session:
            session.add(User(id=1, username="u", timezone="UTC"))
            await session.flush()
            reminder_dao = ReminderDAO(session)
            habit = await reminder_dao.create_reminder(
                user_id=1, text="Ancient habit", execution_time=now + timedelta(hours=1),
                is_habit=True, is_recurring=True, rrule_string="FREQ=DAILY",
            )
            habit.created_at = now - timedelta(days=COMPLETED_REMINDER_RETENTION_DAYS + 500)
            await session.commit()
            habit_id = habit.id

        await _run_cleanup(maker)

        async with maker() as session:
            reminder_dao = ReminderDAO(session)
            assert await reminder_dao.get_by_id(habit_id) is not None
    finally:
        await engine.dispose()


async def test_still_pending_reminder_survives() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with maker() as session:
            session.add(User(id=1, username="u", timezone="UTC"))
            await session.flush()
            reminder_dao = ReminderDAO(session)
            task = await reminder_dao.create_reminder(
                user_id=1, text="Still pending", execution_time=now - timedelta(days=300),
            )
            await session.commit()
            task_id = task.id

        await _run_cleanup(maker)

        async with maker() as session:
            reminder_dao = ReminderDAO(session)
            assert await reminder_dao.get_by_id(task_id) is not None
    finally:
        await engine.dispose()
