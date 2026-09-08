"""Regression tests for 2.5: SchedulerService.remove_orphan_scheduler_jobs.

reconcile_jobs_with_db only ever ADDS jobs missing from the jobstore —
nothing symmetrically removed a leftover job if its reminder became
deleted, completed, soft-deleted, or gave up after repeated
TelegramForbiddenError through some path that skipped remove_reminder_job/
remove_nagging_job. This is the periodic safety net for that.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.services.scheduler import FORBIDDEN_STRIKES_LIMIT, SchedulerService


class _QueryResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, reminder_rows):
        # (id, status, pending_delete_at, forbidden_strikes) tuples, as
        # remove_orphan_scheduler_jobs' own select() shapes them.
        self._rows = reminder_rows

    async def execute(self, _stmt):
        return _QueryResult(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _noop():
    pass


async def test_removes_jobs_for_deleted_completed_soft_deleted_and_given_up_reminders() -> None:
    scheduler = AsyncIOScheduler()
    future = datetime.now(timezone.utc) + timedelta(hours=1)

    # 1: still active — must survive.
    scheduler.add_job(_noop, "date", run_date=future, id="1")
    # 2: no longer in the DB at all (hard-deleted) — must be removed.
    scheduler.add_job(_noop, "date", run_date=future, id="2")
    # 3: completed — must be removed.
    scheduler.add_job(_noop, "date", run_date=future, id="3")
    # 4: soft-deleted (pending_delete_at set) — must be removed.
    scheduler.add_job(_noop, "date", run_date=future, id="4")
    # 5: gave up after repeated TelegramForbiddenError — must be removed.
    scheduler.add_job(_noop, "date", run_date=future, id="5")
    # nag_1: nagging job for the still-active reminder 1 — must survive.
    scheduler.add_job(_noop, "date", run_date=future, id="nag_1")
    # nag_3: nagging job for the completed reminder 3 — must be removed.
    scheduler.add_job(_noop, "date", run_date=future, id="nag_3")
    # A non-reminder system job (fixed string id) — must never be touched.
    scheduler.add_job(_noop, "date", run_date=future, id="daily_briefs_minutely")

    reminder_rows = [
        (1, "pending", None, 0),
        (3, "completed", None, 0),
        (4, "pending", datetime.now(timezone.utc).replace(tzinfo=None), 0),
        (5, "pending", None, FORBIDDEN_STRIKES_LIMIT),
        # 2 is absent entirely — hard-deleted.
    ]
    session_pool = lambda: _FakeSession(reminder_rows)  # noqa: E731
    service = SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=session_pool)

    removed_count = await service.remove_orphan_scheduler_jobs()

    assert removed_count == 5  # 2, 3, 4, 5, nag_3
    assert scheduler.get_job("1") is not None
    assert scheduler.get_job("nag_1") is not None
    assert scheduler.get_job("daily_briefs_minutely") is not None
    for orphan_id in ("2", "3", "4", "5", "nag_3"):
        assert scheduler.get_job(orphan_id) is None


async def test_noop_when_no_reminder_jobs_exist() -> None:
    scheduler = AsyncIOScheduler()
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    scheduler.add_job(_noop, "date", run_date=future, id="write_heartbeat")

    calls = []

    class _AssertNotCalledSession:
        async def execute(self, _stmt):
            calls.append(1)
            return _QueryResult([])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    service = SchedulerService(
        scheduler, bot=SimpleNamespace(), session_pool=lambda: _AssertNotCalledSession()
    )

    removed_count = await service.remove_orphan_scheduler_jobs()

    assert removed_count == 0
    assert calls == []  # no DB round trip when there's nothing to check
    assert scheduler.get_job("write_heartbeat") is not None
