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


async def test_morning_brief_fires_once_per_day_even_if_job_runs_late(monkeypatch) -> None:
    daily_briefs = _load_module("bot/services/daily_briefs.py")

    fake_user = SimpleNamespace(
        id=7,
        timezone="UTC",
        language="en",
        briefs_enabled=True,
        show_utc_offset=False,
        quiet_hours_enabled=False,
        # In the past relative to "now" (unlike the old exact-minute match,
        # a delayed job should still catch this).
        morning_brief_time="00:00",
        evening_brief_time="23:59",
        last_morning_brief_date=None,
        last_evening_brief_date=None,
    )
    task = SimpleNamespace(
        execution_time=datetime.now(),
        reminder_text="Standup",
    )

    class _FakeReminderDAO:
        def __init__(self, session):
            self.session = session

        async def get_active_fluid_habits(self, user_id):
            return []

        async def get_today_pending_tasks(self, user_id, tz):
            return [task]

    class _FakeUserDAO:
        def __init__(self, session):
            self.session = session

        async def get_by_id(self, uid):
            return fake_user

    session = _FakeSession()
    send_message = AsyncMock()
    daily_briefs.ReminderDAO = _FakeReminderDAO
    daily_briefs.UserDAO = _FakeUserDAO
    real_scheduler_module._instance = SimpleNamespace(
        bot=SimpleNamespace(send_message=send_message),
        session_pool=lambda: session,
    )

    try:
        # First run (e.g. job fired a few minutes late — old code's exact
        # "%H:%M" match would have missed it entirely).
        await daily_briefs.process_daily_briefs()
        assert send_message.await_count == 1
        assert fake_user.last_morning_brief_date == datetime.now().date().isoformat()

        # Second run a minute later same day must not resend.
        await daily_briefs.process_daily_briefs()
        assert send_message.await_count == 1
    finally:
        real_scheduler_module._instance = None
