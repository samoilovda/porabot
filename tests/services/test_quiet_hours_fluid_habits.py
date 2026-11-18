"""Audit 2026-11-18 #07: the "habits can wake me during quiet hours" setting
must cover fluid habits as well as fixed-time ones. The scheduler used
is_habit_like(), which by design excludes fluid habits.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.services.scheduler import SchedulerService


class _Result:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _Session:
    def __init__(self, reminder, user):
        self._responses = [_Result(reminder), _Result(user)]
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, _stmt):
        return self._responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _reminder(kind: str) -> SimpleNamespace:
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    fixed = kind == "fixed"
    return SimpleNamespace(
        id=10, user_id=7, status="pending", reminder_text="Task", execution_time=now,
        is_recurring=fixed, rrule_string="FREQ=DAILY" if fixed else None, rrule_dtstart=now if fixed else None,
        is_habit=fixed, is_fluid_habit=(kind == "fluid"), fluid_last_completed_date=None,
        habit_active_due_at=None, habit_last_completed_due_at=None, habit_streak_current=0, habit_streak_best=0,
        is_nagging=False, nagging_max_repeats=3, nagging_sent_count=0, last_nag_chat_id=None,
        last_nag_message_id=None, forbidden_strikes=0, last_fired_at=None, pending_delete_at=None,
        send_retry_count=0, paused_until=None, snoozed_until=None, completed_for_execution_time=None,
    )


def _user(exempt: bool) -> SimpleNamespace:
    # start == end: quiet around the clock, so the test does not depend on the wall clock.
    return SimpleNamespace(
        id=7, language="en", timezone="UTC", bot_blocked_at=None,
        quiet_hours_enabled=True, quiet_hours_start="00:00", quiet_hours_end="00:00",
        quiet_hours_weekend_enabled=False, quiet_hours_habits_exempt=exempt,
    )


async def _run(kind: str, exempt: bool):
    reminder = _reminder(kind)
    scheduler = AsyncIOScheduler()
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=7), message_id=1)),
        delete_message=AsyncMock(),
    )
    service = SchedulerService(scheduler, bot=bot, session_pool=lambda: _Session(reminder, _user(exempt)))
    await service._execute_reminder(reminder.id, is_nagging_execution=False)
    return bot, scheduler, reminder


@pytest.mark.parametrize("kind", ["fixed", "fluid"])
async def test_exempt_habits_are_delivered_during_quiet_hours(kind) -> None:
    bot, _, _ = await _run(kind, exempt=True)
    bot.send_message.assert_awaited()


async def test_regular_task_is_still_deferred_when_habits_are_exempt() -> None:
    bot, scheduler, reminder = await _run("task", exempt=True)
    bot.send_message.assert_not_awaited()
    assert scheduler.get_job(str(reminder.id)) is not None  # deferred, not dropped


@pytest.mark.parametrize("kind", ["fixed", "fluid", "task"])
async def test_without_exemption_everything_is_deferred(kind) -> None:
    bot, scheduler, reminder = await _run(kind, exempt=False)
    bot.send_message.assert_not_awaited()
    assert scheduler.get_job(str(reminder.id)) is not None
