"""Unit tests for is_habit_like() and is_habit_entry() helpers."""

from types import SimpleNamespace
from bot.utils.habits_utils import is_habit_like, is_habit_entry
from datetime import datetime


def _reminder(**kwargs):
    defaults = dict(
        is_habit=False,
        is_fluid_habit=False,
        is_recurring=False,
        is_nagging=False,
        rrule_string=None,
        habit_active_due_at=None,
        habit_last_completed_due_at=None,
        habit_streak_current=0,
        habit_streak_best=0,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# is_habit_like
# ---------------------------------------------------------------------------

class TestIsHabitLike:
    def test_plain_reminder_is_not_habit_like(self):
        r = _reminder()
        assert is_habit_like(r) is False

    def test_is_habit_flag_makes_habit_like(self):
        r = _reminder(is_habit=True)
        assert is_habit_like(r) is True

    def test_active_due_at_makes_habit_like(self):
        r = _reminder(habit_active_due_at=datetime(2026, 1, 1, 9, 0))
        assert is_habit_like(r) is True

    def test_last_completed_due_at_makes_habit_like(self):
        r = _reminder(habit_last_completed_due_at=datetime(2026, 1, 1, 9, 0))
        assert is_habit_like(r) is True

    def test_nonzero_current_streak_makes_habit_like(self):
        r = _reminder(habit_streak_current=3)
        assert is_habit_like(r) is True

    def test_nonzero_best_streak_makes_habit_like(self):
        r = _reminder(habit_streak_best=5)
        assert is_habit_like(r) is True

    def test_fluid_habit_is_excluded_even_with_flag(self):
        r = _reminder(is_habit=True, is_fluid_habit=True)
        assert is_habit_like(r) is False

    def test_fluid_habit_with_streak_is_excluded(self):
        r = _reminder(is_fluid_habit=True, habit_streak_current=10)
        assert is_habit_like(r) is False


# ---------------------------------------------------------------------------
# is_habit_entry
# ---------------------------------------------------------------------------

class TestIsHabitEntry:
    def test_plain_reminder_is_not_habit_entry(self):
        r = _reminder()
        assert is_habit_entry(r) is False

    def test_is_habit_flag_makes_entry(self):
        r = _reminder(is_habit=True)
        assert is_habit_entry(r) is True

    def test_is_fluid_habit_flag_makes_entry(self):
        r = _reminder(is_fluid_habit=True)
        assert is_habit_entry(r) is True

    def test_daily_recurring_nagging_is_entry(self):
        r = _reminder(is_recurring=True, is_nagging=True, rrule_string="FREQ=DAILY")
        assert is_habit_entry(r) is True

    def test_weekly_recurring_nagging_is_not_entry(self):
        # Only FREQ=DAILY counts as the heuristic habit detector
        r = _reminder(is_recurring=True, is_nagging=True, rrule_string="FREQ=WEEKLY")
        assert is_habit_entry(r) is False

    def test_recurring_without_nagging_is_not_entry_by_rrule_heuristic(self):
        r = _reminder(is_recurring=True, is_nagging=False, rrule_string="FREQ=DAILY")
        assert is_habit_entry(r) is False

    def test_active_due_at_makes_entry(self):
        r = _reminder(habit_active_due_at=datetime(2026, 1, 1))
        assert is_habit_entry(r) is True

    def test_streak_makes_entry(self):
        r = _reminder(habit_streak_current=2)
        assert is_habit_entry(r) is True
