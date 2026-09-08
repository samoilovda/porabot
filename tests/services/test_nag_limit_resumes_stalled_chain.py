from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.services.scheduler import SchedulerService


def _make_service() -> SchedulerService:
    return SchedulerService(
        scheduler=AsyncIOScheduler(),
        bot=SimpleNamespace(),
        session_pool=None,
    )


def _make_reminder(**overrides) -> SimpleNamespace:
    defaults = dict(
        id=1,
        is_nagging=True,
        status="pending",
        nagging_max_repeats=3,
        nagging_sent_count=3,
        forbidden_strikes=0,
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_raising_limit_past_exhausted_count_reschedules_nag_job() -> None:
    service = _make_service()
    reminder = _make_reminder(nagging_max_repeats=5, nagging_sent_count=3)

    resumed = service.resume_nagging_if_stalled(reminder)

    assert resumed is True
    job = service.scheduler.get_job(f"nag_{reminder.id}")
    assert job is not None


def test_does_not_resume_when_still_at_or_above_limit() -> None:
    service = _make_service()
    reminder = _make_reminder(nagging_max_repeats=3, nagging_sent_count=3)

    resumed = service.resume_nagging_if_stalled(reminder)

    assert resumed is False
    assert service.scheduler.get_job(f"nag_{reminder.id}") is None


def test_does_not_resume_when_not_overdue() -> None:
    service = _make_service()
    reminder = _make_reminder(
        nagging_max_repeats=5,
        nagging_sent_count=3,
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )

    resumed = service.resume_nagging_if_stalled(reminder)

    assert resumed is False
    assert service.scheduler.get_job(f"nag_{reminder.id}") is None


def test_does_not_double_schedule_when_nag_job_already_pending() -> None:
    service = _make_service()
    reminder = _make_reminder(nagging_max_repeats=5, nagging_sent_count=3)
    existing_run_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    service.scheduler.add_job(
        lambda: None,
        "date",
        run_date=existing_run_at,
        id=f"nag_{reminder.id}",
    )

    resumed = service.resume_nagging_if_stalled(reminder)

    assert resumed is False
    job = service.scheduler.get_job(f"nag_{reminder.id}")
    assert job.trigger.run_date == existing_run_at


def test_does_not_resume_completed_reminder() -> None:
    service = _make_service()
    reminder = _make_reminder(status="completed", nagging_max_repeats=5, nagging_sent_count=3)

    resumed = service.resume_nagging_if_stalled(reminder)

    assert resumed is False


def test_does_not_resume_reminder_that_gave_up_after_forbidden_strikes() -> None:
    service = _make_service()
    reminder = _make_reminder(nagging_max_repeats=5, nagging_sent_count=3, forbidden_strikes=3)

    resumed = service.resume_nagging_if_stalled(reminder)

    assert resumed is False
    assert service.scheduler.get_job(f"nag_{reminder.id}") is None
