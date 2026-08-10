"""Generic async CRUD DAO. Concrete DAOs (ReminderDAO, UserDAO, HabitEventDAO)
set `model` and add domain-specific queries on top.

Transaction boundary lives in DatabaseMiddleware, not here: it commits on
handler success and rolls back on exception, so callers can use multiple
DAOs against one session without managing commits themselves. Job targets
(outside request scope, no middleware) open and commit their own sessions.
"""

from typing import Generic, TypeVar, Type, Optional, Sequence, Any

from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.engine import Base


T = TypeVar("T", bound=Base)


class BaseDAO(Generic[T]):
    """Generic CRUD over one model. Subclasses set `model = SomeModel`."""

    model: Type[T]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, record_id: int) -> Optional[T]:
        result = await self.session.execute(
            select(self.model).where(self.model.id == record_id)
        )
        return result.scalar_one_or_none()

    async def get_all(self, **filters: Any) -> Sequence[T]:
        """Fetch all records, optionally filtered by column=value pairs."""
        stmt = select(self.model)
        for key, value in filters.items():
            stmt = stmt.where(getattr(self.model, key) == value)

        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create(self, **kwargs: Any) -> T:
        """Insert a new record, flush to populate its auto-generated id."""
        instance = self.model(**kwargs)
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def delete_by_id(self, record_id: int) -> None:
        await self.session.execute(
            delete(self.model).where(self.model.id == record_id)
        )
        await self.session.flush()

    async def count(self) -> int:
        result = await self.session.execute(select(func.count()).select_from(self.model))
        return result.scalar_one()
