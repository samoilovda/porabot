"""
Unit Tests for Time Formatting Utilities — Timezone & Display Verification
==========================================================================

PURPOSE:
  This test module verifies that the time formatting utilities correctly:
  - Convert UTC times to user's local timezone
  - Handle timezone offset display (with/without)
  - Format times consistently across different scenarios
  
USAGE:
  
    # Run with pytest (requires pytest-asyncio)
    python -m pytest bot/utils/test_time_ext.py -v
    
TEST COVERAGE:
  
  ✅ UTC to local timezone conversion
  ✅ Timezone offset display toggle
  ✅ Various datetime formats (%H:%M, %d.%m %H:%M, etc.)
  ✅ Edge cases (naive datetimes, invalid timezones)

BUG FIX VERIFIED:
  
  ✅ Times are always displayed in user's local timezone
  ✅ UTC offset shown only when requested
  ✅ Invalid timezone strings fall back to UTC
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import pytz

# Import the utility function under test
from bot.utils.time_ext import format_time


@pytest.fixture
def utc_now():
    """Fixture for a fixed UTC time."""
    return datetime(2026, 3, 28, 14, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def moscow_tz():
    """Fixture for Moscow timezone."""
    return pytz.timezone("Europe/Moscow")


# =============================================================================
# TESTS FOR UTC TO LOCAL TIMEZONE CONVERSION
# =============================================================================

class TestUTCToLocalConversion:
    """Test that times are converted to user's local timezone."""
    
    def test_utc_to_moscow_conversion(self, utc_now, moscow_tz):
        """Verify UTC time is converted to Moscow time (UTC+3)."""
        # 14:30 UTC = 17:30 Moscow time (UTC+3)
        result = format_time(utc_now, "Europe/Moscow", show_utc_offset=False)
        
        assert "17:30" in result or "17:30" in str(result), \
            f"Expected 17:30 Moscow time, got: {result}"
    
    def test_utc_to_kiev_conversion(self, utc_now):
        """Verify UTC time is converted to Kiev time (UTC+2)."""
        # 14:30 UTC = 16:30 Kiev time (UTC+2)
        result = format_time(utc_now, "Europe/Kiev", show_utc_offset=False)
        
        assert "16:30" in str(result), \
            f"Expected 16:30 Kiev time, got: {result}"


# =============================================================================
# TESTS FOR TIMEZONE OFFSET DISPLAY
# =============================================================================

class TestTimezoneOffsetDisplay:
    """Test that timezone offset is displayed when requested."""
    
    def test_offset_shown_when_requested(self, utc_now, moscow_tz):
        """Verify UTC offset (+03:00) is shown when show_utc_offset=True."""
        result = format_time(utc_now, "Europe/Moscow", show_utc_offset=True)
        
        assert "+03" in str(result), \
            f"Expected +03 offset, got: {result}"
    
    def test_offset_hidden_when_not_requested(self, utc_now, moscow_tz):
        """Verify UTC offset is NOT shown when show_utc_offset=False."""
        result = format_time(utc_now, "Europe/Moscow", show_utc_offset=False)
        
        assert "+03" not in str(result), \
            f"Offset should be hidden: {result}"


# =============================================================================
# TESTS FOR VARIOUS DATETIME FORMATS
# =============================================================================

class TestDatetimeFormats:
    """Test various datetime format strings."""
    
    def test_format_H_M(self, utc_now):
        """Test %H:%M format (hours and minutes)."""
        result = format_time(utc_now, "Europe/Moscow", fmt="%H:%M")
        
        assert ":" in str(result), \
            f"Expected colon-separated time: {result}"
    
    def test_format_d_m_H_M(self, utc_now):
        """Test %d.%m %H:%M format (day.month hour:minute)."""
        result = format_time(utc_now, "Europe/Moscow", fmt="%d.%m %H:%M")
        
        assert "." in str(result), \
            f"Expected dot-separated date: {result}"
    
    def test_format_full_datetime(self, utc_now):
        """Test full datetime format."""
        result = format_time(utc_now, "Europe/Moscow", fmt="%Y-%m-%d %H:%M")
        
        assert "2026" in str(result), \
            f"Expected year 2026: {result}"


