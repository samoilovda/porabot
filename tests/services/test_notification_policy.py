"""Step 5 of the 2026-09-26 audit remediation: one shared quiet-hours
policy for every scheduled message kind. Parametrized across all four
kinds, inside the user's quiet window: summaries (brief/report) are
"silent" (still delivered, disable_notification=True), while a regular
reminder and the missed-task recovery digest are "suppress"ed — their
existing defer/skip behavior, unchanged.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from bot.services.notification_policy import notification_policy

_QUIET_USER = SimpleNamespace(
    quiet_hours_enabled=True,
    quiet_hours_start="23:00",
    quiet_hours_end="07:00",
    quiet_hours_weekend_enabled=False,
    quiet_hours_habits_exempt=False,
)

# A Tuesday, 23:30 — inside the 23:00-07:00 quiet window.
_INSIDE_QUIET_WINDOW = datetime(2026, 9, 29, 23, 30)
_OUTSIDE_QUIET_WINDOW = datetime(2026, 9, 29, 12, 0)


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("brief", "silent"),
        ("report", "silent"),
        ("reminder", "suppress"),
        ("missed_recovery", "suppress"),
    ],
)
def test_summaries_are_silent_others_are_suppressed_inside_quiet_hours(kind, expected) -> None:
    assert notification_policy(_QUIET_USER, kind, _INSIDE_QUIET_WINDOW) == expected


@pytest.mark.parametrize("kind", ["brief", "report", "reminder", "missed_recovery"])
def test_every_kind_delivers_normally_outside_quiet_hours(kind) -> None:
    assert notification_policy(_QUIET_USER, kind, _OUTSIDE_QUIET_WINDOW) == "deliver"


def test_quiet_hours_disabled_always_delivers() -> None:
    user = SimpleNamespace(quiet_hours_enabled=False)
    for kind in ("brief", "report", "reminder", "missed_recovery"):
        assert notification_policy(user, kind, _INSIDE_QUIET_WINDOW) == "deliver"


def test_habit_exempt_reminder_still_delivers_inside_quiet_hours() -> None:
    user = SimpleNamespace(
        quiet_hours_enabled=True,
        quiet_hours_start="23:00",
        quiet_hours_end="07:00",
        quiet_hours_weekend_enabled=False,
        quiet_hours_habits_exempt=True,
    )
    assert notification_policy(user, "reminder", _INSIDE_QUIET_WINDOW, is_habit=True) == "deliver"
