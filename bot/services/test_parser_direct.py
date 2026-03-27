"""
Direct Test Script for InputParser — Time Expression Parsing Verification
==========================================================================

PURPOSE:
  This simple test script verifies that the InputParser correctly handles various
  time expression patterns in user messages. It can be run directly without pytest.

USAGE:
  
    # Run with Python (from project root)
    python -m bot.services.test_parser_direct
    
TEST COVERAGE:
  
  ✅ Simple hour expressions (в/at + number)
  ✅ Duration-based expressions (через X минут/часов)
  ✅ Morning/evening indicators (утром/вечером)
  ✅ Combined task descriptions with time
  ✅ English expressions (in/at + number)

OUTPUT FORMAT:
  
    Each test prints:
      - Input message
      - Parsed datetime (or "None")
      - Clean text (task description without time)
"""

import asyncio
import sys
from bot.services.parser import InputParser


# Set UTF-8 encoding for console output on Windows
if sys.platform == 'win32':
    try:
        import locale
        locale.setlocale(locale.LC_ALL, 'Russian_Russia.UTF-8')
    except:
        pass

async def run_tests():
    """Run all parser tests and print results."""
    parser = InputParser()
    
    print("=" * 60)
    print("InputParser - Time Expression Parsing Tests")
    print("=" * 60)
    print()
    
    # Test Group 1: Simple hour expressions
    print("--- Simple Hour Expressions ---")
    simple_tests = [
        ("в 23", 23),
        ("в 9", 9),
        ("at 15", 15),
        ("in 10", 10),
    ]
    
    for text, expected_hour in simple_tests:
        result = await parser.parse(text, "Europe/Moscow")
        status = "[PASS]" if result.parsed_datetime and result.parsed_datetime.hour == expected_hour else "[FAIL]"
        print(f"{status} '{text}' -> {result.parsed_datetime or 'None'}, clean_text='{result.clean_text}'")
    print()
    
    # Test Group 2: Duration-based expressions
    print("--- Duration Expressions ---")
    duration_tests = [
        "через 15 минут",
        "через 2 часа", 
        "через 3 дня",
        "через пару часов",
    ]
    
    for text in duration_tests:
        result = await parser.parse(text, "Europe/Moscow")
        status = "[PASS]" if result.parsed_datetime else "[FAIL]"
        print(f"{status} '{text}' -> {result.parsed_datetime or 'None'}, clean_text='{result.clean_text}'")
    print()
    
    # Test Group 3: Morning/evening indicators
    print("--- Morning/Evening Indicators ---")
    
    result = await parser.parse("вечером", "Europe/Moscow")
    status = "[PASS]" if result.parsed_datetime and result.parsed_datetime.hour == 19 else "[FAIL]"
    print(f"{status} 'вечером' -> {result.parsed_datetime or 'None'}, clean_text='{result.clean_text}'")
    
    result = await parser.parse("утром", "Europe/Moscow")
    status = "[PASS]" if result.parsed_datetime and result.parsed_datetime.hour == 9 else "[FAIL]"
    print(f"{status} 'утром' -> {result.parsed_datetime or 'None'}, clean_text='{result.clean_text}'")
    
    result = await parser.parse("вечером принять лекарство", "Europe/Moscow")
    status = "[PASS]" if result.parsed_datetime and result.parsed_datetime.hour == 19 else "[FAIL]"
    print(f"{status} 'вечером принять лекарство' -> {result.parsed_datetime or 'None'}, clean_text='{result.clean_text}'")
    print()
    
    # Test Group 4: Combined task descriptions with time
    print("--- Combined Task + Time ---")
    combined_tests = [
        "принять таблетку в 23",
        "выпить воду утром",
        "позвонить маме вечером",
        "сделать зарядку в 7 утра",
    ]
    
    for text in combined_tests:
        result = await parser.parse(text, "Europe/Moscow")
        status = "[PASS]" if result.parsed_datetime else "[FAIL]"
        print(f"{status} '{text}' -> {result.parsed_datetime or 'None'}, clean_text='{result.clean_text}'")
    print()
    
    # Test Group 5: English expressions
    print("--- English Expressions ---")
    english_tests = [
        "in 9am",
        "at 3pm", 
        "in 10 minutes",
        "after lunch",
    ]
    
    for text in english_tests:
        result = await parser.parse(text, "Europe/Moscow")
        status = "[PASS]" if result.parsed_datetime else "[FAIL]"
        print(f"{status} '{text}' -> {result.parsed_datetime or 'None'}, clean_text='{result.clean_text}'")
    print()
    
    # Test Group 6: Edge cases
    print("--- Edge Cases ---")
    edge_tests = [
        ("", None),
        ("привет", None),
    ]
    
    for text, expected in edge_tests:
        result = await parser.parse(text, "Europe/Moscow")
        status = "[PASS]" if (result.parsed_datetime is None) == (expected is None) else "[FAIL]"
        print(f"{status} '{text}' -> {result.parsed_datetime or 'None'}, clean_text='{result.clean_text}'")
    print()
    
    # Test Group 7: Timezone awareness
    print("--- Timezone Awareness ---")
    result = await parser.parse("в 23", "Europe/Moscow")
    status = "[PASS]" if result.parsed_datetime and result.parsed_datetime.tzinfo else "[FAIL]"
    print(f"{status} 'в 23' -> {result.parsed_datetime or 'None'}, tzinfo={result.parsed_datetime.tzinfo if result.parsed_datetime else 'N/A'}")
    print()
    
    # Summary
    print("=" * 60)
    print("Tests Complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_tests())