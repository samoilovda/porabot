"""B-15 (2026-11-04 audit): ancient overdue one-off tasks clogged "Today"
forever and counted toward the active-task quota."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.experience import list_items, render_list
from bot.lexicon import get_l10n
from bot.services.ui_context import create_context


@pytest.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        user = User(id=1, username="u", timezone="UTC", language="en")
        s.add(user)
        await s.flush()
        yield ReminderDAO(s), user
    await engine.dispose()


async def _task(dao, text, delta):
    r = await dao.create_reminder(1, text, datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1))
    r.execution_time = datetime.now(timezone.utc).replace(tzinfo=None) + delta
    await dao.session.flush()
    return r


async def test_today_shows_recent_overdue_only_and_hints_at_the_rest(env) -> None:
    dao, user = env
    await _task(dao, "recent", -timedelta(days=2))
    await _task(dao, "ancient", -timedelta(days=40))
    items = await list_items(dao, user, {"kind": "today", "query": None})
    assert [r.reminder_text for r in items] == ["recent"]

    ctx = await create_context(dao.session, 1, "list", {"kind": "today", "query": None})
    msg = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock())
    await render_list(msg, dao, user, get_l10n("en"), ctx, 0, edit=False)
    text = msg.answer.await_args.args[0]
    assert "1 older overdue" in text
    assert json.loads(ctx.payload_json)["kind"] == "today"


async def test_overdue_filter_still_lists_everything(env) -> None:
    dao, user = env
    await _task(dao, "recent", -timedelta(days=2))
    await _task(dao, "ancient", -timedelta(days=40))
    items = await list_items(dao, user, {"kind": "overdue", "query": None})
    assert {r.reminder_text for r in items} == {"recent", "ancient"}


async def test_stale_overdue_tasks_do_not_count_toward_quota(env, monkeypatch) -> None:
    dao, _ = env
    monkeypatch.setattr(ReminderDAO, "MAX_ACTIVE_REMINDERS", 2)
    await _task(dao, "ancient 1", -timedelta(days=40))
    await _task(dao, "ancient 2", -timedelta(days=45))
    await _task(dao, "fresh", timedelta(hours=1))
    await dao.create_reminder(1, "still allowed", datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2))
    with pytest.raises(ValueError):
        await dao.create_reminder(1, "over quota", datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3))
