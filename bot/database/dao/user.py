"""UserDAO — data access for the User model."""

from typing import Optional


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
        elif user.username != username:
            # Telegram usernames can change (or be removed entirely); keep
            # it in sync on every message instead of freezing it at
            # whatever it was when the user first started the bot.
            user.username = username
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

    async def update_briefs_settings(self, user_id: int, **kwargs) -> None:
        """Update any custom daily brief settings dynamically."""
        await self.update_settings(user_id, **kwargs)

    async def update_settings(self, user_id: int, **kwargs) -> None:
        """Update arbitrary user settings fields dynamically."""
        user = await self.get_by_id(user_id)
        if user:
            for key, value in kwargs.items():
                if hasattr(user, key):
                    setattr(user, key, value)
            await self.session.flush()
