from datetime import datetime, timedelta
from types import SimpleNamespace

import pytz
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
    def __init__(self, reminders, user_timezones):
        self._responses = [_QueryResult(reminders), _QueryResult(list(user_timezones.items()))]
        self.committed = False

    async def execute(self, _stmt):
        return self._responses.pop(0)

    async def commit(self):
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_reconcile_advances_recurring_reminder_using_user_local_timezone_across_dst() -> None:
    """A daily habit reminder that's overdue when reconcile runs (bot was
    down across a DST transition) must land on the next occurrence computed
    in the user's LOCAL time, not raw UTC — otherwise the user's daily
    reminder silently shifts by an hour purely because the bot restarted at
    the wrong moment.
    """
    # Europe/Berlin switches to summer time (UTC+1 -> UTC+2) on 2026-03-29.
    # dtstart: 2026-03-28 09:00 local (UTC+1) == 2026-03-28 08:00 UTC.
    dtstart_utc_naive = datetime(2026, 3, 28, 8, 0)

    reminder = SimpleNamespace(
        id=55,
        user_id=7,
        status="pending",
        is_fluid_habit=False,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_nagging=False,
        forbidden_strikes=0,
        pending_delete_at=None,
        # Already delivered — reconcile is just restoring a dropped
        # follow-up job here, so it should jump straight to the true next
        # occurrence (which is what this test is actually verifying, DST
        # correctness of that computation) rather than scheduling a P1-4
        # catch-up for a cycle that was never delivered.
        last_fired_at=dtstart_utc_naive + timedelta(minutes=1),
        execution_time=dtstart_utc_naive,
    )

    scheduler = AsyncIOScheduler()
    session = _FakeSession([reminder], {7: "Europe/Berlin"})
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=lambda: session)

    # Freeze "now" at 2026-03-29 05:00 UTC == 07:00 Berlin local (CEST,
    # post-transition) — after the missed 2026-03-28 09:00-local occurrence,
    # but still before 2026-03-29's own 09:00-local slot.
    import bot.services.scheduler as scheduler_module

    real_datetime = scheduler_module.datetime

    class _FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            frozen = real_datetime(2026, 3, 29, 5, 0, tzinfo=pytz.UTC)
            return frozen.astimezone(tz) if tz else frozen

    scheduler_module.datetime = _FrozenDatetime
    try:
        await service.reconcile_jobs_with_db()
    finally:
        scheduler_module.datetime = real_datetime

    # Next occurrence should be 2026-03-29 09:00 Berlin local == 07:00 UTC
    # (CEST, UTC+2) — NOT 2026-03-29 08:00 UTC, which is what a naive
    # UTC-anchored rrule.after() would have produced (an hour of drift).
    assert reminder.execution_time == datetime(2026, 3, 29, 7, 0)
    job = scheduler.get_job("55")
    assert job is not None
