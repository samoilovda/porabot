"""Shared habit-detection helpers used by handlers and the scheduler."""


def is_habit_like(reminder) -> bool:
    """True if the reminder participates in *fixed* habit streak tracking.

    Fluid habits are excluded because they track streaks differently.
    """
    if getattr(reminder, "is_fluid_habit", False):
        return False
    return bool(
        getattr(reminder, "is_habit", False)
        or getattr(reminder, "habit_active_due_at", None) is not None
        or getattr(reminder, "habit_last_completed_due_at", None) is not None
        or int(getattr(reminder, "habit_streak_current", 0) or 0) > 0
        or int(getattr(reminder, "habit_streak_best", 0) or 0) > 0
    )


def is_habit_entry(reminder) -> bool:
    """True if the reminder was created through any habit flow (fixed or fluid)."""
    return bool(
        getattr(reminder, "is_habit", False)
        or getattr(reminder, "is_fluid_habit", False)
        or (
            bool(getattr(reminder, "is_recurring", False))
            and bool(getattr(reminder, "is_nagging", False))
            and str(getattr(reminder, "rrule_string", "") or "").upper().startswith("FREQ=DAILY")
        )
        or getattr(reminder, "habit_active_due_at", None) is not None
        or getattr(reminder, "habit_last_completed_due_at", None) is not None
        or int(getattr(reminder, "habit_streak_current", 0) or 0) > 0
        or int(getattr(reminder, "habit_streak_best", 0) or 0) > 0
    )
