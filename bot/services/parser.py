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

# Phone numbers and other long digit runs (with or without separators) confuse
# dateparser.search_dates's internal text splitting, causing it to drop
# surrounding date words (e.g. a weekday) and match only a trailing time.
# Masking them before the search keeps offsets stable for everything else.
_PHONE_LIKE_RE = re.compile(r"(?<!\d)(?:\d{2,4}(?:[-\s]\d{2,4}){2,4}|\d{7,})(?!\d)")


def _mask_phone_like(text: str) -> str:
    return _PHONE_LIKE_RE.sub(lambda m: "#" * len(m.group(0)), text)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedInput:
    """Immutable result of parsing user text.

    Attributes:
        clean_text:      Task description with time expressions removed.
        parsed_datetime: Timezone-aware datetime when found, else None.
        confidence:      Parsing confidence in [0.0, 1.0].
        parse_source:    Which stage produced datetime ("dateparser", "regex_*", "none").
    """
    clean_text: str
    parsed_datetime: Optional[datetime]
    confidence: float = 0.0
    parse_source: str = "none"


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
        # Spanish
        "por la mañana": "a las 09:00",
        "por la tarde": "a las 14:00",
        "por la noche": "a las 19:00",
        "en un rato": "en 2 minutos",
        "el fin de semana": "el sábado a las 10:00",
        "media hora": "30 minutos",
        "una hora y media": "1 hora 30 minutos",
        "dentro de un par de minutos": "dentro de 2 minutos",
        "después del almuerzo": "a las 14:00",
        "a finales de la semana": "el viernes a las 18:00",
    }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_heuristics(self, text: str) -> str:
        """Apply heuristic replacements while preserving original casing."""
        normalized = text
        for key, value in self._NORMALIZATIONS.items():
            # Use case-insensitive substitution to match heuristics without forcing lowercase.
            # Word boundaries keep short keys (e.g. "утром") from matching inside longer words.
            normalized = re.sub(r"\b" + re.escape(key) + r"\b", value, normalized, flags=re.IGNORECASE)
        # "5-го числа" / "12 числа" → "5 day of this month"
        normalized = re.sub(r"(\d{1,2})(?:-?го)?\s+числа", r"\1 day of this month", normalized)
        # "в 12" / "at 14" / "a las 9" → "…:00" mapping (helps dateparser avoid treating lonely hours as years)
        normalized = re.sub(r"(?i)\b(в|at)\s+(\d{1,2})\b(?!\s*[:.])", r"\1 \2:00", normalized)
        normalized = re.sub(r"(?i)\ba\s+las?\s+(\d{1,2})\b(?!\s*[:.])", r"a las \1:00", normalized)
        return normalized

    def _process_hour_expression(
        self,
        *,
        hour: int,
        minute: int,
        period_token: Optional[str],
        timezone: str,
        now: datetime,
    ) -> Optional[datetime]:
        """Parse hour(+optional minute/period) into next local datetime."""
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None

        token = (period_token or "").lower()
        has_pm = token in {"pm", "вечера", "послеобеденно", "tarde", "noche"}
        has_am = token in {"am", "утра", "ночи", "mañana"}
        is_24h = hour >= 13

        period_hour = hour
        if not is_24h and (has_pm or has_am):
            if has_pm and hour != 12:
                period_hour = hour + 12
            elif has_am and hour == 12:
                period_hour = 0

        tz_obj = pytz.timezone(timezone)
        dt = tz_obj.localize(datetime(
            year=now.year,
            month=now.month,
            day=now.day,
            hour=period_hour,
            minute=minute,
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
            _mask_phone_like(normalized_text), languages=["ru", "en", "es"], settings=dp_settings
        )

        parsed_datetime: Optional[datetime] = None
        confidence = 0.0
        parse_source = "none"
        normalized_changed = normalized_text != text
        if dp_matches:
            matched_substring, dt_obj = dp_matches[0]
            parsed_datetime = dt_obj
            parse_source = "dateparser"
            confidence = 0.75 if normalized_changed else 0.95
            if matched_substring in clean_text:
                clean_text = clean_text.replace(matched_substring, "", 1)

        # Stage 4a — regex fallback: "в 23", "at 9", "в 10 утра"
        if not parsed_datetime:
            now = datetime.now(pytz.timezone(timezone))
            hour_pattern = re.compile(
                r"(?:^|\s)(?:в|at|a\s+las?)\s+(\d{1,2})"
                r"(?:\s+(?:de\s+la\s+)?(утра|послеобеденно|вечера|ночи|am|pm|mañana|tarde|noche))?",
                re.IGNORECASE,
            )
            hour_match = hour_pattern.search(normalized_text)
            if hour_match:
                result_dt = self._process_hour_expression(
                    hour=int(hour_match.group(1)),
                    minute=0,
                    period_token=hour_match.group(2),
                    timezone=timezone,
                    now=now,
                )
                if result_dt:
                    parsed_datetime = result_dt
                    parse_source = "regex_hour"
                    confidence = 0.55
                    match_str = hour_match.group(0)
                    if match_str in clean_text:
                        clean_text = clean_text.replace(match_str, "", 1)

        # Stage 4b — regex fallback: "через 15 минут", "через 2 часа", "через час", "через 2 дня"
        if not parsed_datetime:
            now = datetime.now(pytz.timezone(timezone))
            duration_match = re.search(
                r"(?:через|dentro\s+de|en)\s+(\d+)?\s*"
                r"(минут(?:ы)?|часов?|час(?:а)?|ч|дн(?:ей|я)|день|сутки|minutos?|horas?|d[ií]as?)",
                normalized_text,
                re.IGNORECASE,
            )
            if duration_match:
                amount = int(duration_match.group(1) or 1)
                unit = duration_match.group(2).lower()
                if unit in ("минут", "минуты", "min", "minutos", "minuto"):
                    parsed_datetime = now + timedelta(minutes=amount)
                elif unit in ("часов", "часа", "час", "ч", "horas", "hora"):
                    parsed_datetime = now + timedelta(hours=amount)
                else:  # дней, дня, день, сутки, días, día
                    parsed_datetime = now + timedelta(days=amount)
                parse_source = "regex_duration"
                confidence = 0.5
                if duration_match.group(0) in clean_text:
                    clean_text = clean_text.replace(duration_match.group(0), "", 1)

        # Stage 4c — regex fallback: "в 23 часа"
        if not parsed_datetime:
            now = datetime.now(pytz.timezone(timezone))
            hour_match = re.search(
                r"(?:в|a\s+las?)\s+(\d{1,2})(?:[:.](\d{2}))?\s*"
                r"(?:de\s+la\s+)?(утра|послеобеденно|вечера|ночи|часов?|mañana|tarde|noche)?",
                normalized_text,
                re.IGNORECASE,
            )
            if hour_match:
                result_dt = self._process_hour_expression(
                    hour=int(hour_match.group(1)),
                    minute=int(hour_match.group(2) or 0),
                    period_token=hour_match.group(3),
                    timezone=timezone,
                    now=now,
                )
                if result_dt:
                    parsed_datetime = result_dt
                    parse_source = "regex_hour_alt"
                    confidence = 0.5
                    if hour_match.group(0) in clean_text:
                        clean_text = clean_text.replace(hour_match.group(0), "", 1)

        # Stage 5 — final cleanup: strip dangling prepositions
        clean_text = re.sub(
            r"^(в|на|через|в районе|a\s+las?|en|dentro\s+de|el)\s+",
            "",
            clean_text.strip(),
            flags=re.IGNORECASE,
        )
        clean_text = " ".join(clean_text.split())

        # Stage 6 — if a heuristic-substituted phrase (e.g. "в 19:00" standing in
        # for "вечером") survived every extraction stage above, it wasn't part of
        # the original text and must not leak into the task description.
        for value in self._NORMALIZATIONS.values():
            if value in clean_text and value.lower() not in text.lower():
                clean_text = clean_text.replace(value, "", 1)
        clean_text = " ".join(clean_text.split())

        logger.debug("Parser: clean_text=%r parsed_datetime=%s", clean_text, parsed_datetime)
        return ParsedInput(
            clean_text=clean_text,
            parsed_datetime=parsed_datetime,
            confidence=confidence,
            parse_source=parse_source,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def parse(self, text: str, timezone: str) -> ParsedInput:
        """Parse *text* and return a :class:`ParsedInput`.

        Offloads blocking NLP work to a thread-pool executor.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._parse_sync, text, timezone)
