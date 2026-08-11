"""Regression (REWORK_PLAN_3 3.5): bot/handlers/habits.py had its own
byte-for-byte copy of bot.database.models.is_habit_like, with a docstring
claiming it "mirrors SchedulerService._is_habit_like" — a method that no
longer exists there (scheduler.py imports is_habit_like from models
directly). Two copies of the same logic is exactly the setup where a future
fix lands in one and not the other.
"""

from bot.database.models import is_habit_like
from bot.handlers.habits import _is_habit_like as habits_is_habit_like


def test_habits_module_reuses_the_single_is_habit_like_implementation() -> None:
    assert habits_is_habit_like is is_habit_like
