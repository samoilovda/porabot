"""1.2: a crash inside _execute_reminder (SQLite "database is locked"
colliding with one of the per-minute cron jobs, a dropped DB connection, a
transient bug) must not silently lose a one-shot "date" job forever.

Before this fix, execute_reminder_job let the exception propagate straight
up to APScheduler — for a one-shot trigger that means the reminder simply
never fires again until the next restart's reconcile_jobs_with_db happens
to notice it (hours or days later for a daily/weekly reminder).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.services.scheduler import (
    SEND_RETRY_BACKOFF_MINUTES,
    SchedulerService,
    execute_reminder_job,
)


def _make_service() -> SchedulerService:
    scheduler = AsyncIOScheduler()
    return SchedulerService(scheduler, bot=SimpleNamespace(), session_pool=SimpleNamespace())


async def test_execute_reminder_job_schedules_retry_on_unexpected_crash() -> None:
    service = _make_service()
    service._execute_reminder = AsyncMock(side_effect=RuntimeError("database is locked"))

    await execute_reminder_job(42, is_nagging_execution=False)

    job = service.scheduler.get_job("42")
    assert job is not None
    delay = job.trigger.run_date - datetime.now(timezone.utc)
    expected = timedelta(minutes=SEND_RETRY_BACKOFF_MINUTES[0])
    assert abs(delay - expected) < timedelta(seconds=10)
    assert service._crash_retry_counts["42"] == 1


async def test_execute_reminder_job_uses_nag_job_id_for_nagging_execution() -> None:
    service = _make_service()
    service._execute_reminder = AsyncMock(side_effect=RuntimeError("boom"))

    await execute_reminder_job(7, is_nagging_execution=True)

    assert service.scheduler.get_job("nag_7") is not None
    assert service.scheduler.get_job("7") is None


async def test_successful_execution_resets_crash_counter() -> None:
    service = _make_service()
    service._crash_retry_counts["42"] = 3
    service._execute_reminder = AsyncMock(return_value=None)

    await execute_reminder_job(42, is_nagging_execution=False)

    assert "42" not in service._crash_retry_counts


async def test_schedule_execution_retry_follows_backoff_then_exhausts() -> None:
    service = _make_service()

    for expected_minutes in SEND_RETRY_BACKOFF_MINUTES:
        service.schedule_execution_retry(99, False)
        job = service.scheduler.get_job("99")
        assert job is not None
        delay = job.trigger.run_date - datetime.now(timezone.utc)
        assert abs(delay - timedelta(minutes=expected_minutes)) < timedelta(seconds=10)
        # Simulate APScheduler auto-removing the one-shot job once it
        # "fires" (and, in this simulation, crashes again) before the next
        # attempt is scheduled.
        service.scheduler.remove_job("99")

    # One more failure beyond the backoff schedule's length — must give up
    # rather than reschedule indefinitely.
    service.schedule_execution_retry(99, False)
    assert service.scheduler.get_job("99") is None
    assert "99" not in service._crash_retry_counts
