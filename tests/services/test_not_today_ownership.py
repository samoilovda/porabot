from datetime import datetime, timezone
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


async def test_not_today_rejects_another_users_reminder_id(session) -> None:
    """P1-1 IDOR: cb_not_today looked reminders up with get_by_id (no owner
    check) instead of get_owned, so a callback carrying someone else's
    reminder_id could flip their habit event, reset their streak, and cancel
    their nagging job. It must now reject with item_not_found and leave the
    victim's data untouched."""
    owner = User(id=1, username="owner", timezone="UTC")
    attacker = User(id=2, username="attacker", timezone="UTC")
    session.add_all([owner, attacker])
    await session.flush()

    reminder_dao = ReminderDAO(session)
    reminder = await reminder_dao.create_reminder(
        user_id=owner.id,
        text="Workout",
        execution_time=datetime(2026, 5, 1, 9, 0, 0),
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_habit=True,
        is_nagging=True,
    )
    reminder.habit_active_due_at = datetime(2026, 5, 1, 9, 0, 0)
    reminder.habit_streak_current = 5
    reminder.habit_streak_best = 9
    await session.flush()

    due_ts = int(reminder.habit_active_due_at.replace(tzinfo=timezone.utc).timestamp())
    callback = SimpleNamespace(
        data=f"not_today_{reminder.id}_{due_ts}",
        message=SimpleNamespace(text="Workout", edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    remove_nagging_job = SimpleNamespace(called_with=None)

    def _remove_nagging_job(reminder_id):
        remove_nagging_job.called_with = reminder_id

    scheduler_service = SimpleNamespace(remove_nagging_job=_remove_nagging_job)
    habit_event_dao = HabitEventDAO(session)
    l10n = {"item_not_found": "not found", "already_done": "y", "habit_not_today_saved": "saved"}

    # Attacker's own user object, but the callback carries the OWNER's reminder_id.
    await cb_not_today(
        callback,
        reminder_dao=reminder_dao,
        habit_event_dao=habit_event_dao,
        scheduler_service=scheduler_service,
        user=attacker,
        l10n=l10n,
    )

    callback.answer.assert_awaited_once_with("not found", show_alert=True)
    callback.message.edit_text.assert_not_awaited()
    assert remove_nagging_job.called_with is None

    # Victim's data must be completely untouched.
    await session.refresh(reminder)
    assert reminder.habit_streak_current == 5
    assert reminder.habit_streak_best == 9
    events = await habit_event_dao.get_events_in_range(owner.id, "2026-01-01", "2026-12-31")
    assert events == []
