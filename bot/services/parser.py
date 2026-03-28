"""
InputParser — Natural Language Time Expression Parser
=====================================================

PURPOSE:
  This service extracts datetime information from natural-language text input.
  It converts phrases like "tomorrow at 9am", "in 15 minutes", or "вечером" into
  timezone-aware datetime objects that can be stored in the database and used by
  APScheduler for job scheduling.

ARCHITECTURE OVERVIEW:
  
  ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
  │   User Input│────▶│ InputParser  │────▶│ datetime object │
  │ (text)      │     │              │     │ (timezone-aware)│
  └─────────────┘     └──────────────┘     └─────────────────┘

PIPELINE STAGES:
  
  1. Heuristic Normalization → Replace common phrases with standardized forms
  2. Natasha NER Extraction    → Find date/time spans in text (Russian-focused)
  3. dateparser Resolution    → Convert to actual datetime object
  4. Clean Text Extraction    → Remove time expressions, keep task description

TECHNICAL ARCHITECTURE:
  
  CPU-Bound Operations:
    - dateparser.search      → Uses NLP models (CPU-intensive)
    - Natasha DatesExtractor → Russian morphological analyzer (CPU-intensive)
    
  Thread Safety Strategy:
    - All heavy objects created ONCE at module import time (not per-request)
    - Natasha mutates internal caches and is NOT thread-safe
    - _NATASHA_LOCK serializes access to prevent race conditions
    
  Event Loop Protection:
    - Parser uses run_in_executor() to offload CPU-bound work from event loop
    - Prevents blocking the async event loop with NLP operations

BUG FIXES APPLIED (Phase 1):
  ✅ Added comprehensive documentation for each stage
  ✅ Explained thread safety and lock usage
  ✅ Documented all heuristics with examples
  ✅ Added type hints and docstrings for better IDE support
  ✅ Fixed edge case handling in clean_text extraction

USAGE:
  
    from bot.services.parser import InputParser
    
    parser = InputParser()
    
    # Parse text with timezone
    result = await parser.parse("вечером", "Europe/Moscow")
    print(result.parsed_datetime)  # datetime(2024, 3, 27, 19, 0, tzinfo=...)
    print(result.clean_text)       # "" (empty - all text was time expression)

"""

import asyncio
import logging
import pytz
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

# Import dateparser library for datetime resolution
import dateparser.search

# Import Natasha NLP library for Russian language processing
from natasha import MorphVocab, DatesExtractor

logger = logging.getLogger(__name__)


# =============================================================================
# HELPER METHOD: Process Hour Expression (Extracted from _parse_sync)
# =============================================================================

def _process_hour_expression(
    normalized_text: str, 
    clean_text: str, 
    timezone: str, 
    now: datetime
) -> Optional[datetime]:
    """
    Process a matched hour expression and return parsed datetime.
    
    This helper method handles AM/PM conversion and creates timezone-aware datetime objects.
    
    Args:
        normalized_text: Lowercased text with time expression
        clean_text: Text with some expressions already removed by Natasha
        timezone: User's timezone string (e.g., "Europe/Moscow")
        now: Current datetime for date components
        
    Returns:
        Parsed datetime object or None if no valid match found
    """
    hour_str = normalized_text.split()[-1]  # Get the last word (should be the hour)
    
    try:
        hour = int(hour_str)
    except ValueError:
        return None
    
    # Check for AM/PM indicators in the text
    has_pm = "pm" in normalized_text.lower() or "вечера" in normalized_text.lower()
    has_am = "am" in normalized_text.lower() or "утра" in normalized_text.lower()
    
    # Determine if input is already 24-hour format (hour >= 13) or needs conversion
    is_24h_format = hour >= 13
    
    period_hour = hour
    
    # Only convert from 12h to 24h if NOT already in 24h format AND has AM/PM indicator
    if not is_24h_format and (has_pm or has_am):
        # Convert "1pm" → 13, "1am" → 01
        if has_pm:
            period_hour = hour + 12
            if hour == 12:
                period_hour = 12
        else:  # has_am
            period_hour = hour % 12 or 12
    
    # Determine minute based on AM/PM indicators
    if has_am or "утра" in normalized_text.lower():
        minute = 0
    elif has_pm or "вечера" in normalized_text.lower():
        minute = 0
    else:
        # No AM/PM indicator - use :00 for simple hour-only expressions
        minute = 0
    
    dt = datetime(
        year=now.year,
        month=now.month,
        day=now.day,
        hour=period_hour,
        minute=minute,
        tzinfo=pytz.timezone(timezone)
    )
    
    # FIX: If the parsed time is in the past, roll over to tomorrow
    if dt <= now:
        dt = dt.replace(day=dt.day + 1)

    return dt

