"""
SQLAlchemy ORM models for Porabot: `User`, `Reminder`, `HabitEvent`.

Invariants that hold across this whole module:
  - Every stored datetime (execution_time, completed_at, habit_active_due_at,
    due_at, ...) is naive UTC. Convert to the user's local timezone only when
    rendering; convert back to naive UTC before writing.
  - `User.id` / `Reminder.user_id` are the Telegram user id (not
    auto-incremented). `Reminder.id` and `HabitEvent.id` are ordinary
    auto-incrementing surrogate keys.
  - A habit is a `Reminder` row with `is_habit=True` (fixed-time) or
    `is_fluid_habit=True` (day-based, no fixed time) — not a separate table.
    See `is_habit_like()` at the bottom of this module for the fixed-habit
    detection used throughout the codebase.
  - Soft-migrations for columns added after initial release live in
    `bot/database/engine.py`'s `init_db`, not here — this file is always the
    CURRENT schema, not a migration history.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

# Import Base from engine module (defines table metadata)
from bot.database.engine import Base


def _utcnow_naive() -> datetime:
    """Python-side default for created_at — UTC regardless of DB dialect (W6)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    """Telegram user — preferences and settings for reminders, habits, and
    the scheduled jobs that check them (briefs, missed-task recovery, habit
    reports). `id` is the Telegram user id itself, not auto-incremented.
    """

    __tablename__ = "users"
    
    __table_args__ = (
        Index('idx_users_timezone', 'timezone'),      # For timezone-based queries
        Index('idx_users_language', 'language'),      # For language filtering
    )

    # Telegram user ID - NOT auto-incremented, must be set from update!
    id: Mapped[int] = mapped_column(
        BigInteger, 
        primary_key=True, 
        autoincrement=False  # Use Telegram ID directly (no auto-inc)
    )
    
    # Optional Telegram username (can be None if not set by user)
    username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # User's timezone string (e.g., "Europe/Moscow", "America/New_York")
    # Default: UTC for new users who haven't set their timezone yet
    timezone: Mapped[str] = mapped_column(
        String, 
        default="UTC"  # Safe default - converts all times to UTC internally
    )
    
    # Language code for i18n (e.g., "ru", "en")
    # Used to select appropriate translations via get_l10n()
    language: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Whether to show UTC offset (+HH:MM) in formatted times
    # Default: False (show local time only for better UX)
    show_utc_offset: Mapped[bool] = mapped_column(
        Boolean, 
        default=False,  # Don't clutter messages with +03:00 by default
        server_default="0"  # SQLite string literal for boolean
    )

    # Quiet hours / sleep mode settings.
    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    quiet_hours_start: Mapped[str] = mapped_column(String, default="23:00", server_default="23:00")
    quiet_hours_end: Mapped[str] = mapped_column(String, default="07:00", server_default="07:00")
    # 3.5: separate weekend window ("Sat/Sun not before 10:00") — used
    # instead of the weekday window above when enabled.
    quiet_hours_weekend_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    quiet_hours_weekend_start: Mapped[str] = mapped_column(String, default="23:00", server_default="23:00")
    quiet_hours_weekend_end: Mapped[str] = mapped_column(String, default="10:00", server_default="10:00")
    # 3.5: "habits can wake, regular tasks can't" — when set, is_quiet_hours
    # returns False for habit-like reminders regardless of the window.
    quiet_hours_habits_exempt: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    # Missed-task recovery settings.
    missed_recovery_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    missed_recovery_time: Mapped[str] = mapped_column(String, default="10:00", server_default="10:00")
    last_missed_recovery_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Local date (YYYY-MM-DD) the morning/evening brief was last sent — used to
    # gate briefs by a "time has passed and not sent yet today" window instead
    # of an exact-minute string match, which is fragile against job delay/jitter.
    last_morning_brief_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_evening_brief_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Message id of the morning brief currently pinned in the user's chat —
    # set when the brief is sent, cleared once the evening summary unpins it.
    # None means nothing of ours is currently pinned.
    pinned_brief_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Custom Daily Briefs Settings
    briefs_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    morning_brief_time: Mapped[str] = mapped_column(String, default="09:00", server_default="09:00")
    evening_brief_time: Mapped[str] = mapped_column(String, default="23:00", server_default="23:00")

    # Weekly/monthly habit reports settings.
    habit_reports_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    # Python datetime.weekday(): Mon=0 .. Sun=6. Default Sunday.
    habit_report_weekday: Mapped[int] = mapped_column(Integer, default=6, server_default="6")
    habit_report_time: Mapped[str] = mapped_column(String, default="23:50", server_default="23:50")
    # Local date (YYYY-MM-DD) the habit report was last sent — without this,
    # an exact-minute match with no persisted dedup flag would resend the
    # report if the job ever fired more than once during that minute.
    last_habit_report_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # 4.4: secret, revocable token for this user's read-only .ics calendar
    # feed (GET /ics/<token>.ics). NULL until the user first opens the
    # feed-link screen in Settings — UserDAO.ensure_ics_feed_token() lazily
    # creates it (secrets.token_urlsafe, effectively collision-free, so no
    # DB-level UNIQUE is enforced here — this is a soft-migrated column,
    # see engine.py, and SQLite's ALTER TABLE can't add one after the fact
    # anyway). Regenerating it (also in Settings) invalidates the previous
    # URL immediately, since the lookup is by exact token match.
    ics_feed_token: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # When user was added to database (for analytics/debugging)
    # Python-side default wins on ORM inserts (SQLAlchemy always applies it),
    # so this is UTC regardless of dialect — server_default=func.now() is a
    # fallback for rows inserted by raw SQL only, and is UTC on SQLite but the
    # server's local time on Postgres. Code that treats created_at as UTC
    # (e.g. habit_sweeper) depends on the Python-side default actually firing.
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_utcnow_naive,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"<User(id={self.id}, username={self.username}, lang={self.language})>"


