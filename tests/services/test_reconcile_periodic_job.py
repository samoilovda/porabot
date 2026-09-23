"""docs/audits/2026-09-22-audit.md#a-11 — reconcile_jobs_with_db used to
run only once, at startup. A pending reminder can end up with no
scheduler job at all outside of "the process just started" too — a
delivery/execution retry chain exhausting its own backoff explicitly
defers to reconcile_jobs_with_db (see SEND_RETRY_BACKOFF_MINUTES's
docstring) rather than retrying forever — so a long-lived process between
deploys used to never notice, leaving such a reminder silently dead until
its next restart. reconcile_jobs_with_db_job is the module-level wrapper
now also registered hourly in bot/__main__.py, same shape as the existing
remove_orphan_scheduler_jobs_job.
"""

from unittest.mock import AsyncMock

import bot.context as context_module
from bot.services.scheduler import reconcile_jobs_with_db_job


async def test_reconcile_job_wrapper_calls_through_the_running_scheduler_service() -> None:
    fake_scheduler_service = type("FakeSchedulerService", (), {})()
    fake_scheduler_service.reconcile_jobs_with_db = AsyncMock()
    fake_ctx = type("FakeCtx", (), {})()
    fake_ctx.scheduler = fake_scheduler_service

    import unittest.mock as mock

    with mock.patch.object(context_module, "_context", fake_ctx):
        await reconcile_jobs_with_db_job()

    fake_scheduler_service.reconcile_jobs_with_db.assert_awaited_once()


async def test_reconcile_job_wrapper_is_a_no_op_without_a_running_context() -> None:
    """Must not raise — mirrors every other periodic job wrapper's
    "AppContext not set" guard (e.g. process_daily_briefs, sweep_habit_cycles)."""
    import unittest.mock as mock

    with mock.patch.object(context_module, "_context", None):
        await reconcile_jobs_with_db_job()  # no exception
