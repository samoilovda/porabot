"""Step 2 of the 2026-09-26 audit remediation: "Clear all" must be a data
RESET (tasks/habits/settings gone, users row kept with defaults) and must
never touch payments — see bot/services/user_data.py's docstring.
"""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.payment import PaymentDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.dao.user import UserDAO
from bot.database.engine import Base
from bot.database.models import HabitEvent, Payment, Reminder, User
from bot.services.user_data import UserDataService

DUE = datetime(2026, 5, 1, 9, 0, 0)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _seed_payment_task_and_habit(session) -> User:
    user = User(id=1, username="u", timezone="Europe/Moscow", language="ru")
    session.add(user)
    await session.flush()

    await PaymentDAO(session).record_once(
        user_id=1,
        telegram_payment_charge_id="charge-1",
        amount=100,
        currency="XTR",
        invoice_payload="support",
    )

    reminder_dao = ReminderDAO(session)
    await reminder_dao.create_reminder(
        user_id=1, text="Buy milk", execution_time=DUE, is_recurring=False,
    )
    habit = await reminder_dao.create_reminder(
        user_id=1, text="Workout", execution_time=DUE, is_recurring=True,
        rrule_string="FREQ=DAILY", is_habit=True, is_nagging=True,
    )
    habit.habit_active_due_at = DUE
    await session.flush()

    await HabitEventDAO(session).record(
        reminder=habit, user_tz="UTC", outcome="done", source="button", due_at_utc_naive=DUE
    )
    await session.commit()
    return user


async def _counts(session) -> dict:
    return {
        "reminders": (await session.execute(select(func.count()).select_from(Reminder))).scalar_one(),
        "habit_events": (await session.execute(select(func.count()).select_from(HabitEvent))).scalar_one(),
        "payments": (await session.execute(select(func.count()).select_from(Payment))).scalar_one(),
        "users": (await session.execute(select(func.count()).select_from(User))).scalar_one(),
    }


async def test_reset_clears_tasks_and_habits_but_keeps_the_payment(session) -> None:
    user = await _seed_payment_task_and_habit(session)
    reminder_dao = ReminderDAO(session)
    habit_event_dao = HabitEventDAO(session)

    reminder_ids = await UserDataService(reminder_dao, habit_event_dao).reset(user)

    assert len(reminder_ids) == 2  # task + habit, so callers can drop both jobs
    counts = await _counts(session)
    assert counts["reminders"] == 0
    assert counts["habit_events"] == 0
    assert counts["payments"] == 1  # never touched
    assert counts["users"] == 1  # row kept, not deleted

    kept_payment = (
        await session.execute(select(Payment).where(Payment.telegram_payment_charge_id == "charge-1"))
    ).scalar_one()
    assert kept_payment.user_id == 1

    # Settings go back to defaults so the user re-onboards.
    reset_user = await UserDAO(session).get_by_id(1)
    assert reset_user.language is None
    assert reset_user.timezone == "UTC"
    assert reset_user.ics_feed_token is None


async def test_failed_commit_leaves_tasks_and_jobs_untouched(session, monkeypatch) -> None:
    user = await _seed_payment_task_and_habit(session)
    reminder_dao = ReminderDAO(session)
    habit_event_dao = HabitEventDAO(session)

    monkeypatch.setattr(
        session, "commit", AsyncMock(side_effect=OperationalError("COMMIT", {}, Exception("database is locked")))
    )

    with pytest.raises(OperationalError):
        await UserDataService(reminder_dao, habit_event_dao).reset(user)

    await session.rollback()

    # Nothing committed: a caller that only removes scheduler jobs after a
    # successful reset() is safe to keep every job registered here too.
    counts = await _counts(session)
    assert counts["reminders"] == 2
    assert counts["habit_events"] == 1
    assert counts["payments"] == 1
