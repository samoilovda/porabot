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
        habit_undo_pending=False,
    )
    dao, session = _make_dao(reminder)

    await dao.revert_habit_streak_completion(1, due_at_utc_naive=due)

    # last_completed_due_at is deliberately left unchanged (not nulled) — it's
    # what lets apply_habit_streak_completion recognize a same-cycle redo and
    # restore the exact streak instead of restarting the chain at 1.
    assert reminder.habit_last_completed_due_at == due
    assert reminder.habit_undo_pending is True
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
        habit_undo_pending=False,
    )
    dao, session = _make_dao(reminder)

    # Undo for an old cycle should not touch a streak advanced by a newer completion.
    await dao.revert_habit_streak_completion(1, due_at_utc_naive=older_due)

    assert reminder.habit_last_completed_due_at == newer_due
    assert reminder.habit_undo_pending is False
    assert reminder.habit_streak_current == 4
    session.flush.assert_not_awaited()


async def test_complete_undo_complete_restores_multi_day_streak() -> None:
    """Regression: redoing the exact cycle an Undo reverted must restore the
    streak it had, not collapse a multi-day streak back down to 1."""
    prev_due = datetime(2026, 5, 4, 9, 0, 0)
    due = datetime(2026, 5, 5, 9, 0, 0)
    completed_at = due + timedelta(hours=1)
    reminder = SimpleNamespace(
        is_habit=True,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        habit_streak_current=4,
        habit_streak_best=4,
        habit_last_completed_due_at=prev_due,
        habit_undo_pending=False,
    )
    dao, _ = _make_dao(reminder)

    first = await dao.apply_habit_streak_completion(
        1, due_at_utc_naive=due, completed_at_utc_naive=completed_at
    )
    assert first == {"already_counted": False, "counted": True, "late": False}
    assert reminder.habit_streak_current == 5

    await dao.revert_habit_streak_completion(1, due_at_utc_naive=due)
    assert reminder.habit_streak_current == 4
    assert reminder.habit_undo_pending is True

    second = await dao.apply_habit_streak_completion(
        1, due_at_utc_naive=due, completed_at_utc_naive=completed_at
    )
    assert second == {"already_counted": False, "counted": True, "late": False}
    assert reminder.habit_streak_current == 5
    assert reminder.habit_streak_best == 5
    assert reminder.habit_undo_pending is False


async def test_complete_undo_complete_recounts_from_zero() -> None:
    due = datetime(2026, 5, 1, 9, 0, 0)
    completed_at = due + timedelta(hours=1)
    reminder = SimpleNamespace(
        is_habit=True,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        habit_streak_current=0,
        habit_streak_best=0,
        habit_last_completed_due_at=None,
        habit_undo_pending=False,
    )
    dao, _ = _make_dao(reminder)

    first = await dao.apply_habit_streak_completion(
        1, due_at_utc_naive=due, completed_at_utc_naive=completed_at
    )
    assert first == {"already_counted": False, "counted": True, "late": False}
    assert reminder.habit_streak_current == 1

    await dao.revert_habit_streak_completion(1, due_at_utc_naive=due)
    assert reminder.habit_streak_current == 0
    assert reminder.habit_undo_pending is True

    second = await dao.apply_habit_streak_completion(
        1, due_at_utc_naive=due, completed_at_utc_naive=completed_at
    )
    assert second == {"already_counted": False, "counted": True, "late": False}
    assert reminder.habit_streak_current == 1


async def test_apply_clears_stale_undo_pending_flag_for_a_different_cycle() -> None:
    """If the user never redoes the reverted cycle and a genuinely new cycle
    gets completed instead, the stale undo_pending flag must not leak into
    that unrelated completion."""
    reverted_due = datetime(2026, 5, 5, 9, 0, 0)
    new_due = datetime(2026, 5, 6, 9, 0, 0)
    completed_at = new_due + timedelta(hours=1)
    reminder = SimpleNamespace(
        is_habit=True,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        habit_streak_current=4,
        habit_streak_best=5,
        habit_last_completed_due_at=reverted_due,
        habit_undo_pending=True,
    )
    dao, _ = _make_dao(reminder)

    result = await dao.apply_habit_streak_completion(
        1, due_at_utc_naive=new_due, completed_at_utc_naive=completed_at
    )

    assert result == {"already_counted": False, "counted": True, "late": False}
    assert reminder.habit_streak_current == 5
    assert reminder.habit_undo_pending is False
    assert reminder.habit_last_completed_due_at == new_due
