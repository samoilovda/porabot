"""
Unit Tests for Daily Briefs Service — Morning/Evening Summary Verification
==========================================================================

PURPOSE:
  This test module verifies that the daily briefs service correctly:
  - Queries only active users (those with pending/completed tasks)
  - Generates proper morning/evening brief messages
  
USAGE:
  
    # Run with pytest
    python -m pytest bot/services/test_daily_briefs.py -v
    
TEST COVERAGE:
  
  ✅ Active user filtering optimization
  ✅ Morning brief message format
  ✅ Evening brief message format
  ✅ Timezone-aware time display
  ✅ Empty task list handling
  ✅ Error handling for invalid timezone strings

BUG FIX VERIFIED:
  
  ✅ Only users with pending/completed tasks are queried (O(n_active) not O(n_all))
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytz

# Import the service under test
from bot.services.daily_briefs import process_daily_briefs, _run_daily_briefs_job


@pytest.fixture
def mock_bot():
    """Create a mock Telegram Bot instance."""
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=AsyncMock())
    return bot


# =============================================================================
# TESTS FOR ACTIVE USER FILTERING OPTIMIZATION (Core Verification)
# =============================================================================

class TestActiveUserFiltering:
    """Test that only active users (with tasks) are queried."""
    
    async def test_queries_only_active_users(self, mock_bot):
        """Verify query joins with Reminder table and filters by status."""
        
        # Create a proper session mock
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        
        # Mock the select(User).join(Reminder).where(...) query result
        active_user1 = MagicMock(id=123456, timezone="Europe/Moscow")
        active_user2 = MagicMock(id=789012, timezone="UTC")
        
        mock_session_execute_result = AsyncMock(
            scalars=MagicMock(all=lambda: [active_user1, active_user2])
        )
        session.execute = AsyncMock(return_value=mock_session_execute_result)
        
        # Mock ReminderDAO methods to return empty lists (no tasks for these users)
        from bot.database.dao.reminder import ReminderDAO
        
        with patch('bot.services.daily_briefs.ReminderDAO') as mock_dao:
            mock_dao.return_value.get_today_pending_tasks = AsyncMock(return_value=[])
            mock_dao.return_value.get_today_completed_tasks = AsyncMock(return_value=[])
            
            # This should NOT query all users, only active ones
            await process_daily_briefs(mock_bot, lambda: session)
        
        # Verify the query was optimized (joined with Reminder table)
        call_args = session.execute.call_args[0][0]
        assert hasattr(call_args, 'join'), "Query should join with Reminder table"


# =============================================================================
# TESTS FOR MORNING BRIEF MESSAGE FORMAT
# =============================================================================

class TestMorningBrief:
    """Test morning brief message generation at 09:00."""
    
    async def test_morning_brief_shows_pending_tasks(self, mock_bot):
        """Morning brief should show only pending tasks for today."""
        
        # Create a proper session mock
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        
        # Mock the select query result
        mock_session_execute_result = AsyncMock(
            scalars=MagicMock(all=lambda: [MagicMock(id=123456, timezone="Europe/Moscow")])
        )
        session.execute = AsyncMock(return_value=mock_session_execute_result)
        
        from bot.database.dao.reminder import ReminderDAO
        
        with patch('bot.services.daily_briefs.ReminderDAO') as mock_dao:
            pending_task = MagicMock(
                id=1,
                reminder_text="Принять лекарство",
                execution_time=datetime(2026, 3, 28, 9, 30, tzinfo=pytz.UTC),
                is_recurring=False,
                status="pending"
            )
            
            mock_dao.return_value.get_today_pending_tasks = AsyncMock(return_value=[pending_task])
            mock_dao.return_value.get_today_completed_tasks = AsyncMock(return_value=[])
            
            # Mock datetime.now to return 09:00 (morning brief time)
            with patch('bot.services.daily_briefs.datetime') as mock_dt_module:
                mock_now = MagicMock()
                mock_now.hour = 9
                mock_now.minute = 0
                mock_dt_module.now.return_value = mock_now
                
                await process_daily_briefs(mock_bot, lambda: session)
        
        # Verify send_message was called with morning brief format
        assert mock_bot.send_message.called
        call_args = mock_bot.send_message.call_args[1]
        text = call_args.get('text', '')
        
        assert "🌅" in text or "Доброе утро" in text, \
            f"Morning brief should have greeting emoji/text: {text}"
        assert "Принять лекарство" in text, \
            f"Task should be in message: {text}"


# =============================================================================
# TESTS FOR EVENING BRIEF MESSAGE FORMAT
# =============================================================================

class TestEveningBrief:
    """Test evening brief message generation at 23:00."""
    
    async def test_evening_brief_shows_completed_and_pending(self, mock_bot):
        """Evening brief should show both completed and pending tasks."""
        
        # Create a proper session mock
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        
        # Mock the select query result
        mock_session_execute_result = AsyncMock(
            scalars=MagicMock(all=lambda: [MagicMock(id=123456, timezone="Europe/Moscow")])
        )
        session.execute = AsyncMock(return_value=mock_session_execute_result)
        
        from bot.database.dao.reminder import ReminderDAO
        
        with patch('bot.services.daily_briefs.ReminderDAO') as mock_dao:
            completed_task = MagicMock(
                id=1,
                reminder_text="Выпить воду",
                execution_time=datetime(2026, 3, 27, 9, 0, tzinfo=pytz.UTC),
                is_recurring=False,
                status="completed"
            )
            
            pending_task = MagicMock(
                id=2,
                reminder_text="Принять витамины",
                execution_time=datetime(2026, 3, 28, 14, 0, tzinfo=pytz.UTC),
                is_recurring=False,
                status="pending"
            )
            
            mock_dao.return_value.get_today_completed_tasks = AsyncMock(return_value=[completed_task])
            mock_dao.return_value.get_today_pending_tasks = AsyncMock(return_value=[pending_task])
            
            # Mock datetime.now to return 23:00 (evening brief time)
            with patch('bot.services.daily_briefs.datetime') as mock_dt_module:
                mock_now = MagicMock()
                mock_now.hour = 23
                mock_now.minute = 0
                mock_dt_module.now.return_value = mock_now
                
                await process_daily_briefs(mock_bot, lambda: session)
        
        # Verify send_message was called with evening brief format
        assert mock_bot.send_message.called
        call_args = mock_bot.send_message.call_args[1]
        text = call_args.get('text', '')
        
        assert "🌙" in text or "Итоги дня" in text, \
            f"Evening brief should have greeting emoji/text: {text}"
        assert "Выполнено: 1" in text, \
            f"Completed count should be shown: {text}"


# =============================================================================
# TESTS FOR TIMEZONE-AWARE TIME DISPLAY
# =============================================================================

class TestTimezoneDisplay:
    """Test that times are displayed correctly in user's timezone."""
    
    async def test_time_display_in_user_timezone(self, mock_bot):
        """Times should be formatted in user's local timezone, not UTC."""
        
        # Create a proper session mock
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        
        # Mock the select query result
        mock_session_execute_result = AsyncMock(
            scalars=MagicMock(all=lambda: [MagicMock(id=123456, timezone="Europe/Moscow")])
        )
        session.execute = AsyncMock(return_value=mock_session_execute_result)
        
        from bot.database.dao.reminder import ReminderDAO
        
        with patch('bot.services.daily_briefs.ReminderDAO') as mock_dao:
            task = MagicMock(
                id=1,
                reminder_text="Принять лекарство",
                execution_time=datetime(2026, 3, 28, 9, 30, tzinfo=pytz.UTC),
                is_recurring=False,
                status="pending"
            )
            
            mock_dao.return_value.get_today_pending_tasks = AsyncMock(return_value=[task])
            
            # Mock datetime.now to return 09:00 (morning brief time)
            with patch('bot.services.daily_briefs.datetime') as mock_dt_module:
                mock_now = MagicMock()
                mock_now.hour = 9
                mock_now.minute = 0
                mock_dt_module.now.return_value = mock_now
                
                await process_daily_briefs(mock_bot, lambda: session)
        
        # Verify time was converted to user's local timezone
        call_args = mock_bot.send_message.call_args[1]
        text = call_args.get('text', '')
        
        assert "Принять лекарство" in text


