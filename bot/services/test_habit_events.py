from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401 — registers models on Base.metadata
from bot.database.engine import Base
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.handlers.habits import cb_not_today


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _make_habit(session, *, is_fluid=False, due=None) -> tuple[ReminderDAO, User, "models.Reminder"]:
    user_dao_user = User(id=1, username="u", timezone="UTC")
    session.add(user_dao_user)
    await session.flush()

    reminder_dao = ReminderDAO(session)
    reminder = await reminder_dao.create_reminder(
        user_id=1,
        text="Workout",
        execution_time=due or datetime(2026, 5, 1, 9, 0, 0),
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_habit=True,
        is_fluid_habit=is_fluid,
        is_nagging=True,
    )
    if not is_fluid:
        reminder.habit_active_due_at = due or datetime(2026, 5, 1, 9, 0, 0)
    await session.flush()
    return reminder_dao, user_dao_user, reminder


async def test_record_is_idempotent_per_cycle(session) -> None:
    habit_event_dao = HabitEventDAO(session)
    _, user, reminder = await _make_habit(session)
    due = datetime(2026, 5, 1, 9, 0, 0)

    first = await habit_event_dao.record(
        reminder=reminder, user_tz=user.timezone, outcome="done", source="button", due_at_utc_naive=due
    )
    second = await habit_event_dao.record(
        reminder=reminder, user_tz=user.timezone, outcome="not_today", source="auto", due_at_utc_naive=due
    )

    assert first is True
    assert second is False
    all_events = await habit_event_dao.get_events_in_range(user.id, "2026-01-01", "2026-12-31")
    assert len(all_events) == 1


async def test_explicit_done_wins_over_later_auto_missed(session) -> None:
    habit_event_dao = HabitEventDAO(session)
    _, user, reminder = await _make_habit(session)
    due = datetime(2026, 5, 1, 9, 0, 0)

    await habit_event_dao.record(
        reminder=reminder, user_tz=user.timezone, outcome="done", source="button", due_at_utc_naive=due
    )
    recorded_again = await habit_event_dao.record(
        reminder=reminder, user_tz=user.timezone, outcome="missed", source="auto", due_at_utc_naive=due
    )

    assert recorded_again is False
    events = await habit_event_dao.get_events_in_range(user.id, "2026-01-01", "2026-12-31")
    assert len(events) == 1
    assert events[0].outcome == "done"


async def test_not_today_resets_current_streak_but_not_best(session) -> None:
    reminder_dao, user, reminder = await _make_habit(session, due=datetime(2026, 5, 1, 9, 0, 0))
    reminder.habit_streak_current = 5
    reminder.habit_streak_best = 9
    await session.flush()

    callback = SimpleNamespace(
        data=f"not_today_{reminder.id}_{int(reminder.habit_active_due_at.replace(tzinfo=timezone.utc).timestamp())}",
        message=SimpleNamespace(text="Workout", edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    scheduler_service = SimpleNamespace(remove_nagging_job=lambda _id: None)
    habit_event_dao = HabitEventDAO(session)
    l10n = {"invalid_action": "x", "already_done": "y", "habit_not_today_saved": "saved"}

    await cb_not_today(
        callback,
        reminder_dao=reminder_dao,
        habit_event_dao=habit_event_dao,
        scheduler_service=scheduler_service,
        user=user,
        l10n=l10n,
    )

    assert reminder.habit_streak_current == 0
    assert reminder.habit_streak_best == 9


async def test_mark_habit_not_today_does_not_set_completed_at(session) -> None:
    reminder_dao, user, reminder = await _make_habit(
        session, due=datetime(2020, 1, 1, 9, 0, 0)
    )
    reminder.execution_time = datetime(2020, 1, 1, 9, 0, 0)  # in the past → cycle should hide
    await session.flush()

    await reminder_dao.mark_habit_not_today(reminder.id)

    assert reminder.completed_at is None
    assert reminder.completed_for_execution_time == reminder.execution_time


async def test_undo_deletes_cycle_event(session) -> None:
    habit_event_dao = HabitEventDAO(session)
    _, user, reminder = await _make_habit(session)
    due = datetime(2026, 5, 1, 9, 0, 0)

    await habit_event_dao.record(
        reminder=reminder, user_tz=user.timezone, outcome="done", source="button", due_at_utc_naive=due
    )
    reminder.habit_last_completed_due_at = due
    await session.flush()

    from bot.database.dao.habit_event import cycle_key_for_fixed

    await habit_event_dao.delete_for_cycle(reminder.id, cycle_key_for_fixed(due))

    remaining = await habit_event_dao.get_events_in_range(user.id, "2026-01-01", "2026-12-31")
    assert remaining == []
