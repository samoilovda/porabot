"""Regression (REWORK_PLAN_3 3.4): tapping Done embeds the credited cycle in
its callback_data (done_task_{id}_{ts}), so tapping Done on a stale/old
notification correctly credits the cycle THAT notification was for, not
whatever habit_active_due_at happens to be by then. The Undo button built
right after didn't carry that same context — it fell back to
reminder.habit_active_due_at, which can have moved on to a LATER cycle by
the time Undo is tapped (e.g. the user was slow, and the habit already
fired again for the next cycle in the meantime). Tapping that stale Undo
button then reverted the LATER (still-wanted) cycle's streak and event
instead of the one Done was actually for.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.dao.habit_event import HabitEventDAO, cycle_key_for_fixed
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.reminders import callback_done_undo, callback_task_done
from bot.lexicon import get_l10n
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


def _callback(data: str, text: str = "🔔 Workout"):
    message = SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=1),
        message_id=1,
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        answer=AsyncMock(),
    )
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message)


def _extract_undo_callback_data(edit_text_mock: AsyncMock) -> str:
    keyboard = edit_text_mock.await_args.kwargs["reply_markup"]
    for row in keyboard.inline_keyboard:
        for button in row:
            if button.callback_data.startswith("done_undo_"):
                return button.callback_data
    raise AssertionError("No done_undo_ button found in keyboard")


async def test_undo_on_stale_done_message_does_not_revert_a_later_cycle(session_factory) -> None:
    scheduler_service = SchedulerService.__new__(SchedulerService)
    scheduler_service.scheduler = SimpleNamespace(add_job=lambda *a, **k: None, get_job=lambda *a, **k: None)
    scheduler_service.remove_reminder_job = lambda *a, **k: None
    scheduler_service.remove_nagging_job = lambda *a, **k: None

    # Within apply_habit_streak_completion's 24h grace window from "now" (so
    # neither completion is flagged "late"), and ~22h apart (within its
    # accepted 12h-36h daily-cycle gap window). Microseconds stripped —
    # SQLite's DATETIME storage doesn't round-trip them, and this compares
    # values read back from the DB against these in-memory ones.
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    day1 = now - timedelta(hours=23)
    day2 = now - timedelta(hours=1)

    # execution_time is the reminder's NEXT scheduled fire (rrule anchor),
    # distinct from habit_active_due_at (the current cycle). Kept safely in
    # the future so day1's mark_done doesn't set completed_for_execution_time
    # to a value that then makes day2's own completion look "already done".
    future_execution_time = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=5)

    async with session_factory() as session:
        session.add(User(id=1, timezone="UTC"))
        await session.flush()
        reminder_dao = ReminderDAO(session)
        reminder = await reminder_dao.create_reminder(
            user_id=1,
            text="Workout",
            execution_time=future_execution_time,
            is_recurring=True,
            rrule_string="FREQ=DAILY",
            is_habit=True,
            is_nagging=True,
        )
        reminder.habit_active_due_at = day1
        reminder_id = reminder.id
        await session.commit()

    # Day 1: Done tapped on the day-1 notification, which embeds day1's
    # timestamp — the notification's own cycle, per get_task_done_keyboard.
    day1_ts = int(day1.replace(tzinfo=timezone.utc).timestamp())
    day1_callback = _callback(f"done_task_{reminder_id}_{day1_ts}")
    async with session_factory() as session:
        reminder_dao = ReminderDAO(session)
        habit_event_dao = HabitEventDAO(session)
        await callback_task_done(
            day1_callback,
            reminder_dao=reminder_dao,
            habit_event_dao=habit_event_dao,
            scheduler_service=scheduler_service,
            user=SimpleNamespace(id=1, timezone="UTC"),
            l10n=L10N,
        )
        await session.commit()
    day1_undo_callback_data = _extract_undo_callback_data(day1_callback.message.edit_text)

    # The habit fires again the next day — habit_active_due_at advances —
    # and the user completes THAT cycle too, via its own (fresh) notification.
    day2_ts = int(day2.replace(tzinfo=timezone.utc).timestamp())
    async with session_factory() as session:
        reminder = (await ReminderDAO(session).get_by_id(reminder_id))
        reminder.habit_active_due_at = day2
        await session.commit()

    day2_callback = _callback(f"done_task_{reminder_id}_{day2_ts}")
    async with session_factory() as session:
        reminder_dao = ReminderDAO(session)
        habit_event_dao = HabitEventDAO(session)
        await callback_task_done(
            day2_callback,
            reminder_dao=reminder_dao,
            habit_event_dao=habit_event_dao,
            scheduler_service=scheduler_service,
            user=SimpleNamespace(id=1, timezone="UTC"),
            l10n=L10N,
        )
        await session.commit()

    async with session_factory() as session:
        reminder = await ReminderDAO(session).get_by_id(reminder_id)
        assert reminder.habit_streak_current == 2
        assert reminder.habit_last_completed_due_at == day2

    # Now the user, confusingly, taps Undo on the STALE day-1 message.
    undo_callback = _callback(day1_undo_callback_data)
    async with session_factory() as session:
        reminder_dao = ReminderDAO(session)
        habit_event_dao = HabitEventDAO(session)
        await callback_done_undo(
            undo_callback,
            reminder_dao=reminder_dao,
            habit_event_dao=habit_event_dao,
            scheduler_service=scheduler_service,
            user=SimpleNamespace(id=1, timezone="UTC"),
            l10n=L10N,
        )
        await session.commit()

    async with session_factory() as session:
        habit_event_dao = HabitEventDAO(session)
        reminder = await ReminderDAO(session).get_by_id(reminder_id)

        # Day 2's completion — the one the user did NOT try to undo — must
        # survive untouched: same streak, same event still on record.
        assert reminder.habit_streak_current == 2
        assert await habit_event_dao.has_event_for_cycle(reminder_id, cycle_key_for_fixed(day2))

        # Day 1's event (what the stale Undo button actually targets) is
        # gone — correctly targeted instead of a no-op.
        assert not await habit_event_dao.has_event_for_cycle(reminder_id, cycle_key_for_fixed(day1))