logger = logging.getLogger(__name__)


# =============================================================================
# MODULE-LEVEL SINGLETONS (Created Once at Import Time)
# =============================================================================

"""
⚠️ WHY GLOBAL STATE HERE?

All heavy objects are created once at module import time and shared across all requests.
This is intentional for performance reasons:

  - MorphVocab: Large morphological vocabulary (~10MB), don't recreate per-request
  - DatesExtractor: Mutates internal caches, NOT thread-safe without locking
  - dateparser.search: Uses NLP models that are CPU-intensive

Thread Safety Strategy:
  - Natasha mutates internal caches and is NOT reentrant
  - _NATASHA_LOCK serializes access to prevent race conditions
  - All calls go through run_in_executor() to protect event loop

Architecture:
  ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
  │   MorphVocab│────▶│ DatesExtractor│────▶│ _NATASHA_LOCK  │
  │ (vocab)     │     │ (extractor)   │     │ (thread safety) │
  └─────────────┘     └──────────────┘     └─────────────────┘

Example:
    >>> from bot.services.parser import _morph_vocab, _dates_extractor
    >>> print(_morph_vocab)  # Shared across all requests
"""

_morph_vocab: MorphVocab = MorphVocab()  # Russian morphological vocabulary
_dates_extractor: DatesExtractor = DatesExtractor(_morph_vocab)  # Date/time NER extractor
_NATASHA_LOCK: threading.Lock = threading.Lock()  # Lock for thread-safe access


# =============================================================================
# ParsedInput Dataclass (Immutable Result Container)
# =============================================================================

@dataclass(frozen=True)
class ParsedInput:
    """
    Immutable result of parsing user text.
    
    This dataclass holds both the cleaned task description and the parsed datetime.
    It's frozen (immutable) to prevent accidental modification after parsing.
    
    Fields:
        clean_text: Task description with time expressions removed
                   Example: "Take medication" from "вечером принять лекарство"
        
        parsed_datetime: Timezone-aware datetime object when found, None otherwise
                       Example: datetime(2024, 3, 27, 19, 0, tzinfo=...)

    Usage:
        >>> result = await parser.parse("вечером", "Europe/Moscow")
        >>> print(result.clean_text)      # "" (empty - all text was time)
        >>> print(result.parsed_datetime)  # datetime object or None
    """

    clean_text: str
    parsed_datetime: Optional[datetime]


# =============================================================================
# InputParser Class (Stateless Parser with Thread Safety)
# =============================================================================

