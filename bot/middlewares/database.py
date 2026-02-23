"""
DatabaseMiddleware — the core Dependency Injection mechanism.

On every incoming update:
1. Opens an AsyncSession from the pool
2. Instantiates UserDAO and ReminderDAO
3. Resolves (get-or-create) the domain User object
4. Injects `session`, `user_dao`, `reminder_dao`, `user` into handler kwargs
5. Commits on success / rolls back on exception (Unit of Work)
"""

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.database.dao.user import UserDAO
from bot.database.dao.reminder import ReminderDAO
from bot.lexicon import get_l10n

logger = logging.getLogger(__name__)


class DatabaseMiddleware(BaseMiddleware):
    """Request-scoped middleware with DAO injection and Unit of Work commit."""

    def __init__(self, session_pool: async_sessionmaker) -> None:
        super().__init__()
        self.session_pool = session_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with self.session_pool() as session:
            # --- Build DAOs ---
            user_dao = UserDAO(session)
            reminder_dao = ReminderDAO(session)

            data["session"] = session
            data["user_dao"] = user_dao
            data["reminder_dao"] = reminder_dao

            # --- Resolve domain User ---
            tg_user: TgUser | None = data.get("event_from_user")
            l10n = get_l10n(None)  # Default fallback
            
            if tg_user:
                try:
                    user = await user_dao.get_or_create(
                        user_id=tg_user.id,
                        username=tg_user.username,
                    )
                    data["user"] = user
                    l10n = get_l10n(user.language)
                except Exception as e:
                    logger.error(
                        f"Error getting/creating user in middleware: {e}",
                        exc_info=True,
                    )
                    raise

            data["l10n"] = l10n

            # --- Execute handler inside the session context ---
            try:
                result = await handler(event, data)
                await session.commit()  # ← single commit (Unit of Work)
                return result
            except Exception:
                await session.rollback()
                raise
