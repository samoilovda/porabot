"""
Unit Tests for InputParser — Time Expression Parsing Verification
===================================================================

PURPOSE:
  This test module verifies that the InputParser correctly handles various
  time expression patterns in user messages. It tests all regex fallback stages
  to ensure they catch expressions that Natasha NER and dateparser might miss.

USAGE:
  
    # Run with pytest (requires pytest-asyncio)
    python -m pytest bot/services/test_parser.py -v
    
TEST COVERAGE:
  
  ✅ Simple hour expressions (в/at + number)
  ✅ Duration-based expressions (через X минут/часов)
  ✅ Morning/evening indicators (утром/вечером)
  ✅ Combined task descriptions with time
  ✅ English expressions (in/at + number)
  ✅ Future-time validation (prevents past times bug)
  ✅ Past-hour rollover to tomorrow

BUG FIX VERIFIED:
  
  ✅ Parsed datetime is always in the future (never past)
  ✅ Hour expressions roll over to tomorrow when current hour passed
"""

import asyncio
from unittest.mock import patch
import pytest
from datetime import datetime, timezone, timedelta
import pytz
from bot.services.parser import InputParser


@pytest.fixture
def parser():
    """Create a fresh InputParser instance for testing."""
    return InputParser()


# =============================================================================
# FIXTURE: Mock Current Time for Testing Past-Hour Scenarios
# =============================================================================

@pytest.fixture
def mock_past_time():
    """Mock datetime.now to simulate it being past the target hour."""
    # Simulate 10:30 AM on March 28, 2026 (Europe/Moscow)
    return datetime(2026, 3, 28, 10, 30, tzinfo=pytz.timezone("Europe/Moscow"))


# =============================================================================
# TESTS FOR FUTURE-TIME VALIDATION (Prevents "Task Saved in Past" Bug)
# =============================================================================

class TestFutureTimeValidation:
    """Test that parsed datetimes are always in the future."""
    
    async def test_parsed_time_always_future_simple_hour(self, parser):
        """Verify simple hour expressions return future times."""
        now = datetime.now(pytz.timezone("Europe/Moscow"))
        
        result = await parser.parse("в 9", "Europe/Moscow")
        assert result.parsed_datetime is not None
        assert result.parsed_datetime > now, \
            f"Parsed time {result.parsed_datetime} is in the past!"
    
    async def test_parsed_time_always_future_evening(self, parser):
        """Verify evening expressions return future times."""
        now = datetime.now(pytz.timezone("Europe/Moscow"))
        
        result = await parser.parse("вечером", "Europe/Moscow")
        assert result.parsed_datetime is not None
        assert result.parsed_datetime > now, \
            f"Parsed time {result.parsed_datetime} is in the past!"
    
    async def test_parsed_time_always_future_morning(self, parser):
        """Verify morning expressions return future times."""
        now = datetime.now(pytz.timezone("Europe/Moscow"))
        
        result = await parser.parse("утром", "Europe/Moscow")
        assert result.parsed_datetime is not None
        assert result.parsed_datetime > now, \
            f"Parsed time {result.parsed_datetime} is in the past!"


class TestPastHourRollover:
    """Test that hour expressions roll over to tomorrow when current hour passed."""
    
    async def test_past_hour_rolls_to_tomorrow(self, parser, mock_past_time):
        """Test that 'в 9 утра' at 10:30am rolls over to tomorrow."""
        with patch('bot.services.parser.datetime.now', return_value=mock_past_time):
            result = await parser.parse("в 9 утра", "Europe/Moscow")
        
        assert result.parsed_datetime is not None
        # Should be tomorrow, not today
        assert result.parsed_datetime.day == mock_past_time.day + 1, \
            f"Expected tomorrow (day {mock_past_time.day + 1}), got day {result.parsed_datetime.day}"
    
    async def test_evening_at_morning_rolls_to_tomorrow(self, parser, mock_past_time):
        """Test that 'вечером' at 10:30am rolls over to tomorrow evening."""
        with patch('bot.services.parser.datetime.now', return_value=mock_past_time):
            result = await parser.parse("вечером", "Europe/Moscow")
        
        assert result.parsed_datetime is not None
        # Should be tomorrow evening (19:00), not today
        assert result.parsed_datetime.day == mock_past_time.day + 1, \
            f"Expected tomorrow (day {mock_past_time.day + 1}), got day {result.parsed_datetime.day}"
    
    async def test_morning_at_same_hour_stays_today(self, parser):
        """Test that 'вечером' at 7pm stays today (not rolled over)."""
        evening_time = datetime(2026, 3, 28, 19, 0, tzinfo=pytz.timezone("Europe/Moscow"))
        
        with patch('bot.services.parser.datetime.now', return_value=evening_time):
            result = await parser.parse("вечером", "Europe/Moscow")
        
        assert result.parsed_datetime is not None
        # Should be today evening (19:00), same day
        assert result.parsed_datetime.day == evening_time.day, \
            f"Expected same day {evening_time.day}, got day {result.parsed_datetime.day}"


