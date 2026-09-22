"""docs/audits/2026-09-22-audit.md#a-13 — process_deferred_deletes used to
read a reminder, check pending_delete_at in Python, then issue a SEPARATE
delete statement with no guard of its own — a TOCTOU window where an Undo
tap's own commit (clearing pending_delete_at) lands between the read and
the write, and the sweep deletes the row anyway, undo or not. The fix
folds the exact same guard directly into the DELETE's own WHERE clause, so
there is no read step left at all for a concurrent Undo to race against —
the guard is evaluated by SQLite as part of the single write statement,
against whatever is truly committed at that instant.

These tests verify the guarded DELETE's two directions directly (still due
-> removed, already undone/not yet due -> survives) — the atomicity claim
itself follows from there being exactly one statement with the condition
built in, not a read followed by an unconditional write; see this
module's diff against bot/services/delete_cleanup.py for the shape.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import bot.context as context_module
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import HabitEvent, User
from bot.services.delete_cleanup import process_deferred_deletes


@pytest.fixture
async def session_pool():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _run_cleanup(maker) -> None:
    import unittest.mock as mock

    fake_instance = type("FakeInstance", (), {})()
    fake_instance.session_pool = maker
    with mock.patch.object(context_module, "_context", fake_instance):
        await process_deferred_deletes()


async def test_already_undone_reminder_survives_the_sweep(session_pool) -> None:
    """An Undo that already committed (pending_delete_at cleared) by the
    time the sweep looks at this row must never be deleted — this is the
    outcome a stale, un-reguarded read-then-delete could get wrong."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with session_pool() as session:
        session.add(User(id=1, username="u", timezone="UTC"))
        await session.flush()
        reminder = await ReminderDAO(session).create_reminder(
            user_id=1, text="Take a walk", execution_time=now + timedelta(hours=1),
        )
        # Set, then immediately clear — as an Undo tap would, right before
        # this sweep's tick runs.
        reminder.pending_delete_at = None
        await session.commit()
        reminder_id = reminder.id

    await _run_cleanup(session_pool)

    async with session_pool() as session:
        reminder_dao = ReminderDAO(session)
        survivor = await reminder_dao.get_by_id(reminder_id)
    assert survivor is not None
    assert survivor.pending_delete_at is None


async def test_still_due_reminder_and_its_habit_events_are_both_removed(session_pool) -> None:
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
        await habit_event_dao.record(
            reminder=habit, user_tz="UTC", outcome="done", source="button",
            local_date=now.date().isoformat(),
        )
        habit.pending_delete_at = now - timedelta(seconds=1)  # undo window already elapsed
        await session.commit()
        habit_id = habit.id

    await _run_cleanup(session_pool)

    async with session_pool() as session:
        reminder_dao = ReminderDAO(session)
        assert await reminder_dao.get_by_id(habit_id) is None
        result = await session.execute(select(HabitEvent).where(HabitEvent.reminder_id == habit_id))
        assert result.first() is None


async def test_not_yet_due_reminder_is_left_alone(session_pool) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with session_pool() as session:
        session.add(User(id=1, username="u", timezone="UTC"))
        await session.flush()
        reminder = await ReminderDAO(session).create_reminder(
            user_id=1, text="Still in undo window", execution_time=now + timedelta(hours=1),
        )
        reminder.pending_delete_at = now + timedelta(seconds=30)  # undo window not elapsed yet
        await session.commit()
        reminder_id = reminder.id

    await _run_cleanup(session_pool)

    async with session_pool() as session:
        reminder_dao = ReminderDAO(session)
        survivor = await reminder_dao.get_by_id(reminder_id)
    assert survivor is not None
    assert survivor.pending_delete_at is not None
