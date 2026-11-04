"""B-11 (2026-11-04 audit): snoozing an occurrence of a RECURRING reminder
used to replace the series' own job (regular occurrences before the snooze
time were silently dropped) and was visible nowhere."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.context import clear_context
from bot.database import models  # noqa: F401
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import Reminder, User
from bot.services.scheduler import SchedulerService


@pytest.fixture
async def setup():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    async with maker() as s:
        s.add(User(id=1, username="u", timezone="UTC", language="en"))
        await s.flush()
        r = await ReminderDAO(s).create_reminder(
            1, "standup", now + timedelta(hours=20), is_recurring=True, rrule_string="FREQ=DAILY"
        )
        r.snoozed_until = now + timedelta(hours=1)
        await s.commit()
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=7)),
        delete_message=AsyncMock(),
    )
    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=bot, session_pool=maker)
    yield service, scheduler, maker, r.id, now, bot
    await engine.dispose()
    clear_context()


async def test_snooze_uses_its_own_job_and_keeps_the_series_job(setup) -> None:
    service, scheduler, _, rid, now, _ = setup
    service.schedule_reminder(rid, now + timedelta(hours=20))
    service.schedule_snooze(rid, now + timedelta(hours=1))
    assert scheduler.get_job(str(rid)) is not None
    assert scheduler.get_job(f"snz_{rid}") is not None


async def test_snooze_execution_sends_without_advancing_the_series(setup) -> None:
    service, scheduler, maker, rid, now, bot = setup
    service.schedule_reminder(rid, now + timedelta(hours=20))
    await service._execute_reminder(rid, is_snooze_execution=True)
    bot.send_message.assert_awaited_once()
    async with maker() as s:
        r = await s.get(Reminder, rid)
        assert r.snoozed_until is None
        assert r.execution_time == now + timedelta(hours=20)
        assert r.last_fired_at is None
    assert scheduler.get_job(str(rid)) is not None


async def test_snooze_after_done_is_skipped(setup) -> None:
    service, _, maker, rid, _, bot = setup
    async with maker() as s:
        await ReminderDAO(s).mark_done(rid)
        await s.commit()
    await service._execute_reminder(rid, is_snooze_execution=True)
    bot.send_message.assert_not_awaited()


async def test_remove_reminder_job_drops_the_snooze_job(setup) -> None:
    service, scheduler, _, rid, now, _ = setup
    service.schedule_snooze(rid, now + timedelta(hours=1))
    service.remove_reminder_job(rid)
    assert scheduler.get_job(f"snz_{rid}") is None
