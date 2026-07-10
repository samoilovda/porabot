from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.services.scheduler import SchedulerService


class _FakeSession:
    def __init__(self, reminders):
        self._reminders = reminders
        self.committed = False

    async def execute(self, _stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self._reminders))

    async def commit(self):
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _session_pool_factory(reminders):
    return lambda: _FakeSession(reminders)


def _make_reminder(**overrides):
    defaults = dict(
        id=1,
        status="pending",
        is_fluid_habit=False,
        is_recurring=False,
        rrule_string=None,
        is_nagging=False,
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def test_reconcile_recreates_missing_job_for_pending_reminder() -> None:
    scheduler = AsyncIOScheduler()
    reminder = _make_reminder(id=42)
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=_session_pool_factory([reminder]))

    await service.reconcile_jobs_with_db()

    assert scheduler.get_job("42") is not None


async def test_reconcile_advances_overdue_recurring_reminder_to_future() -> None:
    scheduler = AsyncIOScheduler()
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
    reminder = _make_reminder(id=7, is_recurring=True, rrule_string="FREQ=DAILY", execution_time=past)
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=_session_pool_factory([reminder]))

    await service.reconcile_jobs_with_db()

    job = scheduler.get_job("7")
    assert job is not None
    assert reminder.execution_time > past


async def test_reconcile_skips_reminder_that_already_has_a_job() -> None:
    scheduler = AsyncIOScheduler()
    future = datetime.now(timezone.utc)
    reminder = _make_reminder(id=99)
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=_session_pool_factory([reminder]))
    service.schedule_reminder(99, future + timedelta(hours=1))
    scheduler.add_job = Mock(wraps=scheduler.add_job)

    await service.reconcile_jobs_with_db()

    scheduler.add_job.assert_not_called()
