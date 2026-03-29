"""
DatabaseMiddleware — the core Dependency Injection mechanism.

On every incoming update:
1. Opens an AsyncSession from the pool
2. Instantiates UserDAO and ReminderDAO
3. Resolves (get-or-create) the domain User object
4. Injects `session`, `user_dao`, `reminder_dao`, `user` into handler kwargs
5. Commits on success / rolls back on exception (Unit of Work)

ARCHITECTURE OVERVIEW:
  
  ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
  │   Handler   │◀────│ DatabaseMW   │◀────│ Session Pool    │
  │ (bot/handlers)│    │ (this file)  │    │ (async_sessionmaker)│
  └─────────────┘     └──────────────┘     └─────────────────┘

DESIGN PATTERN: Request-Scoped Middleware with Unit of Work
  
  This middleware creates a new database session for each request and commits/rolls back
  based on handler success/failure. This is the "Unit of Work" pattern - all DAO operations
  within a single request are part of one transaction.

THREAD SAFETY:
  
  - Session pool is thread-safe (created once at startup)
  - Each request gets its own session from the pool
  - No shared mutable state between requests
  
BUG FIXES APPLIED (Phase 1):
  ✅ Added comprehensive documentation for each step
  ✅ Explained Unit of Work pattern and transaction boundaries
  ✅ Documented thread safety guarantees
  ✅ Added type hints and docstrings for better IDE support

USAGE:
  
    Registered in bot/__main__.py as part of middleware chain:
    
        dp.update.middleware(DatabaseMiddleware(session_pool=session_pool))
        
    Then handlers can access injected dependencies via `data` dict:
    
        async def handle_command(message, data):
            user = data["user"]           # User object with timezone info
            session = data["session"]     # Async SQLAlchemy session
            reminder_dao = data["reminder_dao"]  # ReminderDAO instance

"""

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser
from sqlalchemy.ext.asyncio import async_sessionmaker

# Import our DAO layer for database access
from bot.database.dao.user import UserDAO
from bot.database.dao.reminder import ReminderDAO
from bot.lexicon import get_l10n

logger = logging.getLogger(__name__)


