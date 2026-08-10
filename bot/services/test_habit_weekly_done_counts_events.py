"""Regression (REWORK_PLAN_3 2.5): get_habit_motivation_stats' weekly_done
counted DISTINCT REMINDERS with a completed_at in the window, not
completions. completed_at is a single field overwritten on every
completion (see ReminderDAO.mark_done) — a habit completed every day for a
week showed "weekly_done: 1" on the dashboard, not 5 or 7.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User


async def test_weekly_done_counts_done_events_not_distinct_reminders() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_pool = async_sessionmaker(engine, expire_on_commit=False)

        async with session_pool() as session:
            session.add(User(id=1, timezone="UTC"))
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
            today = datetime.now(timezone.utc).date()
            for i in range(5):
                day = today - timedelta(days=i)
                await habit_event_dao.record(
                    reminder=reminder,
                    user_tz="UTC",
                    outcome="done",
                    source="button",
                    due_at_utc_naive=datetime(day.year, day.month, day.day, 9, 0, 0),
                )
                # mark_done overwrites completed_at each time — the old bug's
                # actual mechanism — so exercise it the same way the real
                # completion flow would.
                await reminder_dao.mark_done(reminder.id)
            await session.commit()

        async with session_pool() as session:
            stats = await ReminderDAO(session).get_habit_motivation_stats(1, "UTC", days=7)

        assert stats["weekly_done"] == 5
    finally:
        await engine.dispose()


async def test_weekly_done_is_zero_with_no_events_in_window() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_pool = async_sessionmaker(engine, expire_on_commit=False)

        async with session_pool() as session:
            session.add(User(id=1, timezone="UTC"))
            await session.flush()
            await session.commit()

        async with session_pool() as session:
            stats = await ReminderDAO(session).get_habit_motivation_stats(1, "UTC", days=7)

        assert stats["weekly_done"] == 0
    finally:
        await engine.dispose()
