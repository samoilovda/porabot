"""
Generic abstract DAO with reusable CRUD operations.

Concrete DAOs inherit from BaseDAO[T] and set `model = SomeModel`.
Transaction boundary (commit/rollback) lives in the middleware — DAOs
only flush to obtain auto-generated IDs.
"""

from typing import Generic, TypeVar, Type, Optional, Sequence, Any

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.engine import Base

T = TypeVar("T", bound=Base)


class BaseDAO(Generic[T]):
    """Generic async DAO with common CRUD operations."""

    model: Type[T]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, record_id: int) -> Optional[T]:
        """Fetch a single record by primary key."""
        result = await self.session.execute(
            select(self.model).where(self.model.id == record_id)  # type: ignore[attr-defined]
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
        """Insert a new record, flush (to populate id), and return it."""
        instance = self.model(**kwargs)
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def delete_by_id(self, record_id: int) -> None:
        """Delete a record by primary key."""
        await self.session.execute(
            delete(self.model).where(self.model.id == record_id)  # type: ignore[attr-defined]
        )
        await self.session.flush()

    async def count(self) -> int:
        """Get total number of records in the table."""
        from sqlalchemy import func
        result = await self.session.execute(select(func.count()).select_from(self.model))
        return result.scalar_one()
