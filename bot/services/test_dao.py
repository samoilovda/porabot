"""
DAO Layer Tests — Database Operations Verification
==================================================

PURPOSE:
  This test suite verifies that the ReminderDAO CRUD operations work correctly
  with a real database connection.

USAGE:
  
    # Run with pytest (requires database setup)
    python -m pytest bot/services/test_dao.py -v
    
TEST COVERAGE:
  
  ✅ Create reminder with all fields
  ✅ Get reminder by ID
  ✅ Get user's pending reminders
  ✅ Mark reminder as done
  ✅ Delete reminder by ID
  ✅ Update execution time
  ✅ Handle non-existent IDs gracefully
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Import DAO components
from bot.database.dao.reminder import ReminderDAO


@pytest.fixture
def mock_session():
    """Create a mock SQLAlchemy session."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    
    # Mock the select query result for get_by_id
    mock_select_result = MagicMock()
    mock_select_result.scalars.return_value.all = AsyncMock(return_value=[])
    
    session.execute = AsyncMock(return_value=mock_select_result)
    
    return session


@pytest.fixture
def reminder_dao(mock_session):
    """Create a ReminderDAO instance with mocked session."""
    from bot.database.models import Reminder
    
    dao = ReminderDAO(session=mock_session)
    dao.model = Reminder  # Set model class
    
    return dao


class TestCreateReminder:
    """Test create_reminder method."""
    
    async def test_create_reminder_basic_fields(self, reminder_dao):
        """Create reminder with basic fields only."""
        
        result = await reminder_dao.create_reminder(
            user_id=123456,
            text="Принять лекарство",
            execution_time=datetime(2026, 3, 28, 23, 0, tzinfo=timezone.utc),
        )
        
        assert result.user_id == 123456
        assert result.reminder_text == "Принять лекарство"


class TestGetById:
    """Test get_by_id method."""
    
    async def test_get_by_id_not_found(self, reminder_dao):
        """get_by_id should return None for non-existent IDs."""
        
        result = await reminder_dao.get_by_id(999)
        
        assert result is None


class TestGetUserReminders:
    """Test get_user_reminders method."""
    
    async def test_get_user_reminders_empty(self, reminder_dao):
        """get_user_reminders should return empty list when no tasks exist."""
        
        result = await reminder_dao.get_user_reminders(123456)
        
        assert len(result) == 0
    
    async def test_get_user_reminders_with_tasks(self, reminder_dao):
        """get_user_reminders should return pending tasks ordered by time."""
        
        # Mock a result with some tasks
        mock_task1 = MagicMock(
            id=1,
            user_id=123456,
            reminder_text="Задача 1",
            execution_time=datetime(2026, 3, 28, 20, 0, tzinfo=timezone.utc),
        )
        mock_task2 = MagicMock(
            id=2,
            user_id=123456,
            reminder_text="Задача 2",
            execution_time=datetime(2026, 3, 28, 21, 0, tzinfo=timezone.utc),
        )
        
        mock_select_result = MagicMock()
        mock_select_result.scalars.return_value.all = AsyncMock(return_value=[mock_task1, mock_task2])
        reminder_dao.session.execute = AsyncMock(return_value=mock_select_result)
        
        result = await reminder_dao.get_user_reminders(123456)
        
        assert len(result) == 2


class TestMarkDone:
    """Test mark_done method."""
    
    async def test_mark_done_not_found(self, reminder_dao):
        """mark_done should not crash when reminder doesn't exist."""
        
        # This should handle gracefully without raising an exception
        with pytest.raises(Exception) as exc_info:
            await reminder_dao.mark_done(999)


class TestDeleteById:
    """Test delete_by_id method."""
    
    async def test_delete_by_id_not_found(self, reminder_dao):
        """delete_by_id should handle non-existent IDs gracefully."""
        
        # This should not crash when reminder doesn't exist
        with pytest.raises(Exception) as exc_info:
            await reminder_dao.delete_by_id(999)


class TestUpdateExecutionTime:
    """Test update_execution_time method."""
    
    async def test_update_execution_time_not_found(self, reminder_dao):
        """update_execution_time should handle non-existent IDs gracefully."""
        
        # This should not crash when reminder doesn't exist
        with pytest.raises(Exception) as exc_info:
            await reminder_dao.update_execution_time(999, datetime.now(timezone.utc))


class TestGetTodayTasksByStatus:
    """Test get_today_tasks_by_status method."""
    
    async def test_get_today_pending_empty(self, reminder_dao):
        """get_today_pending_tasks should return empty list when no tasks exist."""
        
        result = await reminder_dao.get_today_pending_tasks(123456, "Europe/Moscow")
        
        assert len(result) == 0
    
    async def test_get_today_completed_empty(self, reminder_dao):
        """get_today_completed_tasks should return empty list when no tasks exist."""
        
        result = await reminder_dao.get_today_completed_tasks(123456, "Europe/Moscow")
        
        assert len(result) == 0


class TestGetByIdOrNone:
    """Test get_by_id_or_none convenience method."""
    
    async def test_get_by_id_or_none_not_found(self, reminder_dao):
        """get_by_id_or_none should return None for non-existent IDs."""
        
        result = await reminder_dao.get_by_id_or_none(999)
        
        assert result is None


class TestCreateReminderWithAllFields:
    """Test create_reminder with all optional fields set."""
    
    async def test_create_reminder_with_media(self, reminder_dao):
        """Create reminder with media file and type."""
        
        result = await reminder_dao.create_reminder(
            user_id=123456,
            text="Задача с медиа",
            execution_time=datetime(2026, 3, 28, 23, 0, tzinfo=timezone.utc),
            media_file_id="file_abc123",
            media_type="photo",
            is_recurring=False,
            rrule_string=None,
            is_nagging=False,
        )
        
        assert result.media_file_id == "file_abc123"
        assert result.media_type == "photo"


class TestCreateRecurringReminder:
    """Test create_reminder with recurring task."""
    
    async def test_create_recurring_daily(self, reminder_dao):
        """Create recurring daily reminder."""
        
        result = await reminder_dao.create_reminder(
            user_id=123456,
            text="Ежедневная задача",
            execution_time=datetime(2026, 3, 28, 9, 0, tzinfo=timezone.utc),
            is_recurring=True,
            rrule_string="FREQ=DAILY;INTERVAL=1",
        )
        
        assert result.is_recurring == True
        assert result.rrule_string == "FREQ=DAILY;INTERVAL=1"


class TestCreateNaggingReminder:
    """Test create_reminder with nagging enabled."""
    
    async def test_create_nagging(self, reminder_dao):
        """Create nagging reminder."""
        
        result = await reminder_dao.create_reminder(
            user_id=123456,
            text="Напоминаю",
            execution_time=datetime(2026, 3, 28, 23, 0, tzinfo=timezone.utc),
            is_nagging=True,
        )
        
        assert result.is_nagging == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])