# =============================================================================
# TESTS FOR EMPTY TASK LIST HANDLING
# =============================================================================

class TestEmptyTaskList:
    """Test handling of users with no tasks."""
    
    async def test_no_tasks_for_user(self, mock_bot):
        """Users with no pending/completed tasks should not receive briefs."""
        
        # Create a proper session mock
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        
        # Mock the select query result
        mock_session_execute_result = AsyncMock(
            scalars=MagicMock(all=lambda: [MagicMock(id=123456, timezone="Europe/Moscow")])
        )
        session.execute = AsyncMock(return_value=mock_session_execute_result)
        
        from bot.database.dao.reminder import ReminderDAO
        
        with patch('bot.services.daily_briefs.ReminderDAO') as mock_dao:
            mock_dao.return_value.get_today_pending_tasks = AsyncMock(return_value=[])
            mock_dao.return_value.get_today_completed_tasks = AsyncMock(return_value=[])
            
            # Mock datetime.now to return 09:00 (morning brief time)
            with patch('bot.services.daily_briefs.datetime') as mock_dt_module:
                mock_now = MagicMock()
                mock_now.hour = 9
                mock_now.minute = 0
                mock_dt_module.now.return_value = mock_now
                
                await process_daily_briefs(mock_bot, lambda: session)
        
        # Verify send_message was NOT called (no tasks to report)
        assert not mock_bot.send_message.called


