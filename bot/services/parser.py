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
# A numeric group in the plausible-year range means the token is a written-out
# date (e.g. "15-08-2026", "2027-05-01"), not a phone number — don't mask it.
_YEAR_GROUP_RE = re.compile(r"^(?:19|20)\d{2}$")


def _strip_first_occurrence(haystack: str, needle: str) -> str:
    """Remove the first occurrence of *needle* from *haystack*, if present.

    A-06: *needle* (a matched date/time span from Stage 3/4) can legitimately
    no longer exist VERBATIM in clean_text by the time this runs — Stage 2's
    Natasha pass already removed a leading chunk of it as its own, narrower
    span (e.g. for "2 марта в 15:00 врач", Natasha extracts only "2 марта"
    and removes that, so dateparser's own wider "2 марта в 15:00" match is no
    longer a substring of clean_text at all, and the plain `in`/`.replace()`
    check this used to be silently no-ops — leaving "15:00" dangling in the
    saved task text). Falls back to shrinking *needle* from the left one
    whitespace-delimited token at a time until the remaining suffix IS still
    found, on the reasoning that an earlier stage would only ever have eaten
    a PREFIX of a later, wider match (both work left-to-right over the same
    text), never an arbitrary interior slice.
    """
    if needle in haystack:
        return haystack.replace(needle, "", 1)
    tokens = needle.split(" ")
    for i in range(1, len(tokens)):
        shrunk = " ".join(tokens[i:])
        if shrunk and shrunk in haystack:
            return haystack.replace(shrunk, "", 1)
    return haystack


def _mask_phone_like(text: str) -> str:
    def _mask(m: re.Match) -> str:
        token = m.group(0)
        if any(_YEAR_GROUP_RE.match(g) for g in re.split(r"[-\s]", token)):
            return token
        return "#" * len(token)

    return _PHONE_LIKE_RE.sub(_mask, text)


# 1: recurrence phrases ("каждый день", "every weekday", "cada semana", …)
# never resolve to a datetime via dateparser or the regex fallbacks below —
# they describe a repeat pattern, not a point in time — so they used to
# just sit unrecognized in clean_text with no signal that the reminder
# should recur (GPTaudit27.07.26.md #1). Detected as their own stage,
# independent of Stage 3/4. Order matters: the more specific multi-word
# idioms (weekdays/weekend) are checked before the generic daily/weekly
# ones so e.g. "по будням" isn't shadowed by a looser "day" match.
_RECURRENCE_RULES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"\b(?:по\s+будням|в\s+будни(?:е\s+дни)?|каждый\s+будний\s+день|"
            r"every\s+weekday|on\s+weekdays|each\s+weekday|weekdays|"
            r"cada\s+d[ií]a\s+laborable|entre\s+semana|de\s+lunes\s+a\s+viernes|"
            r"los\s+d[ií]as\s+laborables)\b",
            re.IGNORECASE,
        ),
        "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    ),
    (
        re.compile(
            r"\b(?:кажд(?:ые|ый)\s+выходны[ех]|every\s+weekend|each\s+weekend|"
            r"cada\s+fin\s+de\s+semana|todos\s+los\s+fines\s+de\s+semana)\b",
            re.IGNORECASE,
        ),
        "FREQ=WEEKLY;BYDAY=SA,SU",
    ),
    (
        re.compile(
            r"\b(?:каждый\s+день|ежедневно|every\s+day|each\s+day|daily|"
            r"cada\s+d[ií]a|diariamente|todos\s+los\s+d[ií]as)\b",
            re.IGNORECASE,
        ),
        "FREQ=DAILY",
    ),
    (
        re.compile(
            r"\b(?:каждую\s+неделю|еженедельно|every\s+week|each\s+week|weekly|"
            r"cada\s+semana|semanalmente)\b",
            re.IGNORECASE,
        ),
        "FREQ=WEEKLY",
    ),
]

# 2: dateparser reads a bare "в <N> час(а/ов)" / "at <N> hours" / "a las <N>
# horas" as a DURATION offset from now ("N hours from now"), not a clock
# time — apparently because "hour(s)"/"час(а/ов)" is also its own duration
# unit word, and it doesn't distinguish the "в"/"at"/"a las" clock-time
# preposition from "через"/"in"/"dentro de" duration prepositions the way
# the rest of this parser does. "через 2 часа" / "in 2 hours" (a genuine
# duration) is unaffected — those use a different preposition and aren't
# matched here. Checked against dateparser's own matched substring, so it
# only overrides the specific misreading, not dateparser matches in
# general.
_AMBIGUOUS_HOUR_WORD_RE = re.compile(
    r"^(?:в|at|a\s+las?)\s+\d{1,2}(?:[:.]\d{2})?\s*(?:час(?:а|ов)?|hours?|horas?)\b",
    re.IGNORECASE,
)

