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
