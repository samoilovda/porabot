from datetime import datetime

import pytest
import pytz

pytest.importorskip("natasha")
from bot.services.parser import InputParser


def test_hour_expression_uses_period_token_for_pm() -> None:
    parser = InputParser()
    tz = pytz.timezone("Europe/Moscow")
    now = tz.localize(datetime(2026, 1, 1, 8, 0))

    result = parser._process_hour_expression(
        hour=10,
        minute=0,
        period_token="вечера",
        timezone="Europe/Moscow",
        now=now,
    )

    assert result is not None
    assert result.hour == 22
    assert result.day == 1


def test_hour_expression_rolls_to_next_day_if_time_passed() -> None:
    parser = InputParser()
    tz = pytz.timezone("Europe/Moscow")
    now = tz.localize(datetime(2026, 1, 1, 23, 30))

    result = parser._process_hour_expression(
        hour=10,
        minute=0,
        period_token="утра",
        timezone="Europe/Moscow",
        now=now,
    )

    assert result is not None
    assert result.hour == 10
    assert result.day == 2


@pytest.mark.asyncio
async def test_spanish_duration_expression_is_parsed() -> None:
    parser = InputParser()
    result = await parser.parse("recuérdame en 15 minutos beber agua", "Europe/Moscow")

    assert result.parsed_datetime is not None
    assert "beber agua" in result.clean_text


@pytest.mark.asyncio
async def test_spanish_absolute_time_expression_is_parsed() -> None:
    parser = InputParser()
    result = await parser.parse("mañana a las 9 beber agua", "Europe/Moscow")

    assert result.parsed_datetime is not None
    assert result.parsed_datetime.hour == 9
    assert "beber agua" in result.clean_text


def test_normalized_time_phrase_does_not_leak_into_clean_text() -> None:
    parser = InputParser()

    result = parser._parse_sync("напомни вечером выпить чай", "Europe/Moscow")

    assert result.parsed_datetime is not None
    assert result.parsed_datetime.hour == 19
    assert "19:00" not in result.clean_text
    assert result.clean_text == "напомни выпить чай"


def test_normalization_keys_respect_word_boundaries() -> None:
    parser = InputParser()

    # "вечеринка" ("party") must not be corrupted by the "вечером" → "в 19:00"
    # heuristic matching a prefix inside a longer, unrelated word.
    normalized = parser._apply_heuristics("вечеринка в субботу")

    assert normalized.startswith("вечеринка")


def test_spanish_time_phrases_are_parsed() -> None:
    parser = InputParser()

    result = parser._parse_sync("recuérdame por la mañana llamar a mama", "Europe/Madrid")

    assert result.parsed_datetime is not None
    assert result.parsed_datetime.hour == 9
    assert "a las 09:00" not in result.clean_text
    assert "llamar a mama" in result.clean_text


def test_phone_number_does_not_swallow_weekday() -> None:
    parser = InputParser()

    result = parser._parse_sync(
        "Позвони в Минобр 428-94-45 в среду в 11", "Europe/Moscow"
    )

    assert result.parsed_datetime is not None
    assert result.parsed_datetime.weekday() == 2  # Wednesday
    assert result.parsed_datetime.hour == 11
    assert result.clean_text == "Позвони в Минобр 428-94-45"


def test_bare_phone_digits_do_not_swallow_weekday() -> None:
    parser = InputParser()

    result = parser._parse_sync(
        "Позвони маме 89261234567 в пятницу в 18:00", "Europe/Moscow"
    )

    assert result.parsed_datetime is not None
    assert result.parsed_datetime.weekday() == 4  # Friday
    assert result.parsed_datetime.hour == 18
    assert "89261234567" in result.clean_text


# ---------------------------------------------------------------------------
# Written-out numeric dates must survive the phone-number mask
# ---------------------------------------------------------------------------

def test_dashed_numeric_date_is_not_masked_as_phone_number() -> None:
    parser = InputParser()

    result = parser._parse_sync("напомни 15-08-2026 позвонить врачу", "Europe/Moscow")

    assert result.parsed_datetime is not None
    assert (result.parsed_datetime.month, result.parsed_datetime.day) == (8, 15)
    assert result.parsed_datetime.year == 2026
    assert result.clean_text == "напомни позвонить врачу"


def test_space_separated_numeric_date_is_not_masked_as_phone_number() -> None:
    parser = InputParser()

    result = parser._parse_sync("напомни 28 07 2026 позвонить врачу", "Europe/Moscow")

    assert result.parsed_datetime is not None
    assert (result.parsed_datetime.month, result.parsed_datetime.day) == (7, 28)
    assert result.parsed_datetime.year == 2026


def test_iso_date_is_not_masked_as_phone_number() -> None:
    parser = InputParser()

    result = parser._parse_sync("оплатить кредит 2027-05-01", "Europe/Moscow")

    assert result.parsed_datetime is not None
    assert (result.parsed_datetime.year, result.parsed_datetime.month, result.parsed_datetime.day) == (2027, 5, 1)


# ---------------------------------------------------------------------------
# dateparser sometimes reads an unrelated bare number (price, quantity, a
# library bug doubling "N году") as an implausible year — must not surface.
# ---------------------------------------------------------------------------

def test_price_like_number_is_not_misread_as_a_year() -> None:
    parser = InputParser()

    result = parser._parse_sync("заплатить 2000 рублей", "Europe/Moscow")

    assert result.parsed_datetime is None
    assert result.clean_text == "заплатить 2000 рублей"


def test_quantity_number_is_not_misread_as_a_past_year() -> None:
    parser = InputParser()

    result = parser._parse_sync("купить 1500 подарок", "Europe/Moscow")

    assert result.parsed_datetime is None


