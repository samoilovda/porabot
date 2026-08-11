"""HabitEventDAO — data access for the HabitEvent event log."""

from datetime import datetime, timezone
from typing import Optional, Sequence

import pytz
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from bot.database.dao.base import BaseDAO
from bot.database.models import HabitEvent


def cycle_key_for_fixed(due_at_utc_naive: datetime) -> str:
    return f"due:{int(due_at_utc_naive.replace(tzinfo=timezone.utc).timestamp())}"


def cycle_key_for_fluid(local_date: str) -> str:
    return f"day:{local_date}"


class HabitEventDAO(BaseDAO[HabitEvent]):
    model = HabitEvent

    async def record(
        self,
        *,
        reminder,
        user_tz: str,
        outcome: str,
        source: str,
        due_at_utc_naive: Optional[datetime] = None,
        local_date: Optional[str] = None,
    ) -> bool:
        """Record the outcome of one habit cycle.

        Idempotent per (reminder_id, cycle_key): if a row already exists for
        this cycle it is left untouched and this returns False. An explicit
        user mark always wins over a later auto-skip because it is recorded
        first.
        """
        if due_at_utc_naive is not None:
            if due_at_utc_naive.tzinfo is not None:
                due_at_utc_naive = due_at_utc_naive.astimezone(timezone.utc).replace(tzinfo=None)
            cycle_key = cycle_key_for_fixed(due_at_utc_naive)
            try:
                tz = pytz.timezone(user_tz)
            except Exception:
                tz = pytz.UTC
            local_dt = due_at_utc_naive.replace(tzinfo=timezone.utc).astimezone(tz)
            resolved_local_date = local_dt.date().isoformat()
        else:
            if not local_date:
                raise ValueError("local_date is required when due_at_utc_naive is not provided")
            cycle_key = cycle_key_for_fluid(local_date)
            resolved_local_date = local_date

        if await self.has_event_for_cycle(reminder.id, cycle_key):
            return False

        event = HabitEvent(
            user_id=reminder.user_id,
            reminder_id=reminder.id,
            habit_text=reminder.reminder_text,
            cycle_key=cycle_key,
            local_date=resolved_local_date,
            due_at=due_at_utc_naive,
            outcome=outcome,
            source=source,
        )
        try:
            # SAVEPOINT, not session.rollback(): the UNIQUE violation here is a
            # concurrent-tap race on one event row, and rolling back the whole
            # session would silently discard the caller's other pending changes
            # (streak counters, mark_done, ...) in the same Unit of Work.
            async with self.session.begin_nested():
                self.session.add(event)
                await self.session.flush()
        except IntegrityError:
            return False
        return True

    async def delete_for_cycle(self, reminder_id: int, cycle_key: str) -> None:
        await self.session.execute(
            delete(HabitEvent).where(
                HabitEvent.reminder_id == reminder_id,
                HabitEvent.cycle_key == cycle_key,
            )
        )
        await self.session.flush()

    async def delete_for_reminder(self, reminder_id: int) -> None:
        await self.session.execute(delete(HabitEvent).where(HabitEvent.reminder_id == reminder_id))
        await self.session.flush()

    async def delete_for_user(self, user_id: int) -> None:
        """Drop every event of a user — for full account reset."""
        await self.session.execute(delete(HabitEvent).where(HabitEvent.user_id == user_id))
        await self.session.flush()

    async def get_events_in_range(
        self, user_id: int, start_date: str, end_date: str
    ) -> Sequence[HabitEvent]:
        """Inclusive on both ends; YYYY-MM-DD string comparison sorts correctly."""
        result = await self.session.execute(
            select(HabitEvent)
            .where(
                HabitEvent.user_id == user_id,
                HabitEvent.local_date >= start_date,
                HabitEvent.local_date <= end_date,
            )
            .order_by(HabitEvent.local_date)
        )
        return result.scalars().all()

    async def has_event_for_cycle(self, reminder_id: int, cycle_key: str) -> bool:
        result = await self.session.execute(
            select(HabitEvent).where(
                HabitEvent.reminder_id == reminder_id,
                HabitEvent.cycle_key == cycle_key,
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_events_for_reminder(self, reminder_id: int) -> Sequence[HabitEvent]:
        """All events for one habit, oldest first — feeds the 3.2 EMA habit
        score (bot/services/habit_reports.py's compute_habit_score), which
        needs the full chronological history, not a fixed date window."""
        result = await self.session.execute(
            select(HabitEvent)
            .where(HabitEvent.reminder_id == reminder_id)
            .order_by(HabitEvent.local_date, HabitEvent.id)
        )
        return result.scalars().all()

    async def get_latest_done_event(self, reminder_id: int) -> Optional[HabitEvent]:
        """Most recent 'done' event for a reminder, ordered by local_date (works for
        both fixed habits, which set due_at, and fluid habits, which don't)."""
        result = await self.session.execute(
            select(HabitEvent)
            .where(HabitEvent.reminder_id == reminder_id, HabitEvent.outcome == "done")
            .order_by(HabitEvent.local_date.desc(), HabitEvent.id.desc())
        )
        return result.scalars().first()
