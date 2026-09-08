"""Regression tests for 4.5: Reminder.kind / ReminderKind.

kind isn't a stored column — it's the task/fixed-habit/fluid-habit
classification every consumer used to compute inline as
`is_habit_like(reminder) or reminder.is_fluid_habit` (or a bare
`getattr(reminder, "is_fluid_habit", False)`). Covers the four
distinguishable inputs that classification has to get right: a plain
task, a fixed habit via is_habit=True, a *legacy* fixed habit (is_habit
still False, but streak/due fields carry history from before that column
existed — see is_habit_like's own docstring), and a fluid habit.
"""

from datetime import datetime

from bot.database.models import Reminder, ReminderKind

DUE = datetime(2026, 5, 1, 9, 0, 0)


def test_plain_task_is_kind_task() -> None:
    reminder = Reminder(is_habit=False, is_fluid_habit=False, is_recurring=False)
    assert reminder.kind == ReminderKind.TASK


def test_is_habit_true_is_kind_fixed_habit() -> None:
    reminder = Reminder(is_habit=True, is_fluid_habit=False, is_recurring=True, rrule_string="FREQ=DAILY")
    assert reminder.kind == ReminderKind.FIXED_HABIT


def test_legacy_habit_without_is_habit_flag_is_kind_fixed_habit() -> None:
    """Rows that lived as a habit before the is_habit column existed —
    is_habit_like() detects them via habit_active_due_at/streak fields;
    kind must resolve the same way."""
    reminder = Reminder(
        is_habit=False,
        is_fluid_habit=False,
        is_recurring=True,
        habit_active_due_at=DUE,
        habit_streak_current=3,
    )
    assert reminder.kind == ReminderKind.FIXED_HABIT


def test_is_fluid_habit_true_is_kind_fluid_habit() -> None:
    reminder = Reminder(is_habit=False, is_fluid_habit=True, is_recurring=False)
    assert reminder.kind == ReminderKind.FLUID_HABIT


def test_is_fluid_habit_wins_over_fixed_habit_fields() -> None:
    """A row with both is_fluid_habit=True and leftover fixed-habit fields
    set (shouldn't happen, but the schema doesn't forbid it) must resolve
    as fluid — matches is_habit_like()'s own fluid-first precedence."""
    reminder = Reminder(
        is_habit=True,
        is_fluid_habit=True,
        is_recurring=True,
        habit_streak_current=5,
    )
    assert reminder.kind == ReminderKind.FLUID_HABIT
