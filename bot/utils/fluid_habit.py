"""Display rule for a fluid habit's time: the hour the user planned for TODAY,
or "anytime today" when nothing is planned for today."""

from datetime import datetime
from typing import Any, Optional

import pytz

from bot.utils.timezone import resolve_timezone


def fluid_planned_time_today(habit: Any, tz_str: str) -> Optional[str]:
    """"HH:MM" if the habit has a time planned for today in the user's
    timezone, else None. "Today" is local to the user, never UTC."""
    planned_time = getattr(habit, "fluid_planned_time", None)
    if not planned_time or not getattr(habit, "fluid_planned_date", None):
        return None
    try:
        tz = resolve_timezone(tz_str)
    except Exception:
        tz = pytz.UTC
    if habit.fluid_planned_date != datetime.now(tz).date().isoformat():
        return None
    return planned_time


def fluid_time_label(habit: Any, tz_str: str, l10n: dict[str, Any]) -> str:
    planned = fluid_planned_time_today(habit, tz_str)
    if planned:
        return l10n["habit_fluid_time_planned"].format(time=planned)
    return l10n["habit_fluid_time_anytime"]
