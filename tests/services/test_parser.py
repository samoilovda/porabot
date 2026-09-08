from datetime import datetime

import pytz
import pytest

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
