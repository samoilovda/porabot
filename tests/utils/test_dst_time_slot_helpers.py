"""Regression tests for 1.3: local_time_today_or_tomorrow, local_time_tomorrow,
and local_time_today_strict must not drift by an hour across a DST
transition.

The bug they replace — `now.replace(hour=..., minute=...)` on an already
pytz-localized `now`, sometimes followed by `+ timedelta(days=1)` — reuses
the UTC offset baked into `now`'s tzinfo even when the target instant falls
on the other side of a DST transition. These tests pin down the concrete
1-hour drift that produces, using a real transition date, and confirm the
fixed helpers land on the correct UTC instant instead.
"""

from datetime import datetime, timezone

from bot.utils.time_ext import (
    local_day_bounds_utc,
    local_days_ago_utc,
    local_time_today_or_tomorrow,
    local_time_today_strict,
    local_time_tomorrow,
)

# Europe/Berlin switches to summer time (CET, UTC+1 -> CEST, UTC+2) at
# 2026-03-29 02:00 local. "Now" below is 2026-03-28 20:00 CET (19:00 UTC) —
# the evening before the transition, with the requested slot (09:00) already
# passed for "today" (03-28), so every "roll to tomorrow" helper must land
# on 2026-03-29, which is on the OTHER side of the transition.
_NOW_BEFORE_TRANSITION_UTC = datetime(2026, 3, 28, 19, 0, tzinfo=timezone.utc)


def test_today_or_tomorrow_lands_on_correct_utc_instant_across_dst() -> None:
    result = local_time_today_or_tomorrow(
        "Europe/Berlin", 9, now_utc=_NOW_BEFORE_TRANSITION_UTC
    )

    # A buggy `now.replace(hour=9) + timedelta(days=1)` implementation would
    # keep CET's +01:00 offset baked into `now` and land on 08:00 UTC
    # instead — one hour later than the correct 07:00 (09:00 CEST).
    assert result == datetime(2026, 3, 29, 7, 0, tzinfo=timezone.utc)


def test_tomorrow_lands_on_correct_utc_instant_across_dst() -> None:
    result = local_time_tomorrow("Europe/Berlin", 9, now_utc=_NOW_BEFORE_TRANSITION_UTC)
    assert result == datetime(2026, 3, 29, 7, 0, tzinfo=timezone.utc)


def test_today_strict_lands_on_correct_utc_instant_across_dst_when_still_ahead() -> None:
    # "Now" is 2026-03-28 20:00 CET; ask for a slot still ahead THAT day
    # (23:00) — no rollover, but still must resolve via the correct (CET)
    # offset for today, not tomorrow's.
    result = local_time_today_strict("Europe/Berlin", 23, now_utc=_NOW_BEFORE_TRANSITION_UTC)
    assert result == datetime(2026, 3, 28, 22, 0, tzinfo=timezone.utc)  # 23:00 CET == 22:00 UTC


def test_today_strict_returns_none_when_already_passed() -> None:
    result = local_time_today_strict("Europe/Berlin", 9, now_utc=_NOW_BEFORE_TRANSITION_UTC)
    assert result is None


def test_today_or_tomorrow_stays_today_when_slot_still_ahead() -> None:
    # 2026-06-01 08:00 UTC == 10:00 Europe/Berlin (CEST, UTC+2) — 19:00
    # local slot hasn't happened yet today.
    now_utc = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)
    result = local_time_today_or_tomorrow("Europe/Berlin", 19, now_utc=now_utc)
    assert result == datetime(2026, 6, 1, 17, 0, tzinfo=timezone.utc)  # 19:00 CEST == 17:00 UTC


