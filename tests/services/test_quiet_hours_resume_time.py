"""Audit 2026-11-18 #06: the time a reminder resumes after quiet hours must be
a moment at which is_quiet_hours is actually false, reached in one deferral.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.services.scheduler import SchedulerService
from bot.utils.time_ext import is_quiet_hours

BERLIN = pytz.timezone("Europe/Berlin")


def _service() -> SchedulerService:
    return SchedulerService(AsyncIOScheduler(), bot=SimpleNamespace(), session_pool=SimpleNamespace())


def _user(tz="UTC", weekend=True, **kw):
    base = dict(
        timezone=tz, quiet_hours_enabled=True, quiet_hours_start="23:00", quiet_hours_end="07:00",
        quiet_hours_weekend_enabled=weekend, quiet_hours_weekend_start="23:00", quiet_hours_weekend_end="10:00",
        quiet_hours_habits_exempt=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_sunday_night_resumes_monday_morning_not_weekend_end() -> None:
    # 2026-11-22 is a Sunday; Monday's window ends 07:00, not the weekend's 10:00.
    assert _service()._next_quiet_end_utc(_user(), _utc(2026, 11, 22, 23, 30)) == _utc(2026, 11, 23, 7, 0)


def test_friday_night_resumes_saturday_ten_in_one_hop() -> None:
    # 2026-11-20 is a Friday; Saturday 07:00 is still quiet (weekend 23-10).
    user = _user()
    result = _service()._next_quiet_end_utc(user, _utc(2026, 11, 20, 23, 30))
    assert result == _utc(2026, 11, 21, 10, 0)
    assert not is_quiet_hours(user, result)


def test_weekend_window_disabled_uses_weekday_end() -> None:
    user = _user(weekend=False)
    assert _service()._next_quiet_end_utc(user, _utc(2026, 11, 20, 23, 30)) == _utc(2026, 11, 21, 7, 0)
    assert _service()._next_quiet_end_utc(user, _utc(2026, 11, 22, 23, 30)) == _utc(2026, 11, 23, 7, 0)


def test_after_midnight_in_friday_window_still_in_weekday_window() -> None:
    # Saturday 02:00 is inside the WEEKEND window (23-10), ends 10:00.
    assert _service()._next_quiet_end_utc(_user(), _utc(2026, 11, 21, 2, 0)) == _utc(2026, 11, 21, 10, 0)


def test_habits_exempt_habit_is_not_deferred_further_than_needed() -> None:
    user = _user(quiet_hours_habits_exempt=True)
    now = _utc(2026, 11, 20, 23, 30)
    result = _service()._next_quiet_end_utc(user, now, is_habit=True)
    assert not is_quiet_hours(user, result, is_habit=True)
    assert result > now


def test_always_quiet_window_keeps_fallback(caplog) -> None:
    user = _user(weekend=False, quiet_hours_start="07:00", quiet_hours_end="07:00")
    result = _service()._next_quiet_end_utc(user, _utc(2026, 11, 20, 12, 0))
    assert result == _utc(2026, 11, 21, 7, 0)
    assert "No end of quiet hours" in caplog.text


@pytest.mark.parametrize(
    "now_local, expected_local",
    [
        # Berlin springs forward Sun 2026-03-29 02:00 -> 03:00.
        (datetime(2026, 3, 28, 23, 30), datetime(2026, 3, 29, 10, 0)),  # Sat night -> Sun weekend end
        (datetime(2026, 3, 29, 23, 30), datetime(2026, 3, 30, 7, 0)),   # Sun night -> Mon weekday end
        # Berlin falls back Sun 2026-10-25 03:00 -> 02:00.
        (datetime(2026, 10, 24, 23, 30), datetime(2026, 10, 25, 10, 0)),
        (datetime(2026, 10, 25, 23, 30), datetime(2026, 10, 26, 7, 0)),
    ],
)
def test_dst_weekend_boundaries_in_berlin(now_local, expected_local) -> None:
    user = _user("Europe/Berlin")
    now_utc = BERLIN.localize(now_local).astimezone(timezone.utc)
    result = _service()._next_quiet_end_utc(user, now_utc)
    assert result == BERLIN.localize(expected_local).astimezone(timezone.utc)
    assert not is_quiet_hours(user, result.astimezone(BERLIN))


def test_result_is_never_quiet_for_any_start_time() -> None:
    """Property check: every half hour across two weeks resumes at a moment
    that is not quiet and is later than now."""
    from datetime import timedelta

    user, svc = _user("Europe/Berlin"), _service()
    now = _utc(2026, 3, 20, 0, 0)
    for _ in range(14 * 48):
        if is_quiet_hours(user, now.astimezone(BERLIN)):
            result = svc._next_quiet_end_utc(user, now)
            assert result > now and not is_quiet_hours(user, result.astimezone(BERLIN)), now
        now += timedelta(minutes=30)
