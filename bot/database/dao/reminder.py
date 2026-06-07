"""ReminderDAO — data access for the Reminder model."""

from datetime import datetime, timedelta
from typing import Optional, Sequence

import pytz

from sqlalchemy import or_, select

from bot.database.dao.base import BaseDAO
from bot.database.models import Reminder, ReminderStatus
from bot.utils.habits_utils import is_habit_like
from bot.utils.time_ext import resolve_tz

# Reusable WHERE clause: hide recurring reminders already completed for the current cycle.
_NOT_COMPLETED_FOR_CURRENT_CYCLE = or_(
    Reminder.completed_for_execution_time.is_(None),
    Reminder.completed_for_execution_time < Reminder.execution_time,
)


class ReminderDAO(BaseDAO[Reminder]):
    """Data access object for the Reminder model."""

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
        is_habit: bool = False,
        is_fluid_habit: bool = False,
        fluid_mode: Optional[str] = None,
        is_nagging: bool = False,
        nagging_max_repeats: int = 3,
    ) -> Reminder:
        """Insert a new reminder and return it with a populated ``id``."""
        MAX_TEXT_LENGTH = 3000

        if len(text) > MAX_TEXT_LENGTH:
            raise ValueError(
                f"Reminder text too long ({len(text)} chars). "
                f"Maximum allowed: {MAX_TEXT_LENGTH} chars."
            )
        if nagging_max_repeats < 0:
            raise ValueError("nagging_max_repeats cannot be negative.")

        reminder = Reminder(
            user_id=user_id,
            reminder_text=text,
            execution_time=execution_time,
            media_file_id=media_file_id,
            media_type=media_type,
            is_recurring=is_recurring,
            rrule_string=rrule_string,
            is_habit=(is_habit or is_fluid_habit),
            is_fluid_habit=is_fluid_habit,
            fluid_mode=fluid_mode,
            habit_active_due_at=execution_time if is_habit else None,
            is_nagging=is_nagging,
            nagging_max_repeats=nagging_max_repeats,
        )
        self.session.add(reminder)
        await self.session.flush()
        return reminder

    async def get_user_reminders(self, user_id: int) -> Sequence[Reminder]:
        """Return all pending non-fluid reminders for a user, ordered by execution_time."""
        result = await self.session.execute(
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.status == ReminderStatus.PENDING,
                Reminder.is_fluid_habit.is_(False),
                _NOT_COMPLETED_FOR_CURRENT_CYCLE,
            )
            .order_by(Reminder.execution_time)
        )
        return result.scalars().all()

    async def mark_done(self, reminder_id: int) -> None:
        """Mark a reminder done.

        One-time reminders are set to COMPLETED.
        Recurring reminders stay PENDING but the current execution slot is
        hidden via ``completed_for_execution_time`` until the time rolls forward.
        """
        reminder = await self.get_by_id(reminder_id)
        if reminder:
            now_utc_naive = datetime.now(pytz.UTC).replace(tzinfo=None)
            execution_time_cmp = reminder.execution_time
            if execution_time_cmp.tzinfo is not None:
                execution_time_cmp = execution_time_cmp.astimezone(pytz.UTC).replace(tzinfo=None)
            if reminder.is_recurring:
                reminder.status = ReminderStatus.PENDING
                if execution_time_cmp <= now_utc_naive:
                    reminder.completed_for_execution_time = reminder.execution_time
                else:
                    reminder.completed_for_execution_time = None
            else:
                reminder.status = ReminderStatus.COMPLETED
                reminder.completed_for_execution_time = reminder.execution_time

            reminder.completed_at = now_utc_naive
            reminder.last_completion_note = None
            reminder.last_nag_chat_id = None
            reminder.last_nag_message_id = None

            await self.session.flush()

    async def apply_habit_streak_completion(
        self,
        reminder_id: int,
        *,
        due_at_utc_naive: datetime,
        completed_at_utc_naive: datetime,
    ) -> dict[str, bool]:
        """Update habit streak counters for one completed due cycle.

        Completion counts toward streak only if done within 24h after due time.
        Returns a dict with keys: ``already_counted``, ``counted``, ``late``.
        """
        reminder = await self.get_by_id(reminder_id)
        if not reminder or not is_habit_like(reminder):
            return {"already_counted": False, "counted": False, "late": False}

        if due_at_utc_naive.tzinfo is not None:
            due_at_utc_naive = due_at_utc_naive.astimezone(pytz.UTC).replace(tzinfo=None)
        if completed_at_utc_naive.tzinfo is not None:
            completed_at_utc_naive = completed_at_utc_naive.astimezone(pytz.UTC).replace(tzinfo=None)

        last_done_due = reminder.habit_last_completed_due_at
        if last_done_due and due_at_utc_naive <= last_done_due:
            return {"already_counted": True, "counted": False, "late": False}

        if completed_at_utc_naive > (due_at_utc_naive + timedelta(hours=24)):
            reminder.habit_streak_current = 0
            await self.session.flush()
            return {"already_counted": False, "counted": False, "late": True}

        prev_due = reminder.habit_last_completed_due_at
        if prev_due is None:
            new_streak = 1
        else:
            gap = due_at_utc_naive - prev_due
            # Accept DST-shifted daily windows (roughly 24h ± 12h).
            if timedelta(hours=12) <= gap <= timedelta(hours=36):
                new_streak = max(0, int(reminder.habit_streak_current or 0)) + 1
            else:
                new_streak = 1

        reminder.habit_streak_current = new_streak
        reminder.habit_streak_best = max(new_streak, max(0, int(reminder.habit_streak_best or 0)))
        reminder.habit_last_completed_due_at = due_at_utc_naive
        await self.session.flush()
        return {"already_counted": False, "counted": True, "late": False}

    async def set_last_completion_note(self, reminder_id: int, note: str) -> None:
        """Attach a note to the latest completion of a reminder."""
        reminder = await self.get_by_id(reminder_id)
        if reminder:
            reminder.last_completion_note = note
            await self.session.flush()

    async def get_today_tasks_by_status(
        self, user_id: int, user_tz_str: str, status: str
    ) -> Sequence[Reminder]:
        """Return tasks scheduled for the user's current local day.

        ``status`` must be ``"pending"`` or ``"completed"``.
        Day boundaries are converted to UTC so the query is timezone-correct.
        """
        tz = resolve_tz(user_tz_str)
        now_local = datetime.now(tz)
        start_of_day_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day_local = start_of_day_local + timedelta(days=1)

        start_utc = start_of_day_local.astimezone(pytz.UTC).replace(tzinfo=None)
        end_utc = end_of_day_local.astimezone(pytz.UTC).replace(tzinfo=None)

        if status == ReminderStatus.COMPLETED:
            # Use completed_at, not status — recurring tasks remain PENDING across cycles.
            stmt = (
                select(Reminder)
                .where(
                    Reminder.user_id == user_id,
                    Reminder.is_fluid_habit.is_(False),
                    Reminder.completed_at.is_not(None),
                    Reminder.completed_at >= start_utc,
                    Reminder.completed_at < end_utc,
                )
                .order_by(Reminder.completed_at.desc())
            )
        else:
            stmt = (
                select(Reminder)
                .where(
                    Reminder.user_id == user_id,
                    Reminder.is_fluid_habit.is_(False),
                    Reminder.status == status,
                    Reminder.execution_time >= start_utc,
                    Reminder.execution_time < end_utc,
                    _NOT_COMPLETED_FOR_CURRENT_CYCLE,
                )
                .order_by(Reminder.execution_time)
            )

        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_today_pending_tasks(self, user_id: int, user_tz_str: str) -> Sequence[Reminder]:
        """Convenience wrapper: today's pending tasks for daily briefs."""
        return await self.get_today_tasks_by_status(user_id, user_tz_str, ReminderStatus.PENDING)

    async def get_today_completed_tasks(self, user_id: int, user_tz_str: str) -> Sequence[Reminder]:
        """Convenience wrapper: today's completed tasks for daily briefs."""
        return await self.get_today_tasks_by_status(user_id, user_tz_str, ReminderStatus.COMPLETED)

    async def get_recent_completed_tasks(
        self,
        user_id: int,
        user_tz_str: str,
        days: int = 7,
    ) -> Sequence[Reminder]:
        """Return completed reminders within the last *days* days (newest first)."""
        tz = resolve_tz(user_tz_str)
        days = max(1, int(days))
        now_local = datetime.now(tz)
        window_start_local = now_local - timedelta(days=days)

        start_utc = window_start_local.astimezone(pytz.UTC).replace(tzinfo=None)
        end_utc = now_local.astimezone(pytz.UTC).replace(tzinfo=None)

        result = await self.session.execute(
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.is_fluid_habit.is_(False),
                Reminder.completed_at.is_not(None),
                Reminder.completed_at >= start_utc,
                Reminder.completed_at <= end_utc,
            )
            .order_by(Reminder.completed_at.desc())
        )
        return result.scalars().all()

    async def get_overdue_pending_tasks(
        self,
        user_id: int,
        *,
        min_minutes_overdue: int = 30,
        limit: Optional[int] = None,
    ) -> Sequence[Reminder]:
        """Return pending tasks overdue by at least *min_minutes_overdue* minutes."""
        threshold_utc = (
            datetime.now(pytz.UTC).replace(tzinfo=None)
            - timedelta(minutes=max(0, min_minutes_overdue))
        )
        stmt = (
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.status == ReminderStatus.PENDING,
                Reminder.is_fluid_habit.is_(False),
                Reminder.execution_time <= threshold_utc,
                _NOT_COMPLETED_FOR_CURRENT_CYCLE,
            )
            .order_by(Reminder.execution_time.asc())
        )
        if limit is not None:
            stmt = stmt.limit(max(1, int(limit)))
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_habit_motivation_stats(
        self,
        user_id: int,
        user_tz_str: str,
        *,
        days: int = 7,
    ) -> dict[str, int]:
        """Return lightweight motivation metrics for the habits dashboard."""
        days = max(1, int(days))
        tz = resolve_tz(user_tz_str)
        now_local = datetime.now(tz)
        start_utc = (now_local - timedelta(days=days)).astimezone(pytz.UTC).replace(tzinfo=None)

        _habit_filter = or_(
            Reminder.is_habit.is_(True),
            Reminder.habit_active_due_at.is_not(None),
            Reminder.habit_last_completed_due_at.is_not(None),
            Reminder.habit_streak_current > 0,
            Reminder.habit_streak_best > 0,
        )

        active_result = await self.session.execute(
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.is_recurring.is_(True),
                _habit_filter,
                Reminder.status == ReminderStatus.PENDING,
            )
        )
        active_habits = active_result.scalars().all()

        completed_result = await self.session.execute(
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.is_recurring.is_(True),
                _habit_filter,
                Reminder.completed_at.is_not(None),
                Reminder.completed_at >= start_utc,
            )
        )
        completed_habits = completed_result.scalars().all()

        def _current_streak(h) -> int:
            if getattr(h, "is_fluid_habit", False):
                return max(0, int(h.fluid_streak_current or 0))
            return max(0, int(h.habit_streak_current or 0))

        def _best_streak(h) -> int:
            if getattr(h, "is_fluid_habit", False):
                return max(0, int(h.fluid_streak_best or 0))
            return max(0, int(h.habit_streak_best or 0))

        return {
            "active_count": len(active_habits),
            "weekly_done": len(completed_habits),
            "best_current_streak": max((_current_streak(h) for h in active_habits), default=0),
            "best_ever_streak": max((_best_streak(h) for h in active_habits), default=0),
        }

    async def get_active_fluid_habits(self, user_id: int) -> Sequence[Reminder]:
        """Return active fluid habits for a user."""
        result = await self.session.execute(
            select(Reminder).where(
                Reminder.user_id == user_id,
                Reminder.status == ReminderStatus.PENDING,
                Reminder.is_fluid_habit.is_(True),
            )
        )
        return result.scalars().all()

    async def mark_fluid_habit_done_today(self, reminder_id: int, user_tz_str: str) -> bool:
        """Mark a fluid habit as completed for the user's current local day.

        Returns ``True`` if newly recorded, ``False`` if already done today.
        """
        reminder = await self.get_by_id(reminder_id)
        if not reminder or not reminder.is_fluid_habit:
            return False

        tz = resolve_tz(user_tz_str)
        today_local = datetime.now(tz).date()
        today_str = today_local.isoformat()
        if reminder.fluid_last_completed_date == today_str:
            return False

        prev_str = reminder.fluid_last_completed_date
        new_streak = 1
        if prev_str:
            try:
                prev_date = datetime.strptime(prev_str, "%Y-%m-%d").date()
            except ValueError:
                prev_date = None
            if prev_date is not None and prev_date == (today_local - timedelta(days=1)):
                new_streak = max(0, int(reminder.fluid_streak_current or 0)) + 1

        reminder.fluid_streak_current = new_streak
        reminder.fluid_streak_best = max(new_streak, max(0, int(reminder.fluid_streak_best or 0)))
        reminder.fluid_last_completed_date = today_str
        reminder.completed_at = datetime.now(pytz.UTC).replace(tzinfo=None)
        await self.session.flush()
        return True

    async def reset_stale_fluid_streak_if_needed(self, reminder_id: int, user_tz_str: str) -> None:
        """Reset the current fluid streak when the user missed more than one day."""
        reminder = await self.get_by_id(reminder_id)
        if not reminder or not reminder.is_fluid_habit:
            return

        last = reminder.fluid_last_completed_date
        if not last:
            return

        tz = resolve_tz(user_tz_str)
        today_local = datetime.now(tz).date()
        try:
            last_date = datetime.strptime(last, "%Y-%m-%d").date()
        except ValueError:
            return

        if (today_local - last_date).days > 1 and int(reminder.fluid_streak_current or 0) != 0:
            reminder.fluid_streak_current = 0
            await self.session.flush()

    async def update_execution_time(self, reminder_id: int, new_time: datetime) -> None:
        """Update ``execution_time`` for a reminder (used by recurring reschedule)."""
        reminder = await self.get_by_id(reminder_id)
        if reminder:
            reminder.execution_time = new_time
            await self.session.flush()
