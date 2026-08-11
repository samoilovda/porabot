import importlib.util
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot.services.scheduler as real_scheduler_module

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeSession:
    def __init__(self):
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, _stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [7]), rowcount=1)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_report_still_fires_a_few_minutes_after_configured_time() -> None:
    """Regression (REWORK_PLAN_3 1.5): the old exact-minute match
    (now.strftime("%H:%M") != habit_report_time) meant any downtime spanning
    that one minute — deploy, restart, a slow earlier iteration of this same
    per-user loop — silently lost the weekly/monthly report until next week.
    Must behave like daily_briefs: "time has passed and not sent today",
    a window, not an exact match.
    """
    habit_reports = _load_module("bot/services/habit_reports.py")

    # habit_report_time is 23:50; "now" is 23:55 — 5 minutes late, as if the
    # tick that should have fired at 23:50 was skipped or delayed.
    report_weekday_now = datetime(2026, 5, 3, 23, 55, 0)  # a Sunday
    fake_user = SimpleNamespace(
        id=7,
        timezone="UTC",
        language="en",
        habit_reports_enabled=True,
        habit_report_time="23:50",
        habit_report_weekday=report_weekday_now.weekday(),
        last_habit_report_date=None,
    )

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return report_weekday_now.replace(tzinfo=tz)

    class _FakeUserDAO:
        def __init__(self, session):
            self.session = session

        async def get_by_id(self, uid):
            return fake_user

    process_called = AsyncMock(return_value=True)

    habit_reports.UserDAO = _FakeUserDAO
    habit_reports.datetime = _FrozenDatetime
    habit_reports._process_user_reports = process_called

    session = _FakeSession()
    real_scheduler_module._instance = SimpleNamespace(
        bot=SimpleNamespace(),
        session_pool=lambda: session,
    )

    try:
        await habit_reports.process_habit_reports()

        # The atomic claim (session.execute) succeeded — process_habit_reports
        # went on to build/send the report instead of `continue`-ing past it.
        process_called.assert_awaited_once()
    finally:
        real_scheduler_module._instance = None


async def test_report_not_sent_before_configured_time() -> None:
    """Baseline: must not fire early just because the exact match was
    loosened to a window."""
    habit_reports = _load_module("bot/services/habit_reports.py")

    before_report_time = datetime(2026, 5, 3, 23, 40, 0)  # a Sunday, before 23:50
    fake_user = SimpleNamespace(
        id=7,
        timezone="UTC",
        language="en",
        habit_reports_enabled=True,
        habit_report_time="23:50",
        habit_report_weekday=before_report_time.weekday(),
        last_habit_report_date=None,
    )

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return before_report_time.replace(tzinfo=tz)

    class _FakeUserDAO:
        def __init__(self, session):
            self.session = session

        async def get_by_id(self, uid):
            return fake_user

    process_called = AsyncMock(return_value=True)

    habit_reports.UserDAO = _FakeUserDAO
    habit_reports.datetime = _FrozenDatetime
    habit_reports._process_user_reports = process_called

    session = _FakeSession()
    real_scheduler_module._instance = SimpleNamespace(
        bot=SimpleNamespace(),
        session_pool=lambda: session,
    )

    try:
        await habit_reports.process_habit_reports()

        process_called.assert_not_awaited()
        assert fake_user.last_habit_report_date is None
    finally:
        real_scheduler_module._instance = None
