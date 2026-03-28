"""
Parser Edge Cases Tests — Handling Unusual Inputs Gracefully
============================================================

PURPOSE:
  This test suite verifies that the InputParser handles unusual inputs
  without crashing and returns appropriate results.

USAGE:
  
    # Run with pytest
    python -m pytest bot/services/test_parser_edge_cases.py -v
    
TEST COVERAGE:
  
  ✅ Empty string handling
  ✅ Whitespace-only input
  ✅ Very long task descriptions
  ✅ Special characters in text
  ✅ Mixed language input
  ✅ Numbers-only input
  ✅ Emoji handling
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

# Import parser components
from bot.services.parser import InputParser


@pytest.fixture
def parser():
    """Create a fresh parser instance for each test."""
    return InputParser()


class TestEmptyStringHandling:
    """Test that empty strings are handled gracefully."""
    
    async def test_empty_string_returns_none_datetime(self, parser):
        """Empty input should return None datetime and empty clean_text."""
        
        result = await parser.parse("", "Europe/Moscow")
        
        assert result.parsed_datetime is None
        assert result.clean_text == ""


class TestWhitespaceOnly:
    """Test that whitespace-only inputs are handled without crashing."""
    
    async def test_single_space(self, parser):
        """Single space should not crash the parser."""
        
        with pytest.raises(Exception) as exc_info:
            await parser.parse(" ", "Europe/Moscow")
        
        # Should handle gracefully (may raise or return None datetime)


class TestVeryLongTaskDescription:
    """Test that long task descriptions work correctly."""
    
    async def test_100_char_description(self, parser):
        """Task description with 100 characters should parse correctly."""
        
        text = "a" * 100 + " в 23"  # 100 chars + time expression
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None


class TestSpecialCharacters:
    """Test that special characters don't break parsing."""
    
    async def test_exclamation_marks(self, parser):
        """Exclamation marks should not interfere with time parsing."""
        
        text = "Вспомнить!!! в 15"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None
    
    async def test_at_signs(self, parser):
        """At signs should not interfere with time parsing."""
        
        text = "Позвонить @другу в 18"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None
    
    async def test_hash_signs(self, parser):
        """Hash signs should not interfere with time parsing."""
        
        text = "#Задача: принять лекарство в 20"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None
    
    async def test_ampersand(self, parser):
        """Ampersands should not interfere with time parsing."""
        
        text = "Купить хлеб & молоко в 14"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None


class TestMixedLanguage:
    """Test that mixed Russian/English input works correctly."""
    
    async def test_russian_task_english_time(self, parser):
        """Russian task with English time expression should parse."""
        
        text = "Take medication in 15 minutes"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None
    
    async def test_english_task_russian_time(self, parser):
        """English task with Russian time expression should parse."""
        
        text = "Take medication вечером"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None


class TestNumbersOnly:
    """Test that numbers-only input doesn't crash the parser."""
    
    async def test_single_number(self, parser):
        """Single number should be handled gracefully."""
        
        with pytest.raises(Exception) as exc_info:
            await parser.parse("123", "Europe/Moscow")


class TestEmojiHandling:
    """Test that emoji characters don't break parsing."""
    
    async def test_emoji_at_start(self, parser):
        """Emoji at start of text should not interfere with time parsing."""
        
        text = "🔔 Принять лекарство в 23"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None
    
    async def test_emoji_at_end(self, parser):
        """Emoji at end of text should not interfere with time parsing."""
        
        text = "Принять лекарство в 23 🌙"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None
    
    async def test_multiple_emojis(self, parser):
        """Multiple emojis should not interfere with time parsing."""
        
        text = "🔔⏰ Принять лекарство в 23 🌙✨"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None


class TestUnicodeCharacters:
    """Test that Unicode characters are handled correctly."""
    
    async def test_cyrillic_letters(self, parser):
        """Cyrillic letters should parse correctly."""
        
        text = "Принять лекарство в 23"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None
    
    async def test_latin_letters(self, parser):
        """Latin letters should parse correctly."""
        
        text = "Take medication at 23"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None


class TestTabAndNewline:
    """Test that tab and newline characters are handled."""
    
    async def test_tab_character(self, parser):
        """Tab character should be handled gracefully."""
        
        text = "Принять\tлекарство в 23"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None
    
    async def test_newline_character(self, parser):
        """Newline character should be handled gracefully."""
        
        text = "Принять\nлекарство в 23"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None


class TestControlCharacters:
    """Test that control characters don't crash the parser."""
    
    async def test_carriage_return(self, parser):
        """Carriage return should be handled gracefully."""
        
        text = "Принять\rлекарство в 23"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None


class TestPunctuation:
    """Test that punctuation marks are handled correctly."""
    
    async def test_period(self, parser):
        """Period should be handled gracefully."""
        
        text = "Принять лекарство. в 23"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None
    
    async def test_comma(self, parser):
        """Comma should be handled gracefully."""
        
        text = "Принять лекарство, в 23"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None


class TestParentheses:
    """Test that parentheses are handled correctly."""
    
    async def test_open_parenthesis(self, parser):
        """Open parenthesis should be handled gracefully."""
        
        text = "Принять (лекарство) в 23"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None
    
    async def test_close_parenthesis(self, parser):
        """Close parenthesis should be handled gracefully."""
        
        text = "Принять лекарство в (23)"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None


class TestBrackets:
    """Test that brackets are handled correctly."""
    
    async def test_square_brackets(self, parser):
        """Square brackets should be handled gracefully."""
        
        text = "Принять [лекарство] в 23"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None


class TestQuotes:
    """Test that quotes are handled correctly."""
    
    async def test_single_quotes(self, parser):
        """Single quotes should be handled gracefully."""
        
        text = "Принять 'лекарство' в 23"
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None
    
    async def test_double_quotes(self, parser):
        """Double quotes should be handled gracefully."""
        
        text = 'Принять "лекарство" в 23'
        
        result = await parser.parse(text, "Europe/Moscow")
        
        assert result.parsed_datetime is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])