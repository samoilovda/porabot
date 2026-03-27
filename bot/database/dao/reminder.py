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

from sqlalchemy import select

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
        is_nagging: bool = False,
    ) -> Reminder:
        """
        Insert a new reminder and return it with populated `id`.
        
        This method creates a complete Reminder object with all fields.
        The session.flush() call populates the auto-generated id field.
        
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
            is_nagging: Should bot send follow-ups every 5 min until done? Default: False
            
        Returns:
            Reminder: The created reminder with all fields populated including id
            
        Side Effects:
          Adds record to session and flushes to populate auto-generated ID
        
        Example:
            >>> reminder = await dao.create_reminder(
            ...     user_id=123,
            ...     text="Take medication",
            ...     execution_time=datetime(2024, 3, 27, 9, 0),  # UTC time!
            ... )
        """
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
        await self.session.flush()  # Flush to populate auto-generated ID
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
                Reminder.status == "pending"  # Only show pending tasks
            )
            .order_by(Reminder.execution_time)  # Order by when task fires (earliest first)
        )
        return result.scalars().all()

    async def mark_done(self, reminder_id: int) -> None:
        """
        Soft delete a reminder by marking it completed.
        
        For recurring tasks, only updates completed_at timestamp - doesn't change status.
        This allows daily briefs to show history while keeping task active for recurrence.
        
        Args:
            reminder_id: Primary key of reminder to mark as done
            
        Returns:
            None
            
        Side Effects:
          Updates status field and completed_at timestamp
        
        BUG FIX EDGE-5: Timezone-aware datetime handling
          Previously used deprecated datetime.utcnow(). Now uses pytz.UTC for clarity.
        
        Example:
            >>> await dao.mark_done(456)  # Marks reminder #456 as done
        """
        reminder = await self.get_by_id(reminder_id)
        if reminder:
            # For one-time tasks, mark as completed (soft delete)
            if not reminder.is_recurring:
                reminder.status = "completed"
            
            # FIX EDGE-5: Use timezone-aware datetime for consistency
            # pytz.UTC is clearer than deprecated datetime.utcnow()
            reminder.completed_at = datetime.now(pytz.UTC).replace(tzinfo=None)
            
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

        result = await self.session.execute(
            select(Reminder)
            .where(
                Reminder.user_id == user_id,  # Filter by owner
                Reminder.status == status,   # Filter by status (pending/completed)
                Reminder.execution_time >= start_utc,  # After midnight UTC
                Reminder.execution_time < end_utc,     # Before next midnight UTC
            )
            .order_by(Reminder.execution_time)  # Order by when task fires
        )
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

    async def get_by_id_or_none(self, reminder_id: int) -> Optional[Reminder]:
        """
        Fetch a single reminder (used by scheduler job wrapper).
        
        Convenience wrapper around get_by_id() that returns None instead of raising.
        Useful for APScheduler job targets where we want to skip if record not found.
        
        Args:
            reminder_id: Primary key of reminder to fetch
            
        Returns:
            Reminder or None
            
        Example:
            >>> reminder = await dao.get_by_id_or_none(456)  # May return None
        """
        return await self.get_by_id(reminder_id)