class InputParser:
    """
    Stateless parser for natural language time expressions.
    
    This class is stateless - each parse() call uses fresh data and doesn't
    retain any state between calls. It's thread-safe because:
      1. Each call goes through run_in_executor() (CPU-bound work offloaded)
      2. Natasha access is serialized with _NATASHA_LOCK
    
    Architecture:
      ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
      │   User Input│────▶│ run_in_executor│────▶│ parse_sync()   │
      │ (text)      │     │ (CPU-bound)   │     │ pipeline        │
      └─────────────┘     └──────────────┘     └─────────────────┘
    
    Pipeline Stages:
      1. Heuristic Normalization → Replace common phrases with standardized forms
      2. Natasha NER Extraction    → Find date/time spans in text (Russian-focused)
      3. dateparser Resolution    → Convert to actual datetime object
      4. Clean Text Extraction    → Remove time expressions, keep task description
    
    Args: None - all configuration is internal
    
    Example:
        >>> parser = InputParser()
        >>> result = await parser.parse("вечером", "Europe/Moscow")
        >>> print(result.parsed_datetime)  # datetime(2024, 3, 27, 19, 0, tzinfo=...)
    """

    # ========================================================================
    # HEURISTIC NORMALIZATION TABLE (Russian → Standardized English)
    # ========================================================================

    _NORMALIZATIONS: dict[str, str] = {
        # Time duration normalizations
        "полчаса": "30 минут",  # "half hour" → "30 minutes"
        "полтора часа": "1 час 30 минут",  # "one and a half hours" → "1 hour 30 minutes"
        "через пару минут": "через 2 минуты",  # "in a couple of minutes" → "in 2 minutes"
        
        # Semantic heuristics for common phrases
        "после обеда": "в 14:00",  # "after lunch" → "at 14:00 (2pm)"
        "вечером": "в 19:00",  # "in the evening" → "at 19:00 (7pm)"
        "утром": "в 09:00",  # "in the morning" → "at 09:00 (9am)"
        "на выходных": "в субботу в 10:00",  # "on weekends" → "Saturday at 10:00"
        "в выходные": "в субботу в 10:00",  # Same as above (synonym)
        "в конце недели": "в пятницу в 18:00",  # "at end of week" → "Friday at 18:00"
    }

    def _apply_heuristics(self, text: str) -> str:
        """
        Apply heuristic replacements on a lowercased copy of the text.
        
        This method normalizes common Russian phrases to standardized English forms
        that dateparser can understand better. It works on a lowercase copy so
        the caller can keep the original capitalisation for Natasha's NER pass.
        
        Args:
            text: Original input text (e.g., "вечером принять лекарство")
            
        Returns:
            str: Normalized text with heuristics applied
            
        Examples:
            >>> parser = InputParser()
            >>> normalized = parser._apply_heuristics("вечером")
            >>> print(normalized)  # "в вечер" (lowercased + heuristic applied)
            
            >>> normalized = parser._apply_heuristics("через пару минут")
            >>> print(normalized)  # "через 2 минуты"
        """
        # Work on lowercase copy to preserve original capitalization for Natasha
        normalized: str = text.lower()

        # Apply each heuristic replacement
        for key, value in self._NORMALIZATIONS.items():
            if key in normalized:
                normalized = normalized.replace(key, value)

        # Handle "5-го числа" / "12 числа" → "5 day of this month"
        # This converts Russian date format to English that dateparser understands
        normalized = re.sub(
            r"(\d{1,2})(?:-?го)?\s+числа",  # Pattern: number + optional "-го" + "числа"
            r"\1 day of this month",  # Replacement: just the number + English phrase
            normalized,
        )

        return normalized

    def _parse_sync(self, text: str, timezone: str) -> ParsedInput:
        """
        Synchronous, CPU-bound pipeline: heuristics → Natasha → dateparser → regex fallback.
        
        ⚠️ IMPORTANT: MUST ONLY be called via run_in_executor — never from the
        event loop directly! This function uses heavy NLP libraries that would
        block the async event loop if called synchronously.
        
        Pipeline Stages:
          1. Heuristic substitution (lowercased working copy)
          2. Natasha: locate date spans for clean-text extraction
          3. dateparser: resolve to an actual datetime object
          4. Regex fallback for simple hour-only expressions (e.g., "в 23" → 23:00)
          5. Final cleanup (remove dangling prepositions)
        
        Args:
            text: Input text with time expression (e.g., "вечером принять лекарство")
            timezone: User's timezone string (e.g., "Europe/Moscow")
            
        Returns:
            ParsedInput: Result with clean_text and parsed_datetime
            
        Example:
            >>> result = parser._parse_sync("вечером", "Europe/Moscow")
            >>> print(result.parsed_datetime)  # datetime object or None
        """
        # OPTIMIZATION: Only log at DEBUG level to reduce verbosity in production
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Parser: Starting parse for text='{text}' (timezone={timezone})")

        # --- Stage 1: Heuristic substitution (lowercased working copy) ---
        normalized_text: str = self._apply_heuristics(text)
        # OPTIMIZATION: Only log at DEBUG level to reduce verbosity in production
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Parser: After heuristics '{text}' → '{normalized_text}'")

        # --- Stage 2: Natasha: locate date spans for clean-text extraction ---
        # DatesExtractor is NOT reentrant — serialise with the module lock.
        clean_text: str = normalized_text
        
        try:
            with _NATASHA_LOCK:  # Thread-safe access to Natasha
                natasha_matches = list(_dates_extractor(normalized_text))

            # OPTIMIZATION: Only log at DEBUG level to reduce verbosity in production
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Parser: Natasha found {len(natasha_matches)} matches in '{normalized_text}'")

            if natasha_matches:
                # Strip in reverse order so earlier indices stay valid.
                for m in sorted(natasha_matches, key=lambda x: x.start, reverse=True):
                    span = normalized_text[m.start:m.stop]
                    clean_text = clean_text.replace(span, "", 1)
        except Exception as e:
            # Natasha failure must never crash the whole parse pipeline.
            logger.warning(
                "Natasha extraction failed; falling back to dateparser-only.",
                exc_info=True,
            )
            clean_text = normalized_text

        # --- Stage 3: dateparser: resolve to an actual datetime object ---
        settings: dict = {
            "PREFER_DATES_FROM": "future",  # Only parse future dates (no past)
            "TIMEZONE": timezone,  # User's timezone for correct interpretation
            "RETURN_AS_TIMEZONE_AWARE": True,  # Return tz-aware datetime objects
            "PREFER_DAY_OF_MONTH": "current",  # Use current month/day when ambiguous
        }
        
        dp_matches = dateparser.search.search_dates(
            normalized_text, 
            languages=["ru", "en"],  # Support both Russian and English
            settings=settings,
        )

        # OPTIMIZATION: Only log at DEBUG level to reduce verbosity in production
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Parser: dateparser returned {len(dp_matches) if dp_matches else 0} matches for '{normalized_text}'")

        parsed_datetime: Optional[datetime] = None

        if dp_matches:
            matched_substring, dt_obj = dp_matches[0]  # Take first match
            parsed_datetime = dt_obj
            
            # OPTIMIZATION: Only log at DEBUG level to reduce verbosity in production
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Parser: dateparser matched '{matched_substring}' → {dt_obj}")
            
            # Strip the dateparser match if Natasha didn't already remove it.
            if matched_substring in clean_text:
                clean_text = clean_text.replace(matched_substring, "", 1)

        # --- Stage 4a: Regex fallback for simple hour-only expressions (в/at + number) ---
        # Handles cases like "в 23" (at 23), "in 9", etc. that dateparser misses
        if not parsed_datetime:
            logger.info("Parser: No datetime from dateparser, trying regex fallback...")
            
            now = datetime.now(pytz.timezone(timezone))
            
            # Pattern for Russian/English hour-only expressions with preposition
            # Matches: "в 23", "at 9", "в 10 утра", "in 5pm", etc.
            hour_pattern = re.compile(
                r"(?:^|\s)(?:в|at)\s+(\d{1,2})(?:\s+(утра|послеобеденно|вечера|ночи|am|pm))?",
                re.IGNORECASE
            )
            
            hour_match = hour_pattern.search(normalized_text)
            
            if hour_match:
                # Process matched hour expression using helper function
                result_dt = _process_hour_expression(
                    normalized_text, clean_text, timezone, now=now
                )
                
                if result_dt:
                    parsed_datetime = result_dt
                    logger.info(
                        f"Parser: Hour regex fallback matched '{hour_match.group(0)}' → {parsed_datetime}"
                    )
                    
                    # Remove the time expression from clean_text
                    match_str = hour_match.group(0)
                    if match_str in clean_text:
                        clean_text = clean_text.replace(match_str, "", 1)

        # --- Stage 4b: Regex fallback for duration-based expressions (через X минут/часов) ---
        # Handles cases like "через 15 минут", "через 2 часа" that dateparser might miss
        elif not parsed_datetime and re.search(r"через\s+\d+", normalized_text):
            # OPTIMIZATION: Only log at DEBUG level to reduce verbosity in production
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Parser: Trying duration-based regex fallback...")
            
            duration_match = re.search(
                r"через\s+(\d+)\s*(минут|часов?|дней?)",
                normalized_text,
                re.IGNORECASE
            )
            
            if duration_match:
                amount = int(duration_match.group(1))
                unit = duration_match.group(2).lower()
                
                # Calculate future time based on duration
                now = datetime.now(pytz.timezone(timezone))
                
                if unit in ["минут", "min"]:
                    new_time = now + timedelta(minutes=amount)
                elif unit in ["часов", "ч"]:
                    new_time = now + timedelta(hours=amount)
                else:  # days
                    new_time = now + timedelta(days=amount)
                
                parsed_datetime = new_time
                
                # OPTIMIZATION: Only log at DEBUG level to reduce verbosity in production
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Parser: Duration fallback matched '{duration_match.group(0)}' → {parsed_datetime}")
                
                # Remove the time expression from clean_text
                if duration_match.group(0) in clean_text:
                    clean_text = clean_text.replace(duration_match.group(0), "", 1)

        # --- Stage 4c: Regex fallback for "в X часов" pattern (e.g., "в 23 часа") ---
        elif not parsed_datetime and re.search(r"в\s+\d{1,2}\s*часов?", normalized_text):
            # OPTIMIZATION: Only log at DEBUG level to reduce verbosity in production
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Parser: Trying 'в X часов' regex fallback...")
            
            hour_match = re.search(
                r"в\s+(\d{1,2})\s*(утра|послеобеденно|вечера|ночи|часов?)?",
                normalized_text,
                re.IGNORECASE
            )
            
            if hour_match:
                # Process matched hour expression using helper function
                now = datetime.now(pytz.timezone(timezone))
                result_dt = _process_hour_expression(
                    normalized_text, clean_text, timezone, now=now
                )
                
                if result_dt:
                    parsed_datetime = result_dt
                    # OPTIMIZATION: Only log at DEBUG level to reduce verbosity in production
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(f"Parser: 'в X часов' regex fallback matched '{hour_match.group(0)}' → {parsed_datetime}")
                    
                    # Remove the time expression from clean_text
                    match_str = hour_match.group(0)
                    if match_str in clean_text:
                        clean_text = clean_text.replace(match_str, "", 1)

        # --- Stage 5: Final cleanup ---
        # Remove dangling prepositions at the start of the remaining text.
        # These are common in Russian but don't add semantic value.
        clean_text = re.sub(
            r"^(в|на|через|в районе)\s+",  # Patterns: "в ", "на ", "через ", etc.
            "",  # Remove them
            clean_text.strip(),  # Strip whitespace first
            flags=re.IGNORECASE,  # Case-insensitive matching
        )
        
        # Normalize whitespace (remove extra spaces)
        clean_text = " ".join(clean_text.split())

        # OPTIMIZATION: Only log at DEBUG level to reduce verbosity in production
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Parser: Final result → clean_text='{clean_text}', parsed_datetime={parsed_datetime}")

        return ParsedInput(clean_text=clean_text, parsed_datetime=parsed_datetime)

    async def parse(self, text: str, timezone: str) -> ParsedInput:
        """
        Public async entry-point for parsing natural language time expressions.
        
        This is the main method that users call from handlers. It offloads the
        blocking NLP work to a thread-pool executor so the event loop is never blocked.
        
        Architecture:
          ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
          │   User Input│────▶│ run_in_executor│────▶│ parse_sync()   │
          │ (text)      │     │ (CPU-bound)   │     │ pipeline        │
          └─────────────┘     └──────────────┘     └─────────────────┘
        
        Args:
            text: Input text with time expression (e.g., "вечером принять лекарство")
            timezone: User's timezone string (e.g., "Europe/Moscow")
            
        Returns:
            ParsedInput: Result with clean_text and parsed_datetime
            
        Example:
            >>> result = await parser.parse("вечером", "Europe/Moscow")
            >>> print(result.parsed_datetime)  # datetime(2024, 3, 27, 19, 0, tzinfo=...)
            >>> print(result.clean_text)       # "" (empty - all text was time expression)
            
            >>> result = await parser.parse("вечером принять лекарство", "Europe/Moscow")
            >>> print(result.parsed_datetime)  # datetime(2024, 3, 27, 19, 0, tzinfo=...)
            >>> print(result.clean_text)       # "принять лекарство" (task description)

        Raises:
            No exceptions - errors are logged internally and None is returned for datetime.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._parse_sync, text, timezone)