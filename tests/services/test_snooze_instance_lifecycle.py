"""Audit 2026-11-18 #02 (T-02, backend): lifecycle of a recurring reminder's
postponed occurrence (snoozed_until + its own snz_<id> job). The separate-job
model itself landed earlier (B-11); this covers what was still open: the
same-minute coincidence rule, restoring a lost snz_ job after a restart, and
clearing the snooze on delete, pause and repeat-rule changes.
"""

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
from bot.handlers.reminders_shared import _apply_repeat_change, _soft_delete_reminder
from bot.services.habit_pause import apply_pause
from bot.services.scheduler import SchedulerService


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


@pytest.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        s.add(User(id=1, username="u", timezone="UTC", language="en"))
        await s.commit()
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=7)),
        delete_message=AsyncMock(),
    )
    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=bot, session_pool=maker)
    yield service, scheduler, maker, bot
    await engine.dispose()
    clear_context()


async def _daily(maker, *, execution_time, snoozed_until=None, last_fired_at=None, habit=False):
    async with maker() as s:
        r = await ReminderDAO(s).create_reminder(
            1, "standup", execution_time, is_recurring=True, rrule_string="FREQ=DAILY", is_habit=habit,
        )
        r.snoozed_until = snoozed_until
        r.last_fired_at = last_fired_at
        await s.commit()
        return r.id


async def _load(maker, rid) -> Reminder:
    async with maker() as s:
        return await s.get(Reminder, rid)


# --- coincidence rule -------------------------------------------------------

async def test_series_run_that_is_also_the_snooze_sends_once_and_drops_snz(env) -> None:
    service, scheduler, maker, bot = env
    now = _now()
    rid = await _daily(maker, execution_time=now, snoozed_until=now + timedelta(seconds=30))
    service.schedule_snooze(rid, now + timedelta(seconds=30))

    await service._execute_reminder(rid)

    bot.send_message.assert_awaited_once()
    assert (await _load(maker, rid)).snoozed_until is None
    assert scheduler.get_job(f"snz_{rid}") is None
    assert scheduler.get_job(str(rid)) is not None  # next series occurrence scheduled


async def test_snooze_run_in_the_same_minute_as_a_pending_series_run_is_merged(env) -> None:
    service, _, maker, bot = env
    now = _now()
    rid = await _daily(maker, execution_time=now + timedelta(seconds=30), snoozed_until=now + timedelta(seconds=30))

    await service._execute_reminder(rid, is_snooze_execution=True)

    bot.send_message.assert_not_awaited()  # the series run will carry it
    assert (await _load(maker, rid)).snoozed_until is None


async def test_snooze_run_right_after_the_series_fired_is_merged(env) -> None:
    service, _, maker, bot = env
    now = _now()
    rid = await _daily(
        maker, execution_time=now + timedelta(hours=24), snoozed_until=now + timedelta(seconds=20), last_fired_at=now,
    )

    await service._execute_reminder(rid, is_snooze_execution=True)

    bot.send_message.assert_not_awaited()
    assert (await _load(maker, rid)).snoozed_until is None


async def test_later_snooze_lives_independently_of_the_series_run(env) -> None:
    service, scheduler, maker, bot = env
    now = _now()
    rid = await _daily(maker, execution_time=now, snoozed_until=now + timedelta(hours=2))
    service.schedule_snooze(rid, now + timedelta(hours=2))

    await service._execute_reminder(rid)

    bot.send_message.assert_awaited_once()
    assert (await _load(maker, rid)).snoozed_until == now + timedelta(hours=2)
    assert scheduler.get_job(f"snz_{rid}") is not None

    await service._execute_reminder(rid, is_snooze_execution=True)  # 2h later: sends its own notification
    assert bot.send_message.await_count == 2
    assert (await _load(maker, rid)).snoozed_until is None


# --- quiet hours ------------------------------------------------------------

async def test_quiet_hours_defer_the_snooze_job_and_leave_the_series_job_alone(env) -> None:
    service, scheduler, maker, bot = env
    now = _now()
    async with maker() as s:
        u = await s.get(User, 1)
        u.quiet_hours_enabled, u.quiet_hours_start, u.quiet_hours_end = True, "00:00", "00:00"  # always quiet
        await s.commit()
    rid = await _daily(maker, execution_time=now + timedelta(hours=20), snoozed_until=now - timedelta(minutes=5))
    service.schedule_reminder(rid, now + timedelta(hours=20))
    series_run = scheduler.get_job(str(rid)).trigger.run_date

    await service._execute_reminder(rid, is_snooze_execution=True)

    bot.send_message.assert_not_awaited()
    assert scheduler.get_job(f"snz_{rid}") is not None  # the deferred snooze
    assert scheduler.get_job(str(rid)).trigger.run_date == series_run


# --- reconcile --------------------------------------------------------------

