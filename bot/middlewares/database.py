"""Database middleware — request-scoped session injection (Unit of Work).

Opens an AsyncSession, instantiates DAOs, resolves the domain User via
get-or-create, then calls the handler. Commits on success; rolls back on
exception. Must be registered AFTER WhitelistMiddleware.
"""

import logging
from typing import Any, Awaitable, Callable, Optional

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.database.dao.user import UserDAO
from bot.database.dao.reminder import ReminderDAO
from bot.lexicon import get_l10n

logger = logging.getLogger(__name__)


class DatabaseMiddleware(BaseMiddleware):
    """Inject `session`, `user_dao`, `reminder_dao`, `user`, `l10n` into handler kwargs."""

    def __init__(self, session_pool: async_sessionmaker) -> None:
        super().__init__()
        self.session_pool = session_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_pool() as session:
            user_dao = UserDAO(session)
            reminder_dao = ReminderDAO(session)
            data["session"] = session
            data["user_dao"] = user_dao
            data["reminder_dao"] = reminder_dao

            tg_user: Optional[TgUser] = data.get("event_from_user")
            l10n = get_l10n(None)

            if tg_user:
                try:
                    user = await user_dao.get_or_create(
                        user_id=tg_user.id, username=tg_user.username
                    )
                    data["user"] = user
                    l10n = get_l10n(user.language)
                except Exception as e:
                    logger.error("Error resolving user %s: %s", tg_user.id, e, exc_info=True)
                    # Keep session state clean if user bootstrap failed mid-transaction.
                    await session.rollback()
                    if hasattr(event, "answer"):
                        try:
                            await event.answer(
                                l10n.get(
                                    "db_error",
                                    "❌ Database error. Please try again later.",
                                )
                            )
                        except Exception as send_err:
                            logger.warning("Could not send DB error message to user %s: %s", tg_user.id, send_err)
                    return None

            data["l10n"] = l10n

            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
