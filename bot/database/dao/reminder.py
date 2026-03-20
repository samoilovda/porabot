"""ReminderDAO — data access for the Reminder model."""

from datetime import datetime, timedelta
from typing import Optional, Sequence

import pytz

from sqlalchemy import select

from bot.database.dao.base import BaseDAO
from bot.database.models import Reminder


class ReminderDAO(BaseDAO[Reminder]):
    model = Reminder

    async def create_reminder(
        self,
        user_id: int,
        text: str,
        execution_time: datetime,
        *,
        media_file_id: Optional[str] = None,
        media_type: Optional[str] = None,
        is_recurring: bool = False,
        rrule_string: Optional[str] = None,
        is_nagging: bool = False,
    ) -> Reminder:
        """Insert a new reminder and return it with a populated `id`."""
        reminder = Reminder(
            user_id=user_id,
            reminder_text=text,
            execution_time=execution_time,
            media_file_id=media_file_id,
            media_type=media_type,
            is_recurring=is_recurring,
            rrule_string=rrule_string,
            is_nagging=is_nagging,
        )
        self.session.add(reminder)
        await self.session.flush()
        return reminder

    async def get_user_reminders(self, user_id: int) -> Sequence[Reminder]:
        """All PENDING reminders for a user, ordered by execution_time ASC."""
        result = await self.session.execute(
            select(Reminder)
            .where(Reminder.user_id == user_id, Reminder.status == "pending")
            .order_by(Reminder.execution_time)
        )
        return result.scalars().all()

    async def mark_done(self, reminder_id: int) -> None:
        """Soft delete a reminder by marking it completed. For recurring, only updates completed_at."""
        reminder = await self.get_by_id(reminder_id)
        if reminder:
            if not reminder.is_recurring:
                reminder.status = "completed"
            # FIX EDGE-5: use timezone-aware utcnow (datetime.utcnow is deprecated in 3.12)
            reminder.completed_at = datetime.now(pytz.UTC).replace(tzinfo=None)
            await self.session.flush()

    async def get_today_tasks_by_status(
        self, user_id: int, user_tz_str: str, status: str
    ) -> Sequence[Reminder]:
        """Fetch tasks for 'today' based on the user's local timezone."""
        # FIX CRIT-6: convert start/end of the user's local day to UTC before querying.
        # Comparing naive local timestamps against potentially-UTC DB values was causing
        # wrong results for non-UTC users (tasks would appear on the wrong day).
        try:
            tz = pytz.timezone(user_tz_str)
        except Exception:
            tz = pytz.UTC

        now_local = datetime.now(tz)
        start_of_day_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day_local = start_of_day_local + timedelta(days=1)

        # Normalize to UTC (naive) for DB comparison
        start_utc = start_of_day_local.astimezone(pytz.UTC).replace(tzinfo=None)
        end_utc = end_of_day_local.astimezone(pytz.UTC).replace(tzinfo=None)

        result = await self.session.execute(
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.status == status,
                Reminder.execution_time >= start_utc,
                Reminder.execution_time < end_utc,
            )
            .order_by(Reminder.execution_time)
        )
        return result.scalars().all()

    async def get_today_pending_tasks(self, user_id: int, user_tz_str: str) -> Sequence[Reminder]:
        return await self.get_today_tasks_by_status(user_id, user_tz_str, "pending")

    async def get_today_completed_tasks(self, user_id: int, user_tz_str: str) -> Sequence[Reminder]:
        return await self.get_today_tasks_by_status(user_id, user_tz_str, "completed")

    async def update_execution_time(
        self, reminder_id: int, new_time: datetime
    ) -> None:
        """Update execution_time (used by recurring reschedule logic)."""
        reminder = await self.get_by_id(reminder_id)
        if reminder:
            reminder.execution_time = new_time
            await self.session.flush()

    async def get_by_id_or_none(self, reminder_id: int) -> Optional[Reminder]:
        """Fetch a single reminder (used by scheduler job wrapper)."""
        return await self.get_by_id(reminder_id)