async def test_reconcile_restores_a_lost_future_snooze_at_its_time(env) -> None:
    service, scheduler, maker, _ = env
    now = _now()
    rid = await _daily(maker, execution_time=now + timedelta(hours=20), snoozed_until=now + timedelta(hours=3))
    service.schedule_reminder(rid, now + timedelta(hours=20))

    await service.reconcile_jobs_with_db()

    job = scheduler.get_job(f"snz_{rid}")
    assert job is not None
    assert abs(job.trigger.run_date.replace(tzinfo=None) - (now + timedelta(hours=3))) < timedelta(seconds=2)


async def test_reconcile_sends_a_snooze_that_fell_due_during_downtime_once(env) -> None:
    service, scheduler, maker, bot = env
    now = _now()
    rid = await _daily(maker, execution_time=now + timedelta(hours=20), snoozed_until=now - timedelta(hours=1))
    service.schedule_reminder(rid, now + timedelta(hours=20))

    await service.reconcile_jobs_with_db()
    job = scheduler.get_job(f"snz_{rid}")
    assert job is not None
    assert timedelta(0) < job.trigger.run_date.replace(tzinfo=None) - now <= timedelta(minutes=2)

    await service._execute_reminder(rid, is_snooze_execution=True)
    bot.send_message.assert_awaited_once()
    assert (await _load(maker, rid)).snoozed_until is None

    await service.reconcile_jobs_with_db()  # nothing left to restore
    assert scheduler.get_job(f"snz_{rid}") is None or (await _load(maker, rid)).snoozed_until is None


async def test_reconcile_keeps_an_existing_snooze_job(env) -> None:
    service, scheduler, maker, _ = env
    now = _now()
    rid = await _daily(maker, execution_time=now + timedelta(hours=20), snoozed_until=now + timedelta(hours=3))
    service.schedule_reminder(rid, now + timedelta(hours=20))
    service.schedule_snooze(rid, now + timedelta(hours=3))
    before = scheduler.get_job(f"snz_{rid}").trigger.run_date

    await service.reconcile_jobs_with_db()

    assert scheduler.get_job(f"snz_{rid}").trigger.run_date == before


# --- clearing ---------------------------------------------------------------

async def test_soft_delete_clears_the_snooze(env) -> None:
    service, scheduler, maker, _ = env
    now = _now()
    rid = await _daily(maker, execution_time=now + timedelta(hours=20), snoozed_until=now + timedelta(hours=3))
    service.schedule_reminder(rid, now + timedelta(hours=20))
    service.schedule_snooze(rid, now + timedelta(hours=3))
    async with maker() as s:
        r = await s.get(Reminder, rid)
        assert await _soft_delete_reminder(r, ReminderDAO(s), service)

    assert (await _load(maker, rid)).snoozed_until is None
    assert scheduler.get_job(f"snz_{rid}") is None


@pytest.mark.parametrize("new_rule", [(True, "FREQ=WEEKLY"), (False, None)])
async def test_repeat_rule_change_or_off_clears_the_snooze(env, new_rule) -> None:
    service, scheduler, maker, _ = env
    now = _now()
    rid = await _daily(maker, execution_time=now + timedelta(hours=20), snoozed_until=now + timedelta(hours=3))
    service.schedule_reminder(rid, now + timedelta(hours=20))
    service.schedule_snooze(rid, now + timedelta(hours=3))
    async with maker() as s:
        r = await s.get(Reminder, rid)
        user = await s.get(User, 1)
        assert await _apply_repeat_change(r, user, service, ReminderDAO(s), *new_rule)

    assert (await _load(maker, rid)).snoozed_until is None
    assert scheduler.get_job(f"snz_{rid}") is None


async def test_pause_clears_the_snooze(env) -> None:
    service, scheduler, maker, _ = env
    now = _now()
    rid = await _daily(maker, execution_time=now + timedelta(hours=20), snoozed_until=now + timedelta(hours=3), habit=True)
    service.schedule_reminder(rid, now + timedelta(hours=20))
    service.schedule_snooze(rid, now + timedelta(hours=3))
    async with maker() as s:
        r = await s.get(Reminder, rid)
        until = (datetime.now(timezone.utc) + timedelta(days=3)).date()
        assert await apply_pause(r, "UTC", service, s, until)

    assert (await _load(maker, rid)).snoozed_until is None
    assert scheduler.get_job(f"snz_{rid}") is None


async def test_one_off_task_snooze_is_unchanged(env) -> None:
    """A non-recurring reminder never gets a snz_ job: the plain job moves."""
    service, scheduler, maker, _ = env
    now = _now()
    async with maker() as s:
        r = await ReminderDAO(s).create_reminder(1, "once", now + timedelta(hours=1))
        await s.commit()
        rid = r.id
    service.schedule_reminder(rid, now + timedelta(hours=2))
    await service.reconcile_jobs_with_db()
    assert scheduler.get_job(f"snz_{rid}") is None
    assert scheduler.get_job(str(rid)) is not None
