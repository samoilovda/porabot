from datetime import datetime

import pytz

from bot.utils.time_ext import next_occurrence_utc


def test_daily_recurrence_keeps_local_wall_clock_time_across_dst_spring_forward() -> None:
    # Europe/Berlin switches to summer time (UTC+1 -> UTC+2) on 2026-03-29.
    # dtstart: 2026-03-28 09:00 local (UTC+1) == 2026-03-28 08:00 UTC.
    dtstart_utc_naive = datetime(2026, 3, 28, 8, 0)
    # "after" cursor: just past the first occurrence.
    after_utc_naive = datetime(2026, 3, 28, 8, 30)

    next_run_utc_naive = next_occurrence_utc(
        "FREQ=DAILY", dtstart_utc_naive, "Europe/Berlin", after_utc_naive
    )

    assert next_run_utc_naive is not None

    tz = pytz.timezone("Europe/Berlin")
    next_run_local = pytz.UTC.localize(next_run_utc_naive).astimezone(tz)

    # The user should still see 09:00 local the next day...
    assert next_run_local.hour == 9
    assert next_run_local.minute == 0
    assert next_run_local.date() == datetime(2026, 3, 29).date()
    # ...even though the UTC offset changed, so the UTC wall-clock hour moved.
    assert next_run_utc_naive.hour == 7  # 09:00 CEST (UTC+2) == 07:00 UTC
    assert next_run_utc_naive != datetime(2026, 3, 29, 8, 0)  # naive-UTC-anchored rrule would drift here


def test_daily_recurrence_falls_back_to_utc_for_unknown_timezone() -> None:
    dtstart_utc_naive = datetime(2026, 5, 1, 9, 0)
    after_utc_naive = datetime(2026, 5, 1, 9, 30)

    next_run_utc_naive = next_occurrence_utc(
        "FREQ=DAILY", dtstart_utc_naive, "Not/ARealZone", after_utc_naive
    )

    assert next_run_utc_naive == datetime(2026, 5, 2, 9, 0)


def test_returns_none_when_no_future_occurrences() -> None:
    dtstart_utc_naive = datetime(2026, 5, 1, 9, 0)
    after_utc_naive = datetime(2026, 5, 1, 9, 30)

    # A one-shot rule (COUNT=1) has no occurrence after its single run.
    next_run_utc_naive = next_occurrence_utc(
        "FREQ=DAILY;COUNT=1", dtstart_utc_naive, "UTC", after_utc_naive
    )

    assert next_run_utc_naive is None
