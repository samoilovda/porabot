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

import bot.services.scheduler as scheduler_module
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.dao.user import UserDAO
from bot.database.models import HabitEvent, User
import bot.handlers.reminders as reminders_module
from bot.handlers.reminders import callback_delete_task, callback_edit_delete
from bot.handlers.settings import callback_clear_all_confirm
from bot.lexicon import get_l10n
from bot.services.delete_cleanup import process_deferred_deletes

DUE = datetime(2026, 5, 1, 9, 0, 0)
L10N = get_l10n("en")


@pytest.fixture
async def session_factory():
    # Deferred-delete tests need to open a second session against the same
    # in-memory DB (mirrors how process_deferred_deletes opens its own
    # session via scheduler_service.session_pool during its cron sweep).
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


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
        edit_reply_markup=AsyncMock(),
        answer=AsyncMock(),
        delete=AsyncMock(),
    )
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message)


def _fake_scheduler(session_factory) -> SimpleNamespace:
    return SimpleNamespace(
        remove_reminder_job=lambda _id: None,
        remove_nagging_job=lambda _id: None,
        session_pool=session_factory,
    )


async def _run_cleanup_sweep(session_factory) -> None:
    """Run the delete_cleanup cron job body against *session_factory*.

    Mirrors how other services' tests point the module-level scheduler
    singleton at a test session pool.
    """
    scheduler_module._instance = SimpleNamespace(session_pool=session_factory)
    try:
        await process_deferred_deletes()
    finally:
        scheduler_module._instance = None


async def test_del_task_path_removes_habit_events(session, session_factory, monkeypatch) -> None:
    # Fixed habits appear in "My Tasks", whose rows carry a del_task_ button.
    monkeypatch.setattr(reminders_module, "_UNDO_DELETE_WINDOW", 0)
    reminder_dao, habit_event_dao, reminder = await _seed_habit_with_event(session)
    user = await UserDAO(session).get_by_id(1)
    assert await _event_count(session) == 1

    await callback_delete_task(
        _callback(f"del_task_{reminder.id}"),
        reminder_dao=reminder_dao,
        scheduler_service=_fake_scheduler(session_factory),
        user=user,
        l10n=L10N,
    )
    # Deletion is persisted as pending_delete_at (already in the past since
    # the window is monkeypatched to 0) and finalized by the cleanup sweep —
    # restart-safe by construction, unlike the old in-memory timer.
    await _run_cleanup_sweep(session_factory)

    assert await _event_count(session) == 0


async def test_edit_delete_path_removes_habit_events(session, session_factory, monkeypatch) -> None:
    # Habits reach this path through the ⚙️ button in the habits list.
    monkeypatch.setattr(reminders_module, "_UNDO_DELETE_WINDOW", 0)
    reminder_dao, habit_event_dao, reminder = await _seed_habit_with_event(session)
    user = await UserDAO(session).get_by_id(1)
    assert await _event_count(session) == 1

    await callback_edit_delete(
        _callback(f"edit_delete_{reminder.id}"),
        reminder_dao=reminder_dao,
        scheduler_service=_fake_scheduler(session_factory),
        user=user,
        l10n=L10N,
    )
    await _run_cleanup_sweep(session_factory)

    assert await _event_count(session) == 0


async def test_undo_delete_within_window_keeps_reminder_and_events(session, session_factory) -> None:
    # Undo tap before the window elapses must clear pending_delete_at and
    # leave the reminder (and its habit_events) untouched.
    reminder_dao, habit_event_dao, reminder = await _seed_habit_with_event(session)
    user = await UserDAO(session).get_by_id(1)

    await callback_delete_task(
        _callback(f"del_task_{reminder.id}"),
        reminder_dao=reminder_dao,
        scheduler_service=_fake_scheduler(session_factory),
        user=user,
        l10n=L10N,
    )
    assert reminder.pending_delete_at is not None

    await reminders_module.callback_undo_delete(
        _callback(f"undo_del_{reminder.id}"),
        reminder_dao=reminder_dao,
        scheduler_service=SimpleNamespace(schedule_reminder=lambda *a, **kw: None),
        user=user,
        l10n=L10N,
    )

    assert reminder.pending_delete_at is None
    # A cleanup sweep running right after Undo must not delete it either.
    await _run_cleanup_sweep(session_factory)
    assert await _event_count(session) == 1
    restored = await reminder_dao.get_owned(reminder.id, user.id)
    assert restored is not None


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