def test_bare_number_is_not_misread_as_a_far_future_year() -> None:
    parser = InputParser()

    result = parser._parse_sync("занять 3000", "Europe/Moscow")

    assert result.parsed_datetime is None


def test_birth_year_mention_is_not_misread_as_a_date() -> None:
    parser = InputParser()

    result = parser._parse_sync("напомнить про день рождения 1994", "Europe/Moscow")

    assert result.parsed_datetime is None


def test_year_word_dateparser_bug_does_not_produce_absurd_future_date() -> None:
    # dateparser has a bug where "N году" can be read as year 2*N (e.g.
    # "2026 году" -> year 4052). Whatever this resolves to, it must not
    # land wildly outside a plausible reminder window.
    parser = InputParser()

    result = parser._parse_sync("в 2026 году открыть бизнес", "Europe/Moscow")

    if result.parsed_datetime is not None:
        assert result.parsed_datetime.year <= datetime.now().year + 5


# ---------------------------------------------------------------------------
# dateparser.search.search_dates returns None (not []) when nothing matches;
# the year-plausibility filter must not choke on that.
# ---------------------------------------------------------------------------

def test_no_dateparser_match_does_not_crash(monkeypatch) -> None:
    import bot.services.parser as parser_module

    monkeypatch.setattr(
        parser_module.dateparser.search, "search_dates", lambda *a, **kw: None
    )
    parser = InputParser()

    result = parser._parse_sync("просто текст без даты", "Europe/Moscow")

    assert result.parsed_datetime is None
    assert result.clean_text == "просто текст без даты"


# ---------------------------------------------------------------------------
# Regex fallback (stage 4a) must keep minutes and not leak a stray ":MM"
# into the cleaned task text when dateparser fails to match at all.
# ---------------------------------------------------------------------------

def test_regex_hour_fallback_preserves_minutes(monkeypatch) -> None:
    import bot.services.parser as parser_module

    monkeypatch.setattr(
        parser_module.dateparser.search, "search_dates", lambda *a, **kw: None
    )
    parser = InputParser()

    result = parser._parse_sync("в 14:30 позвонить банку", "Europe/Moscow")

    assert result.parsed_datetime is not None
    assert result.parsed_datetime.hour == 14
    assert result.parsed_datetime.minute == 30
    assert result.clean_text == "позвонить банку"
    assert ":30" not in result.clean_text


# ---------------------------------------------------------------------------
# GPTaudit27.07.26.md #1 — a recurrence phrase must be detected and stripped,
# not left dangling in clean_text with no signal to the caller.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected_rrule",
    [
        ("каждый день в 9 тренировка", "FREQ=DAILY"),
        ("every day at 9 gym", "FREQ=DAILY"),
        ("cada día a las 9 entrenar", "FREQ=DAILY"),
        ("по будням в 8 зарядка", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        ("every weekday at 8 workout", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        ("каждые выходные уборка", "FREQ=WEEKLY;BYDAY=SA,SU"),
        ("every weekend cleaning", "FREQ=WEEKLY;BYDAY=SA,SU"),
        ("каждую неделю отчет", "FREQ=WEEKLY"),
        ("every week meeting", "FREQ=WEEKLY"),
    ],
)
def test_recurrence_phrase_is_detected_and_stripped(text, expected_rrule) -> None:
    parser = InputParser()

    result = parser._parse_sync(text, "Europe/Moscow")

    assert result.rrule_string == expected_rrule
    for leftover in ("каждый", "every", "cada", "будням", "weekday", "выходные", "weekend", "неделю", "week"):
        assert leftover not in result.clean_text.lower()


def test_plain_text_has_no_recurrence() -> None:
    parser = InputParser()

    result = parser._parse_sync("вечером принять лекарство", "Europe/Moscow")

    assert result.rrule_string is None


# ---------------------------------------------------------------------------
# GPTaudit27.07.26.md #2 — dateparser misreads "в 23 часа"/"at 14 hours"/
# "a las 23 horas" as a relative duration ("N hours from now") instead of a
# clock time, because the trailing hour-unit word also means "duration" to
# it. Must resolve to the stated clock hour, same as the equivalent phrase
# with an explicit colon ("в 23:00").
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected_hour",
    [
        ("в 23 часа спать", 23),
        ("at 14 hours call", 14),
        ("a las 23 horas dormir", 23),
        ("в 9 часов утра тренировка", 9),
        ("в 15 часов встреча", 15),
    ],
)
def test_ambiguous_hour_word_resolves_to_clock_time_not_duration(text, expected_hour) -> None:
    parser = InputParser()

    result = parser._parse_sync(text, "Europe/Moscow")

    assert result.parsed_datetime is not None
    assert result.parsed_datetime.hour == expected_hour
    # None of these phrases specify minutes — a duration misread carries
    # over "now"'s minute/second instead of landing on the hour exactly.
    assert result.parsed_datetime.minute == 0
    assert result.parsed_datetime.second == 0
    for word in ("час", "hour", "hora"):
        assert word not in result.clean_text.lower()


def test_duration_phrase_with_hour_word_is_unaffected() -> None:
    """"через"/"in" durations must keep working — only the "в"/"at"/"a las"
    clock-time preposition triggers the ambiguous-match override."""
    parser = InputParser()
    tz = pytz.timezone("Europe/Moscow")
    before = datetime.now(tz)

    result = parser._parse_sync("через 2 часа позвонить", "Europe/Moscow")

    assert result.parsed_datetime is not None
    delta_minutes = (result.parsed_datetime - before).total_seconds() / 60
    assert 110 <= delta_minutes <= 130
