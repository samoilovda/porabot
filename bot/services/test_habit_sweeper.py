from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.engine import Base
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.services.habit_sweeper import _sweep_user


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _make_user(session, *, created_at=None) -> User:
    user = User(id=1, username="u", timezone="UTC")
    if created_at is not None:
        user.created_at = created_at
    session.add(user)
    await session.flush()
    return user


async def test_fixed_habit_missed_after_grace_creates_event(session) -> None:
    await _make_user(session)
    reminder_dao = ReminderDAO(session)
    due = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=25)
    reminder = await reminder_dao.create_reminder(
        user_id=1,
        text="Stretch",
        execution_time=due,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_habit=True,
        is_nagging=True,
    )
    reminder.habit_active_due_at = due
    reminder.created_at = due - timedelta(days=30)
    await session.flush()

    await _sweep_user(session, 1)

    habit_event_dao = HabitEventDAO(session)
    events = await habit_event_dao.get_events_in_range(1, "2000-01-01", "2100-01-01")
    assert len(events) == 1
    assert events[0].outcome == "missed"
    assert reminder.habit_streak_current == 0


async def test_fixed_habit_already_done_creates_no_event(session) -> None:
    await _make_user(session)
    reminder_dao = ReminderDAO(session)
    due = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=25)
    reminder = await reminder_dao.create_reminder(
        user_id=1,
        text="Stretch",
        execution_time=due,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_habit=True,
        is_nagging=True,
    )
    reminder.habit_active_due_at = due
    reminder.habit_last_completed_due_at = due
    reminder.created_at = due - timedelta(days=30)
    await session.flush()

    await _sweep_user(session, 1)

    habit_event_dao = HabitEventDAO(session)
    events = await habit_event_dao.get_events_in_range(1, "2000-01-01", "2100-01-01")
    assert events == []


async def test_fixed_habit_beyond_retrospection_window_creates_no_event(session) -> None:
    await _make_user(session)
    reminder_dao = ReminderDAO(session)
    due = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)
    reminder = await reminder_dao.create_reminder(
        user_id=1,
        text="Stretch",
        execution_time=due,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_habit=True,
        is_nagging=True,
    )
    reminder.habit_active_due_at = due
    reminder.created_at = due - timedelta(days=30)
    await session.flush()

    await _sweep_user(session, 1)

    habit_event_dao = HabitEventDAO(session)
    events = await habit_event_dao.get_events_in_range(1, "2000-01-01", "2100-01-01")
    assert events == []


async def test_fluid_habit_not_marked_yesterday_creates_missed_event(session) -> None:
    await _make_user(session)
    reminder_dao = ReminderDAO(session)
    reminder = await reminder_dao.create_reminder(
        user_id=1,
        text="Read",
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=365 * 5),
        is_recurring=True,
        is_habit=True,
        is_fluid_habit=True,
        fluid_mode="brief_only",
        is_nagging=False,
    )
    reminder.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    await session.flush()

    await _sweep_user(session, 1)

    habit_event_dao = HabitEventDAO(session)
    events = await habit_event_dao.get_events_in_range(1, "2000-01-01", "2100-01-01")
    assert len(events) == 1
    assert events[0].outcome == "missed"


async def test_fluid_habit_marked_yesterday_creates_no_event(session) -> None:
    user = await _make_user(session)
    reminder_dao = ReminderDAO(session)
    reminder = await reminder_dao.create_reminder(
        user_id=1,
        text="Read",
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=365 * 5),
        is_recurring=True,
        is_habit=True,
        is_fluid_habit=True,
        fluid_mode="brief_only",
        is_nagging=False,
    )
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    reminder.fluid_last_completed_date = yesterday
    reminder.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    await session.flush()

    await _sweep_user(session, 1)

    habit_event_dao = HabitEventDAO(session)
    events = await habit_event_dao.get_events_in_range(1, "2000-01-01", "2100-01-01")
    assert events == []


async def test_habit_created_today_does_not_close_yesterday(session) -> None:
    await _make_user(session)
    reminder_dao = ReminderDAO(session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    reminder = await reminder_dao.create_reminder(
        user_id=1,
        text="Read",
        execution_time=now + timedelta(days=365 * 5),
        is_recurring=True,
        is_habit=True,
        is_fluid_habit=True,
        fluid_mode="brief_only",
        is_nagging=False,
    )
    reminder.created_at = now  # created today — "yesterday" isn't a real cycle
    await session.flush()

    await _sweep_user(session, 1)

    habit_event_dao = HabitEventDAO(session)
    events = await habit_event_dao.get_events_in_range(1, "2000-01-01", "2100-01-01")
    assert events == []


async def test_fluid_streak_resets_even_without_briefs(session) -> None:
    # W3: streak reset must not depend on process_daily_briefs ever running for
    # this user (briefs disabled, or the evening brief lands in quiet hours).
    await _make_user(session)
    reminder_dao = ReminderDAO(session)
    reminder = await reminder_dao.create_reminder(
        user_id=1,
        text="Read",
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=365 * 5),
        is_recurring=True,
        is_habit=True,
        is_fluid_habit=True,
        fluid_mode="brief_only",
        is_nagging=False,
    )
    stale_date = (datetime.now(timezone.utc).date() - timedelta(days=5)).isoformat()
    reminder.fluid_last_completed_date = stale_date
    reminder.fluid_streak_current = 4
    reminder.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    await session.flush()

    await _sweep_user(session, 1)

    assert reminder.fluid_streak_current == 0
