"""
InputParser — extracts datetime from natural-language text.

CRITICAL: dateparser.search is CPU-bound and MUST run inside
run_in_executor() to avoid blocking the asyncio event loop.
This constraint must be preserved in all future refactors.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import dateparser.search


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
    }

    def _normalize_text(self, text: str) -> str:
        """Replace colloquial expressions with parseable equivalents."""
        normalized = text.lower()
        for key, value in self._NORMALIZATIONS.items():
            if key in normalized:
                normalized = normalized.replace(key, value)
        return normalized

    def _parse_sync(self, text: str, timezone: str) -> ParsedInput:
        """
        Synchronous, CPU-bound parsing.
        MUST ONLY be called via run_in_executor.
        """
        normalized_text = self._normalize_text(text)

        settings = {
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": timezone,
            "RETURN_AS_TIMEZONE_AWARE": True,
        }
        languages = ["ru", "en"]

        matches = dateparser.search.search_dates(
            normalized_text, languages=languages, settings=settings
        )

        parsed_datetime: Optional[datetime] = None
        clean_text = normalized_text

        if matches:
            matched_substring, dt_obj = matches[0]
            parsed_datetime = dt_obj
            clean_text = clean_text.replace(matched_substring, "", 1)
            clean_text = " ".join(clean_text.split())

        return ParsedInput(clean_text=clean_text, parsed_datetime=parsed_datetime)

    async def parse(self, text: str, timezone: str) -> ParsedInput:
        """
        Public async API. Offloads blocking dateparser work to a thread pool.

        Returns:
            ParsedInput with clean_text and optional parsed_datetime.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._parse_sync, text, timezone)
