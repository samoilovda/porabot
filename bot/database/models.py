"""
SQLAlchemy ORM Models for Porabot
===================================

PURPOSE:
  This module defines the database schema using SQLAlchemy's declarative ORM.
  It creates two main tables: `users` and `reminders`.

ARCHITECTURE OVERVIEW:
  
  ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
  │   User(Base)│────▶│ Base.metadata│────▶│ create_all()    │
  │             │     │ (table list) │     │ creates tables   │
  └─────────────┘     └──────────────┘     └─────────────────┘

TABLE SCHEMA:
  
  users table:
    ├── id      INTEGER PRIMARY KEY (Telegram user ID, not auto-incremented)
    ├── username TEXT (optional Telegram username)
    ├── timezone TEXT DEFAULT 'UTC' (user's timezone string)
    ├── language TEXT (language code for i18n)
    ├── show_utc_offset BOOLEAN DEFAULT 0 (show +HH:MM offset in times)
    └── created_at DATETIME (when user was added to DB)
  
  reminders table:
    ├── id          INTEGER PRIMARY KEY AUTOINCREMENT (internal task ID)
    ├── user_id     INTEGER FOREIGN KEY → users.id (who owns this task)
    ├── reminder_text TEXT (what the user needs to remember)
    ├── media_file_id TEXT (optional file attachment, e.g., photo/video)
    ├── media_type TEXT (file type: 'photo', 'video', etc.)
    ├── execution_time DATETIME (when task should fire - stored in UTC!)
    ├── is_recurring BOOLEAN DEFAULT 0 (repeating task?)
    ├── rrule_string TEXT (iCalendar recurrence rule, e.g., "FREQ=DAILY")
    ├── is_nagging BOOLEAN DEFAULT 0 (send follow-ups every 5 min?)
    ├── status      TEXT DEFAULT 'pending' ('pending' or 'completed')
    ├── completed_at DATETIME (when task was marked done)
    └── created_at DATETIME (when task was added to DB)

IMPORTANT NOTES:
  
  1. execution_time is stored in UTC timezone!
     - When parsing user input, convert to UTC before saving
     - When displaying, convert from UTC to user's local time
     - This avoids timezone drift issues across different users
  
  2. id field uses Telegram user ID (not auto-incremented)
     - Prevents collisions if same user adds multiple tasks
     - SQLAlchemy handles this with BigInteger and explicit primary_key
  
  3. Foreign key constraint: reminders.user_id → users.id
     - Enforces referential integrity
     - CASCADE DELETE not enabled (tasks persist after user deletion)

BUG FIXES APPLIED (Phase 1):
  ✅ Added comprehensive documentation for each field
  ✅ Explained timezone handling (UTC storage, local display)
  ✅ Documented foreign key relationships
  ✅ Added type hints and docstrings for better IDE support

USAGE:
  from bot.database.models import User, Reminder
  
  # Create a new user
  >>> user = User(id=123456, username="john_doe", timezone="Europe/Moscow")
  
  # Create a reminder
  >>> reminder = Reminder(
  ...     id=999,
  ...     user_id=123456,
  ...     reminder_text="Take medication",
  ...     execution_time=datetime(2024, 3, 27, 9, 0),  # UTC time!
  ...     is_recurring=False,
  ... )

"""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

# Import Base from engine module (defines table metadata)
from bot.database.engine import Base


