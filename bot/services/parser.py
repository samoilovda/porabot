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
from datetime import date, datetime, timedelta
from typing import Optional

import dateparser.search
from natasha import DatesExtractor, MorphVocab

from bot.utils.timezone import resolve_timezone

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
    (
        re.compile(
            r"\b(?:каждый\s+месяц|ежемесячно|every\s+month|each\s+month|monthly|cada\s+mes|"
            r"todo\s+m[eê]s|mensalmente)\b",
            re.IGNORECASE,
        ),
        "FREQ=MONTHLY",
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
    r"^(?:в|at|a\s+las?|às|as)\s+\d{1,2}(?:[:.]\d{2})?\s*(?:час(?:а|ов)?|hours?|horas?)\b",
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
# Clock-time shape only ("в 2 дня", "в 3 часа дня") — NOT a bare "3 дня",
# which after "через" is a duration ("in 3 days") that dateparser handles.
_RU_PERIOD_IN_MATCH_RE = re.compile(
    r"(?<![^\W\d_])в\s+\d{1,2}(?:[:.]\d{2})?\s*(?:час(?:а|ов)?\s+)?(?:утра|дня|вечера|ночи)(?![^\W\d_])",
    re.IGNORECASE,
)

# Stage 4a's clock-time regex: "в 23", "at 9", "в 10 утра", "в 23 часа",
# "a las 5 de la tarde". The час-word group consumes a trailing bare hour
# unit so it doesn't leak into clean_text; the period group uses \s* (not
# \s+) because the час-group's own \s* may already have eaten the space.
_HOUR_RE = re.compile(
    r"(?:^|\s)(?:в|at|a\s+las?|às|as)\s+(\d{1,2})(?:[:.](\d{2}))?"
    r"\s*(?:час(?:а|ов)?|hours?|horas?)?"
    r"(?:\s*(?:de\s+la\s+|da\s+|de\s+)?(утра|дня|послеобеденно|вечера|ночи|am|pm|mañana|manhã|manha|tarde|noche|noite))?",
    re.IGNORECASE,
)
_CLOCK_IN_SPAN_RE = re.compile(r"(?<!\d)(\d{1,2})[:.](\d{2})(?!\d)")


# Clock-time preposition directly before a span ("at " in "at 14 hours").
_PREPOSITION_TAIL_RE = re.compile(r"(?<![^\W\d_])(?:в|at|a\s+las?|às|as)\s+$", re.IGNORECASE)

_TRAILING_PERIOD_WORD_RE = re.compile(
    r"^\s+(?:de\s+la\s+|la\s+|da\s+|de\s+)?(?:утра|дня|послеобеденно|вечера|ночи|am|pm|mañana|manhã|tarde|noche|noite)\b",
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
    r"|\b(?:в|at|a\s+las?|às|as)\s*\d{1,2}\b"
    # Period-of-day words must stand alone: bare "дня" also matched inside
    # "сегодня", so "позвонить сегодня" slipped past the date-only check.
    # Guarded against adjacent LETTERS only (not \b), so "5pm" — a digit
    # right before "pm" — still counts.
    r"|(?<![^\W\d_])(?:утра|дня|послеобеденно|вечера|ночи|am|pm"
    r"|полдень|полночь|midnight|noon|mediod[ií]a|medianoche|meio-dia|meia-noite)(?![^\W\d_])"
    r"|(?:de|por)\s+la\s+ma[ñn]ana|\btarde\b|\bnoche\b|\bnoite\b|\b(?:da|de|pela)\s+manh[ãa]\b"
    # A relative offset ("через час", "in an hour", "en 2 horas") fully
    # determines the moment — "now + N units" — so it is not a bare day
    # with a filler time and must not be demoted to date_only.
    r"|\b(?:через|in|en|dentro\s+de|em|daqui\s+a)\s+(?:\d+\s*|[^\W\d_]+\s+)?"
    r"(?:минут\w*|час\w*|секунд\w*|дн\w+|день|сутки|недел\w+"
    r"|(?:minute|hour|day|week)s?|minutos?|horas?|d[ií]as?|semanas?)(?![^\W\d_])",
    re.IGNORECASE,
)


# dateparser's multi-language search auto-detects ONE language for the whole
# text, so a phrase in a different language than the rest of the sentence
# ("Купи пылесос en una hora", "Buy milk завтра") is silently missed. The
# parser must not depend on which language the sentence — or the user's
# interface — is in, so each supported language is searched on its own, plus
# the combined list, and the most specific (longest) span wins.
_DATE_LANGUAGES = ("ru", "en", "es", "pt")


def _search_dates_any_language(text: str, settings: dict) -> list:
    seen: dict[tuple[str, datetime], None] = {}
    candidates: list[tuple[str, datetime]] = []
    for languages in (list(_DATE_LANGUAGES), *([lang] for lang in _DATE_LANGUAGES)):
        try:
            found = dateparser.search.search_dates(text, languages=languages, settings=settings)
        except Exception:
            logger.debug("dateparser search failed for %s", languages, exc_info=True)
            continue
        for span, dt in found or []:
            # A single stray letter ("a" in "llamar a mamá") is not a date.
            if len(span.strip()) < 2 or (span, dt) in seen:
                continue
            seen[(span, dt)] = None
            candidates.append((span, dt))
    # Longest span first; stable sort keeps the earlier (combined) result on ties.
    return sorted(candidates, key=lambda c: -len(c[0].strip()))


# ---------------------------------------------------------------------------
# Structured pre-stage (2026-11-04 audit, B-01/B-02): recurrence phrases and
# numeric dates are extracted deterministically BEFORE dateparser runs.
# dateparser can't express "every Monday" at all and, searching all four
# languages at once with no DATE_ORDER, read "12.11" as a time or dropped it
# and "12/11" as December 11th.
# ---------------------------------------------------------------------------

# Weekday stems → RRULE codes, per language. A stem must match the START of
# a word ("понедельник", "понедельникам"; "lunes"; "segunda", "segundas").
_WEEKDAY_STEMS: list[tuple[str, str]] = [
    (r"понедельник\w*", "MO"), (r"вторник\w*", "TU"), (r"сред[уаы]\w*|сред", "WE"),
    (r"четверг\w*", "TH"), (r"пятниц\w*", "FR"), (r"суббот\w*", "SA"), (r"воскресень\w*", "SU"),
    (r"mondays?", "MO"), (r"tuesdays?", "TU"), (r"wednesdays?", "WE"), (r"thursdays?", "TH"),
    (r"fridays?", "FR"), (r"saturdays?", "SA"), (r"sundays?", "SU"),
    (r"lunes", "MO"), (r"martes", "TU"), (r"mi[eé]rcoles", "WE"), (r"jueves", "TH"),
    (r"viernes", "FR"), (r"s[aá]bados?", "SA"), (r"domingos?", "SU"),
    (r"segundas?(?:-feiras?)?", "MO"), (r"ter[cç]as?(?:-feiras?)?", "TU"), (r"quartas?(?:-feiras?)?", "WE"),
    (r"quintas?(?:-feiras?)?", "TH"), (r"sextas?(?:-feiras?)?", "FR"),
]
_WEEKDAY_ORDER = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_WEEKDAY_ALT = "|".join(f"(?:{stem})" for stem, _ in _WEEKDAY_STEMS)
_WEEKDAY_LIST = rf"(?:{_WEEKDAY_ALT})(?:\s*(?:,|и|and|y|e)\s*(?:{_WEEKDAY_ALT}))*"
_WEEKDAY_RECURRENCE_RE = re.compile(
    r"(?<![^\W\d_])(?:"
    r"кажд(?:ый|ую|ое|ые)|по|every|each|on|cada|todos\s+los|todas\s+las|los|las|tod[ao]s?\s+(?:as|os)?|tod[ao]s?|às|aos"
    rf")\s+({_WEEKDAY_LIST})(?![^\W\d_])",
    re.IGNORECASE,
)
_UNIT_FREQ = [
    (r"дн(?:я|ей)|день|days?|d[ií]as?|dias?", "DAILY"),
    (r"недел[ьию]\w*|weeks?|semanas?", "WEEKLY"),
    (r"месяц\w*|months?|mes(?:es)?|m[eê]s(?:es)?", "MONTHLY"),
]
_INTERVAL_RE = re.compile(
    r"(?<![^\W\d_])(?:кажд(?:ые|ый|ую)|every|cada|a\s+cada|tod[ao]s?\s+(?:as|os)?)\s+(\d{1,2})\s+("
    + "|".join(u for u, _ in _UNIT_FREQ) + r")(?![^\W\d_])",
    re.IGNORECASE,
)
_MONTH_EVERY = r"(?:каждого\s+месяца|каждый\s+месяц|ежемесячно|every\s+month|of\s+every\s+month|monthly|de\s+cada\s+mes|cada\s+mes|todo\s+m[eê]s|de\s+cada\s+m[eê]s|mensalmente)"
_MONTHDAY = r"(\d{1,2})(?:-?(?:го|е|st|nd|rd|th|º|°))?(?:\s+(?:числа|число))?"
_MONTHLY_RE = re.compile(
    r"(?<![^\W\d_])(?:"
    rf"(?:the\s+|on\s+the\s+|el\s+d[ií]a\s+|d[ií]a\s+|dia\s+|no\s+dia\s+)?{_MONTHDAY}\s+{_MONTH_EVERY}"
    rf"|{_MONTH_EVERY}\s+(?:on\s+the\s+|the\s+|el\s+d[ií]a\s+|el\s+|d[ií]a\s+|dia\s+|no\s+dia\s+)?{_MONTHDAY}"
    r"|кажд(?:ое|ый)\s+(\d{1,2})(?:-?е|-?го)?\s+числ[оа]"
    r")(?![^\W\d_])",
    re.IGNORECASE,
)
_PERIOD_RECURRENCE_RE = re.compile(
    r"(?<![^\W\d_])(?:кажд(?:ое|ый)\s+(утро|вечер)|every\s+(morning|evening)|"
    r"cada\s+(mañana|noche)|toda\s+(manhã|noite))(?![^\W\d_])",
    re.IGNORECASE,
)
_PERIOD_REPLACEMENT = {
    "утро": "утром", "вечер": "вечером", "morning": "в 09:00", "evening": "в 19:00",
    "mañana": "por la mañana", "noche": "por la noche", "manhã": "pela manhã", "noite": "à noite",
}
# A recurrence word still in the text after every rule above means the user
# asked for a repeat this parser couldn't model — never save that silently.
_LEFTOVER_RECURRENCE_RE = re.compile(
    r"(?<![^\W\d_])(?:кажд\w+|ежедневн\w*|еженедельн\w*|ежемесячн\w*|every|each|cada|todos|todas|toda|todo)(?![^\W\d_])",
    re.IGNORECASE,
)

# B-01: DD.MM[.YYYY] / DD/MM[/YYYY]. Not preceded by a clock-time preposition
# ("в 14.30" is a time), not part of a longer number/version, not followed by
# a unit ("1.5 часа", "1.20 руб").
_NUMERIC_DATE_RE = re.compile(
    r"(?<![\w.:/,])(\d{1,2})([./])(\d{1,2})(?:\2(\d{4}|\d{2}))?(?![\w.:/,]|\s*(?:час|ч\b|мин|min|h\b|hours?|horas?|руб|р\.|₽|\$|€|%|kg|кг|usd|eur))",
    re.IGNORECASE,
)
_CLOCK_PREPOSITION_TAIL_RE = re.compile(r"(?:^|\s)(?:в|во|at|a\s+las?|às|as)\s*$", re.IGNORECASE)

_LEADING_REMINDER_VERB_RE = re.compile(
    r"^(?:напомни(?:те|ть)?(?:\s+мне)?|remind\s+me(?:\s+to)?|recu[eé]rdame(?:\s+que)?|lembre-me(?:\s+de)?|me\s+lembre(?:\s+de)?)(?:\s+|$)",
    re.IGNORECASE,
)


def _weekday_codes(listing: str) -> list[str]:
    codes = set()
    for token in re.split(r"\s*(?:,|\bи\b|\band\b|\by\b|\be\b)\s*", listing, flags=re.IGNORECASE):
        token = token.strip()
        for stem, code in _WEEKDAY_STEMS:
            if re.fullmatch(stem, token, flags=re.IGNORECASE):
                codes.add(code)
                break
    return [c for c in _WEEKDAY_ORDER if c in codes]


def extract_recurrence(text: str) -> tuple[str, Optional[str]]:
    """Return (text with the recurrence phrase cut out, RRULE or None).

    Specific shapes go first (monthly-by-day, every-N-units, weekday lists,
    every morning/evening), then the original generic phrases
    (_RECURRENCE_RULES: weekdays/weekend/daily/weekly). "Every morning"
    is swapped for the plain period word so the time stages still read it.
    """
    m = _MONTHLY_RE.search(text)
    if m:
        day = next(int(g) for g in m.groups() if g)
        if 1 <= day <= 31:
            return _cut(text, m), f"FREQ=MONTHLY;BYMONTHDAY={day}"
    m = _INTERVAL_RE.search(text)
    if m:
        n = int(m.group(1))
        freq = next(f for pattern, f in _UNIT_FREQ if re.fullmatch(pattern, m.group(2), re.IGNORECASE))
        if n >= 1:
            return _cut(text, m), f"FREQ={freq}" + (f";INTERVAL={n}" if n > 1 else "")
    m = _WEEKDAY_RECURRENCE_RE.search(text)
    if m:
        codes = _weekday_codes(m.group(1))
        prefix = m.group(0)[: m.start(1) - m.start(0)].strip().lower()
        # Bare "по"/"on"/"los"/"às" only signal a repeat with a PLURAL day
        # ("по понедельникам", "on Mondays", "los lunes"); "в понедельник"
        # never reaches here and "on Monday" is a one-off.
        plural_needed = prefix in {"по", "on", "los", "las", "às", "aos"}
        plural = re.search(r"(?:ам|ям|s)\b", m.group(1), re.IGNORECASE) or prefix in {"los", "las"}
        if codes and (not plural_needed or plural):
            return _cut(text, m), "FREQ=WEEKLY;BYDAY=" + ",".join(codes)
    m = _PERIOD_RECURRENCE_RE.search(text)
    if m:
        word = next(g for g in m.groups() if g).lower()
        replaced = text[: m.start()] + _PERIOD_REPLACEMENT[word] + text[m.end():]
        return " ".join(replaced.split()), "FREQ=DAILY"
    for pattern, candidate_rrule in _RECURRENCE_RULES:
        m = pattern.search(text)
        if m:
            return _cut(text, m), candidate_rrule
    return text, None


def _cut(text: str, match: re.Match) -> str:
    return " ".join((text[: match.start()] + " " + text[match.end():]).split())


def extract_numeric_date(text: str, language: Optional[str], today: date) -> Optional[tuple[date, re.Match]]:
    """Find a DD.MM[.YYYY] or DD/MM[/YYYY] date (MM/DD for "/" in English).

    Without a year, the next such date from *today* (inclusive) is used.
    Returns (date, match) or None."""
    for m in _NUMERIC_DATE_RE.finditer(text):
        if _CLOCK_PREPOSITION_TAIL_RE.search(text[: m.start()]):
            continue  # "в 14.30" — a clock time, not a date
        a, sep, b, year_raw = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
        day, month = (b, a) if sep == "/" and (language or "").lower().startswith("en") else (a, b)
        if year_raw is None:
            year = today.year
        else:
            year = int(year_raw) + (2000 if len(year_raw) == 2 else 0)
        try:
            found = date(year, month, day)
        except ValueError:
            continue
        if year_raw is None and found < today:
            try:
                found = date(year + 1, month, day)
            except ValueError:
                continue
        if not (today.year - 1 <= found.year <= today.year + 5):
            continue
        return found, m
    return None


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
        date_only:       True when a DAY was recognized ("понедельник",
                         "tomorrow", a calendar date) with no clock time at
                         all — parsed_datetime's time-of-day is a filler
                         (local midnight, or a bare "tomorrow"'s
                         now-carried-over time), not something the user
                         actually said. Callers must ask for the hour
                         instead of treating this like any other
                         low-confidence-but-has-a-time result (step 3,
                         2026-09-26 audit remediation) — see
                         bot/handlers/reminders_shared.py's
                         _resolve_time_and_respond.
    """
    clean_text: str
    parsed_datetime: Optional[datetime]
    confidence: float = 0.0
    parse_source: str = "none"
    rrule_string: Optional[str] = None
    date_only: bool = False


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
        "на следующей неделе": "next week",
        "на следующую неделю": "next week",
        "на след неделе": "next week",
        "в следующем месяце": "next month",
        # Spanish
        "por la mañana": "a las 09:00",
        "por la tarde": "a las 14:00",
        "por la noche": "a las 19:00",
        "en un rato": "en 2 minutos",
        "el fin de semana": "el sábado a las 10:00",
        # "mañana" alone would be read as "tomorrow" inside "pasado mañana".
        "pasado mañana": "послезавтра",
        "media hora": "30 minutos",
        "half an hour": "30 minutes",
        "una hora y media": "1 hora 30 minutos",
        "dentro de un par de minutos": "dentro de 2 minutos",
        "después del almuerzo": "a las 14:00",
        "la semana que viene": "next week",
        "la próxima semana": "next week",
        "la semana próxima": "next week",
        "el mes que viene": "next month",
        "el próximo mes": "next month",
        "day after tomorrow": "послезавтра",
        # Portuguese
        "pela manhã": "às 09:00",
        "de manhã": "às 09:00",
        "à tarde": "às 14:00",
        "à noite": "às 19:00",
        "daqui a pouco": "em 2 minutos",
        "daqui a": "em",
        "meia hora": "30 minutos",
        "uma hora e meia": "1 hora 30 minutos",
        "depois de amanhã": "послезавтра",
        "no fim de semana": "no sábado às 10:00",
        "semana que vem": "next week",
        "próxima semana": "next week",
        "mês que vem": "next month",
        "próximo mês": "next month",
        "a finales de la semana": "el viernes a las 18:00",
        # B-03 (2026-11-04 audit): noon / lunch / midnight
        "в полдень": "в 12:00",
        "в обед": "в 13:00",
        "в полночь": "в 23:59",
        "at noon": "at 12:00",
        "at midnight": "at 23:59",
        "al mediodía": "a las 12:00",
        "a mediodía": "a las 12:00",
        "ao meio-dia": "às 12:00",
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
        # B-03: "1.5 часа" / "2,5 hours" → "1 час 30 минут"
        normalized = re.sub(r"(?i)\b(\d+)[.,]5\s*(час\w*|ч\b)", r"\1 час 30 минут", normalized)
        normalized = re.sub(r"(?i)\b(\d+)[.,]5\s*(hours?|horas?)\b", r"\1 hours 30 minutes", normalized)
        # B-03: "в следующий вторник" — "следующий" stayed in the task text;
        # PREFER_DATES_FROM=future already picks the coming weekday.
        normalized = re.sub(
            r"(?i)\b(?:в|во)\s+следующ(?:ий|ую|ее|ей)\s+"
            r"(?=понедельник|вторник|сред|четверг|пятниц|суббот|воскресень)",
            "в ",
            normalized,
        )
        # "5-го числа" / "12 числа" → "5 day of this month"
        normalized = re.sub(r"(\d{1,2})(?:-?го)?\s+числа", r"\1 day of this month", normalized)
        # "в 12" / "at 14" / "a las 9" → "…:00" mapping (helps dateparser avoid treating lonely hours as years)
        normalized = re.sub(r"(?i)\b(в|at|às|as)\s+(\d{1,2})\b(?!\s*[:.])", r"\1 \2:00", normalized)
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
        has_pm = token in {"pm", "вечера", "дня", "послеобеденно", "tarde", "noche", "noite"}
        has_am = token in {"am", "утра", "ночи", "mañana", "manhã", "manha"}
        is_24h = hour >= 13

        period_hour = hour
        if not is_24h and (has_pm or has_am):
            if has_pm and hour != 12:
                period_hour = hour + 12
            elif has_am and hour == 12:
                period_hour = 0

        tz_obj = resolve_timezone(timezone)
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

    def _clock_from_match(self, hour_match: re.Match, timezone: str) -> Optional[datetime]:
        return self._process_hour_expression(
            hour=int(hour_match.group(1)),
            minute=int(hour_match.group(2) or 0),
            period_token=hour_match.group(3),
            timezone=timezone,
            now=datetime.now(resolve_timezone(timezone)),
        )

    @staticmethod
    def _on_day(day_dt: datetime, clock_dt: datetime, timezone: str) -> datetime:
        """*day_dt*'s local date at *clock_dt*'s local wall-clock time."""
        tz_obj = resolve_timezone(timezone)
        day = day_dt.astimezone(tz_obj) if day_dt.tzinfo else day_dt
        clock = clock_dt.astimezone(tz_obj)
        return tz_obj.localize(datetime(day.year, day.month, day.day, clock.hour, clock.minute))

    @staticmethod
    def _find_day_only(text: str, timezone: str) -> Optional[tuple[str, datetime]]:
        """A dateparser span naming only a day ("завтра", "в понедельник",
        "mañana") — no clock time, at least one real word (a lone number is
        a quantity, not a day), plausible year."""
        settings = {
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": timezone,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DAY_OF_MONTH": "current",
        }
        year = datetime.now(resolve_timezone(timezone)).year
        for span, dt in _search_dates_any_language(_mask_phone_like(text), settings):
            if _EXPLICIT_TIME_MARKER_RE.search(span) or not re.search(r"[^\W\d_]{3,}", span):
                continue
            if year <= dt.year <= year + 5:
                return span.strip(), dt
        return None

    def _parse_sync(self, text: str, timezone: str, language: Optional[str] = None) -> ParsedInput:
        """Synchronous parse pipeline. Must be called via run_in_executor only.

        2026-11-04 audit: recurrence phrases (B-02) and numeric dates (B-01)
        are pulled out first by deterministic rules; the remaining text goes
        through the NLP pipeline (_parse_core) for the clock time, and the
        two are combined here. *language* (the user's interface language)
        only decides DD/MM vs MM/DD for slash dates.
        """
        tz_obj = resolve_timezone(timezone)
        now_local = datetime.now(tz_obj)

        core_text, rrule_string = extract_recurrence(text)
        numeric = extract_numeric_date(core_text, language, now_local.date())
        if numeric is not None:
            numeric_date, match = numeric
            core_text = _cut(core_text, match)

        core = self._parse_core(core_text, timezone)
        parsed_datetime, confidence = core.parsed_datetime, core.confidence
        parse_source, date_only = core.parse_source, core.date_only

        if numeric is not None:
            has_clock = parsed_datetime is not None and not date_only and parse_source != "regex_duration"
            if has_clock:
                clock = parsed_datetime.astimezone(tz_obj)
                parsed_datetime = tz_obj.localize(
                    datetime(numeric_date.year, numeric_date.month, numeric_date.day, clock.hour, clock.minute)
                )
            else:
                parsed_datetime = tz_obj.localize(
                    datetime(numeric_date.year, numeric_date.month, numeric_date.day)
                )
                confidence, date_only = 0.4, True
            parse_source = "numeric_date"

        clean_text = _LEADING_REMINDER_VERB_RE.sub("", core.clean_text).strip()
        if rrule_string is None and parsed_datetime is not None and _LEFTOVER_RECURRENCE_RE.search(clean_text):
            # A repeat the rules above can't model ("каждый раз", "every
            # other Friday") — ask instead of saving a one-off silently.
            confidence = min(confidence, 0.6)

        return ParsedInput(
            clean_text=clean_text,
            parsed_datetime=parsed_datetime,
            confidence=confidence,
            parse_source=parse_source,
            rrule_string=rrule_string,
            date_only=date_only,
        )

    def _parse_core(self, text: str, timezone: str) -> ParsedInput:
        """The NLP time pipeline (Natasha + dateparser + regex fallbacks) on
        text that no longer contains a recurrence phrase or numeric date."""
        logger.debug("Parser: input_length=%s timezone=%s", len(text), timezone)

        # Stage 1 — heuristics
        normalized_text = self._apply_heuristics(text)
        clean_text = normalized_text

        # Stage 1.5 — recurrence phrase detection (see _RECURRENCE_RULES).
        # Deliberately independent of the date/time stages below: none of
        # them ever resolve "каждый день"/"every day"/etc. to a datetime,
        # so this can't steal a match from Stage 3/4.
        # (Recurrence phrases were already cut out by _parse_sync.)
        rrule_string: Optional[str] = None

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
        dp_matches = _search_dates_any_language(_mask_phone_like(normalized_text), dp_settings)

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
        now_local = datetime.now(resolve_timezone(timezone))
        min_year, max_year = now_local.year, now_local.year + 5
        dp_matches = [(s, dt) for s, dt in (dp_matches or []) if min_year <= dt.year <= max_year]

        # 2: drop a dateparser match that misread "в 23 часа"/"at 14 hours"/
        # "a las 23 horas" as a duration offset from now instead of a clock
        # time — see _AMBIGUOUS_HOUR_WORD_RE. Falls through to the Stage 4a
        # regex fallback below, which resolves the same text correctly.
        # The per-language search can return the bare "14 hours" without its
        # "at", so the preposition is also looked up in the text before it.
        def _is_ambiguous_hour_match(span: str) -> bool:
            span = span.strip()
            if _AMBIGUOUS_HOUR_WORD_RE.match(span):
                return True
            idx = normalized_text.find(span)
            preposition = _PREPOSITION_TAIL_RE.search(normalized_text[:idx]) if idx > 0 else None
            return bool(preposition and _AMBIGUOUS_HOUR_WORD_RE.match(preposition.group(0) + span))

        # Sub-spans of a dropped match ("14:00" inside "at 14:00 hours") go too,
        # or the per-language search would resurrect it as a bare clock time
        # and leave the hour word dangling in the task text.
        dropped_spans = [s.strip() for s, _ in dp_matches if _is_ambiguous_hour_match(s)]
        dp_matches = [
            (s, dt) for s, dt in dp_matches if not any(s.strip() in d for d in dropped_spans)
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

        # Same bug class, opposite span: for "в 2 дня" dateparser DOES
        # swallow the period word into its own match — and then resolves
        # the whole thing to "right now". Russian period-of-day words are
        # handled correctly by the regex fallback below, so a dateparser
        # match containing one is dropped in favor of it. (English "5pm"/
        # "at 5 pm" dateparser gets right, so those are left alone.)
        dp_matches = [(s, dt) for s, dt in dp_matches if not _RU_PERIOD_IN_MATCH_RE.search(s)]

        # A matched "HH:MM" that the resolved datetime disagrees with is a
        # misread ("в 18:00 день рождения" came back as "now" — dateparser
        # took "день" for a unit). Drop it; the regex fallback reads the
        # clock time itself.
        def _clock_agrees(span: str, dt: datetime) -> bool:
            clock = _CLOCK_IN_SPAN_RE.search(span)
            return clock is None or (int(clock.group(1)), int(clock.group(2))) == (dt.hour, dt.minute)

        dp_matches = [(s, dt) for s, dt in dp_matches if _clock_agrees(s, dt)]

        date_only = False
        if dp_matches:
            matched_substring, dt_obj = dp_matches[0]
            extra_span = None
            if not _EXPLICIT_TIME_MARKER_RE.search(matched_substring):
                # A day and a clock time written in different languages
                # ("послезавтра a las 10:00") come back as separate spans;
                # join the day of the first with the time of the second.
                for other_span, other_dt in dp_matches[1:]:
                    if (
                        _EXPLICIT_TIME_MARKER_RE.search(other_span)
                        and other_span not in matched_substring
                        and matched_substring not in other_span
                    ):
                        dt_obj = dt_obj.replace(
                            hour=other_dt.hour,
                            minute=other_dt.minute,
                            second=0,
                            microsecond=0,
                        )
                        extra_span = other_span
                        break
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
            if extra_span is None and not _EXPLICIT_TIME_MARKER_RE.search(matched_substring):
                confidence = min(confidence, 0.4)
                date_only = True
            clean_text = _strip_first_occurrence(clean_text, matched_substring)
            if extra_span is not None:
                clean_text = _strip_first_occurrence(clean_text, extra_span)
            if date_only:
                # B-03: "в 10 утра в понедельник" — the clock part was
                # dropped above (trailing period word), the day survived.
                # Merge the regex-read clock time into that day.
                rest = normalized_text.replace(matched_substring, " ", 1)
                hour_match = _HOUR_RE.search(rest)
                clock = self._clock_from_match(hour_match, timezone) if hour_match else None
                if clock is not None:
                    parsed_datetime = self._on_day(parsed_datetime, clock, timezone)
                    date_only = False
                    confidence = 0.75
                    clean_text = _strip_first_occurrence(clean_text, hour_match.group(0).strip())

        # Stage 4a — regex fallback: "в 23", "at 9", "в 10 утра", "в 23 часа"
        if not parsed_datetime:
            now = datetime.now(resolve_timezone(timezone))
            hour_match = _HOUR_RE.search(normalized_text)
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
                    # B-03: "завтра в 10 утра" — dateparser's match was
                    # dropped for its trailing period word, taking the day
                    # with it. Look for a day-only phrase in what's left.
                    day = self._find_day_only(normalized_text.replace(hour_match.group(0), " ", 1), timezone)
                    if day is not None:
                        day_span, day_dt = day
                        parsed_datetime = self._on_day(day_dt, result_dt, timezone)
                        clean_text = _strip_first_occurrence(clean_text, day_span)
                    parse_source = "regex_hour"
                    # An explicit period-of-day word ("в 7 утра", "at 5 pm")
                    # leaves nothing ambiguous to confirm. Since the trailing-
                    # period fix routes exactly these phrases here instead of
                    # through dateparser (0.75), keeping the generic 0.55
                    # fallback score made every one of them ask for
                    # confirmation — "в 7 утра" used to save directly.
                    confidence = 0.75 if hour_match.group(3) else 0.55
                    clean_text = _strip_first_occurrence(clean_text, hour_match.group(0))

        # Stage 4b — regex fallback: "через 15 минут", "через 2 часа", "через час", "через 2 дня"
        if not parsed_datetime:
            now = datetime.now(resolve_timezone(timezone))
            duration_match = re.search(
                # A-06: a leading \b on the preposition, and a trailing
                # (?!\w) on the unit word, stop this from firing on plain
                # words that merely CONTAIN the pattern — "через 5 человек"
                # used to match "через 5 ч" (the bare "ч" alternative is a
                # prefix of "человек"), and "garden 2 horas" used to match
                # "en 2 horas" (no boundary before "en", which is also the
                # last two letters of "garden").
                r"\b(?:через|dentro\s+de|en|in|em|daqui\s+a)\s+(\d+|un[ao]?|uma?|an?|один|одну)?\s*"
                r"(минут(?:ы)?|часов?|час(?:а)?|ч|дн(?:ей|я)|день|сутки|minutos?|horas?|d[ií]as?|minutes?|hours?|days?)(?!\w)",
                normalized_text,
                re.IGNORECASE,
            )
            if duration_match:
                amount_token = duration_match.group(1) or "1"
                amount = int(amount_token) if amount_token.isdigit() else 1
                unit = duration_match.group(2).lower()
                if unit in ("минут", "минуты", "min", "minutos", "minuto", "minute", "minutes"):
                    parsed_datetime = now + timedelta(minutes=amount)
                elif unit in ("часов", "часа", "час", "ч", "horas", "hora", "hour", "hours"):
                    parsed_datetime = now + timedelta(hours=amount)
                else:  # дней, дня, день, сутки, días, día
                    parsed_datetime = now + timedelta(days=amount)
                parse_source = "regex_duration"
                confidence = 0.75
                clean_text = _strip_first_occurrence(clean_text, duration_match.group(0))

        # Stage 4c — regex fallback: "в 23 часа"
        if not parsed_datetime:
            now = datetime.now(resolve_timezone(timezone))
            hour_match = re.search(
                r"(?:в|a\s+las?|às|as)\s+(\d{1,2})(?:[:.](\d{2}))?\s*"
                r"(?:de\s+la\s+|da\s+|de\s+)?(утра|дня|послеобеденно|вечера|ночи|часов?|mañana|manhã|manha|tarde|noche|noite)?",
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
            r"^(в|на|через|в районе|a\s+las?|en|dentro\s+de|el|em|às|as|daqui\s+a)\s+",
            "",
            clean_text.strip(),
            flags=re.IGNORECASE,
        )
        # Modifier/preposition left stranded where the date phrase was cut out
        # ("Купи молоко next", "Buy milk el") — none of them ends a task text.
        clean_text = re.sub(
            r"(?:\s+(?:next|this|on|at|el|la|este|pr[oó]ximo|pr[oó]xima|no|na|em|às|в|во|на|в следующий))+\s*$",
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

        logger.debug("Parser: has_datetime=%s", parsed_datetime is not None)
        return ParsedInput(
            clean_text=clean_text,
            parsed_datetime=parsed_datetime,
            confidence=confidence,
            parse_source=parse_source,
            rrule_string=rrule_string,
            date_only=date_only,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def parse(self, text: str, timezone: str, language: Optional[str] = None) -> ParsedInput:
        """Parse *text* and return a :class:`ParsedInput`.

        Offloads blocking NLP work to a thread-pool executor.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._parse_sync, text, timezone, language)