def test_unknown_timezone_falls_back_to_utc() -> None:
    # 09:00 is still ahead of "now" (08:00 UTC) when treated as UTC itself
    # (the fallback zone) — no rollover to tomorrow.
    now_utc = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)
    result = local_time_today_or_tomorrow("Not/ARealZone", 9, now_utc=now_utc)
    assert result == datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)

    # 09:00 has already passed "today" relative to a later "now" — rolls to
    # tomorrow, still as plain UTC.
    later_now_utc = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    result_rolled = local_time_today_or_tomorrow("Not/ARealZone", 9, now_utc=later_now_utc)
    assert result_rolled == datetime(2026, 6, 2, 9, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# docs/audits/2026-09-22-audit.md#a-20 — local_day_bounds_utc/
# local_days_ago_utc must not drift a day boundary by an hour across a DST
# transition, the same bug class as the helpers above but for
# ReminderDAO's "today"/"this week"/"last N days" queries.
# ---------------------------------------------------------------------------

# Europe/Berlin switches to summer time (CET, UTC+1 -> CEST, UTC+2) at
# 2026-03-29 02:00 local. "Now" is 2026-03-29 10:00 CEST (08:00 UTC) — AFTER
# the transition, but TODAY's own local midnight (2026-03-29 00:00) was
# still on the CET side of it (the transition happens at 02:00, not 00:00).
_NOW_ON_TRANSITION_DAY_UTC = datetime(2026, 3, 29, 8, 0, tzinfo=timezone.utc)


def test_local_day_bounds_start_uses_todays_own_offset_not_nows() -> None:
    start_utc, end_utc = local_day_bounds_utc("Europe/Berlin", now_utc=_NOW_ON_TRANSITION_DAY_UTC)

    # A buggy `now_local.replace(hour=0, ...)` implementation keeps "now"'s
    # CEST (+02:00) offset baked in, landing on 2026-03-28 22:00 UTC — one
    # hour EARLIER than the correct 23:00 UTC (2026-03-29 00:00 CET, still
    # +01:00 since the transition hadn't happened yet at local midnight).
    assert start_utc == datetime(2026, 3, 28, 23, 0)
    assert end_utc == datetime(2026, 3, 29, 22, 0)  # tomorrow's midnight, CEST (+02:00)


def test_local_day_bounds_excludes_a_task_the_buggy_start_would_have_included() -> None:
    """A task at 2026-03-28 22:30 UTC is 23:30 CET on March 28 local —
    yesterday, not "today" (March 29). The old buggy start boundary
    (22:00 UTC) would have wrongly counted it as today's."""
    start_utc, end_utc = local_day_bounds_utc("Europe/Berlin", now_utc=_NOW_ON_TRANSITION_DAY_UTC)
    task_utc = datetime(2026, 3, 28, 22, 30)

    assert not (start_utc <= task_utc < end_utc)


def test_local_day_bounds_week_window_is_seven_days_from_todays_midnight() -> None:
    start_utc, end_utc = local_day_bounds_utc("Europe/Berlin", days=7, now_utc=_NOW_ON_TRANSITION_DAY_UTC)

    assert start_utc == datetime(2026, 3, 28, 23, 0)
    assert end_utc == datetime(2026, 4, 4, 22, 0)  # +7 local days, CEST by then


def test_local_days_ago_start_uses_that_days_own_offset() -> None:
    """"7 days ago" from 2026-03-29 10:00 CEST lands on 2026-03-22 10:00
    CET (+01:00, well before the transition) — a buggy implementation
    that keeps "now"'s CEST (+02:00) offset would land an hour off."""
    start_utc, end_utc = local_days_ago_utc("Europe/Berlin", 7, now_utc=_NOW_ON_TRANSITION_DAY_UTC)

    assert start_utc == datetime(2026, 3, 22, 9, 0)  # 10:00 CET == 09:00 UTC
    assert end_utc == _NOW_ON_TRANSITION_DAY_UTC.replace(tzinfo=None)


def test_local_day_bounds_unknown_timezone_falls_back_to_utc() -> None:
    start_utc, end_utc = local_day_bounds_utc("Not/ARealZone", now_utc=datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc))
    assert start_utc == datetime(2026, 6, 1, 0, 0)
    assert end_utc == datetime(2026, 6, 2, 0, 0)
