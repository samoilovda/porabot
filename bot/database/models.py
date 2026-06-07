"""SQLAlchemy ORM models for Porabot.

Two tables: ``users`` (preferences) and ``reminders`` (scheduled tasks).
``execution_time`` is always stored as naive UTC; convert on display.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.engine import Base


class ReminderStatus(str, Enum):
    """Allowed values for ``Reminder.status``.

    Using ``str`` mixin so SQLAlchemy stores plain strings and existing DB rows
    remain compatible without a migration.
    """
    PENDING = "pending"
    COMPLETED = "completed"


class User(Base):
    """Telegram user preferences (timezone, language, daily brief times, quiet hours)."""

    __tablename__ = "users"

    __table_args__ = (
        Index("idx_users_timezone", "timezone"),
        Index("idx_users_language", "language"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    timezone: Mapped[str] = mapped_column(String, default="UTC")
    language: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    show_utc_offset: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    quiet_hours_start: Mapped[str] = mapped_column(String, default="23:00", server_default="23:00")
    quiet_hours_end: Mapped[str] = mapped_column(String, default="07:00", server_default="07:00")

    missed_recovery_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    last_missed_recovery_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    briefs_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    morning_brief_time: Mapped[str] = mapped_column(String, default="09:00", server_default="09:00")
    evening_brief_time: Mapped[str] = mapped_column(String, default="23:00", server_default="23:00")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now())

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username}, lang={self.language})>"


class Reminder(Base):
    """Scheduled task / reminder owned by a Telegram user.

    ``status`` is one of ``ReminderStatus.PENDING`` / ``ReminderStatus.COMPLETED``.
    Recurring reminders stay ``PENDING``; ``completed_for_execution_time`` hides
    the current cycle from active lists until ``execution_time`` rolls forward.
    """

    __tablename__ = "reminders"

    __table_args__ = (
        Index("idx_reminders_user_id", "user_id"),
        Index("idx_reminders_execution_time", "execution_time"),
        Index("idx_reminders_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    reminder_text: Mapped[str] = mapped_column(String, nullable=False)
    media_file_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Stored as naive UTC; always convert before display.
    execution_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    rrule_string: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    is_habit: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    is_fluid_habit: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    fluid_mode: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    habit_streak_current: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    habit_streak_best: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    habit_active_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    habit_last_completed_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    fluid_streak_current: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    fluid_streak_best: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    fluid_last_completed_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    fluid_planned_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    fluid_planned_time: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    is_nagging: Mapped[bool] = mapped_column(Boolean, default=False)
    nagging_max_repeats: Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False)
    nagging_sent_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    # Tracks the single active nag message so the scheduler can delete-and-replace it.
    last_nag_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_nag_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    completed_for_execution_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_completion_note: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    status: Mapped[str] = mapped_column(String, default=ReminderStatus.PENDING)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now())

    def __repr__(self) -> str:
        return f"<Reminder(id={self.id}, user_id={self.user_id}, time={self.execution_time})>"
