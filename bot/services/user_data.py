"""Full account data reset ("Clear all" in Settings) — a reset, not an
account deletion: the `users` row survives with default values so the
user goes through onboarding again, and `payments` is never touched (a
completed Telegram Stars payment must stay look-up-able from /paysupport
regardless of what the user later clears — see Payment's docstring in
bot/database/models.py).
"""

from typing import Sequence

from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User

# Every column here is a user-configurable *setting*; `id`, `username`,
# `created_at` and `bot_blocked_at` are Telegram/delivery-state, not
# settings, and are left alone.
_DEFAULTS: dict[str, object] = {
    "timezone": "UTC",
    "language": None,
    "show_utc_offset": False,
    "quiet_hours_enabled": False,
    "quiet_hours_start": "23:00",
    "quiet_hours_end": "07:00",
    "quiet_hours_weekend_enabled": False,
    "quiet_hours_weekend_start": "23:00",
    "quiet_hours_weekend_end": "10:00",
    "quiet_hours_habits_exempt": False,
    "missed_recovery_enabled": True,
    "missed_recovery_time": "10:00",
    "last_missed_recovery_date": None,
    "last_morning_brief_date": None,
    "last_evening_brief_date": None,
    "pinned_brief_message_id": None,
    "briefs_enabled": True,
    "morning_brief_time": "09:00",
    "evening_brief_time": "23:00",
    "habit_reports_enabled": True,
    "habit_report_weekday": 6,
    "habit_report_time": "23:50",
    "last_habit_report_date": None,
    "ics_feed_token": None,
}


def _apply_defaults(user: User) -> None:
    for field, value in _DEFAULTS.items():
        setattr(user, field, value)


class UserDataService:
    """Resets one user's tasks/habits/settings back to a fresh account,
    keeping the `users` row and every `payments` row in place."""

    def __init__(
        self,
        reminder_dao: ReminderDAO,
        habit_event_dao: HabitEventDAO,
    ) -> None:
        self.reminder_dao = reminder_dao
        self.habit_event_dao = habit_event_dao

    async def reset(self, user: User) -> Sequence[int]:
        """Deletes tasks/habits/events, resets settings, and commits —
        all in the caller's session/transaction. Returns the ids of the
        reminders that existed (and therefore may still have a scheduler
        job), so the caller can remove those jobs *after* this commit
        succeeds. Raises (e.g. sqlalchemy.exc.OperationalError) without
        touching anything if the commit fails — the caller must not
        remove any scheduler job in that case.
        """
        reminders = await self.reminder_dao.get_all(user_id=user.id)
        reminder_ids = [r.id for r in reminders]

        # habit_events is a FK child of reminders, so it goes first.
        await self.habit_event_dao.delete_for_user(user.id)
        await self.reminder_dao.delete_for_user(user.id)
        _apply_defaults(user)

        await self.reminder_dao.session.flush()
        await self.reminder_dao.session.commit()
        return reminder_ids