# A-06: dateparser also misreads "в 5 вечера"/"at 5 pm"/"a las 5 tarde" —
# it matches only the bare "в 5"/"at 5" clock-time span and ignores the
# period-of-day word trailing right after it, so "5 вечера" (17:00) comes
# back as 05:00 and the un-consumed "вечера" leaks into clean_text. Unlike
# _AMBIGUOUS_HOUR_WORD_RE above (a duration misread — wrong *kind* of
# match), this is a wrong *span*: dateparser's own match is simply too
# short. Detected the same way — by testing what immediately follows the
# match in normalized_text — and handled the same way: drop the match so
# Stage 4a/4c's regex fallback (which already asks for this exact trailing
# period-of-day word and folds it into the computed hour via
# _process_hour_expression) recomputes it, correctly, instead.
_TRAILING_PERIOD_WORD_RE = re.compile(
    r"^\s+(?:de\s+la\s+)?(?:утра|дня|послеобеденно|вечера|ночи|am|pm|mañana|tarde|noche)\b",
    re.IGNORECASE,
)

# A-05: a phrase that names only a DAY (a weekday, "tomorrow", a calendar
# date) with no clock time at all must not be saved outright — dateparser
# resolves the missing time to local midnight (or, for a bare "tomorrow",
# to right-now's time-of-day carried over), and either one is silently
# wrong for a reminder the user never actually gave a time for. Detected
# by testing dateparser's OWN matched substring (not the resulting
# datetime, which could legitimately be exact midnight) for anything that
# looks like a clock time — if none is found, downstream lowers confidence
# below the confirmation threshold so the user is asked instead of
# assumed at. Bare "mañana"/"tarde"/"noche" are deliberately excluded from
# the period-word branch here: unlike the trailing-period check above
# (which only fires directly after an already-found clock number),
# "mañana" on its own overwhelmingly means "tomorrow" in Spanish, not "in
# the morning" — only "de/por la mañana" is unambiguous.
_EXPLICIT_TIME_MARKER_RE = re.compile(
    r"\d{1,2}[:.]\d{2}"
    r"|\b(?:в|at|a\s+las?)\s*\d{1,2}\b"
    r"|утра|дня|послеобеденно|вечера|ночи|am|pm"
    r"|(?:de|por)\s+la\s+ma[ñn]ana|\btarde\b|\bnoche\b"
    r"|полдень|полночь|midnight|noon|mediod[ií]a|medianoche",
    re.IGNORECASE,
)


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
        rrule_string:    RRULE string when a recurrence phrase ("every day",
                         "по будням", …) was detected in the input, else None.
    """
    clean_text: str
    parsed_datetime: Optional[datetime]
    confidence: float = 0.0
    parse_source: str = "none"
    rrule_string: Optional[str] = None


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

        # Stage 1.5 — recurrence phrase detection (see _RECURRENCE_RULES).
        # Deliberately independent of the date/time stages below: none of
        # them ever resolve "каждый день"/"every day"/etc. to a datetime,
        # so this can't steal a match from Stage 3/4.
        rrule_string: Optional[str] = None
        for pattern, candidate_rrule in _RECURRENCE_RULES:
            recurrence_match = pattern.search(normalized_text)
            if recurrence_match:
                rrule_string = candidate_rrule
                matched_phrase = recurrence_match.group(0)
                if matched_phrase in clean_text:
                    clean_text = clean_text.replace(matched_phrase, "", 1)
                break

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

        # dateparser sometimes latches onto an unrelated bare number (a price,
        # a quantity, a leftover digit run) and reads it as a year — including
        # outright bogus ones (e.g. "2026 году" → year 4052, a library bug).
        # Bound-check keeps only matches landing in a plausible reminder
        # window; an implausible first match is skipped in favor of the next
        # candidate rather than accepted outright.
        now_local = datetime.now(pytz.timezone(timezone))
        min_year, max_year = now_local.year, now_local.year + 5
        dp_matches = [(s, dt) for s, dt in (dp_matches or []) if min_year <= dt.year <= max_year]

        # 2: drop a dateparser match that misread "в 23 часа"/"at 14 hours"/
        # "a las 23 horas" as a duration offset from now instead of a clock
        # time — see _AMBIGUOUS_HOUR_WORD_RE. Falls through to the Stage 4a
        # regex fallback below, which resolves the same text correctly.
        dp_matches = [
            (s, dt) for s, dt in dp_matches if not _AMBIGUOUS_HOUR_WORD_RE.match(s.strip())
        ]

        # A-06: drop a dateparser match immediately followed by a period-of-
        # day word it didn't consume ("в 5 вечера") — see
        # _TRAILING_PERIOD_WORD_RE's docstring. normalized_text.find here
        # mirrors the existing clean_text.replace(matched_substring, "", 1)
        # calls throughout this method: a first-occurrence lookup, not a
        # tracked offset — fine for the short, single-clause phrases this
        # parser is built for.
        def _has_trailing_period_word(matched_substring: str) -> bool:
            idx = normalized_text.find(matched_substring)
            if idx == -1:
                return False
            tail = normalized_text[idx + len(matched_substring):]
            return bool(_TRAILING_PERIOD_WORD_RE.match(tail))

        dp_matches = [(s, dt) for s, dt in dp_matches if not _has_trailing_period_word(s)]

        if dp_matches:
            matched_substring, dt_obj = dp_matches[0]
            parsed_datetime = dt_obj
            parse_source = "dateparser"
            confidence = 0.75 if normalized_changed else 0.95
            # A-05: dateparser's matched substring names only a day
            # ("понедельник"/"friday"/"завтра"/a bare calendar date), with
            # no clock time in it at all — it still returns a full
            # datetime (local midnight, or "tomorrow" at whatever the
            # current time-of-day happens to be), which this parser must
            # not treat as confidently as an explicit "в 18:00". Capping
            # confidence below _PARSE_CONFIDENCE_THRESHOLD routes it
            # through the existing low-confidence confirmation prompt
            # (see reminders_shared._handle_parsed_result) instead of
            # silently saving a time the user never actually gave.
            if not _EXPLICIT_TIME_MARKER_RE.search(matched_substring):
                confidence = min(confidence, 0.4)
            clean_text = _strip_first_occurrence(clean_text, matched_substring)

        # Stage 4a — regex fallback: "в 23", "at 9", "в 10 утра", "в 23 часа"
        if not parsed_datetime:
            now = datetime.now(pytz.timezone(timezone))
            hour_pattern = re.compile(
                r"(?:^|\s)(?:в|at|a\s+las?)\s+(\d{1,2})(?:[:.](\d{2}))?"
                # 2: consume a trailing bare hour-unit word ("часа"/"hours"/
                # "horas") so it doesn't leak into clean_text — same words
                # _AMBIGUOUS_HOUR_WORD_RE screens out of Stage 3 above.
                r"\s*(?:час(?:а|ов)?|hours?|horas?)?"
                # A-06: \s* (not \s+) — when the час-word group above is
                # absent (matches empty, e.g. "в 5 вечера" has no "час(а)"
                # at all), the \s* right before it already greedily
                # consumed the ONE separating space, leaving nothing left
                # for a \s+ here to match and silently dropping the period
                # word (and its AM/PM meaning) out of the whole match —
                # "в 5 вечера" matched only "в 5", leaving group(3) empty,
                # 17:00 misread as 05:00, and "вечера" leaking into
                # clean_text. \s* accepts that already-consumed, now-empty
                # gap just as well as a real one when a час-word WAS
                # present and used its own trailing space.
                r"(?:\s*(?:de\s+la\s+)?(утра|послеобеденно|вечера|ночи|am|pm|mañana|tarde|noche))?",
                re.IGNORECASE,
            )
            hour_match = hour_pattern.search(normalized_text)
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
                    parse_source = "regex_hour"
                    confidence = 0.55
                    clean_text = _strip_first_occurrence(clean_text, hour_match.group(0))

        # Stage 4b — regex fallback: "через 15 минут", "через 2 часа", "через час", "через 2 дня"
        if not parsed_datetime:
            now = datetime.now(pytz.timezone(timezone))
            duration_match = re.search(
                # A-06: a leading \b on the preposition, and a trailing
                # (?!\w) on the unit word, stop this from firing on plain
                # words that merely CONTAIN the pattern — "через 5 человек"
                # used to match "через 5 ч" (the bare "ч" alternative is a
                # prefix of "человек"), and "garden 2 horas" used to match
                # "en 2 horas" (no boundary before "en", which is also the
                # last two letters of "garden").
                r"\b(?:через|dentro\s+de|en)\s+(\d+)?\s*"
                r"(минут(?:ы)?|часов?|час(?:а)?|ч|дн(?:ей|я)|день|сутки|minutos?|horas?|d[ií]as?)(?!\w)",
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
                clean_text = _strip_first_occurrence(clean_text, duration_match.group(0))

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
                    clean_text = _strip_first_occurrence(clean_text, hour_match.group(0))

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
            rrule_string=rrule_string,
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
