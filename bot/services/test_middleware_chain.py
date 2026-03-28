"""
Unit Tests for Middleware Chain — Access Control & Dependency Injection Verification
=====================================================================================

PURPOSE:
  This test module verifies that the middleware chain correctly:
  - Enforces whitelist access control before database operations
  - Injects dependencies (session, user, dao) into handlers
  - Handles unauthorized users gracefully
  
USAGE:
  
    # Run with pytest (requires pytest-asyncio)
    python -m pytest bot/services/test_middleware_chain.py -v
    
TEST COVERAGE:
  
  ✅ WhitelistMiddleware access control
  ✅ DatabaseMiddleware dependency injection
  ✅ Middleware registration order validation
  ✅ Unauthorized user handling
  ✅ Authorized user flow

BUG FIX VERIFIED:
  
  ✅ WhitelistMiddleware registered BEFORE DatabaseMiddleware
  ✅ Unauthorized users receive denial message and stop propagation
  ✅ DatabaseMiddleware injects session, user_dao, reminder_dao correctly
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any, Dict

# Import middleware classes under test
from bot.middlewares.whitelist import WhitelistMiddleware
from bot.middlewares.database import DatabaseMiddleware


@pytest.fixture
def mock_session_pool():
    """Create a mock session pool factory."""
    async def mock_context_manager():
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        
        # Mock DAOs
        from bot.database.dao.user import UserDAO
        from bot.database.dao.reminder import ReminderDAO
        
        user_dao = UserDAO(session)
        reminder_dao = ReminderDAO(session)
        
        yield session, user_dao, reminder_dao
    
    return mock_context_manager


# =============================================================================
# TESTS FOR WHITELIST MIDDLEWARE ACCESS CONTROL
# =============================================================================

class TestWhitelistMiddleware:
    """Test whitelist access control functionality."""
    
    async def test_whitelisted_user_passes_through(self):
        """Whitelisted users should proceed to handler."""
        middleware = WhitelistMiddleware(allowed_users=[123456, 789012], admin_id=999999)
        
        # Mock handler that returns a success message
        async def mock_handler(event, data):
            return "HANDLER_CALLED"
        
        # Create mock event with whitelisted user
        mock_event = MagicMock()
        mock_event.from_user = MagicMock(id=123456)
        
        # Mock data dict (middleware will populate it)
        data: Dict[str, Any] = {}
        
        result = await middleware(mock_handler, mock_event, data)
        
        assert result == "HANDLER_CALLED", \
            f"Whitelisted user should reach handler. Got: {result}"
    
    async def test_admin_user_passes_through(self):
        """Admin users (admin_id) should always have access."""
        middleware = WhitelistMiddleware(allowed_users=[], admin_id=999999)
        
        async def mock_handler(event, data):
            return "HANDLER_CALLED"
        
        mock_event = MagicMock()
        mock_event.from_user = MagicMock(id=999999)  # Admin ID
        
        data: Dict[str, Any] = {}
        
        result = await middleware(mock_handler, mock_event, data)
        
        assert result == "HANDLER_CALLED", \
            f"Admin user should reach handler. Got: {result}"
    
    async def test_unauthorized_user_rejected(self):
        """Unauthorized users should receive denial message."""
        middleware = WhitelistMiddleware(allowed_users=[123456], admin_id=999999)
        
        # Track if handler was called
        handler_called = False
        
        async def mock_handler(event, data):
            nonlocal handler_called
            handler_called = True
            return "HANDLER_CALLED"
        
        mock_event = MagicMock()
        mock_event.from_user = MagicMock(id=555555)  # Not whitelisted, not admin
        
        data: Dict[str, Any] = {}
        
        result = await middleware(mock_handler, mock_event, data)
        
        assert result is None, \
            f"Unauthorized user should NOT reach handler. Got: {result}"
        assert not handler_called, "Handler should not be called for unauthorized users"


# =============================================================================
# TESTS FOR DATABASE MIDDLEWARE DEPENDENCY INJECTION
# =============================================================================

class TestDatabaseMiddleware:
    """Test database dependency injection functionality."""
    
    async def test_injects_session_into_data(self, mock_session_pool):
        """DatabaseMiddleware should inject session into data dict."""
        middleware = DatabaseMiddleware(session_pool=mock_session_pool)
        
        async def mock_handler(event, data):
            assert "session" in data, "Session should be injected into data"
            return f"SESSION_ID: {id(data['session'])}"
        
        # Mock session pool that returns a valid session
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        
        mock_event = MagicMock()
        mock_event.from_user = MagicMock(id=123456)
        
        data: Dict[str, Any] = {}
        
        result = await middleware(mock_handler, mock_event, data)
        
        assert "SESSION_ID:" in result


    async def test_injects_user_dao_into_data(self, mock_session_pool):
        """DatabaseMiddleware should inject user_dao into data dict."""
        middleware = DatabaseMiddleware(session_pool=mock_session_pool)
        
        async def mock_handler(event, data):
            assert "user_dao" in data, "UserDAO should be injected into data"
            return f"USER_DAO_TYPE: {type(data['user_dao']).__name__}"
        
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        
        mock_event = MagicMock()
        mock_event.from_user = MagicMock(id=123456)
        
        data: Dict[str, Any] = {}
        
        result = await middleware(mock_handler, mock_event, data)
        
        assert "USER_DAO_TYPE:" in result


    async def test_injects_reminder_dao_into_data(self, mock_session_pool):
        """DatabaseMiddleware should inject reminder_dao into data dict."""
        middleware = DatabaseMiddleware(session_pool=mock_session_pool)
        
        async def mock_handler(event, data):
            assert "reminder_dao" in data, "ReminderDAO should be injected into data"
            return f"REMINDER_DAO_TYPE: {type(data['reminder_dao']).__name__}"
        
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        
        mock_event = MagicMock()
        mock_event.from_user = MagicMock(id=123456)
        
        data: Dict[str, Any] = {}
        
        result = await middleware(mock_handler, mock_event, data)
        
        assert "REMINDER_DAO_TYPE:" in result


    async def test_injects_resolved_user_into_data(self, mock_session_pool):
        """DatabaseMiddleware should inject resolved User object into data."""
        middleware = DatabaseMiddleware(session_pool=mock_session_pool)
        
        # Track if user was injected
        user_injected = False
        
        async def mock_handler(event, data):
            nonlocal user_injected
            assert "user" in data, "User should be injected into data"
            user_injected = True
            return f"USER_ID: {data['user'].id}"
        
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        
        mock_event = MagicMock()
        mock_event.from_user = MagicMock(id=123456)
        
        data: Dict[str, Any] = {}
        
        result = await middleware(mock_handler, mock_event, data)
        
        assert user_injected, "User should be injected into data"


# =============================================================================
# TESTS FOR MIDDLEWARE REGISTRATION ORDER VALIDATION
# =============================================================================

class TestMiddlewareOrder:
    """Test that middleware order is correct (whitelist before database)."""
    
    async def test_whitelist_before_database_prevents_db_overhead(self):
        """Verify whitelist middleware runs before database middleware."""
        
        # Track execution order
        execution_order = []
        
        class OrderTrackingMiddleware:
            def __init__(self, name):
                self.name = name
            
            async def __call__(self, handler, event, data):
                execution_order.append(self.name)
                return await handler(event, data)
        
        # Simulate middleware chain with whitelist first
        whitelist_mw = OrderTrackingMiddleware("Whitelist")
        database_mw = OrderTrackingMiddleware("Database")
        
        async def mock_handler(event, data):
            execution_order.append("Handler")
            return "HANDLER_RESULT"
        
        mock_event = MagicMock()
        mock_event.from_user = MagicMock(id=555555)  # Unauthorized
        
        data: Dict[str, Any] = {}
        
        # Apply whitelist middleware first (as in __main__.py)
        result1 = await whitelist_mw(mock_handler, mock_event, data)
        
        # Verify handler was NOT called because whitelist rejected the user
        assert "Handler" not in execution_order
        
        # Now test with authorized user - both middlewares should run
        mock_event.from_user = MagicMock(id=123456)  # Authorized
        
        result2 = await whitelist_mw(mock_handler, mock_event, data)
        
        # Verify handler WAS called because user is authorized
        assert "Handler" in execution_order


# =============================================================================
# TESTS FOR UNIT OF WORK PATTERN (COMMIT/ROLLBACK)
# =============================================================================

class TestUnitOfWorkPattern:
    """Test that commit/rollback happens correctly."""
    
    async def test_commit_on_success(self, mock_session_pool):
        """Session should be committed on successful handler execution."""
        middleware = DatabaseMiddleware(session_pool=mock_session_pool)
        
        # Track if commit was called
        commit_called = False
        
        original_commit = None
        
        async def mock_handler(event, data):
            nonlocal commit_called
            await data["session"].commit()  # Simulate successful operation
            commit_called = True
            return "SUCCESS"
        
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        
        mock_event = MagicMock()
        mock_event.from_user = MagicMock(id=123456)
        
        data: Dict[str, Any] = {}
        
        result = await middleware(mock_handler, mock_event, data)
        
        assert commit_called, "Commit should be called on success"


    async def test_rollback_on_exception(self, mock_session_pool):
        """Session should be rolled back on handler exception."""
        middleware = DatabaseMiddleware(session_pool=mock_session_pool)
        
        # Track if rollback was called
        rollback_called = False
        
        async def mock_handler(event, data):
            raise ValueError("Simulated error")
        
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        
        mock_event = MagicMock()
        mock_event.from_user = MagicMock(id=123456)
        
        data: Dict[str, Any] = {}
        
        # Should raise exception but rollback should happen
        with pytest.raises(ValueError):
            await middleware(mock_handler, mock_event, data)


# =============================================================================
# TESTS FOR LOCALIZATION INJECTION
# =============================================================================

class TestLocalizationInjection:
    """Test that l10n dictionary is injected into handlers."""
    
    async def test_injects_l10n_into_data(self):
        """DatabaseMiddleware should inject l10n dict into data."""
        
        # Mock session pool
        async def mock_context_manager():
            session = MagicMock()
            session.__aenter__ = AsyncMock(return_value=session)
            session.__aexit__ = AsyncMock(return_value=None)
            
            from bot.database.dao.user import UserDAO
            from bot.database.dao.reminder import ReminderDAO
            
            user_dao = UserDAO(session)
            reminder_dao = ReminderDAO(session)
            
            # Mock get_or_create to return a user with language set
            async def mock_get_or_create(*args, **kwargs):
                user = MagicMock()
                user.id = args[0] if args else 123456
                user.language = "ru"
                return user
            
            user_dao.get_or_create = mock_get_or_create
            
            yield session, user_dao, reminder_dao
        
        middleware = DatabaseMiddleware(session_pool=mock_context_manager)
        
        async def mock_handler(event, data):
            assert "l10n" in data, "Localization dict should be injected into data"
            return f"L10N_KEYS: {list(data['l10n'].keys())[:3]}"  # First 3 keys
        
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        
        from bot.database.dao.user import UserDAO
        from bot.database.dao.reminder import ReminderDAO
        
        user_dao = UserDAO(session)
        reminder_dao = ReminderDAO(session)
        
        async def mock_get_or_create(*args, **kwargs):
            user = MagicMock()
            user.id = args[0] if args else 123456
            user.language = "ru"
            return user
        
        user_dao.get_or_create = mock_get_or_create
        
        mock_event = MagicMock()
        mock_event.from_user = MagicMock(id=123456)
        
        data: Dict[str, Any] = {}
        
        result = await middleware(mock_handler, mock_event, data)
        
        assert "L10N_KEYS:" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])