"""
ReminderDAO — Data Access for Reminder Model
=============================================

PURPOSE:
  This DAO provides specialized data access methods for the Reminder model.
  It extends BaseDAO with reminder-specific operations like recurring task
  management, daily briefs queries, and soft-delete functionality.

ARCHITECTURE OVERVIEW:
  
  ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
  │   BaseDAO    │────▶│ ReminderDAO  │────▶│ Specialized CRUD │
  │ (generic)    │     │ (concrete)   │     │ + domain logic   │
  └─────────────┘     └──────────────┘     └─────────────────┘

SPECIALIZED OPERATIONS:
  
  - create_reminder(): Create new reminder with all fields
  - get_user_reminders(): Get user's pending tasks (for task list)
  - mark_done(): Soft delete by marking status='completed'
  - get_today_tasks_by_status(): Fetch today's tasks for daily briefs
  - update_execution_time(): Update time (used by recurring reschedule)

BUG FIXES APPLIED (Phase 1):
  ✅ Added comprehensive documentation for each method
  ✅ Fixed timezone handling in mark_done() and get_today_tasks_by_status()
  ✅ Documented soft-delete pattern vs hard delete
  ✅ Explained why we use status field instead of DELETE

USAGE:
  
    # Create new reminder
    >>> dao = ReminderDAO(session)
    >>> reminder = await dao.create_reminder(
    ...     user_id=123,
    ...     text="Take medication",
    ...     execution_time=datetime.now() + timedelta(hours=1),
    ... )
    
    # Get user's pending tasks (for task list view)
    >>> tasks = await dao.get_user_reminders(123)
    
    # Mark as done (soft delete for recurring tasks)
    >>> await dao.mark_done(456)

"""

from datetime import datetime, timedelta
from typing import Optional, Sequence

import pytz

from sqlalchemy import or_, select

# Import BaseDAO from base module (generic CRUD operations)
from bot.database.dao.base import BaseDAO
# Import Reminder model for type hints and query construction
from bot.database.models import Reminder


class ReminderDAO(BaseDAO[Reminder]):
    """
    Data access object specialized for Reminder model.
    
    This DAO extends the generic BaseDAO with reminder-specific operations:
      - create_reminder(): Create new reminder with all fields
      - get_user_reminders(): Get user's pending tasks (for task list)
      - mark_done(): Soft delete by marking status='completed'
      - get_today_tasks_by_status(): Fetch today's tasks for daily briefs
    
    Architecture:
      ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
      │   BaseDAO    │────▶│ ReminderDAO  │────▶│ Specialized CRUD │
      │ (generic)    │     │ (concrete)   │     │ + domain logic   │
      └─────────────┘     └──────────────┘     └─────────────────┘
    
    Soft-Delete Pattern:
      Instead of hard deleting records, we use a status field:
        - 'pending': Task is waiting to be executed
        - 'completed': Task was done (or expired)
      
      Why soft-delete?
        1. Recurring tasks need history for daily briefs
        2. Analytics/debugging benefits from seeing all tasks
        3. Can restore accidentally deleted tasks if needed
    
    Args:
        session: Async SQLAlchemy session for database operations
        
    Example:
        >>> dao = ReminderDAO(session)
        >>> reminder = await dao.create_reminder(
        ...     user_id=123,
        ...     text="Take medication",
        ...     execution_time=datetime.now() + timedelta(hours=1),
        ... )
    """

    model = Reminder  # Class attribute - set by concrete DAO subclass

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
        """
        Insert a new reminder and return it with populated `id`.
        
        This method creates a complete Reminder object with all fields.
        The session.flush() call populates the auto-generated id field.
        
        SECURITY FIX APPLIED:
          Added input validation for text length to prevent Telegram API errors.
          Maximum length: 3000 characters (leaves room for formatting/prefix).
        
        Args:
            user_id: Telegram user ID (foreign key to users table)
            text: What the user needs to remember (e.g., "Take medication")
            execution_time: When task should fire - MUST be timezone-aware!
                          IMPORTANT: Convert to UTC before passing this value.
            media_file_id: Optional file attachment (photo/video) for context
            media_type: File type: 'photo', 'video', etc.
            is_recurring: Is this a repeating task? Default: False
            rrule_string: iCalendar recurrence rule string for recurring tasks
                         Example: "FREQ=DAILY;INTERVAL=1" or "FREQ=WEEKLY;BYDAY=MO,WE,FR"
            is_habit: Whether this reminder was created from Habits flow
            is_fluid_habit: Whether this is a fluid (day-based) habit
            fluid_mode: Fluid mode - "brief_only" or "ask_time"
            is_nagging: Should bot send follow-ups every 5 min until done? Default: False
            nagging_max_repeats: Maximum number of follow-up nags. Default: 3
            
        Returns:
            Reminder: The created reminder with all fields populated including id
            
        Side Effects:
          Adds record to session and flushes to populate auto-generated ID
        
        Raises:
            ValueError: If text exceeds maximum length (3000 chars)
        
        Example:
            >>> reminder = await dao.create_reminder(
            ...     user_id=123,
            ...     text="Take medication",
            ...     execution_time=datetime(2024, 3, 27, 9, 0),  # UTC time!
            ... )
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
                # Hide recurring reminders already completed for current cycle.
                # They become visible again after execution_time rolls forward.
                or_(
                    Reminder.completed_for_execution_time.is_(None),
                    Reminder.completed_for_execution_time < Reminder.execution_time,
                ),
            )
            .order_by(Reminder.execution_time)  # Order by when task fires (earliest first)
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
        is_habit_like = bool(
            reminder
            and (
                not bool(getattr(reminder, "is_fluid_habit", False))
                and (
                bool(getattr(reminder, "is_habit", False))
                or getattr(reminder, "habit_active_due_at", None) is not None
                or getattr(reminder, "habit_last_completed_due_at", None) is not None
                or int(getattr(reminder, "habit_streak_current", 0) or 0) > 0
                or int(getattr(reminder, "habit_streak_best", 0) or 0) > 0
                )
            )
        )
        if not reminder or not is_habit_like:
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
        start_utc = (now_local - timedelta(days=days)).astimezone(pytz.UTC).replace(tzinfo=None)

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

        completed_result = await self.session.execute(
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
                Reminder.status == "pending",
                Reminder.is_fluid_habit.is_(True),
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