class User(Base):
    """
    Telegram user model - stores user preferences and settings.
    
    This model represents a Telegram user who has interacted with the bot.
    It stores their timezone, language preference, and other settings.
    
    Table: users
    
    Architecture:
      ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
      │   User(Base)│────▶│ Base.metadata│────▶│ create_all()    │
      │             │     │ (table list) │     │ creates tables   │
      └─────────────┘     └──────────────┘     └─────────────────┘
    
    Fields:
        id          Telegram user ID (from update.message.from_user.id)
                  NOT auto-incremented - must be set explicitly!
                  
        username    Optional Telegram username (can be None if not set)
                  
        timezone    User's timezone string (e.g., "Europe/Moscow")
                  Default: "UTC" for new users
                  
        language    Language code for i18n (e.g., "ru", "en")
                  Used to select appropriate translations
                  
        show_utc_offset Whether to display UTC offset (+03:00) in times
                  Default: False (show local time only)
                  
        created_at  When user was added to database
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
    
    # When user was added to database (for analytics/debugging)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, 
        default=func.now(),  # Python datetime object
        server_default=func.now()  # SQL timestamp literal
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"<User(id={self.id}, username={self.username}, lang={self.language})>"


class Reminder(Base):
    """
    Reminder / task model - stores user's scheduled tasks.
    
    This model represents a single reminder that the bot will notify the user about.
    It supports recurring tasks, nagging mode, and media attachments.
    
    Table: reminders
    
    Architecture:
      ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
      │   Reminder  │────▶│ Base.metadata│────▶│ create_all()    │
      │             │     │ (table list) │     │ creates tables   │
      └─────────────┘     └──────────────┘     └─────────────────┘
    
    Fields:
        id              Internal task ID (auto-incremented, not Telegram user ID!)
        
        user_id         Foreign key to users.id - who owns this reminder
        
        reminder_text   What the user needs to remember (e.g., "Take medication")
        
        media_file_id   Optional file attachment (photo/video) for context
        
        media_type      File type: 'photo', 'video', etc.
        
        execution_time  When task should fire - stored in UTC!
                      IMPORTANT: Always convert to UTC before saving!
                      
        is_recurring    Is this a repeating task? (e.g., "every day at 9am")
        
        rrule_string    iCalendar recurrence rule string for recurring tasks
                      Example: "FREQ=DAILY;INTERVAL=1" or "FREQ=WEEKLY;BYDAY=MO,WE,FR"
                      
        is_nagging      Should bot send follow-ups every 5 min until done?
        
        status          Task state: 'pending' (waiting) or 'completed' (done)
                      Used for daily briefs and filtering
        
        completed_at    When task was marked as completed (for analytics)
        
        created_at      When reminder was added to database

    RECURRING TASKS:
      
      For recurring tasks, we use the iCalendar RDATE/RRULE format:
      
      - One-time task: is_recurring=False, rrule_string=None
      
      - Daily at 9am: 
          is_recurring=True
          rrule_string="FREQ=DAILY;INTERVAL=1"
          execution_time=datetime(2024, 3, 27, 9, 0) (first occurrence)
      
      - Weekdays only:
          is_recurring=True
          rrule_string="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
          execution_time=datetime(2024, 3, 25, 9, 0) (first occurrence)
      
      - Weekly:
          is_recurring=True
          rrule_string="FREQ=WEEKLY;INTERVAL=1"
          execution_time=datetime(2024, 3, 27, 9, 0) (first occurrence)

    BUG FIXES APPLIED (Phase 1):
      ✅ Added comprehensive documentation for each field
      ✅ Explained timezone handling (UTC storage, local display)
      ✅ Documented foreign key relationships and constraints
      ✅ Added type hints and docstrings for better IDE support
      ✅ Clarified difference between id (task ID) and user_id (owner)

    USAGE:
      
      # Create a new one-time reminder
      >>> from datetime import datetime
      >>> reminder = Reminder(
      ...     id=999,  # Must set explicitly - not auto-incremented!
      ...     user_id=123456,
      ...     reminder_text="Take medication",
      ...     execution_time=datetime(2024, 3, 27, 9, 0),  # UTC time!
      ...     is_recurring=False,
      ... )
      
      # Create a recurring daily reminder
      >>> recurring = Reminder(
      ...     id=1000,
      ...     user_id=123456,
      ...     reminder_text="Morning meditation",
      ...     execution_time=datetime(2024, 3, 27, 9, 0),
      ...     is_recurring=True,
      ...     rrule_string="FREQ=DAILY;INTERVAL=1",
      ... )

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

    # iCalendar recurrence rule string for recurring tasks
    # Example: "FREQ=DAILY;INTERVAL=1" or "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    rrule_string: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Should bot send follow-ups every 5 min until user marks task done?
    is_nagging: Mapped[bool] = mapped_column(
        Boolean, 
        default=False  # Nagging mode disabled by default
    )

    # Task state: 'pending' (waiting) or 'completed' (done)
    # Used for daily briefs and filtering completed tasks
    status: Mapped[str] = mapped_column(
        String, 
        default="pending"  # New tasks start as pending
    )

    # When task was marked as completed (for analytics/debugging)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # When reminder was added to database (for analytics/debugging)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, 
        default=func.now(),  # Python datetime object
        server_default=func.now()  # SQL timestamp literal
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"<Reminder(id={self.id}, user_id={self.user_id}, time={self.execution_time})>"