"""SQLAlchemy ORM models for Porabot."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.engine import Base


class User(Base):
    """Telegram user (table 'users')."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    timezone: Mapped[str] = mapped_column(String, default="UTC")
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    show_utc_offset: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username}, lang={self.language})>"


class Reminder(Base):
    """Reminder / task (table 'reminders')."""

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    reminder_text: Mapped[str] = mapped_column(String, nullable=False)

    # Media attachments
    media_file_id: Mapped[str | None] = mapped_column(String, nullable=True)
    media_type: Mapped[str | None] = mapped_column(String, nullable=True)

    execution_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Recurrence settings
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    rrule_string: Mapped[str | None] = mapped_column(String, nullable=True)

    # Nagging mode
    is_nagging: Mapped[bool] = mapped_column(Boolean, default=False)

    # Added fields for daily briefs / soft-delete
    status: Mapped[str] = mapped_column(String, default="pending")  # 'pending', 'completed'
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Reminder(id={self.id}, user_id={self.user_id}, time={self.execution_time})>"
