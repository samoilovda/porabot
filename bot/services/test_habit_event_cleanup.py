"""Regression tests: habit_events must never outlive the reminder/user they point at.

habit_events carries FKs to both reminders.id and users.id, and every deletion
path in the bot is a hard DELETE with no cascade in the schema. SQLite ships
with PRAGMA foreign_keys=OFF, so orphans accumulate silently here while the
same code raises IntegrityError on PostgreSQL — which is exactly why these
paths need explicit coverage rather than trust in the FK.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import HabitEvent, User
from bot.handlers.reminders import callback_delete_task, callback_edit_delete
from bot.handlers.settings import callback_clear_all_confirm
from bot.lexicon import get_l10n

DUE = datetime(2026, 5, 1, 9, 0, 0)
L10N = get_l10n("en")


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _seed_habit_with_event(session):
    """A habit plus one recorded 'done' event for its current cycle."""
    session.add(User(id=1, username="u", timezone="UTC"))
    await session.flush()

    reminder_dao = ReminderDAO(session)
    habit_event_dao = HabitEventDAO(session)
    reminder = await reminder_dao.create_reminder(
        user_id=1,
        text="Workout",
        execution_time=DUE,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_habit=True,
        is_nagging=True,
    )
    reminder.habit_active_due_at = DUE
    await session.flush()
    await habit_event_dao.record(
        reminder=reminder, user_tz="UTC", outcome="done", source="button", due_at_utc_naive=DUE
    )
    return reminder_dao, habit_event_dao, reminder


async def _event_count(session) -> int:
    return (await session.execute(select(func.count()).select_from(HabitEvent))).scalar_one()


def _callback(data: str):
    message = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        message_id=1,
        edit_text=AsyncMock(),
        answer=AsyncMock(),
        delete=AsyncMock(),
    )
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message)


async def test_del_task_path_removes_habit_events(session) -> None:
    # Fixed habits appear in "My Tasks", whose rows carry a del_task_ button.
    reminder_dao, habit_event_dao, reminder = await _seed_habit_with_event(session)
    assert await _event_count(session) == 1

    await callback_delete_task(
        _callback(f"del_task_{reminder.id}"),
        reminder_dao=reminder_dao,
        habit_event_dao=habit_event_dao,
        scheduler_service=SimpleNamespace(remove_reminder_job=lambda _id: None),
        l10n=L10N,
    )

    assert await _event_count(session) == 0


async def test_edit_delete_path_removes_habit_events(session) -> None:
    # Habits reach this path through the ⚙️ button in the habits list.
    reminder_dao, habit_event_dao, reminder = await _seed_habit_with_event(session)
    assert await _event_count(session) == 1

    await callback_edit_delete(
        _callback(f"edit_delete_{reminder.id}"),
        reminder_dao=reminder_dao,
        habit_event_dao=habit_event_dao,
        scheduler_service=SimpleNamespace(remove_reminder_job=lambda _id: None),
        l10n=L10N,
    )

    assert await _event_count(session) == 0


async def test_clear_all_removes_habit_events(session) -> None:
    from bot.database.dao.user import UserDAO

    reminder_dao, habit_event_dao, reminder = await _seed_habit_with_event(session)
    user = await UserDAO(session).get_by_id(1)
    assert await _event_count(session) == 1

    await callback_clear_all_confirm(
        _callback("settings_clear_all_confirm"),
        state=SimpleNamespace(clear=AsyncMock()),
        user=user,
        user_dao=UserDAO(session),
        reminder_dao=reminder_dao,
        habit_event_dao=habit_event_dao,
        scheduler_service=SimpleNamespace(remove_reminder_job=lambda _id: None),
        l10n=L10N,
    )

    assert await _event_count(session) == 0


async def test_lost_race_keeps_callers_unit_of_work_intact(session) -> None:
    """A UNIQUE violation on one event must not roll back the caller's changes.

    record() rolls back to a SAVEPOINT rather than calling session.rollback(),
    which would discard the streak update the handler already made in the same
    Unit of Work.
    """
    reminder_dao, habit_event_dao, reminder = await _seed_habit_with_event(session)

    reminder.habit_streak_current = 7  # caller's pending business change
    await session.flush()

    # Simulate losing the race: the existence check misses a row that is
    # already committed, so the INSERT hits the UNIQUE constraint.
    habit_event_dao.has_event_for_cycle = AsyncMock(return_value=False)
    recorded = await habit_event_dao.record(
        reminder=reminder, user_tz="UTC", outcome="done", source="button", due_at_utc_naive=DUE
    )

    assert recorded is False
    assert reminder.habit_streak_current == 7
    assert await _event_count(session) == 1  # no duplicate
