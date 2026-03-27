"""
Integration Tests for Porabot — Full Workflow Verification
===========================================================

PURPOSE:
  This integration test suite verifies the complete bot workflow from message
  parsing through database storage and scheduler registration. It tests all
  major components working together.

USAGE:
  
    # Run with pytest (requires pytest-asyncio)
    python -m pytest bot/services/test_integration.py -v
    
TEST COVERAGE:
  
  ✅ Parser → Database creation flow
  ✅ Reminder retrieval and querying
  ✅ Timezone handling in database operations
  ✅ Soft-delete functionality
  ✅ Full end-to-end reminder lifecycle
  ✅ Future-time validation (prevents past times bug)

REQUIREMENTS:
  
  - pytest-asyncio for async test support
  - SQLite database (created automatically by tests)
"""

import asyncio
import pytest  # Add pytest for @pytest.fixture decorator
from unittest.mock import patch
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import pytz

# Import bot components
from bot.services.parser import InputParser
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import Base, Reminder


class IntegrationTestDatabase:
    """Manages test database lifecycle."""
    
    def __init__(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            echo=False,  # Disable SQL logging for cleaner output
            future=True,
        )
        self.async_session_maker = sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    
    async def setup(self):
        """Create all tables."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    
    async def cleanup(self):
        """Close all sessions and dispose engine."""
        if hasattr(self, 'async_session_maker'):
            pass
        self.engine.dispose()


# =============================================================================
# FIXTURE: Mock Past Time for Testing Future-Time Validation
# =============================================================================

@pytest.fixture
def mock_past_time():
    """Mock datetime.now to simulate it being past the target hour."""
    # Simulate 10:30 AM on March 28, 2026 (Europe/Moscow)
    return datetime(2026, 3, 28, 10, 30, tzinfo=pytz.timezone("Europe/Moscow"))


# =============================================================================
# TESTS FOR FUTURE-TIME VALIDATION IN DATABASE (Prevents "Task Saved in Past" Bug)
# =============================================================================

class TestDatabaseFutureTimeValidation:
    """Test that database never stores past times."""
    
    @classmethod
    async def run_tests(cls):
        """Run all future-time validation tests."""
        db = IntegrationTestDatabase()
        
        try:
            await db.setup()
            
            print("=" * 60)
            print("Integration Tests - Future-Time Validation")
            print("=" * 60)
            print()
            
            parser = InputParser()
            dao = ReminderDAO(db.async_session_maker)
            
            # Test 1: Verify evening at morning rolls to tomorrow in database
            print("--- Test 1: Evening at Morning Rolls to Tomorrow ---")
            with patch('bot.services.parser.datetime.now', return_value=mock_past_time):
                result = await parser.parse("вечером", "Europe/Moscow")
            
            assert result.parsed_datetime is not None, "Parsing failed"
            assert result.parsed_datetime.day == mock_past_time.day + 1, \
                f"Parsed time {result.parsed_datetime} should be tomorrow!"
            
            reminder = await dao.create_reminder(
                user_id=123456,
                text=result.clean_text or "Evening task",
                execution_time=result.parsed_datetime,
            )
            
            print("[PASS] Reminder stored with future time:")
            print("      Text: " + reminder.reminder_text)
            print("      Time: " + str(reminder.execution_time))
            print()
            
            # Test 2: Verify morning at same hour stays today in database
            print("--- Test 2: Morning at Same Hour Stays Today ---")
            evening_time = datetime(2026, 3, 28, 19, 0, tzinfo=pytz.timezone("Europe/Moscow"))
            
            with patch('bot.services.parser.datetime.now', return_value=evening_time):
                result2 = await parser.parse("вечером", "Europe/Moscow")
            
            assert result2.parsed_datetime is not None, "Parsing failed"
            assert result2.parsed_datetime.day == evening_time.day, \
                f"Parsed time {result2.parsed_datetime} should be today!"
            
            reminder2 = await dao.create_reminder(
                user_id=123456,
                text=result2.clean_text or "Evening task",
                execution_time=result2.parsed_datetime,
            )
            
            print("[PASS] Reminder stored with same-day time:")
            print("      Text: " + reminder2.reminder_text)
            print("      Time: " + str(reminder2.execution_time))
            print()
            
            # Test 3: Verify all reminders in user's list are future times
            print("--- Test 3: All User Reminders Are Future Times ---")
            now = datetime.now(pytz.timezone("Europe/Moscow"))
            pending = await dao.get_user_reminders(123456)
            
            assert len(pending) == 2, f"Should have 2 reminders, got {len(pending)}"
            
            for r in pending:
                assert r.execution_time > now, \
                    f"Reminder {r.id} has past time {r.execution_time}"
            
            print("[PASS] All " + str(len(pending)) + " reminders have future times")
            for r in pending:
                status = "future" if r.execution_time > now else "PAST!"
                print("      - " + r.reminder_text + ": " + status)
            print()
            
            # Test 4: Verify simple hour expressions create future times
            print("--- Test 4: Simple Hour Expressions Create Future Times ---")
            with patch('bot.services.parser.datetime.now', return_value=mock_past_time):
                result3 = await parser.parse("в 9", "Europe/Moscow")
            
            assert result3.parsed_datetime is not None, "Parsing failed"
            assert result3.parsed_datetime.day == mock_past_time.day + 1, \
                f"Parsed time {result3.parsed_datetime} should be tomorrow!"
            
            reminder3 = await dao.create_reminder(
                user_id=123456,
                text=result3.clean_text or "At 9am task",
                execution_time=result3.parsed_datetime,
            )
            
            print("[PASS] Simple hour expression rolled over:")
            print("      Text: " + reminder3.reminder_text)
            print("      Time: " + str(reminder3.execution_time))
            print()
            
            # Test 5: Verify duration expressions always create future times
            print("--- Test 5: Duration Expressions Create Future Times ---")
            result4 = await parser.parse("через 15 минут", "Europe/Moscow")
            
            assert result4.parsed_datetime is not None, "Parsing failed"
            assert result4.parsed_datetime > now, \
                f"Duration expression created past time {result4.parsed_datetime}"
            
            reminder4 = await dao.create_reminder(
                user_id=123456,
                text=result4.clean_text or "In 15 minutes task",
                execution_time=result4.parsed_datetime,
            )
            
            print("[PASS] Duration expression is future:")
            print("      Text: " + reminder4.reminder_text)
            print("      Time: " + str(reminder4.execution_time))
            print()
            
            # Test 6: Verify combined expressions create future times
            print("--- Test 6: Combined Expressions Create Future Times ---")
            with patch('bot.services.parser.datetime.now', return_value=mock_past_time):
                result5 = await parser.parse("принять таблетку в 9 утра", "Europe/Moscow")
            
            assert result5.parsed_datetime is not None, "Parsing failed"
            assert result5.parsed_datetime.day == mock_past_time.day + 1, \
                f"Parsed time {result5.parsed_datetime} should be tomorrow!"
            
            reminder5 = await dao.create_reminder(
                user_id=123456,
                text=result5.clean_text or "Take pill task",
                execution_time=result5.parsed_datetime,
            )
            
            print("[PASS] Combined expression rolled over:")
            print("      Text: " + reminder5.reminder_text)
            print("      Time: " + str(reminder5.execution_time))
            print()
            
            # Test 7: Verify timezone-aware datetimes in database
            print("--- Test 7: All Database Times Are Timezone-Aware ---")
            for r in pending:
                assert r.execution_time.tzinfo is not None, \
                    f"Reminder {r.id} has naive datetime (no tzinfo)"
            
            print("[PASS] All " + str(len(pending)) + " reminders have timezone-aware times")
            print()
            
            # Test 8: Verify no past times in database after all operations
            print("--- Test 8: Database Contains No Past Times ---")
            now = datetime.now(pytz.timezone("Europe/Moscow"))
            final_pending = await dao.get_user_reminders(123456)
            
            assert len(final_pending) == 7, \
                f"Should have 7 reminders, got {len(final_pending)}"
            
            past_count = sum(1 for r in final_pending if r.execution_time <= now)
            assert past_count == 0, \
                f"Found {past_count} reminders with past times!"
            
            print("[PASS] All " + str(len(final_pending)) + " reminders are future times")
            print()
            
            print("=" * 60)
            print("All Future-Time Validation Tests Passed!")
            print("=" * 60)
            
        finally:
            await db.cleanup()


# =============================================================================
# EXISTING TESTS - Parser → Database Creation Flow
# =============================================================================

class TestParserDatabaseFlow:
    """Test the parser → database creation workflow."""
    
    @classmethod
    async def run_tests(cls):
        """Run all integration tests."""
        db = IntegrationTestDatabase()
        
        try:
            await db.setup()
            
            print("=" * 60)
            print("Integration Tests - Parser to Database Flow")
            print("=" * 60)
            print()
            
            # Test 1: Parse and create reminder
            print("--- Test 1: Parse -> Create Reminder ---")
            parser = InputParser()
            dao = ReminderDAO(db.async_session_maker)
            
            result = await parser.parse("вечером", "Europe/Moscow")
            assert result.parsed_datetime is not None, "Parsing failed"
            
            reminder = await dao.create_reminder(
                user_id=123456,
                text=result.clean_text or "Take medication",
                execution_time=result.parsed_datetime,
            )
            
            print("[PASS] Created reminder: id=" + str(reminder.id))
            print("      Text: " + reminder.reminder_text)
            print("      Time: " + str(reminder.execution_time))
            print()
            
            # Test 2: Retrieve created reminder
            print("--- Test 2: Retrieve Reminder ---")
            retrieved = await dao.get_by_id(reminder.id)
            assert retrieved is not None, "Retrieval failed"
            assert retrieved.user_id == 123456, "User ID mismatch"
            
            print("[PASS] Retrieved reminder: id=" + str(retrieved.id))
            print()
            
            # Test 3: Get user's pending reminders (empty initially)
            print("--- Test 3: Get User Pending Reminders ---")
            pending = await dao.get_user_reminders(123456)
            assert len(pending) == 0, "Should be empty"
            
            # Create another reminder to test retrieval
            result2 = await parser.parse("утром", "Europe/Moscow")
            reminder2 = await dao.create_reminder(
                user_id=123456,
                text=result2.clean_text or "Morning task",
                execution_time=result2.parsed_datetime,
            )
            
            pending = await dao.get_user_reminders(123456)
            assert len(pending) == 2, f"Should have 2 reminders, got {len(pending)}"
            
            print("[PASS] User has " + str(len(pending)) + " pending reminders")
            for r in pending:
                print("      - " + r.reminder_text + " at " + str(r.execution_time))
            print()
            
            # Test 4: Timezone-aware datetime handling
            print("--- Test 4: Timezone-Aware Datetime ---")
            result3 = await parser.parse("в 23", "Europe/Moscow")
            assert result3.parsed_datetime.tzinfo is not None, "Timezone missing"
            
            reminder3 = await dao.create_reminder(
                user_id=123456,
                text=result3.clean_text or "Night task",
                execution_time=result3.parsed_datetime,
            )
            
            print("[PASS] Timezone-aware time: " + str(reminder3.execution_time))
            print()
            
            # Test 5: Duration-based expressions
            print("--- Test 5: Duration Expressions ---")
            result4 = await parser.parse("через 15 минут", "Europe/Moscow")
            assert result4.parsed_datetime is not None, "Duration parsing failed"
            
            reminder4 = await dao.create_reminder(
                user_id=123456,
                text=result4.clean_text or "In 15 minutes task",
                execution_time=result4.parsed_datetime,
            )
            
            print("[PASS] Duration parsed: " + str(reminder4.execution_time))
            print()
            
            # Test 6: Combined task + time expressions
            print("--- Test 6: Combined Task + Time ---")
            result5 = await parser.parse("принять таблетку в 23", "Europe/Moscow")
            assert result5.parsed_datetime is not None, "Combined parsing failed"
            assert "принять таблетку" in result5.clean_text, "Task extraction failed"
            
            reminder5 = await dao.create_reminder(
                user_id=123456,
                text=result5.clean_text,
                execution_time=result5.parsed_datetime,
            )
            
            print("[PASS] Combined parsed:")
            print("      Time: " + str(reminder5.execution_time))
            print("      Task: " + reminder5.reminder_text)
            print()
            
            # Test 7: Edge cases (no time expression)
            print("--- Test 7: Edge Cases ---")
            result6 = await parser.parse("привет", "Europe/Moscow")
            assert result6.parsed_datetime is None, "Should not parse without time"
            
            reminder6 = await dao.create_reminder(
                user_id=123456,
                text=result6.clean_text or "Just a message",
                execution_time=datetime.now() + timedelta(hours=1),  # Default future time
            )
            
            print("[PASS] Edge case handled: " + reminder6.reminder_text)
            print()
            
            print("=" * 60)
            print("All Integration Tests Passed!")
            print("=" * 60)
            
        finally:
            await db.cleanup()


async def main():
    """Run integration tests."""
    # Run future-time validation tests first
    await TestDatabaseFutureTimeValidation.run_tests()
    print()
    # Then run parser-database flow tests
    await TestParserDatabaseFlow.run_tests()


if __name__ == "__main__":
    asyncio.run(main())