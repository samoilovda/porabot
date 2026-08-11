"""ReminderDAO — data access for Reminder rows: plain tasks, recurring
tasks, and (is_habit/is_fluid_habit) habits share this one model and DAO.

Soft-delete, not hard-delete, for both tasks and completions: a one-off
task's row becomes status='completed'; a recurring one stays 'pending' but
hides the just-finished cycle via completed_for_execution_time until the
next occurrence rolls around (see mark_done). A tap on Delete instead sets
pending_delete_at and leaves the row alone for the undo window — every
query here that lists/schedules/reconciles active reminders excludes rows
where it's set; bot/services/delete_cleanup.py hard-deletes them once the
window elapses.
"""

from datetime import datetime, timedelta
from typing import Optional, Sequence

import pytz

from sqlalchemy import func, or_, select

# Import BaseDAO from base module (generic CRUD operations)
from bot.database.dao.base import BaseDAO
# Import Reminder model for type hints and query construction
from bot.database.models import Reminder, is_habit_like


class ReminderDAO(BaseDAO[Reminder]):
    """CRUD plus habit-streak, fluid-habit, and daily-brief queries for
    Reminder rows — see the module docstring above for the soft-delete
    conventions every method here follows."""

    model = Reminder  # Class attribute - set by concrete DAO subclass

    # 3.2: access is intentionally open (WhitelistMiddleware disabled) with
    # only a per-user request-RATE cap (RateLimitMiddleware), no cap on
    # total VOLUME — one user could create an unbounded number of reminders,
    # each a DB row plus (for habits) work for five different minutely cron
    # jobs. Separate pools so a heavy task list doesn't block a new habit
    # and vice versa.
    MAX_ACTIVE_REMINDERS = 200
    MAX_ACTIVE_HABITS = 50

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
        """Insert a new reminder, flush, and return it with `id` populated.

        *execution_time* must already be naive UTC (callers convert before
        calling this). Raises ValueError if *text* exceeds 3000 chars, if
        *nagging_max_repeats* is negative, or if the user is already at
        MAX_ACTIVE_REMINDERS / MAX_ACTIVE_HABITS (whichever pool applies).
        """
        # SECURITY FIX: Validate text length to prevent Telegram API errors
        # Telegram message limit is 4096 chars, but we need room for prefix/formatting
        MAX_TEXT_LENGTH = 3000
        
        if len(text) > MAX_TEXT_LENGTH:
            raise ValueError(
                f"Reminder text too long ({len(text)} chars). "
                f"Maximum allowed: {MAX_TEXT_LENGTH} chars."
            )
        if nagging_max_repeats < 0:
            raise ValueError("nagging_max_repeats cannot be negative.")

        is_any_habit = is_habit or is_fluid_habit
        active_count_result = await self.session.execute(
            select(func.count())
            .select_from(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.status == "pending",
                Reminder.pending_delete_at.is_(None),
                Reminder.is_habit.is_(is_any_habit),
            )
        )
        active_count = active_count_result.scalar_one()
        limit = self.MAX_ACTIVE_HABITS if is_any_habit else self.MAX_ACTIVE_REMINDERS
        if active_count >= limit:
            raise ValueError(
                f"You already have {active_count} active {'habits' if is_any_habit else 'reminders'}. "
                f"Maximum allowed: {limit}. Delete some before adding more."
            )

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
        await self.session.flush()  # Flush to populate auto-generated ID
        return reminder

    async def get_owned(self, reminder_id: int, user_id: int) -> Optional[Reminder]:
        """
        Fetch a reminder by id, but only if it belongs to *user_id*.

        Prevents IDOR: callback handlers must never act on a reminder_id
        taken from callback.data without confirming the caller owns it.

        Returns:
            Reminder if found and owned by user_id, otherwise None.
        """
        reminder = await self.get_by_id(reminder_id)
        if reminder is None or reminder.user_id != user_id:
            return None
        return reminder

    async def get_user_reminders(self, user_id: int) -> Sequence[Reminder]:
        """
        Get all PENDING reminders for a user, ordered by execution_time ASC.
        
        This is the primary method for displaying the task list to users.
        It filters by status='pending' so completed tasks don't appear in the list.
        
        Args:
            user_id: Telegram user ID (foreign key)
            
        Returns:
            Sequence[Reminder]: List of pending reminders ordered by execution time
            
        Side Effects:
          None - read-only operation
        
        Example:
            >>> tasks = await dao.get_user_reminders(123456)
            # Returns list like [Reminder(id=1, ...), Reminder(id=2, ...)]
            
            for task in tasks:
                print(f"🔔 {task.reminder_text} at {task.execution_time}")
        """
        result = await self.session.execute(
            select(Reminder)
            .where(
                Reminder.user_id == user_id,  # Filter by owner
                Reminder.status == "pending",  # Only show pending tasks
                Reminder.is_fluid_habit.is_(False),  # Fluid habits live in Habits UI
                Reminder.pending_delete_at.is_(None),  # Hide soft-deleted (undo window)
                # Hide recurring reminders already completed for current cycle.
                # They become visible again after execution_time rolls forward.
                or_(
                    Reminder.completed_for_execution_time.is_(None),
                    Reminder.completed_for_execution_time < Reminder.execution_time,
                ),
            )
            # P1-12: .id as a tiebreaker makes the order fully stable across
            # requests (two tasks with the identical execution_time could
            # otherwise swap positions between page loads, which would
            # shuffle pagination).
            .order_by(Reminder.execution_time, Reminder.id)  # Order by when task fires (earliest first)
        )
        return result.scalars().all()

    async def mark_done(self, reminder_id: int) -> None:
        """
        Mark a reminder as done.
        
        For one-time tasks, sets status='completed'.
        For recurring tasks, keeps status='pending' but marks the current execution
        slot as completed so active lists can hide it until next cycle.
        
        Args:
            reminder_id: Primary key of reminder to mark as done
            
        Returns:
            None
            
        Side Effects:
          Updates status/completion fields
        
        BUG FIX EDGE-5: Timezone-aware datetime handling
          Previously used deprecated datetime.utcnow(). Now uses pytz.UTC for clarity.
        
        Example:
            >>> await dao.mark_done(456)  # Marks reminder #456 as done
        """
        reminder = await self.get_by_id(reminder_id)
        if reminder:
            now_utc_naive = datetime.now(pytz.UTC).replace(tzinfo=None)
            execution_time_cmp = reminder.execution_time
            if execution_time_cmp.tzinfo is not None:
                execution_time_cmp = execution_time_cmp.astimezone(pytz.UTC).replace(tzinfo=None)
            if reminder.is_recurring:
                # Recurring reminders stay active, but current execution slot
                # disappears from active lists until execution_time moves forward.
                reminder.status = "pending"
                # If execution_time has already been rolled forward by scheduler
                # (common path after reminder firing), do not hide the next cycle.
                if execution_time_cmp <= now_utc_naive:
                    reminder.completed_for_execution_time = reminder.execution_time
                else:
                    reminder.completed_for_execution_time = None
            else:
                reminder.status = "completed"
                reminder.completed_for_execution_time = reminder.execution_time
            
            # Store naive UTC datetime for consistency with execution_time field
            # and local-day boundary queries converted to naive UTC.
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
        """
        Update habit streak counters for one completed due cycle.

        Completion counts toward streak only if done within 24h after due time.
        """
        reminder = await self.get_by_id(reminder_id)
        if not reminder or not is_habit_like(reminder):
            return {"already_counted": False, "counted": False, "late": False}

        if due_at_utc_naive.tzinfo is not None:
            due_at_utc_naive = due_at_utc_naive.astimezone(pytz.UTC).replace(tzinfo=None)
        if completed_at_utc_naive.tzinfo is not None:
            completed_at_utc_naive = completed_at_utc_naive.astimezone(pytz.UTC).replace(tzinfo=None)

        # Re-completing the exact cycle an Undo just reverted restores the
        # streak it had before the revert (which only decremented by one),
        # instead of restarting the chain at 1 — there is no stored history
        # of earlier cycles to recompute the gap-based streak from.
        if (
            getattr(reminder, "habit_undo_pending", False)
            and reminder.habit_last_completed_due_at == due_at_utc_naive
        ):
            reminder.habit_undo_pending = False
            if completed_at_utc_naive > (due_at_utc_naive + timedelta(hours=24)):
                reminder.habit_streak_current = 0
                await self.session.flush()
                return {"already_counted": False, "counted": False, "late": True}
            reminder.habit_streak_current = max(0, int(reminder.habit_streak_current or 0)) + 1
            reminder.habit_streak_best = max(
                reminder.habit_streak_current, max(0, int(reminder.habit_streak_best or 0))
            )
            await self.session.flush()
            return {"already_counted": False, "counted": True, "late": False}
        if getattr(reminder, "habit_undo_pending", False):
            reminder.habit_undo_pending = False

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

    async def mark_habit_not_today(self, reminder_id: int) -> None:
        """
        Close the current habit cycle as unresolved ("Not today").

        Hides the cycle from active lists like mark_done, but deliberately
        does NOT set completed_at — the evening brief counts completions by
        completed_at, and a "Not today" habit must not show up there as done.
        """
        reminder = await self.get_by_id(reminder_id)
        if reminder:
            now_utc_naive = datetime.now(pytz.UTC).replace(tzinfo=None)
            execution_time_cmp = reminder.execution_time
            if execution_time_cmp.tzinfo is not None:
                execution_time_cmp = execution_time_cmp.astimezone(pytz.UTC).replace(tzinfo=None)
            # Same guard as mark_done: only hide the cycle that already fired,
            # otherwise we'd hide a cycle already rolled forward by the scheduler.
            if execution_time_cmp <= now_utc_naive:
                reminder.completed_for_execution_time = reminder.execution_time
            reminder.last_nag_chat_id = None
            reminder.last_nag_message_id = None
            await self.session.flush()

    async def revert_habit_streak_completion(
        self,
        reminder_id: int,
        *,
        due_at_utc_naive: datetime,
    ) -> None:
        """
        Undo the streak effect of apply_habit_streak_completion() for one due cycle.

        Only reverts when habit_last_completed_due_at still matches due_at_utc_naive
        (i.e. nothing else completed a later cycle in the meantime), so an Undo can't
        clobber streak progress made after the completion it's undoing. Leaves
        habit_last_completed_due_at pointing at this cycle and sets
        habit_undo_pending so a re-completion of the same cycle restores the
        exact streak instead of restarting the chain at 1.
        """
        reminder = await self.get_by_id(reminder_id)
        if not reminder:
            return

        if due_at_utc_naive.tzinfo is not None:
            due_at_utc_naive = due_at_utc_naive.astimezone(pytz.UTC).replace(tzinfo=None)

        if reminder.habit_last_completed_due_at != due_at_utc_naive:
            return

        reminder.habit_streak_current = max(0, int(reminder.habit_streak_current or 0) - 1)
        reminder.habit_undo_pending = True
        await self.session.flush()

    async def set_last_completion_note(self, reminder_id: int, note: str) -> None:
        """Attach a note to the latest completion of a reminder."""
        reminder = await self.get_by_id(reminder_id)
        if reminder:
            reminder.last_completion_note = note
            await self.session.flush()

    async def get_today_tasks_by_status(
        self, user_id: int, user_tz_str: str, status: str
    ) -> Sequence[Reminder]:
        """
        Fetch tasks for 'today' based on the user's local timezone.
        
        This method is used by daily briefs to show morning/evening summaries.
        It converts the user's local day boundaries to UTC before querying,
        ensuring correct results regardless of user timezone.
        
        Args:
            user_id: Telegram user ID (foreign key)
            user_tz_str: User's timezone string (e.g., "Europe/Moscow")
            status: Filter by 'pending' or 'completed'
            
        Returns:
            Sequence[Reminder]: List of tasks for today ordered by execution time
            
        BUG FIX CRIT-6: Timezone-aware day boundary calculation
          Previously compared naive local timestamps against potentially-UTC DB values,
          causing wrong results for non-UTC users (tasks appeared on wrong day).
          
          Now converts start/end of user's local day to UTC before querying.
        
        Example:
            >>> today_pending = await dao.get_today_tasks_by_status(
            ...     user_id=123456,
            ...     user_tz_str="Europe/Moscow",
            ...     status="pending"
            ... )
        """
        # FIX CRIT-6: Convert start/end of user's local day to UTC before querying.
        # Comparing naive local timestamps against potentially-UTC DB values was causing
        # wrong results for non-UTC users (tasks would appear on the wrong day).
        
        try:
            tz = pytz.timezone(user_tz_str)  # Parse timezone string
        except Exception:
            tz = pytz.UTC  # Fallback to UTC if invalid timezone
        
        now_local = datetime.now(tz)  # Current time in user's local timezone
        start_of_day_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day_local = start_of_day_local + timedelta(days=1)

        # Normalize to UTC (naive) for DB comparison
        # This ensures we're comparing apples-to-apples with execution_time (stored in UTC)
        start_utc = start_of_day_local.astimezone(pytz.UTC).replace(tzinfo=None)
        end_utc = end_of_day_local.astimezone(pytz.UTC).replace(tzinfo=None)

        if status == "completed":
            # Completed list is based on completion moment, not current status,
            # so recurring tasks that continue to next cycle still appear here.
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
                    Reminder.user_id == user_id,  # Filter by owner
                    Reminder.is_fluid_habit.is_(False),
                    Reminder.status == status,   # Filter by status (pending/completed)
                    Reminder.pending_delete_at.is_(None),  # Hide soft-deleted (undo window)
                    Reminder.execution_time >= start_utc,  # After midnight UTC
                    Reminder.execution_time < end_utc,     # Before next midnight UTC
                    # Same active-list clutter rule for daily briefs pending section.
                    or_(
                        Reminder.completed_for_execution_time.is_(None),
                        Reminder.completed_for_execution_time < Reminder.execution_time,
                    ),
                )
                .order_by(Reminder.execution_time)  # Order by when task fires
            )

        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_today_pending_tasks(self, user_id: int, user_tz_str: str) -> Sequence[Reminder]:
        """
        Get today's pending tasks for daily briefs.
        
        Convenience wrapper around get_today_tasks_by_status(status='pending').
        
        Args:
            user_id: Telegram user ID (foreign key)
            user_tz_str: User's timezone string (e.g., "Europe/Moscow")
            
        Returns:
            Sequence[Reminder]: List of pending tasks for today
            
        Example:
            >>> pending = await dao.get_today_pending_tasks(123456, "Europe/Moscow")
        """
        return await self.get_today_tasks_by_status(user_id, user_tz_str, "pending")

    async def get_today_completed_tasks(self, user_id: int, user_tz_str: str) -> Sequence[Reminder]:
        """
        Get today's completed tasks for daily briefs.
        
        Convenience wrapper around get_today_tasks_by_status(status='completed').
        
        Args:
            user_id: Telegram user ID (foreign key)
            user_tz_str: User's timezone string (e.g., "Europe/Moscow")
            
        Returns:
            Sequence[Reminder]: List of completed tasks for today
            
        Example:
            >>> completed = await dao.get_today_completed_tasks(123456, "Europe/Moscow")
        """
        return await self.get_today_tasks_by_status(user_id, user_tz_str, "completed")

    async def get_recent_completed_tasks(
        self,
        user_id: int,
        user_tz_str: str,
        days: int = 7,
    ) -> Sequence[Reminder]:
        """
        Get recently completed tasks for UI history (e.g., last 7 days).

        Uses completion timestamp (completed_at), not status, so recurring
        reminders are included in history even when they remain active.

        Args:
            user_id: Telegram user ID (foreign key)
            user_tz_str: User's timezone string (e.g., "Europe/Moscow")
            days: Rolling window size in days. Minimum: 1

        Returns:
            Sequence[Reminder]: Completed reminders in the rolling window,
            newest first.
        """
        try:
            tz = pytz.timezone(user_tz_str)
        except Exception:
            tz = pytz.UTC

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
        """
        Get pending tasks that are overdue by at least *min_minutes_overdue*.
        """
        threshold_utc = datetime.now(pytz.UTC).replace(tzinfo=None) - timedelta(minutes=max(0, min_minutes_overdue))
        stmt = (
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.status == "pending",
                Reminder.is_fluid_habit.is_(False),
                Reminder.pending_delete_at.is_(None),  # Hide soft-deleted (undo window)
                Reminder.execution_time <= threshold_utc,
                or_(
                    Reminder.completed_for_execution_time.is_(None),
                    Reminder.completed_for_execution_time < Reminder.execution_time,
                ),
            )
            .order_by(Reminder.execution_time.asc())
        )
        if limit is not None:
            stmt = stmt.limit(max(1, int(limit)))
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def search_user_reminders(self, user_id: int, query: str) -> Sequence[Reminder]:
        """3.4: `/find <text>` — case-insensitive substring search over the
        user's pending, non-fluid tasks. Same active-list rules as
        get_user_reminders (hides soft-deleted and already-completed
        recurring cycles)."""
        needle = f"%{query.strip()}%"
        result = await self.session.execute(
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.status == "pending",
                Reminder.is_fluid_habit.is_(False),
                Reminder.pending_delete_at.is_(None),
                Reminder.reminder_text.ilike(needle),
                or_(
                    Reminder.completed_for_execution_time.is_(None),
                    Reminder.completed_for_execution_time < Reminder.execution_time,
                ),
            )
            .order_by(Reminder.execution_time, Reminder.id)
        )
        return result.scalars().all()

    async def get_user_reminders_today(self, user_id: int, user_tz_str: str) -> Sequence[Reminder]:
        """3.4 "today" filter — convenience alias, same query as the daily
        brief's pending-today list."""
        return await self.get_today_tasks_by_status(user_id, user_tz_str, "pending")

    async def get_user_reminders_this_week(self, user_id: int, user_tz_str: str) -> Sequence[Reminder]:
        """3.4 "this week" filter: pending tasks due within the next 7 local
        days (today through +6 days inclusive)."""
        try:
            tz = pytz.timezone(user_tz_str)
        except Exception:
            tz = pytz.UTC

        now_local = datetime.now(tz)
        start_of_day_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_of_day_local + timedelta(days=7)

        start_utc = start_of_day_local.astimezone(pytz.UTC).replace(tzinfo=None)
        end_utc = end_local.astimezone(pytz.UTC).replace(tzinfo=None)

        result = await self.session.execute(
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.status == "pending",
                Reminder.is_fluid_habit.is_(False),
                Reminder.pending_delete_at.is_(None),
                Reminder.execution_time >= start_utc,
                Reminder.execution_time < end_utc,
                or_(
                    Reminder.completed_for_execution_time.is_(None),
                    Reminder.completed_for_execution_time < Reminder.execution_time,
                ),
            )
            .order_by(Reminder.execution_time, Reminder.id)
        )
        return result.scalars().all()

    async def get_user_reminders_recurring(self, user_id: int) -> Sequence[Reminder]:
        """3.4 "recurring" filter — active recurring tasks (habits included,
        since they're also Reminder rows with is_recurring=True)."""
        result = await self.session.execute(
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.status == "pending",
                Reminder.is_fluid_habit.is_(False),
                Reminder.pending_delete_at.is_(None),
                Reminder.is_recurring.is_(True),
                or_(
                    Reminder.completed_for_execution_time.is_(None),
                    Reminder.completed_for_execution_time < Reminder.execution_time,
                ),
            )
            .order_by(Reminder.execution_time, Reminder.id)
        )
        return result.scalars().all()

    async def get_habit_motivation_stats(
        self,
        user_id: int,
        user_tz_str: str,
        *,
        days: int = 7,
    ) -> dict[str, int]:
        """
        Lightweight motivation metrics for habits dashboard.
        """
        days = max(1, int(days))
        try:
            tz = pytz.timezone(user_tz_str)
        except Exception:
            tz = pytz.UTC

        now_local = datetime.now(tz)

        active_result = await self.session.execute(
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.is_recurring.is_(True),
                or_(
                    Reminder.is_habit.is_(True),
                    Reminder.habit_active_due_at.is_not(None),
                    Reminder.habit_last_completed_due_at.is_not(None),
                    Reminder.habit_streak_current > 0,
                    Reminder.habit_streak_best > 0,
                ),
                Reminder.status == "pending",
            )
        )
        active_habits = active_result.scalars().all()

        # 2.5: weekly_done used to count DISTINCT REMINDERS with a
        # completed_at in the window — but completed_at is a single field
        # overwritten on every completion (see mark_done), so a habit
        # completed every day for a week counted as 1, and three different
        # habits completed once each counted as 3. habit_events is the
        # actual per-completion log this dashboard is trying to summarize;
        # count "done" events instead, same source habit_reports.py uses.
        today_local = now_local.date()
        start_local = today_local - timedelta(days=days - 1)
        from bot.database.dao.habit_event import HabitEventDAO

        events = await HabitEventDAO(self.session).get_events_in_range(
            user_id, start_local.isoformat(), today_local.isoformat()
        )
        weekly_done = sum(1 for e in events if e.outcome == "done")

        def _current_streak(h) -> int:
            if getattr(h, "is_fluid_habit", False):
                return max(0, int(h.fluid_streak_current or 0))
            return max(0, int(h.habit_streak_current or 0))

        def _best_streak(h) -> int:
            if getattr(h, "is_fluid_habit", False):
                return max(0, int(h.fluid_streak_best or 0))
            return max(0, int(h.habit_streak_best or 0))

        # 3.2: average EMA habit score across active habits, for the
        # dashboard motivational block — additive to the streak numbers
        # above, not a replacement.
        from bot.services.habit_reports import compute_habit_score

        habit_event_dao = HabitEventDAO(self.session)
        scores = []
        for h in active_habits:
            habit_events = await habit_event_dao.get_events_for_reminder(h.id)
            if habit_events:
                scores.append(compute_habit_score(habit_events))
        avg_score = round(sum(scores) / len(scores)) if scores else 0

        return {
            "active_count": len(active_habits),
            "weekly_done": weekly_done,
            "best_current_streak": max((_current_streak(h) for h in active_habits), default=0),
            "best_ever_streak": max((_best_streak(h) for h in active_habits), default=0),
            "avg_score": avg_score,
        }

    async def get_active_fluid_habits(self, user_id: int) -> Sequence[Reminder]:
        """Return active fluid habits for a user."""
        result = await self.session.execute(
            select(Reminder).where(
                Reminder.user_id == user_id,
                Reminder.status == "pending",
                Reminder.is_fluid_habit.is_(True),
                Reminder.pending_delete_at.is_(None),  # Hide soft-deleted (undo window)
            )
        )
        return result.scalars().all()

    async def mark_fluid_habit_done_today(self, reminder_id: int, user_tz_str: str) -> bool:
        """
        Mark fluid habit as completed for user's current local day.

        Returns True when completion was newly recorded, False if already done today.
        """
        reminder = await self.get_by_id(reminder_id)
        if not reminder or not reminder.is_fluid_habit:
            return False

        try:
            tz = pytz.timezone(user_tz_str)
        except Exception:
            tz = pytz.UTC

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
        """Reset current fluid streak when user has already missed more than one day."""
        reminder = await self.get_by_id(reminder_id)
        if not reminder or not reminder.is_fluid_habit:
            return

        last = reminder.fluid_last_completed_date
        if not last:
            return

        try:
            tz = pytz.timezone(user_tz_str)
        except Exception:
            tz = pytz.UTC

        today_local = datetime.now(tz).date()
        try:
            last_date = datetime.strptime(last, "%Y-%m-%d").date()
        except ValueError:
            return

        if (today_local - last_date).days > 1 and int(reminder.fluid_streak_current or 0) != 0:
            reminder.fluid_streak_current = 0
            await self.session.flush()


    async def update_execution_time(
        self, reminder_id: int, new_time: datetime
    ) -> None:
        """
        Update execution_time for a reminder.
        
        Used by recurring task reschedule logic - when APScheduler calculates
        the next occurrence, we update this field and re-schedule the job.
        
        Args:
            reminder_id: Primary key of reminder to update
            new_time: New execution time (timezone-aware datetime)
            
        Returns:
            None
            
        Side Effects:
          Updates execution_time field in database
        
        Example:
            >>> await dao.update_execution_time(456, datetime(2024, 3, 27, 10, 0))
        """
        reminder = await self.get_by_id(reminder_id)
        if reminder:
            reminder.execution_time = new_time
            await self.session.flush()
