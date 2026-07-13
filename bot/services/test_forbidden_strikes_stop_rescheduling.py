from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.services.scheduler import FORBIDDEN_STRIKES_LIMIT, SchedulerService


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
        is_nagging=True,
        nagging_max_repeats=3,
        nagging_sent_count=0,
        last_nag_chat_id=None,
        last_nag_message_id=None,
        forbidden_strikes=0,
        last_fired_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _forbidden_bot():
    async def _raise(*args, **kwargs):
        raise TelegramForbiddenError(method=SimpleNamespace(), message="Forbidden: bot was blocked by the user")

    return SimpleNamespace(send_message=_raise, delete_message=AsyncMock())


async def test_forbidden_strikes_increment_but_still_reschedules_below_limit() -> None:
    reminder = _make_reminder(forbidden_strikes=0)
    user = SimpleNamespace(id=7, language="en", timezone="UTC", quiet_hours_enabled=False)
    session = _FakeSession(reminder, user)
    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=_forbidden_bot(), session_pool=lambda: session)

    await service._execute_reminder(reminder.id, is_nagging_execution=True)

    assert reminder.forbidden_strikes == 1
    # Still below the limit — nagging follow-up must still be scheduled.
    assert scheduler.get_job("nag_42") is not None
    session.commit.assert_awaited()


async def test_reaching_forbidden_strikes_limit_stops_rescheduling_and_clears_jobs() -> None:
    reminder = _make_reminder(forbidden_strikes=FORBIDDEN_STRIKES_LIMIT - 1)
    user = SimpleNamespace(id=7, language="en", timezone="UTC", quiet_hours_enabled=False)
    session = _FakeSession(reminder, user)
    scheduler = AsyncIOScheduler()
    service = SchedulerService(scheduler, bot=_forbidden_bot(), session_pool=lambda: session)

    # Pre-existing nagging job that must be cleared once we give up.
    scheduler.add_job(lambda: None, "date", run_date=datetime.now(timezone.utc) + timedelta(minutes=5), id="nag_42")

    await service._execute_reminder(reminder.id, is_nagging_execution=True)

    assert reminder.forbidden_strikes == FORBIDDEN_STRIKES_LIMIT
    assert scheduler.get_job("nag_42") is None
    assert scheduler.get_job("42") is None
    session.commit.assert_awaited()


async def test_successful_send_resets_forbidden_strikes() -> None:
    reminder = _make_reminder(forbidden_strikes=2, is_nagging=False)
    user = SimpleNamespace(id=7, language="en", timezone="UTC", quiet_hours_enabled=False)
    session = _FakeSession(reminder, user)
    scheduler = AsyncIOScheduler()
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=7), message_id=1)),
        delete_message=AsyncMock(),
    )
    service = SchedulerService(scheduler, bot=bot, session_pool=lambda: session)

    await service._execute_reminder(reminder.id, is_nagging_execution=False)

    assert reminder.forbidden_strikes == 0


async def test_main_execution_stamps_last_fired_at_but_nagging_execution_does_not() -> None:
    reminder = _make_reminder(is_nagging=False, last_fired_at=None)
    user = SimpleNamespace(id=7, language="en", timezone="UTC", quiet_hours_enabled=False)
    session = _FakeSession(reminder, user)
    scheduler = AsyncIOScheduler()
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=7), message_id=1)),
        delete_message=AsyncMock(),
    )
    service = SchedulerService(scheduler, bot=bot, session_pool=lambda: session)

    before = datetime.now(timezone.utc).replace(tzinfo=None)
    await service._execute_reminder(reminder.id, is_nagging_execution=False)
    after = datetime.now(timezone.utc).replace(tzinfo=None)

    assert reminder.last_fired_at is not None
    assert before <= reminder.last_fired_at <= after


async def test_nagging_execution_does_not_touch_last_fired_at() -> None:
    reminder = _make_reminder(is_nagging=True, last_fired_at=None)
    user = SimpleNamespace(id=7, language="en", timezone="UTC", quiet_hours_enabled=False)
    session = _FakeSession(reminder, user)
    scheduler = AsyncIOScheduler()
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=7), message_id=1)),
        delete_message=AsyncMock(),
    )
    service = SchedulerService(scheduler, bot=bot, session_pool=lambda: session)

    await service._execute_reminder(reminder.id, is_nagging_execution=True)

    assert reminder.last_fired_at is None
