"""Shared state and text-rendering helpers for the bot.handlers.settings
package (step 9, 2026-09-26 audit remediation: split out of the original
1191-line bot/handlers/settings.py — see the package's __init__.py
docstring for the full module family and why).

Nothing in this module registers a handler or owns a Router; it exists so
locale_time.py/notifications.py/data.py, and this package's own
__init__.py, share one copy of this state instead of each reimplementing
it.
"""

from datetime import datetime
from typing import Any

import pytz
from aiogram.fsm.state import State, StatesGroup

from bot.database.models import User


class SettingsState(StatesGroup):
    waiting_for_brief_time = State()
    waiting_for_timezone = State()
    waiting_for_quiet_time = State()
    waiting_for_habit_report_time = State()
    waiting_for_missed_recovery_time = State()


def _format_tz_display_label(tz_name: str) -> str:
    """Friendly timezone label with current UTC offset."""
    try:
        tz = pytz.timezone(tz_name)
        now_local = datetime.now(tz)
        raw = now_local.strftime("%z")  # +HHMM
        if raw and len(raw) == 5:
            return f"{tz_name} (UTC{raw[:3]}:{raw[3:]})"
    except Exception:
        pass
    return tz_name


def _quiet_hours_label(user: User, l10n: dict[str, Any]) -> str:
    enabled = bool(getattr(user, "quiet_hours_enabled", False))
    start = getattr(user, "quiet_hours_start", "23:00")
    end = getattr(user, "quiet_hours_end", "07:00")
    status = l10n.get("status_on", "ON") if enabled else l10n.get("status_off", "OFF")
    return l10n.get("quiet_hours_summary", "{status} ({start}–{end})").format(
        status=status,
        start=start,
        end=end,
    )


def _render_settings_text(user: User, l10n: dict[str, Any]) -> str:
    return l10n["settings_text"].format(
        timezone=_format_tz_display_label(user.timezone),
        quiet_hours=_quiet_hours_label(user, l10n),
    )
