"""docs/audits/2026-09-22-audit.md#a-09 — APScheduler's AsyncIOExecutor.
shutdown() cancels every pending future regardless of its own wait=
argument (see its own source: "There is no way to honor wait=True without
converting this method into a coroutine method"). A reminder job cancelled
mid-send-then-commit (the Telegram send already went out, the commit that
records last_fired_at hasn't happened yet) gets redelivered after restart
— reconcile_jobs_with_db sees an "undelivered" cycle that was, in fact,
already sent.

execute_reminder_job now tracks itself in a module-level set while
running; wait_for_in_flight_jobs lets bot/__main__.py's shutdown sequence
wait for it to finish ON ITS OWN, before scheduler.shutdown() ever runs
and would otherwise cancel it.
"""

import asyncio

import bot.context as context_module
import bot.services.scheduler as scheduler_module
from bot.services.scheduler import execute_reminder_job, wait_for_in_flight_jobs


async def test_wait_for_in_flight_jobs_waits_for_a_slow_job_to_finish(monkeypatch) -> None:
    commit_happened = False

    async def _slow_execute_reminder(reminder_id, is_nagging_execution=False):
        nonlocal commit_happened
        await asyncio.sleep(0.05)  # stands in for "send already went out, about to commit"
        commit_happened = True

    fake_scheduler = type("FakeScheduler", (), {})()
    fake_scheduler._execute_reminder = _slow_execute_reminder
    fake_scheduler._crash_retry_counts = {}
    fake_ctx = type("FakeCtx", (), {})()
    fake_ctx.scheduler = fake_scheduler

    monkeypatch.setattr(context_module, "_context", fake_ctx)

    job_task = asyncio.ensure_future(execute_reminder_job(1))
    await asyncio.sleep(0)  # let it start and register itself
    assert len(scheduler_module._in_flight_jobs) == 1

    await wait_for_in_flight_jobs(timeout=5.0)

    assert commit_happened is True
    assert len(scheduler_module._in_flight_jobs) == 0
    assert job_task.done()
    job_task.result()  # no exception


async def test_wait_for_in_flight_jobs_is_a_no_op_when_nothing_is_running() -> None:
    scheduler_module._in_flight_jobs.clear()
    await wait_for_in_flight_jobs(timeout=1.0)  # must return immediately, not hang


async def test_wait_for_in_flight_jobs_times_out_without_raising(monkeypatch) -> None:
    """A job that's still running past the grace period must not crash
    shutdown — it's logged and shutdown proceeds anyway (the same
    "leave for reconcile_jobs_with_db" fallback every other exhausted-
    retry path in this codebase already relies on)."""

    async def _never_finishes(reminder_id, is_nagging_execution=False):
        await asyncio.Event().wait()

    fake_scheduler = type("FakeScheduler", (), {})()
    fake_scheduler._execute_reminder = _never_finishes
    fake_scheduler._crash_retry_counts = {}
    fake_ctx = type("FakeCtx", (), {})()
    fake_ctx.scheduler = fake_scheduler

    monkeypatch.setattr(context_module, "_context", fake_ctx)

    job_task = asyncio.ensure_future(execute_reminder_job(1))
    await asyncio.sleep(0)

    await wait_for_in_flight_jobs(timeout=0.05)  # must return, not hang forever

    job_task.cancel()
    try:
        await job_task
    except asyncio.CancelledError:
        pass
    scheduler_module._in_flight_jobs.clear()


async def test_execute_reminder_job_deregisters_itself_even_on_crash(monkeypatch) -> None:
    """A crashing job (schedule_execution_retry path) must not leave a
    stale entry in _in_flight_jobs forever."""

    async def _crashing_execute_reminder(reminder_id, is_nagging_execution=False):
        raise RuntimeError("boom")

    fake_scheduler = type("FakeScheduler", (), {})()
    fake_scheduler._execute_reminder = _crashing_execute_reminder
    fake_scheduler._crash_retry_counts = {}
    fake_scheduler.schedule_execution_retry = lambda *a, **k: None
    fake_ctx = type("FakeCtx", (), {})()
    fake_ctx.scheduler = fake_scheduler

    monkeypatch.setattr(context_module, "_context", fake_ctx)

    await execute_reminder_job(1)

    assert len(scheduler_module._in_flight_jobs) == 0
