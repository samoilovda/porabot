from datetime import datetime, timezone

import pytz

from bot.utils.time_ext import format_time, to_utc_aware, to_utc_naive


def test_to_utc_naive_converts_local_time_to_naive_utc() -> None:
    dubai = pytz.timezone("Asia/Dubai")
    local_dt = dubai.localize(datetime(2026, 4, 27, 23, 30))

    stored = to_utc_naive(local_dt)

    assert stored == datetime(2026, 4, 27, 19, 30)
    assert stored.tzinfo is None
    assert format_time(stored, "Asia/Dubai", False, "%H:%M") == "23:30"


def test_to_utc_aware_keeps_naive_values_as_utc() -> None:
    naive_utc = datetime(2026, 4, 27, 23, 0)

    aware = to_utc_aware(naive_utc)

    assert aware == datetime(2026, 4, 27, 23, 0, tzinfo=timezone.utc)
    assert aware.tzinfo == timezone.utc


def test_to_utc_aware_converts_non_utc_aware_input() -> None:
    moscow = pytz.timezone("Europe/Moscow")
    local_dt = moscow.localize(datetime(2026, 4, 27, 23, 0))

    aware_utc = to_utc_aware(local_dt)

    assert aware_utc == datetime(2026, 4, 27, 20, 0, tzinfo=timezone.utc)
