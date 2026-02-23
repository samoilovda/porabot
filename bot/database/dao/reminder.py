"""ReminderDAO — data access for the Reminder model."""

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
        """All reminders for a user, ordered by execution_time ASC."""
        result = await self.session.execute(
            select(Reminder)
            .where(Reminder.user_id == user_id)
            .order_by(Reminder.execution_time)
        )
        return result.scalars().all()

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
