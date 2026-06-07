"""Unit tests for parse_hhmm() — HH:MM string validation."""

import pytest
from bot.utils.time_ext import parse_hhmm


# ---------------------------------------------------------------------------
# Valid inputs
# ---------------------------------------------------------------------------

def test_standard_time_returns_tuple():
    assert parse_hhmm("09:30") == (9, 30)


def test_midnight_is_valid():
    assert parse_hhmm("00:00") == (0, 0)


def test_end_of_day_is_valid():
    assert parse_hhmm("23:59") == (23, 59)


def test_single_digit_hour_is_accepted():
    assert parse_hhmm("9:00") == (9, 0)


def test_leading_whitespace_stripped():
    assert parse_hhmm("  14:30") == (14, 30)


def test_trailing_whitespace_stripped():
    assert parse_hhmm("08:45  ") == (8, 45)


def test_returns_int_tuple():
    result = parse_hhmm("07:05")
    assert isinstance(result, tuple)
    h, m = result
    assert isinstance(h, int) and isinstance(m, int)


# ---------------------------------------------------------------------------
# Invalid format
# ---------------------------------------------------------------------------

def test_empty_string_returns_none():
    assert parse_hhmm("") is None


def test_no_colon_returns_none():
    assert parse_hhmm("0900") is None


def test_single_digit_minute_returns_none():
    assert parse_hhmm("9:5") is None


def test_letters_return_none():
    assert parse_hhmm("ab:cd") is None


def test_natural_language_returns_none():
    assert parse_hhmm("in 30 minutes") is None


def test_date_string_returns_none():
    assert parse_hhmm("09:30:00") is None


# ---------------------------------------------------------------------------
# Out-of-range values
# ---------------------------------------------------------------------------

def test_hour_24_returns_none():
    assert parse_hhmm("24:00") is None


def test_hour_99_returns_none():
    assert parse_hhmm("99:00") is None


def test_minute_60_returns_none():
    assert parse_hhmm("12:60") is None


def test_minute_99_returns_none():
    assert parse_hhmm("00:99") is None
