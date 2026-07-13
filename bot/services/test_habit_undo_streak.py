from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.database.dao.reminder import ReminderDAO


def _make_dao(reminder):
    session = SimpleNamespace(flush=AsyncMock())
    dao = ReminderDAO(session)  # type: ignore[arg-type]
    dao.get_by_id = AsyncMock(return_value=reminder)  # type: ignore[method-assign]
    return dao, session


async def test_revert_habit_streak_completion_undoes_matching_cycle() -> None:
    due = datetime(2026, 5, 1, 9, 0, 0)
    reminder = SimpleNamespace(
        habit_streak_current=3,
        habit_streak_best=5,
        habit_last_completed_due_at=due,
    )
    dao, session = _make_dao(reminder)

    await dao.revert_habit_streak_completion(1, due_at_utc_naive=due)

    assert reminder.habit_last_completed_due_at is None
    assert reminder.habit_streak_current == 2
    assert reminder.habit_streak_best == 5  # best is never rolled back
    session.flush.assert_awaited()


async def test_revert_habit_streak_completion_ignores_stale_due_at() -> None:
    older_due = datetime(2026, 5, 1, 9, 0, 0)
    newer_due = older_due + timedelta(days=1)
    reminder = SimpleNamespace(
        habit_streak_current=4,
        habit_streak_best=4,
        habit_last_completed_due_at=newer_due,
    )
    dao, session = _make_dao(reminder)

    # Undo for an old cycle should not touch a streak advanced by a newer completion.
    await dao.revert_habit_streak_completion(1, due_at_utc_naive=older_due)

    assert reminder.habit_last_completed_due_at == newer_due
    assert reminder.habit_streak_current == 4
    session.flush.assert_not_awaited()


async def test_complete_undo_complete_recounts_instead_of_already_counted() -> None:
    due = datetime(2026, 5, 1, 9, 0, 0)
    completed_at = due + timedelta(hours=1)
    reminder = SimpleNamespace(
        is_habit=True,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        habit_streak_current=0,
        habit_streak_best=0,
        habit_last_completed_due_at=None,
    )
    dao, _ = _make_dao(reminder)

    first = await dao.apply_habit_streak_completion(
        1, due_at_utc_naive=due, completed_at_utc_naive=completed_at
    )
    assert first == {"already_counted": False, "counted": True, "late": False}
    assert reminder.habit_streak_current == 1

    await dao.revert_habit_streak_completion(1, due_at_utc_naive=due)
    assert reminder.habit_streak_current == 0
    assert reminder.habit_last_completed_due_at is None

    second = await dao.apply_habit_streak_completion(
        1, due_at_utc_naive=due, completed_at_utc_naive=completed_at
    )
    assert second == {"already_counted": False, "counted": True, "late": False}
    assert reminder.habit_streak_current == 1
