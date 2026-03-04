"""UserDAO — data access for the User model."""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.dao.base import BaseDAO
from bot.database.models import User


class UserDAO(BaseDAO[User]):
    model = User

    async def get_or_create(
        self,
        user_id: int,
        username: Optional[str] = None,
        timezone: str = "UTC",
        language: Optional[str] = None,
    ) -> User:
        """
        Idempotent: returns existing user or creates a new one.
        Used by DatabaseMiddleware on every incoming update.
        """
        user = await self.get_by_id(user_id)
        if user is None:
            user = User(id=user_id, username=username, timezone=timezone, language=language)
            self.session.add(user)
            await self.session.flush()
        return user

    async def update_timezone(self, user_id: int, timezone: str) -> None:
        """Update the user's timezone preference."""
        user = await self.get_by_id(user_id)
        if user:
            user.timezone = timezone
            await self.session.flush()

    async def update_language(self, user_id: int, language: str) -> None:
        """Update the user's language preference."""
        user = await self.get_by_id(user_id)
        if user:
            user.language = language
            await self.session.flush()

    async def update_show_utc_offset(self, user_id: int, show: bool) -> None:
        """Update whether to show UTC offset in formatted times."""
        user = await self.get_by_id(user_id)
        if user:
            user.show_utc_offset = show
            await self.session.flush()
