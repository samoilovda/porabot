"""
InputParser — extracts datetime from natural-language text.

Architecture notes
------------------
* dateparser.search  — CPU-bound, run via run_in_executor().
* natasha DatesExtractor — CPU-bound AND NOT REENTRANT (mutates internal
  morphological caches).  Access is serialised with _NATASHA_LOCK so that
  concurrent ThreadPoolExecutor calls cannot race each other.
* All heavy objects (Segmenter, MorphVocab, DatesExtractor) are created
  once at module import time — never per-message.
"""

import asyncio
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import dateparser.search
from natasha import (
    MorphVocab,
    DatesExtractor,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singletons — initialised once at import time, shared across
# all requests.  DatesExtractor mutates internal caches and is therefore NOT
# thread-safe; _NATASHA_LOCK serialises every call into it.
# ---------------------------------------------------------------------------
_morph_vocab: MorphVocab = MorphVocab()
_dates_extractor: DatesExtractor = DatesExtractor(_morph_vocab)
_NATASHA_LOCK: threading.Lock = threading.Lock()


@dataclass(frozen=True)
class ParsedInput:
    """Immutable result of parsing user text."""
    clean_text: str
    parsed_datetime: Optional[datetime]


class InputParser:
    """
    Stateless parser. Thread-safe — each call uses run_in_executor.
    """

    _NORMALIZATIONS: dict[str, str] = {
        "полчаса": "30 минут",
        "полтора часа": "1 час 30 минут",
        "через пару минут": "через 2 минуты",
        # Semantic heuristics
        "после обеда": "в 14:00",
        "вечером": "в 19:00",
        "утром": "в 09:00",
        "на выходных": "в субботу в 10:00",
        "в выходные": "в субботу в 10:00",
        "в конце недели": "в пятницу в 18:00",
    }

    def _apply_heuristics(self, text: str) -> str:
        """Apply heuristic replacements on a *lowercased* copy of the text.

        Works on a lowercase copy so the caller can keep the original
        capitalisation for Natasha's NER pass.
        """
        normalized = text.lower()

        for key, value in self._NORMALIZATIONS.items():
            if key in normalized:
                normalized = normalized.replace(key, value)

        # "5-го числа" / "12 числа" → "5 day of this month"
        normalized = re.sub(
            r"(\d{1,2})(?:-?го)?\s+числа",
            r"\1 day of this month",
            normalized,
        )

        return normalized

    def _parse_sync(self, text: str, timezone: str) -> ParsedInput:
        """Synchronous, CPU-bound pipeline: heuristics → Natasha → dateparser.

        MUST ONLY be called via run_in_executor — never from the event loop
        directly.
        """
        # --- 1. Heuristic substitution (lowercased working copy) ----------
        normalized_text: str = self._apply_heuristics(text)

        # --- 2. Natasha: locate date spans for *clean-text extraction* ----
        # DatesExtractor is NOT reentrant — serialise with the module lock.
        clean_text: str = normalized_text
        try:
            with _NATASHA_LOCK:
                natasha_matches = list(_dates_extractor(normalized_text))

            if natasha_matches:
                # Strip in reverse order so earlier indices stay valid.
                for m in sorted(natasha_matches, key=lambda x: x.start, reverse=True):
                    span = normalized_text[m.start:m.stop]
                    clean_text = clean_text.replace(span, "", 1)
        except Exception:
            # Natasha failure must never crash the whole parse pipeline.
            logger.warning("Natasha extraction failed; falling back to dateparser-only.", exc_info=True)
            clean_text = normalized_text

        # --- 3. dateparser: resolve to an actual datetime object ----------
        settings: dict = {
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": timezone,
            "RETURN_AS_TIMEZONE_AWARE": True,
        }
        dp_matches = dateparser.search.search_dates(
            normalized_text, languages=["ru", "en"], settings=settings
        )

        parsed_datetime: Optional[datetime] = None

        if dp_matches:
            matched_substring, dt_obj = dp_matches[0]
            parsed_datetime = dt_obj
            # Strip the dateparser match if Natasha didn't already remove it.
            if matched_substring in clean_text:
                clean_text = clean_text.replace(matched_substring, "", 1)

        # --- 4. Final cleanup -------------------------------------------
        # Remove dangling prepositions at the start of the remaining text.
        clean_text = re.sub(
            r"^(в|на|через|в районе)\s+", "", clean_text.strip(), flags=re.IGNORECASE
        )
        clean_text = " ".join(clean_text.split())

        return ParsedInput(clean_text=clean_text, parsed_datetime=parsed_datetime)

    async def parse(self, text: str, timezone: str) -> ParsedInput:
        """Public async entry-point.  Offloads the blocking NLP work to a
        thread-pool executor so the event loop is never blocked.

        Returns:
            ParsedInput with ``clean_text`` (task description) and an optional
            timezone-aware ``parsed_datetime``.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._parse_sync, text, timezone)
