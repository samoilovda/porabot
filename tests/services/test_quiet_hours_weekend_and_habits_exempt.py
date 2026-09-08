"""3.5: quiet hours — separate weekend window, and "habits can wake,
regular tasks can't" exemption."""

from datetime import datetime
from types import SimpleNamespace

from bot.utils.time_ext import is_quiet_hours


def _user(**overrides) -> SimpleNamespace:
    base = dict(
        quiet_hours_enabled=True,
        quiet_hours_start="23:00",
        quiet_hours_end="07:00",
        quiet_hours_weekend_enabled=False,
        quiet_hours_weekend_start="23:00",
        quiet_hours_weekend_end="10:00",
        quiet_hours_habits_exempt=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_weekday_uses_regular_window() -> None:
    user = _user()
    # Wednesday 2026-05-06, 08:00 — after the 07:00 weekday end, quiet.
    monday_early = datetime(2026, 5, 6, 6, 0)  # before 07:00 end -> still quiet
    assert is_quiet_hours(user, monday_early) is True
    monday_after = datetime(2026, 5, 6, 8, 0)  # after 07:00 -> not quiet
    assert is_quiet_hours(user, monday_after) is False


def test_weekend_window_used_when_enabled() -> None:
    user = _user(quiet_hours_weekend_enabled=True, quiet_hours_weekend_end="10:00")
    # Saturday 2026-05-09, 08:00 — inside the weekend window (ends 10:00),
    # but would NOT be quiet under the weekday window (ends 07:00).
    saturday_8am = datetime(2026, 5, 9, 8, 0)
    assert saturday_8am.weekday() == 5
    assert is_quiet_hours(user, saturday_8am) is True
    saturday_11am = datetime(2026, 5, 9, 11, 0)
    assert is_quiet_hours(user, saturday_11am) is False


def test_weekend_window_ignored_when_disabled() -> None:
    user = _user(quiet_hours_weekend_enabled=False)
    saturday_8am = datetime(2026, 5, 9, 8, 0)
    # Falls back to the weekday window (ends 07:00) -> not quiet at 08:00.
    assert is_quiet_hours(user, saturday_8am) is False


def test_habits_exempt_lets_habits_through_during_quiet_hours() -> None:
    user = _user(quiet_hours_habits_exempt=True)
    midnight = datetime(2026, 5, 6, 0, 30)
    assert is_quiet_hours(user, midnight, is_habit=False) is True
    assert is_quiet_hours(user, midnight, is_habit=True) is False


def test_habits_exempt_off_still_silences_habits() -> None:
    user = _user(quiet_hours_habits_exempt=False)
    midnight = datetime(2026, 5, 6, 0, 30)
    assert is_quiet_hours(user, midnight, is_habit=True) is True
