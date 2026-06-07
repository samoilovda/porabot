"""Unit tests for next_rrule_occurrence()."""

from datetime import datetime, timezone
from bot.utils.recurrence import next_rrule_occurrence


_BASE = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)   # Monday 2026-01-05 09:00 UTC


def test_daily_rule_returns_next_day():
    after = _BASE
    result = next_rrule_occurrence("FREQ=DAILY", _BASE, after=after)
    assert result is not None
    assert result > after
    assert result.day == 6


def test_weekly_rule_returns_next_week():
    after = _BASE
    result = next_rrule_occurrence("FREQ=WEEKLY", _BASE, after=after)
    assert result is not None
    assert (result - _BASE).days == 7


def test_daily_rule_far_in_future():
    after = datetime(2026, 12, 31, 9, 0, tzinfo=timezone.utc)
    result = next_rrule_occurrence("FREQ=DAILY", _BASE, after=after)
    assert result is not None
    assert result > after


def test_invalid_rrule_returns_none():
    result = next_rrule_occurrence("NOT_A_RULE", _BASE, after=_BASE)
    assert result is None


def test_naive_after_treated_as_utc():
    naive_after = datetime(2026, 1, 5, 9, 0)    # no tzinfo
    result = next_rrule_occurrence("FREQ=DAILY", _BASE, after=naive_after)
    assert result is not None


def test_naive_execution_time_treated_as_utc():
    naive_base = datetime(2026, 1, 5, 9, 0)     # no tzinfo
    result = next_rrule_occurrence("FREQ=DAILY", naive_base, after=_BASE)
    assert result is not None


def test_weekday_rule_skips_weekend():
    # FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR — Fri→next Mon
    friday = datetime(2026, 1, 9, 9, 0, tzinfo=timezone.utc)   # Friday
    result = next_rrule_occurrence(
        "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        friday,
        after=friday,
    )
    assert result is not None
    assert result.weekday() == 0   # Monday (0)
