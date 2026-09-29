"""Step 13 of the 2026-09-26 audit remediation: pause a habit until a date.
No notifications, no `missed` events, no burst of catch-up afterwards, out
of the calendar feed; resuming schedules the next occurrence."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.services.habit_pause import apply_pause, is_paused, pause_end_utc_naive, valid_pause_date
from bot.services.habit_reports import compute_habit_score
from bot.services.habit_sweeper import _sweep_user
from bot.services.ics_feed import build_ics_calendar


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _habit(session, due):
    session.add(User(id=1, username="u", timezone="UTC"))
    await session.flush()
    habit = await ReminderDAO(session).create_reminder(
        user_id=1, text="Stretch", execution_time=due, is_recurring=True,
        rrule_string="FREQ=DAILY", is_habit=True, is_nagging=True,
    )
    habit.habit_active_due_at = due
    habit.created_at = due - timedelta(days=30)
    await session.commit()
    return habit


def _scheduler():
    return SimpleNamespace(schedule_reminder=MagicMock(), remove_nagging_job=MagicMock())


def test_pause_end_is_local_midnight_of_the_chosen_date() -> None:
    assert pause_end_utc_naive("Europe/Moscow", date(2026, 10, 5)) == datetime(2026, 10, 4, 21, 0)


def test_valid_pause_date_must_be_future_and_bounded() -> None:
    today = datetime.now(timezone.utc).date()
    assert valid_pause_date(today + timedelta(days=3), "UTC")
    assert not valid_pause_date(today, "UTC")
    assert not valid_pause_date(today + timedelta(days=400), "UTC")


async def test_apply_pause_sets_until_and_reschedules_at_first_occurrence_after_it(session) -> None:
    habit = await _habit(session, _now() - timedelta(hours=1))
    scheduler = _scheduler()
    until = datetime.now(timezone.utc).date() + timedelta(days=5)

    assert await apply_pause(habit, "UTC", scheduler, session, until)

    assert is_paused(habit)
    assert habit.execution_time >= habit.paused_until  # nothing fires inside the pause
    scheduler.remove_nagging_job.assert_called_once_with(habit.id)
    scheduler.schedule_reminder.assert_called_once()


async def test_sweeper_records_no_missed_events_for_paused_or_just_resumed_habit(session) -> None:
    due = _now() - timedelta(hours=30)
    habit = await _habit(session, due)
    user = await session.get(User, 1)
    scheduler = _scheduler()
    # Paused across the missed cycle, then resumed right away: the cycle
    # that fell inside the pause must NOT surface as a miss afterwards.
    await apply_pause(habit, "UTC", scheduler, session, datetime.now(timezone.utc).date() + timedelta(days=2))
    await apply_pause(habit, "UTC", scheduler, session, None)

    await _sweep_user(session, user)

    events = await HabitEventDAO(session).get_events_in_range(1, "2000-01-01", "2100-01-01")
    assert events == []
    assert compute_habit_score(events) == 0  # score/reports derive from events: untouched


async def test_sweeper_still_records_misses_for_an_unpaused_habit(session) -> None:
    habit = await _habit(session, _now() - timedelta(hours=30))
    await _sweep_user(session, await session.get(User, 1))
    events = await HabitEventDAO(session).get_events_in_range(1, "2000-01-01", "2100-01-01")
    assert [e.outcome for e in events] == ["missed"]
    assert habit.paused_until is None


async def test_paused_habit_is_absent_from_the_calendar_feed(session) -> None:
    habit = await _habit(session, _now() + timedelta(hours=1))
    user = await session.get(User, 1)
    assert "Stretch" in build_ics_calendar(user, [habit])

    await apply_pause(habit, "UTC", _scheduler(), session, datetime.now(timezone.utc).date() + timedelta(days=3))

    assert "Stretch" not in build_ics_calendar(user, [habit])


async def test_paused_habit_fire_sends_nothing_and_jumps_to_after_the_pause(session) -> None:
    """The job firing during a pause is a no-op that reschedules past it —
    the real _execute_reminder path, against a real DB row."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from bot.services.scheduler import SchedulerService

    habit = await _habit(session, _now() - timedelta(minutes=1))
    habit.paused_until = _now() + timedelta(days=3)
    await session.commit()

    bot = SimpleNamespace(send_message=MagicMock())
    pool = async_sessionmaker(session.bind, expire_on_commit=False)
    service = SchedulerService(AsyncIOScheduler(), bot=bot, session_pool=pool)

    await service._execute_reminder(habit.id)

    bot.send_message.assert_not_called()
    async with pool() as check:
        fresh = await ReminderDAO(check).get_by_id(habit.id)
    assert fresh.execution_time >= habit.paused_until
    assert service.scheduler.get_job(str(habit.id)) is not None


def _callback(data: str):
    from unittest.mock import AsyncMock

    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
    return SimpleNamespace(data=data, message=message, answer=AsyncMock())


async def test_pause_handlers_set_clear_and_refuse_foreign_or_bad_input(session) -> None:
    from bot.handlers.habits import cb_habit_pause_clear, cb_habit_pause_menu, cb_habit_pause_set
    from bot.lexicon import get_l10n

    l10n = get_l10n("en")
    habit = await _habit(session, _now() + timedelta(hours=1))
    user = await session.get(User, 1)
    dao = ReminderDAO(session)
    scheduler = _scheduler()
    until = (datetime.now(timezone.utc).date() + timedelta(days=4)).isoformat()

    menu = _callback(f"habit_pause_{habit.id}")
    await cb_habit_pause_menu(menu, user, dao, l10n)
    buttons = [b.callback_data for row in menu.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
    assert sum(b.startswith(f"habit_pause_set_{habit.id}_") for b in buttons) == 3

    bad = _callback(f"habit_pause_set_{habit.id}_2000-01-01")  # not in the future
    await cb_habit_pause_set(bad, user, dao, scheduler, l10n)
    bad.answer.assert_awaited_with(l10n["invalid_action"], show_alert=True)
    assert habit.paused_until is None

    foreign = _callback(f"habit_pause_set_9999_{until}")
    await cb_habit_pause_set(foreign, user, dao, scheduler, l10n)
    foreign.answer.assert_awaited_with(l10n["item_not_found"], show_alert=True)

    ok = _callback(f"habit_pause_set_{habit.id}_{until}")
    await cb_habit_pause_set(ok, user, dao, scheduler, l10n)
    assert is_paused(habit)

    await cb_habit_pause_clear(_callback(f"habit_pause_clear_{habit.id}"), user, dao, scheduler, l10n)
    assert not is_paused(habit)
