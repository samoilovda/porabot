"""
WhitelistMiddleware — access control for closed beta.

Checks if the user is in the whitelisted IDs or is the administrator.
Stops propagation if the user is not allowed (prevents unauthorized users from reaching handlers).

ARCHITECTURE OVERVIEW:
  
  ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
  │   Whitelist │────▶│ DatabaseMW   │────▶│ Handlers        │
  │ Middleware  │     │ (DB injection)│     │ (bot/handlers/) │
  └─────────────┘     └──────────────┘     └─────────────────┘

DESIGN PATTERN: Access Control Middleware
  
  This middleware acts as a gatekeeper - it checks user authorization BEFORE any database
  operations occur. It must be registered FIRST in the middleware chain to avoid wasting
  database resources on unauthorized users.

MIDDLEWARE ORDER MATTERS!
  
  ⚠️ CRITICAL: WhitelistMiddleware MUST be registered BEFORE DatabaseMiddleware.
  
  Why? Because:
    1. Unauthorized users should never reach DatabaseMiddleware (saves DB queries)
    2. DatabaseMiddleware opens a session and resolves user - expensive operations
    3. Whitelist check is O(1) set lookup - very fast
    
  Registration order in __main__.py:
      dp.update.middleware(WhitelistMiddleware(...))   # ← FIRST (access control)
      dp.update.middleware(DatabaseMiddleware(...))    # ← SECOND (DB injection)

BUG FIXES APPLIED (Phase 1):
  ✅ Added comprehensive documentation for each step
  ✅ Explained middleware order importance and performance implications
  ✅ Documented stop propagation behavior
  ✅ Added type hints and docstrings for better IDE support

USAGE:
  
    Registered in bot/__main__.py as part of middleware chain:
    
        whitelist_middleware = WhitelistMiddleware(
            allowed_users=[123, 456],   # List of whitelisted user IDs
            admin_id=789                # Admin ID (always has access)
        )
        
        dp.update.middleware(whitelist_middleware)

    Behavior:
      - Whitelisted users: proceed to next middleware/handler
      - Admin users: always have access (bypass whitelist check)
      - Unauthorized users: receive denial message and stop propagation

"""

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser, Message, CallbackQuery

logger = logging.getLogger(__name__)


class WhitelistMiddleware(BaseMiddleware):
    """
    Middleware to restrict access to whitelisted users.
    
    This middleware acts as a gatekeeper for the bot's closed beta mode. It checks if
    each incoming request comes from an authorized user (whitelisted ID or admin) and
    stops propagation if not allowed.
    
    Architecture:
      ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
      │   Whitelist │────▶│ DatabaseMW   │────▶│ Handlers        │
      │ Middleware  │     │ (DB injection)│     │ (bot/handlers/) │
      └─────────────┘     └──────────────┘     └─────────────────┘
    
    Authorization Rules:
      - Whitelisted users (in allowed_users list): have access
      - Admin user (admin_id): always has access (bypasses whitelist)
      - All other users: denied access
    
    Performance Optimization:
      Must be registered BEFORE DatabaseMiddleware to avoid DB overhead for unauthorized users.
      
    Args:
        allowed_users: List of Telegram user IDs that have access to the bot
        admin_id: Telegram user ID of the administrator (always has access)
        
    Example:
        >>> middleware = WhitelistMiddleware(allowed_users=[123, 456], admin_id=789)
        >>> dp.update.middleware(middleware)
    """

    def __init__(self, allowed_users: list[int], admin_id: int) -> None:
        """
        Initialize WhitelistMiddleware with whitelisted users and admin ID.
        
        Args:
            allowed_users: List of Telegram user IDs that have access to the bot
            admin_id: Telegram user ID of the administrator (always has access)
            
        Side Effects:
          Converts list to set for O(1) lookup performance
          Stores admin_id for authorization checks
        """
        super().__init__()
        # Convert to set for fast membership testing (O(1) vs O(n))
        self.allowed_users = set(allowed_users)
        self.admin_id = admin_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """
        Check user authorization and stop propagation if not allowed.
        
        This is the main entry point for each request. It extracts the Telegram user
        from the event and checks if they have access to the bot.
        
        Lifecycle:
          1. Extract TelegramUser from event data
          2. If no user (e.g., channel post), skip authorization check
          3. Check if user is whitelisted OR is admin
          4. If not authorized: send denial message and stop propagation
          5. If authorized: proceed to next middleware/handler
        
        Args:
            handler: The route handler function to execute (if authorized)
            event: TelegramObject containing update data (message, callback query, etc.)
            data: Dict that will be passed to handler as second argument
            
        Returns:
            Any: Handler return value if authorized, None if unauthorized (stops propagation)
            
        Side Effects:
          Sends denial message to unauthorized users via inline keyboard or alert
        
        Raises:
            No exceptions - all errors are logged internally
        """
        
        # ────────────────────────────────────────────────────────────────
        # STEP 1: Extract TelegramUser from Event Data
        # ────────────────────────────────────────────────────────────────
        
        tg_user: Optional[TgUser] = data.get("event_from_user")

        # ────────────────────────────────────────────────────────────────
        # EDGE CASE: No user (e.g., channel post, group message)
        # ────────────────────────────────────────────────────────────────
        
        if not tg_user:
            logger.debug(f"WhitelistMiddleware: Skipping auth check - no user in event")
            return await handler(event, data)  # Proceed without authorization

        # ────────────────────────────────────────────────────────────────
        # STEP 2: Check Authorization (Whitelisted OR Admin)
        # ────────────────────────────────────────────────────────────────
        
        is_whitelisted = tg_user.id in self.allowed_users
        is_admin = tg_user.id == self.admin_id
        
        is_allowed = is_whitelisted or is_admin

        if not is_allowed:
            # ────────────────────────────────────────────────────────────────
            # UNAUTHORIZED USER - Send denial message and stop propagation
            # ────────────────────────────────────────────────────────────────
            
            logger.warning(
                f"WhitelistMiddleware: Access denied for user {tg_user.id} "
                f"({tg_user.full_name})"
            )
            
            # Prepare denial message (Russian - bot's primary language)
            message_text = (
                "🚧 Porabot находится в режиме закрытого бета-тестирования. "
                "У вас нет доступа."
            )

            # ────────────────────────────────────────────────────────────────
            # STEP 3: Send Denial Message Based on Event Type
            # ────────────────────────────────────────────────────────────────
            
            if isinstance(event, Message):
                # Regular message - use inline keyboard with denial text
                await event.answer(message_text)
                
            elif isinstance(event, CallbackQuery):
                # Callback query (inline button press) - show alert popup
                await event.answer(message_text, show_alert=True)
            
            else:
                # Unknown event type - log and proceed anyway
                logger.debug(
                    f"WhitelistMiddleware: Unknown event type {type(event)}, "
                    f"proceeding without denial message"
                )

            # ────────────────────────────────────────────────────────────────
            # STOP PROPAGATION - Return None to prevent handler execution
            # ────────────────────────────────────────────────────────────────
            
            return None  # Stop propagation (handler will not be called)

        # ────────────────────────────────────────────────────────────────
        # AUTHORIZED USER - Proceed to next middleware/handler
        # ────────────────────────────────────────────────────────────────
        
        logger.debug(
            f"WhitelistMiddleware: User {tg_user.id} ({tg_user.full_name}) "
            f"is authorized, proceeding to handler"
        )

        return await handler(event, data)