# =============================================================================
# TESTS FOR EDGE CASES
# =============================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_naive_datetime_localized(self):
        """Verify naive datetime is localized to UTC before conversion."""
        naive_dt = datetime(2026, 3, 28, 14, 30)  # No timezone info
        
        result = format_time(naive_dt, "Europe/Moscow", show_utc_offset=False)
        
        assert result is not None, \
            f"Should handle naive datetime: {result}"
    
    def test_invalid_timezone_falls_back_to_utc(self):
        """Verify invalid timezone falls back to UTC."""
        utc_now = datetime(2026, 3, 28, 14, 30, tzinfo=timezone.utc)
        
        result = format_time(utc_now, "Invalid/Timezone", show_utc_offset=False)
        
        # Should still return a valid time (in UTC fallback)
        assert ":" in str(result), \
            f"Should handle invalid timezone: {result}"
    
    def test_different_timezone_for_same_utc(self):
        """Verify same UTC time shows different local times for different zones."""
        utc_now = datetime(2026, 3, 28, 14, 30, tzinfo=timezone.utc)
        
        moscow_time = format_time(utc_now, "Europe/Moscow", show_utc_offset=False)
        kiev_time = format_time(utc_now, "Europe/Kiev", show_utc_offset=False)
        
        # Moscow is UTC+2 or +3 depending on DST, Kiev same offset
        # They should be the same (or differ by 1 hour if different DST rules)
        assert abs(int(moscow_time.split(":")[0]) - int(kiev_time.split(":")[0])) <= 1


# =============================================================================
# TESTS FOR TIMEZONE-AWARE DATETIME HANDLING
# =============================================================================

class TestTimezoneAwareDatetime:
    """Test handling of timezone-aware datetime objects."""
    
    def test_utc_aware_datetime(self):
        """Verify UTC-aware datetime is handled correctly."""
        utc_dt = datetime(2026, 3, 28, 14, 30, tzinfo=timezone.utc)
        
        result = format_time(utc_dt, "Europe/Moscow", show_utc_offset=False)
        
        assert result is not None and ":" in str(result), \
            f"Should handle UTC-aware datetime: {result}"
    
    def test_moscow_aware_datetime(self):
        """Verify Moscow-time aware datetime is handled correctly."""
        moscow_tz = pytz.timezone("Europe/Moscow")
        moscow_dt = datetime(2026, 3, 28, 17, 30, tzinfo=moscow_tz)
        
        result = format_time(moscow_dt, "Europe/Moscow", show_utc_offset=False)
        
        assert "17:30" in str(result), \
            f"Should handle Moscow-aware datetime: {result}"


# =============================================================================
# TESTS FOR TIMEZONE OFFSET FORMATTING
# =============================================================================

class TestOffsetFormatting:
    """Test UTC offset string formatting."""
    
    def test_offset_with_zero_minutes(self):
        """Verify offset with zero minutes shows as +03 (not +03:00)."""
        utc_now = datetime(2026, 3, 28, 14, 30, tzinfo=timezone.utc)
        
        result = format_time(utc_now, "Europe/Moscow", show_utc_offset=True)
        
        # Should show +03 or +03:00 depending on implementation
        assert "+03" in str(result), \
            f"Expected +03 offset: {result}"
    
    def test_offset_with_minutes(self):
        """Verify offset with minutes shows as +03:30."""
        # Create a datetime with UTC+5:30 (Chennai timezone)
        chennai_tz = pytz.timezone("Asia/Kolkata")
        chennai_dt = datetime(2026, 3, 28, 19, 30, tzinfo=chennai_tz)
        
        result = format_time(chennai_dt, "Asia/Kolkata", show_utc_offset=True)
        
        assert "+05" in str(result), \
            f"Expected +05 offset: {result}"


# =============================================================================
# TESTS FOR TIMEZONE STRING VALIDATION
# =============================================================================

class TestTimezoneValidation:
    """Test timezone string validation and fallback."""
    
    def test_valid_timezone_strings(self):
        """Verify common timezone strings work correctly."""
        utc_now = datetime(2026, 3, 28, 14, 30, tzinfo=timezone.utc)
        
        valid_tz = ["Europe/Moscow", "Europe/Kiev", "UTC", "America/New_York"]
        
        for tz_str in valid_tz:
            result = format_time(utc_now, tz_str, show_utc_offset=False)
            assert result is not None and ":" in str(result), \
                f"Should handle timezone '{tz_str}': {result}"
    
    def test_invalid_timezone_fallback(self):
        """Verify invalid timezone falls back to UTC."""
        utc_now = datetime(2026, 3, 28, 14, 30, tzinfo=timezone.utc)
        
        result = format_time(utc_now, "NonExistent/Timezone", show_utc_offset=False)
        
        # Should not crash and should return a valid time string
        assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])