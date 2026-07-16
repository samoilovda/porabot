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