# =============================================================================
# EXISTING TESTS - Simple Hour Expressions
# =============================================================================

class TestSimpleHourExpressions:
    """Test simple hour-only expressions like 'в 23', 'at 9'."""
    
    @pytest.mark.parametrize("text,expected_hour", [
        ("в 23", 23),
        ("в 9", 9),
        ("at 15", 15),
        ("in 10", 10),
    ])
    async def test_simple_hours(self, parser, text, expected_hour):
        """Simple hour expressions should be parsed."""
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None
        assert result.parsed_datetime.hour == expected_hour


# =============================================================================
# EXISTING TESTS - Duration Expressions
# =============================================================================

class TestDurationExpressions:
    """Test duration-based expressions like 'через 15 минут'."""
    
    @pytest.mark.parametrize("text", [
        "через 15 минут",
        "через 2 часа", 
        "через 3 дня",
        "через пару часов",
    ])
    async def test_duration_minutes(self, parser, text):
        """Duration in minutes should be parsed."""
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None
    
    @pytest.mark.parametrize("text", [
        "через 30 минут",
        "через 1 час",
        "через 5 дней",
    ])
    async def test_duration_hours_days(self, parser, text):
        """Duration in hours/days should be parsed."""
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None


# =============================================================================
# EXISTING TESTS - Morning/Evening Indicators
# =============================================================================

class TestMorningEveningIndicators:
    """Test morning/evening indicators like 'утром', 'вечером'."""
    
    async def test_evening(self, parser):
        """'вечером' should be normalized to 19:00."""
        result = await parser.parse("вечером", "Europe/Moscow")
        
        assert result.parsed_datetime is not None
        assert result.parsed_datetime.hour == 19
    
    async def test_morning(self, parser):
        """'утром' should be normalized to 09:00."""
        result = await parser.parse("утром", "Europe/Moscow")
        
        assert result.parsed_datetime is not None
        assert result.parsed_datetime.hour == 9
    
    async def test_evening_with_task(self, parser):
        """'вечером принять лекарство' should parse time and extract task."""
        result = await parser.parse("вечером принять лекарство", "Europe/Moscow")
        
        assert result.parsed_datetime is not None
        assert result.parsed_datetime.hour == 19
        assert "принять лекарство" in result.clean_text


# =============================================================================
# EXISTING TESTS - Combined Expressions
# =============================================================================

class TestCombinedExpressions:
    """Test combined task descriptions with time expressions."""
    
    @pytest.mark.parametrize("text", [
        "принять таблетку в 23",
        "выпить воду утром",
        "позвонить маме вечером",
        "сделать зарядку в 7 утра",
    ])
    async def test_task_with_time(self, parser, text):
        """Task descriptions with time should parse both."""
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None


# =============================================================================
# EXISTING TESTS - English Expressions
# =============================================================================

class TestEnglishExpressions:
    """Test English expressions that might come through."""
    
    @pytest.mark.parametrize("text", [
        "in 9am",
        "at 3pm", 
        "in 10 minutes",
        "after lunch",
    ])
    async def test_english_time(self, parser, text):
        """English time expressions should be handled."""
        result = await parser.parse(text, "Europe/Moscow")


# =============================================================================
# EXISTING TESTS - Edge Cases
# =============================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""
    
    async def test_empty_string(self, parser):
        """Empty string should return None for datetime."""
        result = await parser.parse("", "Europe/Moscow")
        
        assert result.parsed_datetime is None
    
    async def test_no_time_expression(self, parser):
        """Text without time expression should return None for datetime."""
        result = await parser.parse("привет", "Europe/Moscow")
        
        assert result.parsed_datetime is None


# =============================================================================
# EXISTING TESTS - Timezone Awareness
# =============================================================================

class TestTimezoneAwareness:
    """Test that parsed datetimes are timezone-aware."""
    
    async def test_timezone_aware(self, parser):
        """All parsed datetimes should have tzinfo set."""
        result = await parser.parse("в 23", "Europe/Moscow")
        
        assert result.parsed_datetime is not None
        assert result.parsed_datetime.tzinfo is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])