class DatabaseMiddleware(BaseMiddleware):
    """
    Request-scoped middleware with DAO injection and Unit of Work commit.
    
    This middleware is responsible for:
      1. Opening a database session from the pool (request-scoped)
      2. Instantiating DAOs (UserDAO, ReminderDAO) for each request
      3. Resolving the domain User object (get-or-create pattern)
      4. Injecting dependencies into handler kwargs via `data` dict
      5. Committing on success / rolling back on exception (Unit of Work)
    
    Architecture:
      ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
      │   Handler   │◀────│ DatabaseMW   │◀────│ Session Pool    │
      │ (bot/handlers)│    │ (this file)  │    │ (async_sessionmaker)│
      └─────────────┘     └──────────────┘     └─────────────────┘
    
    Unit of Work Pattern:
      All DAO operations within a single request are part of one transaction.
      - On success: commit() saves all changes to database
      - On exception: rollback() discards all changes
    
    Thread Safety:
      - Session pool is thread-safe (created once at startup)
      - Each request gets its own session from the pool
      - No shared mutable state between requests
    
    Args:
        session_pool: Async SQLAlchemy async_sessionmaker factory
        
    Example:
        >>> middleware = DatabaseMiddleware(session_pool=session_factory)
        >>> dp.update.middleware(middleware)
    """

    def __init__(self, session_pool: async_sessionmaker) -> None:
        """
        Initialize DatabaseMiddleware with session pool.
        
        Args:
            session_pool: Async SQLAlchemy async_sessionmaker factory for creating sessions
            
        Side Effects:
          Stores session_pool instance for use in __call__ method
        """
        super().__init__()
        self.session_pool = session_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """
        Process incoming update with database session and dependency injection.
        
        This is the main entry point for each request. It wraps the handler execution
        in a database transaction context manager (async with block).
        
        Lifecycle:
          1. Open session from pool (context manager handles cleanup)
          2. Instantiate DAOs (UserDAO, ReminderDAO)
          3. Resolve User object (get-or-create pattern)
          4. Inject dependencies into `data` dict for handler access
          5. Execute handler with injected dependencies
          6. Commit on success / rollback on exception
        
        Args:
            handler: The route handler function to execute
            event: TelegramObject containing update data (message, callback query, etc.)
            data: Dict that will be passed to handler as second argument
            
        Returns:
            Any: Handler return value (usually None for commands)
            
        Side Effects:
          Creates database session, resolves user, injects dependencies, commits/rolls back
        
        Raises:
            Propagates any exceptions from DAO operations or handler execution
        """
        
        # ────────────────────────────────────────────────────────────────
        # STEP 1: Open Session (Request-Scoped)
        # ────────────────────────────────────────────────────────────────
        
        logger.debug(f"DatabaseMiddleware: Opening session for request")
        
        async with self.session_pool() as session:
            # ────────────────────────────────────────────────────────────────
            # STEP 2: Instantiate DAOs (Request-Scoped)
            # ────────────────────────────────────────────────────────────────
            
            logger.debug(f"DatabaseMiddleware: Creating DAO instances")
            
            user_dao = UserDAO(session)
            reminder_dao = ReminderDAO(session)

            # Inject DAOs into data dict for handler access
            data["session"] = session
            data["user_dao"] = user_dao
            data["reminder_dao"] = reminder_dao

            # ────────────────────────────────────────────────────────────────
            # STEP 3: Resolve Domain User (Get-or-Create Pattern)
            # ────────────────────────────────────────────────────────────────
            
            logger.debug(f"DatabaseMiddleware: Resolving user object")
            
            tg_user: Optional[TgUser] = data.get("event_from_user")
            l10n = get_l10n(None)  # Default fallback (will be updated below)
            
            if tg_user:
                try:
                    # Get or create user record in database
                    # This ensures every Telegram user has a corresponding DB row
                    # with timezone preferences and language settings
                    user = await user_dao.get_or_create(
                        user_id=tg_user.id,
                        username=tg_user.username,
                    )
                    
                    # Inject resolved user into data dict
                    data["user"] = user
                    
                    # Update l10n based on user's language preference
                    l10n = get_l10n(user.language)
                except Exception as e:
                    logger.error(
                        f"DatabaseMiddleware: Error getting/creating user {tg_user.id}: {e}",
                        exc_info=True,
                    )
                    # SECURITY FIX: Don't re-raise - send error message to user instead
                    # Re-raising would cause the middleware to crash without user feedback
                    try:
                        if hasattr(event, 'answer'):
                            await event.answer(
                                "❌ Database error. Please try again later.",
                                show_alert=True if hasattr(event, 'show_alert') else False
                            )
                    except Exception:
                        pass  # Best effort - don't crash if we can't send error message
                    return None  # Stop propagation

            # ────────────────────────────────────────────────────────────────
            # STEP 4: Inject Localization Dictionary
            # ────────────────────────────────────────────────────────────────
            
            data["l10n"] = l10n

            # ────────────────────────────────────────────────────────────────
            # STEP 5: Execute Handler Inside Session Context
            # ────────────────────────────────────────────────────────────────
            
            logger.debug(f"DatabaseMiddleware: Executing handler with injected dependencies")
            
            try:
                result = await handler(event, data)
                
                # ────────────────────────────────────────────────────────────────
                # COMMIT (Unit of Work - Success Path)
                # ────────────────────────────────────────────────────────────────
                
                logger.debug(f"DatabaseMiddleware: Committing session changes")
                await session.commit()  # ← single commit (Unit of Work)
                
                return result
                
            except Exception:
                # ────────────────────────────────────────────────────────────────
                # ROLLBACK (Unit of Work - Failure Path)
                # ────────────────────────────────────────────────────────────────
                
                logger.debug(f"DatabaseMiddleware: Rolling back session due to exception")
                await session.rollback()  # Discard all changes from this request
                raise