"""B-04 (2026-11-04 audit): SQLite's lower()/LIKE only fold ASCII, so a
Cyrillic search in a different case found nothing."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User


@pytest.fixture
async def dao():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        s.add(User(id=1, username="u", timezone="UTC"))
        await s.flush()
        d = ReminderDAO(s)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        await d.create_reminder(1, "Купить Молоко", now + timedelta(hours=1))
        await d.create_reminder(1, "ÉCOLE réunion", now + timedelta(hours=2))
        await d.create_reminder(1, "Позвонить", now + timedelta(hours=3))
        yield d
    await engine.dispose()


@pytest.mark.parametrize("query", ["молоко", "МОЛОКО", "Молоко", "купить мол"])
async def test_cyrillic_search_ignores_case(dao, query) -> None:
    results = await dao.search_user_reminders(1, query)
    assert [r.reminder_text for r in results] == ["Купить Молоко"]


async def test_non_ascii_latin_search_ignores_case(dao) -> None:
    results = await dao.search_user_reminders(1, "école")
    assert [r.reminder_text for r in results] == ["ÉCOLE réunion"]


async def test_blank_query_matches_nothing(dao) -> None:
    assert await dao.search_user_reminders(1, "   ") == []