class Reminder(Base):
    """One-off or recurring task — and, via is_habit/is_fluid_habit, a habit.

    `id` is an ordinary auto-incrementing surrogate key (unlike `User.id`).
    Recurring tasks use iCalendar RRULE strings, e.g.
    `FREQ=DAILY;INTERVAL=1`, `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR` — see
    bot/utils/time_ext.py's `next_occurrence_utc` for how the next
    occurrence is computed (in the user's local timezone, to stay
    DST-correct). Per-field comments below cover the habit-tracking and
    delivery-retry columns, which make up most of this model.
    """

    __tablename__ = "reminders"
    
    __table_args__ = (
        Index('idx_reminders_user_id', 'user_id'),           # For user-specific queries
        Index('idx_reminders_execution_time', 'execution_time'),  # For time-based filtering
        Index('idx_reminders_status', 'status'),              # For status filtering (pending/completed)
    )

    # Internal task ID - auto-incremented (NOT Telegram user ID!)
    id: Mapped[int] = mapped_column(
        primary_key=True, 
        autoincrement=True  # Auto-increment for internal task ID
    )
    
    # Foreign key to users table - who owns this reminder
    user_id: Mapped[int] = mapped_column(
        BigInteger, 
        ForeignKey("users.id"),  # Reference users.id column
        nullable=False  # Every reminder must have an owner
    )
    
    # What the user needs to remember (e.g., "Take medication at 9am")
    reminder_text: Mapped[str] = mapped_column(
        String, 
        nullable=False  # Required - can't create empty reminders
    )

    # Optional media attachment for context (photo/video)
    media_file_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Media type: 'photo', 'video', etc.
    media_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # When task should fire - stored in UTC timezone!
    # IMPORTANT: Always convert to UTC before saving to avoid timezone drift
    execution_time: Mapped[datetime] = mapped_column(
        DateTime, 
        nullable=False  # Required - can't schedule without a time
    )

    # Is this a repeating task? (e.g., "every day at 9am")
    is_recurring: Mapped[bool] = mapped_column(
        Boolean, 
        default=False  # One-time tasks are the default
    )

    # True only for reminders created through Habits flow.
    is_habit: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
        nullable=False,
    )
    # Daily "fluid" habits: not fixed time, completed within local day.
    is_fluid_habit: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
        nullable=False,
    )
    # Fluid reminder mode: "brief_only" | "ask_time".
    fluid_mode: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # iCalendar recurrence rule string for recurring tasks
    # Example: "FREQ=DAILY;INTERVAL=1" or "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    rrule_string: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Habit streak tracking.
    habit_streak_current: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    habit_streak_best: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    # Due timestamp of the current active habit cycle (UTC naive).
    habit_active_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Due timestamp of the last completed habit cycle (UTC naive).
    habit_last_completed_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # True right after an Undo reverts the cycle at habit_last_completed_due_at.
    # Lets a re-completion of that same cycle restore the exact streak it had
    # before the revert instead of restarting the chain at 1 (there is no
    # stored history of prior cycles to recompute the gap-based streak from).
    habit_undo_pending: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
        nullable=False,
    )
    # Fluid habit streaks are day-based (local date strings YYYY-MM-DD).
    fluid_streak_current: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    fluid_streak_best: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    fluid_last_completed_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    fluid_planned_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    fluid_planned_time: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Should bot send follow-ups every 5 min until user marks task done?
    is_nagging: Mapped[bool] = mapped_column(
        Boolean, 
        default=False  # Nagging mode disabled by default
    )

    # Maximum number of follow-up nagging messages after the main reminder.
    # Example: 3 means user receives up to 3 extra nudges.
    nagging_max_repeats: Mapped[int] = mapped_column(
        Integer,
        default=3,
        server_default="3",
        nullable=False,
    )

    # Number of follow-up nags already sent in the current cycle.
    # Reset to 0 every time the main reminder fires.
    nagging_sent_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )

    # Consecutive TelegramForbiddenError count (user blocked the bot).
    # Reset to 0 on any successful send; once it reaches
    # SchedulerService.FORBIDDEN_STRIKES_LIMIT, this reminder stops being
    # rescheduled (recurrence and nagging both stop) instead of retrying
    # forever against a user who will never receive it.
    forbidden_strikes: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )

    # Last Telegram message used for nagging rotation (single active nag message).
    # When sending a new nag, scheduler deletes this message first (best effort),
    # then stores the new message id here.
    last_nag_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_nag_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # When the main (non-nagging) notification for the CURRENT execution_time
    # cycle was last sent (naive UTC). One-off reminders stay status='pending'
    # until the user taps Done, so reconcile_jobs_with_db needs this to tell
    # "never delivered, needs a catch-up job" apart from "already delivered,
    # just waiting on the user" after a restart.
    last_fired_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Consecutive retryable/permanent delivery failures (timeout, network
    # error, bad request) for the CURRENT cycle — drives the retry job's
    # backoff schedule. Reset to 0 on a successful send. Capped: once
    # exhausted, no further retry job is scheduled and this cycle relies on
    # reconcile_jobs_with_db (last_fired_at stays unset) to catch up on the
    # next restart instead of retrying forever against a payload that keeps
    # failing identically.
    send_retry_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )

    # Naive UTC deadline for a soft-deleted reminder: NULL means "not
    # deleted". Set (persisted, committed) the instant the user taps Delete,
    # so an Undo tap can still restore it, and cleared back to NULL by Undo.
    # A cron sweep (see delete_cleanup.py) hard-deletes the row once this
    # deadline passes. Every query that lists/schedules/reconciles active
    # reminders must exclude rows where this is set — otherwise a process
    # restart during the undo window resurrects a task the user just
    # deleted, because reconcile_jobs_with_db only looks at status='pending'
    # and finds no scheduler job for it.
    pending_delete_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # For recurring reminders, stores the execution_time value that user has
    # already completed. Active list hides reminders when this equals current
    # execution_time, and shows them again after recurrence rolls forward.
    completed_for_execution_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )

    # Optional note attached when user marks task done.
    last_completion_note: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # 4.3: tags and priority parsed out of the phrase at creation time
    # ("купить молоко #дом !2"). tags is a comma-separated lowercase list —
    # no separate table, this is a flat filter, not a taxonomy. priority is
    # 1 (highest) .. 3 (lowest); NULL means "no priority set". Both affect
    # sort order in the task list and the morning brief (NULL priority
    # sorts last) — see ReminderDAO's ordering helpers.
    tags: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    priority: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Task state: 'pending' (waiting) or 'completed' (done)
    # Used for daily briefs and filtering completed tasks
    status: Mapped[str] = mapped_column(
        String, 
        default="pending"  # New tasks start as pending
    )

    # When task was marked as completed (for analytics/debugging)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # When reminder was added to database (for analytics/debugging).
    # See User.created_at above — Python-side default keeps this UTC on both
    # SQLite and Postgres; habit_sweeper relies on that (W6).
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_utcnow_naive,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"<Reminder(id={self.id}, user_id={self.user_id}, time={self.execution_time})>"


