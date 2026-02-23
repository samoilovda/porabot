from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class User(Base):
    """
    Модель пользователя Telegram бота (таблица 'users').
    """
    __tablename__ = "users"

    # Telegram ID - используем BigInteger, так как ID могут превышать Integer
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    timezone: Mapped[str] = mapped_column(String, default="UTC")
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username})>"


class Reminder(Base):
    """
    Модель напоминания (таблица 'reminders').
    """
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    
    reminder_text: Mapped[str] = mapped_column(String, nullable=False)
    
    # Поля для медиа-вложений
    media_file_id: Mapped[str | None] = mapped_column(String, nullable=True)
    media_type: Mapped[str | None] = mapped_column(String, nullable=True)
    
    execution_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    
    # Настройки повтора
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    rrule_string: Mapped[str | None] = mapped_column(String, nullable=True)
    
    # "Назойливый" режим
    is_nagging: Mapped[bool] = mapped_column(Boolean, default=False)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Reminder(id={self.id}, user_id={self.user_id}, time={self.execution_time})>"
