from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.database.dao.reminder import ReminderDAO
from bot.keyboards.inline import get_task_done_keyboard


def _make_dao(reminder):
    session = SimpleNamespace(flush=AsyncMock())
    dao = ReminderDAO(session)  # type: ignore[arg-type]
    dao.get_by_id = AsyncMock(return_value=reminder)  # type: ignore[method-assign]
    return dao, session


async def test_habit_streak_counts_completion_within_24h() -> None:
    due = datetime(2026, 5, 1, 9, 0, 0)
    completed = due + timedelta(hours=23, minutes=59)
    reminder = SimpleNamespace(
        is_habit=True,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        habit_streak_current=0,
        habit_streak_best=0,
        habit_last_completed_due_at=None,
    )
    dao, session = _make_dao(reminder)

    result = await dao.apply_habit_streak_completion(
        1,
        due_at_utc_naive=due,
        completed_at_utc_naive=completed,
    )

    assert result == {"already_counted": False, "counted": True, "late": False}
    assert reminder.habit_streak_current == 1
    assert reminder.habit_streak_best == 1
    assert reminder.habit_last_completed_due_at == due
    session.flush.assert_awaited()


async def test_habit_streak_increments_for_next_daily_cycle() -> None:
    prev_due = datetime(2026, 5, 1, 9, 0, 0)
    due = prev_due + timedelta(days=1)
    reminder = SimpleNamespace(
        is_habit=True,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        habit_streak_current=1,
        habit_streak_best=1,
        habit_last_completed_due_at=prev_due,
    )
    dao, _ = _make_dao(reminder)

    result = await dao.apply_habit_streak_completion(
        1,
        due_at_utc_naive=due,
        completed_at_utc_naive=due + timedelta(hours=4),
    )

    assert result == {"already_counted": False, "counted": True, "late": False}
    assert reminder.habit_streak_current == 2
    assert reminder.habit_streak_best == 2
    assert reminder.habit_last_completed_due_at == due


async def test_habit_streak_resets_when_completion_is_late() -> None:
    prev_due = datetime(2026, 5, 1, 9, 0, 0)
    due = prev_due + timedelta(days=1)
    reminder = SimpleNamespace(
        is_habit=True,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        habit_streak_current=4,
        habit_streak_best=7,
        habit_last_completed_due_at=prev_due,
    )
    dao, _ = _make_dao(reminder)

    result = await dao.apply_habit_streak_completion(
        1,
        due_at_utc_naive=due,
        completed_at_utc_naive=due + timedelta(hours=24, minutes=1),
    )

    assert result == {"already_counted": False, "counted": False, "late": True}
    assert reminder.habit_streak_current == 0
    assert reminder.habit_streak_best == 7
    assert reminder.habit_last_completed_due_at == prev_due


async def test_non_habit_daily_reminder_does_not_mutate_streak() -> None:
    due = datetime(2026, 5, 1, 9, 0, 0)
    reminder = SimpleNamespace(
        is_habit=False,
        is_recurring=True,
        is_nagging=False,
        rrule_string="FREQ=DAILY",
        habit_streak_current=0,
        habit_streak_best=0,
        habit_active_due_at=None,
        habit_last_completed_due_at=None,
    )
    dao, session = _make_dao(reminder)

    result = await dao.apply_habit_streak_completion(
        1,
        due_at_utc_naive=due,
        completed_at_utc_naive=due + timedelta(hours=1),
    )

    assert result == {"already_counted": False, "counted": False, "late": False}
    session.flush.assert_not_awaited()


def test_done_button_can_embed_cycle_due_timestamp() -> None:
    markup = get_task_done_keyboard(123, {"btn_done": "Done", "snooze_15m": "+15m", "snooze_30m": "+30m", "snooze_1h": "+1h", "snooze_2h": "+2h", "snooze_1d": "+1d", "snooze_custom": "Custom"}, cycle_due_ts=1700000000)
    first_button = markup.inline_keyboard[0][0]
    assert first_button.callback_data == "done_task_123_1700000000"
