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
