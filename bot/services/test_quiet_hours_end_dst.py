"""Regression (REWORK_PLAN_3 3.3): _next_quiet_end_utc computed the next
quiet-hours-end time via now_local.replace(...) + timedelta(days=1) on a
pytz-aware datetime. replace() keeps now_local's ORIGINAL tzinfo — a fixed
UTC-offset snapshot pytz bakes in at astimezone() time — instead of
resolving the correct offset for the candidate's own date. A `+= timedelta`
that crosses a DST transition then carries the stale offset, landing the
computed UTC time an hour off.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.services.scheduler import SchedulerService


def test_next_quiet_end_resolves_correct_offset_across_spring_forward() -> None:
    # Europe/Berlin switches to summer time (UTC+1 -> UTC+2) at 2026-03-29
    # 02:00 local, same transition test_dst_recurring_reminder.py uses.
    service = SchedulerService(AsyncIOScheduler(), bot=SimpleNamespace(), session_pool=SimpleNamespace())
    user = SimpleNamespace(timezone="Europe/Berlin", quiet_hours_end="07:00")

    # "now" is 2026-03-28 23:30 CET (UTC+1) — before the transition. Quiet
    # hours end (07:00) has already passed today, so the candidate rolls to
    # tomorrow (2026-03-29 07:00), which is AFTER the transition — CEST
    # (UTC+2).
    now_utc = datetime(2026, 3, 28, 22, 30, tzinfo=timezone.utc)

    result_utc = service._next_quiet_end_utc(user, now_utc)

    # 2026-03-29 07:00 CEST (UTC+2) == 05:00 UTC. The bug computed 06:00 UTC
    # instead — carrying forward the pre-transition UTC+1 offset.
    assert result_utc == datetime(2026, 3, 29, 5, 0, tzinfo=timezone.utc)


def test_next_quiet_end_same_day_when_not_yet_passed() -> None:
    service = SchedulerService(AsyncIOScheduler(), bot=SimpleNamespace(), session_pool=SimpleNamespace())
    user = SimpleNamespace(timezone="UTC", quiet_hours_end="07:00")

    now_utc = datetime(2026, 5, 1, 3, 0, tzinfo=timezone.utc)

    result_utc = service._next_quiet_end_utc(user, now_utc)

    assert result_utc == datetime(2026, 5, 1, 7, 0, tzinfo=timezone.utc)
