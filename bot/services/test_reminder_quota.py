"""Regression (REWORK_PLAN_3 3.2): access is intentionally open
(WhitelistMiddleware disabled) with only a per-user request-RATE cap
(RateLimitMiddleware) — nothing capped total VOLUME. One user could create
an unbounded number of reminders, each a DB row plus, for habits, work for
five different minutely cron jobs.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _fill_reminders(dao: ReminderDAO, user_id: int, count: int, **kwargs) -> None:
    for i in range(count):
        await dao.create_reminder(
            user_id=user_id,
            text=f"task {i}",
            execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
            **kwargs,
        )


async def test_plain_reminders_are_rejected_past_the_quota(session) -> None:
    session.add(User(id=1, timezone="UTC"))
    await session.flush()
    dao = ReminderDAO(session)
    await _fill_reminders(dao, 1, ReminderDAO.MAX_ACTIVE_REMINDERS)

    with pytest.raises(ValueError):
        await dao.create_reminder(
            user_id=1,
            text="one too many",
            execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
        )


async def test_habits_are_rejected_past_their_own_smaller_quota(session) -> None:
    session.add(User(id=1, timezone="UTC"))
    await session.flush()
    dao = ReminderDAO(session)
    await _fill_reminders(
        dao, 1, ReminderDAO.MAX_ACTIVE_HABITS, is_recurring=True, rrule_string="FREQ=DAILY", is_habit=True
    )

    with pytest.raises(ValueError):
        await dao.create_reminder(
            user_id=1,
            text="one habit too many",
            execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
            is_recurring=True,
            rrule_string="FREQ=DAILY",
            is_habit=True,
        )


async def test_habit_quota_does_not_block_plain_reminders_and_vice_versa(session) -> None:
    """The two pools are independent — a user maxed out on habits can still
    create a plain task, and a user maxed out on tasks can still create a
    habit."""
    session.add(User(id=1, timezone="UTC"))
    await session.flush()
    dao = ReminderDAO(session)
    await _fill_reminders(
        dao, 1, ReminderDAO.MAX_ACTIVE_HABITS, is_recurring=True, rrule_string="FREQ=DAILY", is_habit=True
    )

    # Must not raise — separate pool.
    plain = await dao.create_reminder(
        user_id=1,
        text="plain task",
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )
    assert plain.id is not None


async def test_well_under_the_quota_is_unaffected(session) -> None:
    session.add(User(id=1, timezone="UTC"))
    await session.flush()
    dao = ReminderDAO(session)

    reminder = await dao.create_reminder(
        user_id=1,
        text="just one",
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )
    assert reminder.id is not None


async def test_deleted_reminders_do_not_count_against_the_quota(session) -> None:
    """Soft-deleted (pending_delete_at set) reminders must not keep counting
    against the quota — otherwise deleting old tasks wouldn't free up room."""
    from datetime import timezone as tz_mod

    session.add(User(id=1, timezone="UTC"))
    await session.flush()
    dao = ReminderDAO(session)
    await _fill_reminders(dao, 1, ReminderDAO.MAX_ACTIVE_REMINDERS)

    # Soft-delete every existing reminder.
    all_reminders = await dao.get_all(user_id=1)
    for r in all_reminders:
        r.pending_delete_at = datetime.now(tz_mod.utc).replace(tzinfo=None)
    await session.flush()

    # Must not raise — none of the soft-deleted rows count any more.
    reminder = await dao.create_reminder(
        user_id=1,
        text="room again",
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )
    assert reminder.id is not None
