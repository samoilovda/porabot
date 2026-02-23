from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


DATABASE_URL = "sqlite+aiosqlite:///porabot.db"


class Base(DeclarativeBase):
    """
    Базовый класс для всех моделей SQLAlchemy (DeclarativeBase).
    """
    pass


# Создаем асинхронный движок
engine: AsyncEngine = create_async_engine(DATABASE_URL, echo=False)

# Создаем фабрику сессий
async_session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Асинхронный генератор сессии базы данных.
    Используется как зависимость (dependency) в обработчиках.
    Yields:
        AsyncSession: Сезссия SQLAlchemy.
    """
    async with async_session_maker() as session:
        yield session


async def init_db() -> None:
    """
    Инициализирует базу данных: создает таблицы, если они не существуют.
    Должна вызываться при старте приложения.
    """
    # Импорт моделей внутри функции для избежания круговых зависимостей
    # и гарантии регистрации моделей в metadata
    import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
