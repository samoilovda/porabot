"""B-14 / A-09 (2026-11-04 audit): the missed-task digest's count, length
budget, and once-a-day check."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import bot.context as context_module
from bot.database import models  # noqa: F401
from bot.database.engine import Base
from bot.database.models import User
from bot.lexicon import get_l10n
from bot.services.missed_recovery import RECOVERY_DIGEST_LIMIT, build_digest_text, process_missed_task_recovery

USER = SimpleNamespace(timezone="UTC", show_utc_offset=False)


def _task(i, text="task"):
    return SimpleNamespace(execution_time=datetime(2026, 11, 1, 9, 0) + timedelta(minutes=i), reminder_text=text)


def test_title_shows_the_true_total_not_the_truncated_length() -> None:
    l10n = get_l10n("en")
    text = build_digest_text([_task(i) for i in range(30)], USER, l10n)
    assert "30 overdue" in text
    assert text.count("▫️") == RECOVERY_DIGEST_LIMIT
    assert l10n["brief_items_more"].format(count=30 - RECOVERY_DIGEST_LIMIT) in text


def test_long_task_texts_stay_within_telegram_limit() -> None:
    text = build_digest_text([_task(i, "x" * 3000) for i in range(RECOVERY_DIGEST_LIMIT)], USER, get_l10n("ru"))
    assert len(text) < 4096


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False), engine
    await engine.dispose()


async def test_user_with_nothing_overdue_is_checked_once_per_day(maker, monkeypatch) -> None:
    session_maker, engine = maker
    async with session_maker() as s:
        s.add(User(id=1, username="u", timezone="UTC", missed_recovery_enabled=True, missed_recovery_time="00:00"))
        await s.commit()
    bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(context_module, "_context", SimpleNamespace(bot=bot, session_pool=session_maker))

    reminder_queries = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count(conn, cursor, statement, params, context, executemany):
        if "FROM reminders" in statement:
            reminder_queries.append(statement)

    await process_missed_task_recovery()
    first = len(reminder_queries)
    await process_missed_task_recovery()
    await process_missed_task_recovery()

    assert first == 1
    assert len(reminder_queries) == first  # later ticks the same day skip the user
    bot.send_message.assert_not_awaited()
    async with session_maker() as s:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert (await s.get(User, 1)).last_missed_recovery_date == today
