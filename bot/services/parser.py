"""
InputParser — Natural Language Time Expression Parser.

Converts user text like "вечером принять лекарство" or "in 15 minutes" into
a timezone-aware datetime + a cleaned task description string.

Pipeline:
  1. Heuristic normalization  → replace common Russian phrases with standardised forms
  2. Natasha NER              → locate date/time spans for clean-text extraction
  3. dateparser               → resolve the normalised string to a datetime object
  4. Regex fallbacks          → handle simple hour-only and duration expressions

Thread safety: Natasha's DatesExtractor is not reentrant; all access is
serialised via _NATASHA_LOCK. The blocking _parse_sync is offloaded to a
thread-pool executor so the async event loop is never blocked.
"""

import asyncio
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import dateparser.search
import pytz
from natasha import DatesExtractor, MorphVocab

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singletons (created once, shared across all requests)
# ---------------------------------------------------------------------------
_morph_vocab: MorphVocab = MorphVocab()
_dates_extractor: DatesExtractor = DatesExtractor(_morph_vocab)
_NATASHA_LOCK: threading.Lock = threading.Lock()


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedInput:
    """Immutable result of parsing user text.

    Attributes:
        clean_text:      Task description with time expressions removed.
        parsed_datetime: Timezone-aware datetime when found, else None.
    """
    clean_text: str
    parsed_datetime: Optional[datetime]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class InputParser:
    """Stateless NLP parser for natural-language time expressions.

    Usage::

        parser = InputParser()
        result = await parser.parse("вечером принять лекарство", "Europe/Moscow")
        # result.parsed_datetime → datetime(…, 19, 0, tzinfo=…)
        # result.clean_text      → "принять лекарство"
    """

    _NORMALIZATIONS: dict[str, str] = {
        "полчаса": "30 минут",
        "полтора часа": "1 час 30 минут",
        "через пару минут": "через 2 минуты",
        "после обеда": "в 14:00",
        "вечером": "в 19:00",
        "утром": "в 09:00",
        "на выходных": "в субботу в 10:00",
        "в выходные": "в субботу в 10:00",
        "в конце недели": "в пятницу в 18:00",
    }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_heuristics(self, text: str) -> str:
        """Lower-case the text and apply heuristic replacements."""
        normalized = text.lower()
        for key, value in self._NORMALIZATIONS.items():
            if key in normalized:
                normalized = normalized.replace(key, value)
        # "5-го числа" / "12 числа" → "5 day of this month"
        normalized = re.sub(r"(\d{1,2})(?:-?го)?\s+числа", r"\1 day of this month", normalized)
        return normalized

    def _process_hour_expression(
        self,
        normalized_text: str,
        timezone: str,
        now: datetime,
    ) -> Optional[datetime]:
        """Parse a simple hour-only expression (e.g. "в 23", "в 10 утра") into a datetime."""
        hour_str = normalized_text.split()[-1]
        try:
            hour = int(hour_str)
        except ValueError:
            return None

        has_pm = "pm" in normalized_text or "вечера" in normalized_text
        has_am = "am" in normalized_text or "утра" in normalized_text
        is_24h = hour >= 13

        period_hour = hour
        if not is_24h and (has_pm or has_am):
            period_hour = hour + 12 if has_pm and hour != 12 else hour % 12 or 12

        tz_obj = pytz.timezone(timezone)
        dt = tz_obj.localize(datetime(
            year=now.year,
            month=now.month,
            day=now.day,
            hour=period_hour,
            minute=0,
        ))
        if dt <= now:
            dt = tz_obj.normalize(dt + timedelta(days=1))
        return dt

    def _parse_sync(self, text: str, timezone: str) -> ParsedInput:
        """Synchronous parse pipeline. Must be called via run_in_executor only."""
        logger.debug("Parser: text=%r timezone=%s", text, timezone)

        # Stage 1 — heuristics
        normalized_text = self._apply_heuristics(text)
        clean_text = normalized_text

        # Stage 2 — Natasha NER (serialised for thread safety)
        try:
            with _NATASHA_LOCK:
                natasha_matches = list(_dates_extractor(normalized_text))
            for m in sorted(natasha_matches, key=lambda x: x.start, reverse=True):
                span = normalized_text[m.start:m.stop]
                clean_text = clean_text.replace(span, "", 1)
        except Exception:
            logger.warning("Natasha extraction failed; falling back to dateparser-only.", exc_info=True)
            clean_text = normalized_text

        # Stage 3 — dateparser
        dp_settings = {
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": timezone,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DAY_OF_MONTH": "current",
        }
        dp_matches = dateparser.search.search_dates(
            normalized_text, languages=["ru", "en"], settings=dp_settings
        )

        parsed_datetime: Optional[datetime] = None
        if dp_matches:
            matched_substring, dt_obj = dp_matches[0]
            parsed_datetime = dt_obj
            if matched_substring in clean_text:
                clean_text = clean_text.replace(matched_substring, "", 1)

        # Stage 4a — regex fallback: "в 23", "at 9", "в 10 утра"
        if not parsed_datetime:
            now = datetime.now(pytz.timezone(timezone))
            hour_pattern = re.compile(
                r"(?:^|\s)(?:в|at)\s+(\d{1,2})(?:\s+(утра|послеобеденно|вечера|ночи|am|pm))?",
                re.IGNORECASE,
            )
            hour_match = hour_pattern.search(normalized_text)
            if hour_match:
                result_dt = self._process_hour_expression(normalized_text, timezone, now)
                if result_dt:
                    parsed_datetime = result_dt
                    match_str = hour_match.group(0)
                    if match_str in clean_text:
                        clean_text = clean_text.replace(match_str, "", 1)

        # Stage 4b — regex fallback: "через 15 минут", "через 2 часа", "через час", "через 2 дня"
        if not parsed_datetime:
            now = datetime.now(pytz.timezone(timezone))
            duration_match = re.search(
                r"через\s+(\d+)?\s*(минут(?:ы)?|часов?|час(?:а)?|ч|дн(?:ей|я)|день|сутки)",
                normalized_text,
                re.IGNORECASE,
            )
            if duration_match:
                amount = int(duration_match.group(1) or 1)
                unit = duration_match.group(2).lower()
                if unit in ("минут", "минуты", "min"):
                    parsed_datetime = now + timedelta(minutes=amount)
                elif unit in ("часов", "часа", "час", "ч"):
                    parsed_datetime = now + timedelta(hours=amount)
                else:  # дней, дня, день, сутки
                    parsed_datetime = now + timedelta(days=amount)
                if duration_match.group(0) in clean_text:
                    clean_text = clean_text.replace(duration_match.group(0), "", 1)

        # Stage 4c — regex fallback: "в 23 часа"
        if not parsed_datetime:
            now = datetime.now(pytz.timezone(timezone))
            hour_match = re.search(
                r"в\s+(\d{1,2})(?:[:.](\d{2}))?\s*(утра|послеобеденно|вечера|ночи|часов?)?",
                normalized_text,
                re.IGNORECASE,
            )
            if hour_match:
                result_dt = self._process_hour_expression(normalized_text, timezone, now)
                if result_dt:
                    parsed_datetime = result_dt
                    if hour_match.group(0) in clean_text:
                        clean_text = clean_text.replace(hour_match.group(0), "", 1)

        # Stage 5 — final cleanup: strip dangling prepositions
        clean_text = re.sub(r"^(в|на|через|в районе)\s+", "", clean_text.strip(), flags=re.IGNORECASE)
        clean_text = " ".join(clean_text.split())

        logger.debug("Parser: clean_text=%r parsed_datetime=%s", clean_text, parsed_datetime)
        return ParsedInput(clean_text=clean_text, parsed_datetime=parsed_datetime)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def parse(self, text: str, timezone: str) -> ParsedInput:
        """Parse *text* and return a :class:`ParsedInput`.

        Offloads blocking NLP work to a thread-pool executor.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._parse_sync, text, timezone)