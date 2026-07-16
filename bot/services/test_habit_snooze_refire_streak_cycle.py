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


async def test_snooze_refire_does_not_shift_habit_active_due_to_tomorrow() -> None:
    """Simulates: habit fires at 09:00 today (active_due=today 09:00,
    execution_time advanced to tomorrow 09:00 by the recurring-reschedule
    step), user snoozes +2h, and the snoozed job fires again at 11:00 today.
    The re-fire must NOT reassign active_due to tomorrow's cycle.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    today_due = now.replace(hour=9, minute=0, second=0)
    if today_due > now:
        today_due -= timedelta(days=1)
    tomorrow_due = today_due + timedelta(days=1)

    reminder = SimpleNamespace(
        id=1,
        user_id=7,
        status="pending",
        reminder_text="Stretch",
        execution_time=tomorrow_due,  # already advanced by the on-time fire
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_habit=True,
        is_fluid_habit=False,
        habit_active_due_at=today_due,  # set by the original on-time fire
        habit_last_completed_due_at=None,
        habit_streak_current=1,
        habit_streak_best=1,
        is_nagging=False,
        nagging_max_repeats=3,
        nagging_sent_count=0,
        last_nag_chat_id=None,
        last_nag_message_id=None,
        forbidden_strikes=0,
        last_fired_at=today_due,
    )
    user = SimpleNamespace(id=7, language="en", timezone="UTC", quiet_hours_enabled=False)
    session = _FakeSession(reminder, user)
    scheduler = AsyncIOScheduler()
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=7), message_id=1)),
        delete_message=AsyncMock(),
    )
    service = SchedulerService(scheduler, bot=bot, session_pool=lambda: session)

    # Snoozed re-fire of the SAME cycle, main (non-nagging) job.
    await service._execute_reminder(reminder.id, is_nagging_execution=False)

    assert reminder.habit_active_due_at == today_due


async def test_on_time_fire_still_adopts_execution_time_as_active_due() -> None:
    """Sanity check that the fix doesn't break the normal on-time-fire path."""
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)

    reminder = SimpleNamespace(
        id=2,
        user_id=7,
        status="pending",
        reminder_text="Meditate",
        execution_time=now,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        is_habit=True,
        is_fluid_habit=False,
        habit_active_due_at=now - timedelta(days=1),
        habit_last_completed_due_at=now - timedelta(days=1),
        habit_streak_current=3,
        habit_streak_best=3,
        is_nagging=False,
        nagging_max_repeats=3,
        nagging_sent_count=0,
        last_nag_chat_id=None,
        last_nag_message_id=None,
        forbidden_strikes=0,
        last_fired_at=None,
    )
    user = SimpleNamespace(id=7, language="en", timezone="UTC", quiet_hours_enabled=False)
    session = _FakeSession(reminder, user)
    scheduler = AsyncIOScheduler()
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=7), message_id=1)),
        delete_message=AsyncMock(),
    )
    service = SchedulerService(scheduler, bot=bot, session_pool=lambda: session)

    await service._execute_reminder(reminder.id, is_nagging_execution=False)

    assert reminder.habit_active_due_at == now
