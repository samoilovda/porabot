"""
WhitelistMiddleware — access control for closed beta.

Checks if the user is in the whitelisted IDs or is the administrator.
Stops propagation if the user is not allowed.
"""

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser, Message, CallbackQuery

logger = logging.getLogger(__name__)


class WhitelistMiddleware(BaseMiddleware):
    """
    Middleware to restrict access to whitelisted users.
    Must be registered BEFORE DatabaseMiddleware to save DB resources.
    """

    def __init__(self, allowed_users: list[int], admin_id: int) -> None:
        super().__init__()
        self.allowed_users = set(allowed_users)
        self.admin_id = admin_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        tg_user: Optional[TgUser] = data.get("event_from_user")

        if not tg_user:
            return await handler(event, data)

        is_allowed = (tg_user.id in self.allowed_users) or (tg_user.id == self.admin_id)

        if not is_allowed:
            logger.warning(f"Access denied for user {tg_user.id} ({tg_user.full_name})")
            
            message_text = "🚧 Porabot находится в режиме закрытого бета-тестирования. У вас нет доступа."
            
            if isinstance(event, Message):
                await event.answer(message_text)
            elif isinstance(event, CallbackQuery):
                await event.answer(message_text, show_alert=True)
                
            return None  # Stop propagation

        return await handler(event, data)