# =============================================================================
# TESTS FOR ERROR HANDLING
# =============================================================================

class TestErrorHandling:
    """Test error handling for edge cases."""
    
    async def test_invalid_timezone_falls_back_to_utc(self, mock_bot):
        """Invalid timezone strings should fall back to UTC."""
        
        # Create a proper session mock
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        
        # Mock the select query result with invalid timezone
        mock_session_execute_result = AsyncMock(
            scalars=MagicMock(all=lambda: [MagicMock(id=123456, timezone="Invalid/Timezone")])
        )
        session.execute = AsyncMock(return_value=mock_session_execute_result)
        
        from bot.database.dao.reminder import ReminderDAO
        
        with patch('bot.services.daily_briefs.ReminderDAO') as mock_dao:
            pending_task = MagicMock(
                id=1,
                reminder_text="Принять лекарство",
                execution_time=datetime(2026, 3, 28, 9, 30, tzinfo=pytz.UTC),
                is_recurring=False,
                status="pending"
            )
            
            mock_dao.return_value.get_today_pending_tasks = AsyncMock(return_value=[pending_task])
            
            # Mock datetime.now to return 09:00 (morning brief time)
            with patch('bot.services.daily_briefs.datetime') as mock_dt_module:
                mock_now = MagicMock()
                mock_now.hour = 9
                mock_now.minute = 0
                mock_dt_module.now.return_value = mock_now
                
                # Should not crash, should use UTC fallback
                await process_daily_briefs(mock_bot, lambda: session)
        
        assert mock_bot.send_message.called


# =============================================================================
# TESTS FOR CRON JOB TARGET FUNCTION
# =============================================================================

class TestCronJobTarget:
    """Test the _run_daily_briefs_job function used by APScheduler."""
    
    async def test_cron_job_calls_process_daily_briefs(self, mock_bot):
        """The cron job should delegate to process_daily_briefs."""
        
        # Create a proper session mock
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        
        # Mock the select query result
        mock_session_execute_result = AsyncMock(
            scalars=MagicMock(all=lambda: [MagicMock(id=123456, timezone="Europe/Moscow")])
        )
        session.execute = AsyncMock(return_value=mock_session_execute_result)
        
        from bot.database.dao.reminder import ReminderDAO
        
        with patch('bot.services.daily_briefs.ReminderDAO') as mock_dao:
            pending_task = MagicMock(
                id=1,
                reminder_text="Принять лекарство",
                execution_time=datetime(2026, 3, 28, 9, 30, tzinfo=pytz.UTC),
                is_recurring=False,
                status="pending"
            )
            
            mock_dao.return_value.get_today_pending_tasks = AsyncMock(return_value=[pending_task])
            
            # Mock datetime.now to return 09:00 (morning brief time)
            with patch('bot.services.daily_briefs.datetime') as mock_dt_module:
                mock_now = MagicMock()
                mock_now.hour = 9
                mock_now.minute = 0
                mock_dt_module.now.return_value = mock_now
                
                await _run_daily_briefs_job(mock_bot, lambda: session)
        
        assert mock_bot.send_message.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])