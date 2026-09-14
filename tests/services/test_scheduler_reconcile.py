from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.services.scheduler import SchedulerService


class _QueryResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)

    def all(self):
        return self._rows


class _FakeSession:
    """Serves the two queries reconcile_jobs_with_db issues, in order:
    (1) the pending-reminders select (consumed via .scalars().all()),
    (2) the User.id/timezone select (consumed via .all())."""

    def __init__(self, reminders, user_timezones=None):
        self._responses = [_QueryResult(reminders), _QueryResult(list((user_timezones or {}).items()))]
        self.committed = False

    async def execute(self, _stmt):
        return self._responses.pop(0)

    async def commit(self):
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _session_pool_factory(reminders, user_timezones=None):
    return lambda: _FakeSession(reminders, user_timezones)


def _make_reminder(**overrides):
    defaults = dict(
        id=1,
        user_id=1,
        status="pending",
        is_fluid_habit=False,
        is_recurring=False,
        rrule_string=None,
        is_nagging=False,
        last_fired_at=None,
        forbidden_strikes=0,
        pending_delete_at=None,
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


async def test_reconcile_schedules_near_term_catchup_for_never_delivered_recurring_cycle() -> None:
    """P1-4: an overdue recurring reminder whose current cycle was NEVER
    delivered (last_fired_at unset/stale) must get a near-term catch-up job
    for that SAME cycle instead of silently jumping straight to the next
    occurrence — otherwise the missed cycle vanishes without a trace and
    never reaches recovery/habit tracking."""
    scheduler = AsyncIOScheduler()
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
    reminder = _make_reminder(id=7, is_recurring=True, rrule_string="FREQ=DAILY", execution_time=past)
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=_session_pool_factory([reminder]))

    await service.reconcile_jobs_with_db()

    job = scheduler.get_job("7")
    assert job is not None
    # Fires soon (catch-up), not literally at the stale past time.
    assert job.trigger.run_date > datetime.now(timezone.utc)
    assert job.trigger.run_date < datetime.now(timezone.utc) + timedelta(minutes=5)
    # execution_time is left pointing at the missed cycle — _execute_reminder
    # computes the true next occurrence once the catch-up actually fires.
    assert reminder.execution_time == past


async def test_reconcile_advances_already_delivered_recurring_reminder_to_future() -> None:
    """If the current cycle WAS already delivered (last_fired_at shows it),
    reconcile is just restoring a dropped follow-up job — that should still
    jump straight to the true next occurrence, not resend a stale cycle."""
    scheduler = AsyncIOScheduler()
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
    reminder = _make_reminder(
        id=7,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        execution_time=past,
        last_fired_at=past + timedelta(minutes=1),
    )
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


async def test_reconcile_staggers_catchup_sends_instead_of_bursting_at_once() -> None:
    """2.8: downtime spanning many users' overdue reminders used to land
    ALL of them on the identical now+1min run_date — a send burst that
    trips Telegram's own flood control on top of whatever reconcile
    already had to catch up on. Consecutive never-delivered catch-ups
    must get distinct, increasing run_dates."""
    scheduler = AsyncIOScheduler()
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    reminders = [_make_reminder(id=i, execution_time=past) for i in range(1, 6)]
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=_session_pool_factory(reminders))

    await service.reconcile_jobs_with_db()

    run_dates = [scheduler.get_job(str(i)).trigger.run_date for i in range(1, 6)]
    assert run_dates == sorted(run_dates)
    assert len(set(run_dates)) == len(run_dates)  # every one distinct, not a pile-up
    # Still all within a reasonably short catch-up window, not spread hours apart.
    assert run_dates[-1] - run_dates[0] < timedelta(minutes=1)


async def test_reconcile_calls_get_jobs_once_not_get_job_per_reminder() -> None:
    """3.4: SQLAlchemyJobStore is SYNCHRONOUS — every get_job()/get_jobs()
    call blocks the event loop on a SQLite query. reconcile_jobs_with_db
    used to call get_job() once per candidate reminder; with hundreds
    pending after downtime, that's hundreds of blocking round trips where
    one get_jobs() suffices."""
    scheduler = AsyncIOScheduler()
    reminders = [_make_reminder(id=i) for i in range(1, 11)]
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=_session_pool_factory(reminders))

    original_get_jobs = scheduler.get_jobs
    get_jobs_calls = []
    get_job_calls = []

    def _tracked_get_jobs(*args, **kwargs):
        get_jobs_calls.append(1)
        return original_get_jobs(*args, **kwargs)

    def _tracked_get_job(*args, **kwargs):
        get_job_calls.append(args)
        return None

    scheduler.get_jobs = _tracked_get_jobs
    scheduler.get_job = _tracked_get_job

    await service.reconcile_jobs_with_db()

    assert len(get_jobs_calls) == 1
    assert get_job_calls == []
    # All 10 reminders still actually got jobs scheduled (using the
    # original, un-patched get_jobs so this check doesn't itself count
    # toward get_jobs_calls).
    assert len(original_get_jobs()) == 10
