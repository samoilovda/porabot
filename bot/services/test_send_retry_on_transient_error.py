from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.services.scheduler import SchedulerService


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeSession:
    def __init__(self, reminder, user):
        self._responses = [_FakeResult(reminder), _FakeResult(user)]
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, _stmt):
        return self._responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _make_reminder(**overrides):
    defaults = dict(
        id=42,
        user_id=7,
        status="pending",
        reminder_text="Take a walk",
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
        is_recurring=False,
        rrule_string=None,
        is_habit=False,
        is_fluid_habit=False,
        habit_active_due_at=None,
        habit_last_completed_due_at=None,
        habit_streak_current=0,
        habit_streak_best=0,
        is_nagging=False,
        nagging_max_repeats=3,
        nagging_sent_count=0,
        last_nag_chat_id=None,
        last_nag_message_id=None,
        forbidden_strikes=0,
        last_fired_at=None,
        send_retry_count=0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_user():
    return SimpleNamespace(id=7, language="en", timezone="UTC", quiet_hours_enabled=False)


def _network_error_bot():
    async def _raise(*args, **kwargs):
        raise TimeoutError("network hiccup")

    return SimpleNamespace(send_message=_raise, delete_message=AsyncMock())


async def test_one_off_network_error_does_not_mark_delivered_and_schedules_retry() -> None:
    reminder = _make_reminder(is_recurring=False)
    session = _FakeSession(reminder, _make_user())
    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=_network_error_bot(), session_pool=lambda: session)

    await service._execute_reminder(reminder.id, is_nagging_execution=False)

    assert reminder.last_fired_at is None
    assert reminder.send_retry_count == 1
    retry_job = scheduler.get_job("42")
    assert retry_job is not None
    # First backoff step is 1 minute — the retry must land in the near future.
    assert retry_job.trigger.run_date > datetime.now(timezone.utc)
    assert retry_job.trigger.run_date <= datetime.now(timezone.utc) + timedelta(minutes=2)
    session.commit.assert_awaited()


async def test_recurring_network_error_does_not_advance_execution_time() -> None:
    original_execution_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    reminder = _make_reminder(
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        execution_time=original_execution_time,
    )
    session = _FakeSession(reminder, _make_user())
    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=_network_error_bot(), session_pool=lambda: session)

    await service._execute_reminder(reminder.id, is_nagging_execution=False)

    assert reminder.execution_time == original_execution_time


async def test_nagging_network_error_does_not_consume_nag_attempt() -> None:
    reminder = _make_reminder(is_nagging=True, nagging_sent_count=1)
    session = _FakeSession(reminder, _make_user())
    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=_network_error_bot(), session_pool=lambda: session)

    await service._execute_reminder(reminder.id, is_nagging_execution=True)

    assert reminder.nagging_sent_count == 1
    # The retry must reuse the nag job slot, not the main reminder slot.
    assert scheduler.get_job("nag_42") is not None
    assert scheduler.get_job("42") is None


async def test_successful_retry_advances_state_exactly_once() -> None:
    reminder = _make_reminder(is_recurring=False, send_retry_count=2)
    session = _FakeSession(reminder, _make_user())
    scheduler = AsyncIOScheduler()
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=7), message_id=1)),
        delete_message=AsyncMock(),
    )
    service = SchedulerService(scheduler, bot=bot, session_pool=lambda: session)

    await service._execute_reminder(reminder.id, is_nagging_execution=False)

    assert reminder.last_fired_at is not None
    assert reminder.send_retry_count == 0
    assert bot.send_message.await_count == 1


async def test_retry_backoff_is_exhausted_after_max_attempts() -> None:
    from bot.services.scheduler import SEND_RETRY_BACKOFF_MINUTES

    reminder = _make_reminder(send_retry_count=len(SEND_RETRY_BACKOFF_MINUTES))
    session = _FakeSession(reminder, _make_user())
    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=_network_error_bot(), session_pool=lambda: session)

    await service._execute_reminder(reminder.id, is_nagging_execution=False)

    assert reminder.send_retry_count == len(SEND_RETRY_BACKOFF_MINUTES) + 1
    # Backoff exhausted — no further retry job scheduled. reconcile_jobs_with_db
    # (last_fired_at still unset) is the safety net on next restart instead.
    assert scheduler.get_job("42") is None
