from datetime import datetime
from types import SimpleNamespace

from bot.database.models import is_habit_like
from bot.services.scheduler import SchedulerService
from bot.utils.time_ext import is_quiet_hours, parse_hhmm


def test_parse_hhmm_valid_and_fallback() -> None:
    assert parse_hhmm("07:30", "00:00") == parse_hhmm("07:30", "23:00")
    assert parse_hhmm("bogus", "09:15").hour == 9
    assert parse_hhmm("bogus", "09:15").minute == 15
    assert parse_hhmm("25:99", "09:15").hour == 9  # out-of-range falls back too


def test_is_quiet_hours_handles_overnight_window() -> None:
    user = SimpleNamespace(quiet_hours_enabled=True, quiet_hours_start="23:00", quiet_hours_end="07:00")
    assert is_quiet_hours(user, datetime(2026, 5, 1, 23, 30)) is True
    assert is_quiet_hours(user, datetime(2026, 5, 1, 3, 0)) is True
    assert is_quiet_hours(user, datetime(2026, 5, 1, 12, 0)) is False


def test_is_quiet_hours_disabled_is_always_false() -> None:
    user = SimpleNamespace(quiet_hours_enabled=False, quiet_hours_start="23:00", quiet_hours_end="07:00")
    assert is_quiet_hours(user, datetime(2026, 5, 1, 23, 30)) is False


def test_is_habit_like_excludes_fluid_habits() -> None:
    fluid = SimpleNamespace(is_fluid_habit=True, is_habit=True, habit_streak_current=5)
    fixed = SimpleNamespace(is_fluid_habit=False, is_habit=True, habit_streak_current=0)
    plain = SimpleNamespace(is_fluid_habit=False, is_habit=False, habit_streak_current=0)

    assert is_habit_like(fluid) is False
    assert is_habit_like(fixed) is True
    assert is_habit_like(plain) is False


def test_scheduler_service_quiet_hours_delegates_to_shared_helper() -> None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    service = SchedulerService(AsyncIOScheduler(), bot=SimpleNamespace(), session_pool=None)
    user = SimpleNamespace(timezone="UTC", quiet_hours_enabled=True, quiet_hours_start="23:00", quiet_hours_end="07:00")

    import pytz

    now_utc = pytz.UTC.localize(datetime(2026, 5, 1, 23, 30))
    assert service._is_quiet_hours_now(user, now_utc) is True

    now_utc_awake = pytz.UTC.localize(datetime(2026, 5, 1, 12, 0))
    assert service._is_quiet_hours_now(user, now_utc_awake) is False
