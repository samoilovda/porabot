"""4.3: tags/priority persistence, sort order, and tag filtering
(ReminderDAO.create_reminder / get_user_reminders / get_today_pending_tasks /
get_distinct_tags / get_reminders_by_tag)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.engine import Base
from bot.database.dao.reminder import ReminderDAO
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


async def _seed_user(session) -> None:
    session.add(User(id=1, username="u", timezone="UTC"))
    await session.flush()


async def test_create_reminder_persists_tags_and_priority(session) -> None:
    await _seed_user(session)
    dao = ReminderDAO(session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    r = await dao.create_reminder(
        user_id=1, text="Buy milk", execution_time=now + timedelta(hours=1),
        tags="дом,покупки", priority=2,
    )
    assert r.tags == "дом,покупки"
    assert r.priority == 2


async def test_get_user_reminders_sorts_by_priority_then_time(session) -> None:
    await _seed_user(session)
    dao = ReminderDAO(session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Earliest execution_time but no priority — should sort AFTER prioritized tasks.
    await dao.create_reminder(user_id=1, text="No priority, early", execution_time=now + timedelta(minutes=5))
    await dao.create_reminder(user_id=1, text="Priority 2", execution_time=now + timedelta(hours=3), priority=2)
    await dao.create_reminder(user_id=1, text="Priority 1", execution_time=now + timedelta(hours=5), priority=1)

    results = await dao.get_user_reminders(1)
    texts = [r.reminder_text for r in results]
    assert texts == ["Priority 1", "Priority 2", "No priority, early"]


async def test_get_distinct_tags_returns_sorted_unique_tags(session) -> None:
    await _seed_user(session)
    dao = ReminderDAO(session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await dao.create_reminder(user_id=1, text="a", execution_time=now + timedelta(hours=1), tags="дом,работа")
    await dao.create_reminder(user_id=1, text="b", execution_time=now + timedelta(hours=2), tags="работа")
    await dao.create_reminder(user_id=1, text="c", execution_time=now + timedelta(hours=3))

    tags = await dao.get_distinct_tags(1)
    assert list(tags) == ["дом", "работа"]


async def test_get_reminders_by_tag_exact_match_not_substring(session) -> None:
    await _seed_user(session)
    dao = ReminderDAO(session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await dao.create_reminder(user_id=1, text="dom task", execution_time=now + timedelta(hours=1), tags="дом")
    await dao.create_reminder(user_id=1, text="domino task", execution_time=now + timedelta(hours=2), tags="домино")

    results = await dao.get_reminders_by_tag(1, "дом")
    texts = {r.reminder_text for r in results}
    assert texts == {"dom task"}


async def test_get_reminders_by_tag_no_match(session) -> None:
    await _seed_user(session)
    dao = ReminderDAO(session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await dao.create_reminder(user_id=1, text="a", execution_time=now + timedelta(hours=1), tags="дом")

    results = await dao.get_reminders_by_tag(1, "nonexistent")
    assert results == []
