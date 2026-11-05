from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
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
    user = await _make_user(session)
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

    await _sweep_user(session, user)

    habit_event_dao = HabitEventDAO(session)
    events = await habit_event_dao.get_events_in_range(1, "2000-01-01", "2100-01-01")
    assert len(events) == 1
    assert events[0].outcome == "missed"
    assert reminder.habit_streak_current == 0


async def test_fixed_habit_already_done_creates_no_event(session) -> None:
    user = await _make_user(session)
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

    await _sweep_user(session, user)

    habit_event_dao = HabitEventDAO(session)
    events = await habit_event_dao.get_events_in_range(1, "2000-01-01", "2100-01-01")
    assert events == []


async def test_fixed_habit_beyond_retrospection_window_creates_no_event(session) -> None:
    """The 10-day-old cycle is outside the window and stays unrecorded.
    B-09 (2026-11-04 audit): the habit's later cycles — which never fired
    because nothing advanced it — ARE recorded as missed, but only those
    inside the retrospection window."""
    user = await _make_user(session)
    reminder_dao = ReminderDAO(session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    due = now - timedelta(days=10)
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

    await _sweep_user(session, user)

    habit_event_dao = HabitEventDAO(session)
    events = await habit_event_dao.get_events_in_range(1, "2000-01-01", "2100-01-01")
    assert all(e.due_at > now - timedelta(days=8, minutes=1) for e in events)
    assert all(e.outcome == "missed" for e in events)
    assert 6 <= len(events) <= 7


async def test_every_cycle_missed_during_an_outage_is_recorded(session) -> None:
    """B-09: three days without a single notification firing (bot down or
    blocked) used to yield one missed event; each elapsed cycle counts."""
    user = await _make_user(session)
    reminder_dao = ReminderDAO(session)
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    first = now - timedelta(days=3, hours=1)
    reminder = await reminder_dao.create_reminder(
        user_id=1, text="Stretch", execution_time=first,
        is_recurring=True, rrule_string="FREQ=DAILY", is_habit=True,
    )
    reminder.habit_active_due_at = first
    reminder.created_at = first - timedelta(days=1)
    reminder.habit_streak_current = 5
    await session.flush()

    await _sweep_user(session, user)
    await _sweep_user(session, user)  # idempotent

    events = await HabitEventDAO(session).get_events_in_range(1, "2000-01-01", "2100-01-01")
    # cycles at -3d1h, -2d1h, -1d1h have passed their 24h grace; today's hasn't
    assert sorted(e.due_at for e in events) == [first, first + timedelta(days=1), first + timedelta(days=2)]
    assert reminder.habit_streak_current == 0


async def test_weekday_habit_friday_is_not_missed_over_the_weekend(session) -> None:
    """B-08: a Mon–Fri habit's Friday cycle stays open until Monday."""
    from unittest.mock import patch

    import bot.services.habit_sweeper as sweeper

    user = await _make_user(session)
    reminder_dao = ReminderDAO(session)
    friday = datetime(2026, 11, 6, 6, 0)
    reminder = await reminder_dao.create_reminder(
        user_id=1, text="Gym", execution_time=friday,
        is_recurring=True, rrule_string="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", is_habit=True,
    )
    reminder.habit_active_due_at = friday
    reminder.created_at = friday - timedelta(days=1)
    await session.flush()

    class _Sunday(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 8, 20, 0, tzinfo=timezone.utc)

    with patch.object(sweeper, "datetime", _Sunday):
        await _sweep_user(session, user)
    assert await HabitEventDAO(session).get_events_in_range(1, "2000-01-01", "2100-01-01") == []


async def test_fluid_habit_not_marked_yesterday_creates_missed_event(session) -> None:
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
    reminder.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    await session.flush()

    await _sweep_user(session, user)

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

    await _sweep_user(session, user)

    habit_event_dao = HabitEventDAO(session)
    events = await habit_event_dao.get_events_in_range(1, "2000-01-01", "2100-01-01")
    assert events == []


async def test_habit_created_today_does_not_close_yesterday(session) -> None:
    user = await _make_user(session)
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

    await _sweep_user(session, user)

    habit_event_dao = HabitEventDAO(session)
    events = await habit_event_dao.get_events_in_range(1, "2000-01-01", "2100-01-01")
    assert events == []


async def test_fluid_streak_resets_even_without_briefs(session) -> None:
    # W3: streak reset must not depend on process_daily_briefs ever running for
    # this user (briefs disabled, or the evening brief lands in quiet hours).
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
    stale_date = (datetime.now(timezone.utc).date() - timedelta(days=5)).isoformat()
    reminder.fluid_last_completed_date = stale_date
    reminder.fluid_streak_current = 4
    reminder.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    await session.flush()

    await _sweep_user(session, user)

    assert reminder.fluid_streak_current == 0
