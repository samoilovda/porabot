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
