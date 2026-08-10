"""Regression (REWORK_PLAN_3 2.4): cb_del_habit used to hard-delete a habit
AND its whole habit_events history immediately, with no confirmation and no
undo — a mistap on the delete button (which sits right next to the settings
gear in the habits list) permanently destroyed streaks and report history.

Mirrors test_delete_survives_restart.py's pattern for reminders.py's
del_task_/undo_del_ pair, which this now reuses.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.dao.user import UserDAO
from bot.database.engine import Base
from bot.database.models import HabitEvent, Reminder, User
from bot.handlers.habits import cb_del_habit
from bot.handlers.reminders import callback_undo_delete
from bot.lexicon import get_l10n
from bot.services.delete_cleanup import process_deferred_deletes
from bot.services.scheduler import SchedulerService

L10N = get_l10n("en")


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _callback(data: str):
    message = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        message_id=1,
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        answer=AsyncMock(),
    )
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message)


async def _make_habit_with_history(session):
    session.add(User(id=1, username="u", timezone="UTC"))
    await session.flush()
    reminder_dao = ReminderDAO(session)
    reminder = await reminder_dao.create_reminder(
        user_id=1,
        text="Workout",
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_habit=True,
        is_nagging=True,
    )
    habit_event_dao = HabitEventDAO(session)
    await habit_event_dao.record(
        reminder=reminder,
        user_tz="UTC",
        outcome="done",
        source="button",
        due_at_utc_naive=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
    )
    reminder_id = reminder.id
    await session.commit()
    return reminder_id


async def test_deleting_a_habit_does_not_immediately_destroy_it_or_its_history(session_factory) -> None:
    async with session_factory() as session:
        reminder_id = await _make_habit_with_history(session)

    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=session_factory)

    async with session_factory() as session:
        reminder_dao = ReminderDAO(session)
        user = await UserDAO(session).get_by_id(1)
        await cb_del_habit(
            _callback(f"del_habit_{reminder_id}"),
            reminder_dao=reminder_dao,
            scheduler_service=service,
            user=user,
            l10n=L10N,
        )

    # Not gone: soft-deleted, pending_delete_at set — an Undo tap can still
    # bring it back. Its habit_events (streak/report history) are untouched.
    async with session_factory() as session:
        reminder = (await session.execute(select(Reminder).where(Reminder.id == reminder_id))).scalar_one_or_none()
        assert reminder is not None
        assert reminder.pending_delete_at is not None

        events = (
            (await session.execute(select(HabitEvent).where(HabitEvent.reminder_id == reminder_id)))
            .scalars()
            .all()
        )
        assert len(events) == 1

    assert scheduler.get_job(str(reminder_id)) is None


async def test_undo_after_deleting_a_habit_restores_it_and_its_history(session_factory) -> None:
    async with session_factory() as session:
        reminder_id = await _make_habit_with_history(session)

    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=session_factory)

    async with session_factory() as session:
        reminder_dao = ReminderDAO(session)
        user = await UserDAO(session).get_by_id(1)
        await cb_del_habit(
            _callback(f"del_habit_{reminder_id}"),
            reminder_dao=reminder_dao,
            scheduler_service=service,
            user=user,
            l10n=L10N,
        )

    async with session_factory() as session:
        reminder_dao = ReminderDAO(session)
        user = await UserDAO(session).get_by_id(1)
        await callback_undo_delete(
            _callback(f"undo_del_{reminder_id}"),
            reminder_dao=reminder_dao,
            scheduler_service=service,
            user=user,
            l10n=L10N,
        )
        # callback_undo_delete only flush()es — like every other handler, the
        # commit normally comes from DatabaseMiddleware wrapping the request.
        # Calling the handler directly (no middleware here) needs it explicit.
        await session.commit()

    async with session_factory() as session:
        reminder = (await session.execute(select(Reminder).where(Reminder.id == reminder_id))).scalar_one_or_none()
        assert reminder is not None
        assert reminder.pending_delete_at is None
        assert reminder.status == "pending"

        events = (
            (await session.execute(select(HabitEvent).where(HabitEvent.reminder_id == reminder_id)))
            .scalars()
            .all()
        )
        assert len(events) == 1


async def test_habit_is_hard_deleted_with_its_history_once_undo_window_elapses(session_factory) -> None:
    async with session_factory() as session:
        reminder_id = await _make_habit_with_history(session)

    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=session_factory)
    from bot.services import scheduler as scheduler_module

    scheduler_module._instance = service
    try:
        async with session_factory() as session:
            reminder_dao = ReminderDAO(session)
            user = await UserDAO(session).get_by_id(1)
            await cb_del_habit(
                _callback(f"del_habit_{reminder_id}"),
                reminder_dao=reminder_dao,
                scheduler_service=service,
                user=user,
                l10n=L10N,
            )

        # Force the deadline into the past instead of sleeping in the test.
        async with session_factory() as session:
            reminder = (await session.execute(select(Reminder).where(Reminder.id == reminder_id))).scalar_one()
            reminder.pending_delete_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
            await session.commit()

        await process_deferred_deletes()

        async with session_factory() as session:
            reminder = (await session.execute(select(Reminder).where(Reminder.id == reminder_id))).scalar_one_or_none()
            assert reminder is None
            events = (
                (await session.execute(select(HabitEvent).where(HabitEvent.reminder_id == reminder_id)))
                .scalars()
                .all()
            )
            assert events == []
    finally:
        scheduler_module._instance = None