class HabitEvent(Base):
    """
    One row per resolved habit cycle (done / not_today / missed).

    This is the event log that streak counters on Reminder never provided —
    it powers weekly/monthly habit reports and lets a completion be undone
    without guessing which cycle it belonged to.

    `habit_text` is a snapshot, not a join: habits are hard-deleted, so the
    event log must survive a deleted or renamed habit without dangling FKs
    or rewriting history.

    `cycle_key` makes recording idempotent per (reminder_id, cycle_key):
      - fixed habits:  f"due:{int(due_at_utc.timestamp())}"
      - fluid habits:  f"day:{local_date}"
    """

    __tablename__ = "habit_events"

    __table_args__ = (
        UniqueConstraint("reminder_id", "cycle_key", name="uq_habit_events_cycle"),
        Index("idx_habit_events_user_date", "user_id", "local_date"),
        Index("idx_habit_events_reminder", "reminder_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    reminder_id: Mapped[int] = mapped_column(Integer, ForeignKey("reminders.id"), nullable=False)
    habit_text: Mapped[str] = mapped_column(String, nullable=False)
    cycle_key: Mapped[str] = mapped_column(String, nullable=False)
    local_date: Mapped[str] = mapped_column(String, nullable=False)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # done | not_today | missed
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    # button | wrapup | auto
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_utcnow_naive,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<HabitEvent(reminder_id={self.reminder_id}, outcome={self.outcome}, local_date={self.local_date})>"


def is_habit_like(reminder) -> bool:
    """Detect reminders that participate in fixed-time habit streak tracking.

    Fluid habits (day-based, no fixed time) are tracked separately and are
    never habit-like in this sense.
    """
    if getattr(reminder, "is_fluid_habit", False):
        return False
    return bool(
        getattr(reminder, "is_habit", False)
        or getattr(reminder, "habit_active_due_at", None) is not None
        or getattr(reminder, "habit_last_completed_due_at", None) is not None
        or int(getattr(reminder, "habit_streak_current", 0) or 0) > 0
        or int(getattr(reminder, "habit_streak_best", 0) or 0) > 0
    )
