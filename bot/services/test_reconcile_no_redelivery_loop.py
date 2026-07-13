from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.services.scheduler import FORBIDDEN_STRIKES_LIMIT, SchedulerService


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
        forbidden_strikes=0,
        last_fired_at=None,
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def test_reconcile_does_not_resend_a_one_off_that_already_fired() -> None:
    """A fired-but-not-yet-Done one-off must not be redelivered on every restart."""
    scheduler = AsyncIOScheduler()
    execution_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    reminder = _make_reminder(
        id=1,
        execution_time=execution_time,
        last_fired_at=execution_time + timedelta(seconds=1),
    )
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=_session_pool_factory([reminder]))

    await service.reconcile_jobs_with_db()

    assert scheduler.get_job("1") is None


async def test_reconcile_still_delivers_a_one_off_that_never_fired() -> None:
    scheduler = AsyncIOScheduler()
    execution_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    reminder = _make_reminder(id=2, execution_time=execution_time, last_fired_at=None)
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=_session_pool_factory([reminder]))

    await service.reconcile_jobs_with_db()

    assert scheduler.get_job("2") is not None


async def test_reconcile_delivers_when_last_fired_predates_current_cycle() -> None:
    """last_fired_at from a PREVIOUS cycle (e.g. before a manual edit moved
    execution_time later, then earlier again) must not suppress delivery."""
    scheduler = AsyncIOScheduler()
    execution_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    reminder = _make_reminder(
        id=3,
        execution_time=execution_time,
        last_fired_at=execution_time - timedelta(days=1),
    )
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=_session_pool_factory([reminder]))

    await service.reconcile_jobs_with_db()

    assert scheduler.get_job("3") is not None


class _FilteringFakeSession:
    """Mimics the real query's WHERE forbidden_strikes < FORBIDDEN_STRIKES_LIMIT
    filter in Python, so this test exercises SchedulerService's actual
    behavior rather than just the SQL text."""

    def __init__(self, reminders):
        self._reminders = reminders
        self.committed = False

    async def execute(self, _stmt):
        matching = [r for r in self._reminders if r.forbidden_strikes < FORBIDDEN_STRIKES_LIMIT]
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: matching))

    async def commit(self):
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_reconcile_skips_reminder_that_gave_up_after_forbidden_strikes() -> None:
    scheduler = AsyncIOScheduler()
    execution_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    reminder = _make_reminder(
        id=4,
        execution_time=execution_time,
        last_fired_at=None,
        forbidden_strikes=FORBIDDEN_STRIKES_LIMIT,
    )
    session = _FilteringFakeSession([reminder])
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=lambda: session)

    await service.reconcile_jobs_with_db()

    assert scheduler.get_job("4") is None
