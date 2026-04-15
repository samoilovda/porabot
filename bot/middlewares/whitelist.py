"""Whitelist middleware — access control for closed beta.

Registered BEFORE DatabaseMiddleware so unauthorized users never cause
a DB query. The check is O(1) set lookup.
"""

import logging
from typing import Any, Awaitable, Callable, Optional

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser, Message, CallbackQuery

logger = logging.getLogger(__name__)


class WhitelistMiddleware(BaseMiddleware):
    """Stop unauthorized users before they reach handlers or the DB.

    Args:
        allowed_users: List of Telegram user IDs with access.
        admin_id:      Admin always passes regardless of the whitelist.
    """

    def __init__(self, allowed_users: list[int], admin_id: int) -> None:
        super().__init__()
        self.allowed_users = set(allowed_users)
        self.admin_id = admin_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: Optional[TgUser] = data.get("event_from_user")

        if not tg_user:
            return await handler(event, data)

        if tg_user.id not in self.allowed_users and tg_user.id != self.admin_id:
            logger.warning("Access denied: user %s (%s)", tg_user.id, tg_user.full_name)
            msg = "🚧 Porabot находится в режиме закрытого бета-тестирования. У вас нет доступа."
            if isinstance(event, Message):
                await event.answer(msg)
            elif isinstance(event, CallbackQuery):
                await event.answer(msg, show_alert=True)
            return None

        return await handler(event, data)