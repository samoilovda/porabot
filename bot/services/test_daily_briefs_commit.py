import importlib.util
from datetime import datetime, timedelta, timezone
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


async def test_evening_brief_commits_fluid_streak_reset(monkeypatch) -> None:
    daily_briefs = _load_module("bot/services/daily_briefs.py")

    # fake_user.timezone is "UTC", and the code under test resolves "now" via
    # pytz.timezone(user.timezone) — so this fixture must use UTC-aware time
    # too, or the test is flaky depending on the machine's local timezone.
    now_utc = datetime.now(timezone.utc)
    now_hhmm = now_utc.strftime("%H:%M")
    fake_user = SimpleNamespace(
        id=7,
        timezone="UTC",
        language="en",
        briefs_enabled=True,
        show_utc_offset=False,
        quiet_hours_enabled=False,
        morning_brief_time="00:00",
        evening_brief_time=now_hhmm,
        # Already "sent" today so the morning branch is skipped regardless of
        # what time the test happens to run at — only the evening branch (whose
        # commit behavior this test targets) should fire.
        last_morning_brief_date=now_utc.date().isoformat(),
        last_evening_brief_date=None,
    )
    fluid_habit = SimpleNamespace(
        id=99,
        reminder_text="Meditate",
        fluid_mode="brief_only",
        fluid_planned_date=None,
        fluid_last_completed_date=(now_utc.date() - timedelta(days=2)).isoformat(),
        fluid_streak_current=5,
        fluid_streak_best=5,
    )
    reset_calls = []

    class _FakeReminderDAO:
        def __init__(self, session):
            self.session = session

        async def get_active_fluid_habits(self, user_id):
            return [fluid_habit]

        async def reset_stale_fluid_streak_if_needed(self, reminder_id, tz):
            reset_calls.append((reminder_id, tz))
            fluid_habit.fluid_streak_current = 0

        async def get_today_completed_tasks(self, user_id, tz):
            return []

        async def get_today_pending_tasks(self, user_id, tz):
            return []

    class _FakeUserDAO:
        def __init__(self, session):
            self.session = session

        async def get_by_id(self, uid):
            return fake_user

    class _FakeHabitEventDAO:
        def __init__(self, session):
            self.session = session

        async def has_event_for_cycle(self, reminder_id, cycle_key):
            return False

    session = _FakeSession()
    daily_briefs.ReminderDAO = _FakeReminderDAO
    daily_briefs.UserDAO = _FakeUserDAO
    daily_briefs.HabitEventDAO = _FakeHabitEventDAO
    real_scheduler_module._instance = SimpleNamespace(
        bot=SimpleNamespace(send_message=AsyncMock()),
        session_pool=lambda: session,
    )

    try:
        await daily_briefs.process_daily_briefs()
    finally:
        real_scheduler_module._instance = None

    assert reset_calls == [(99, "UTC")]
    assert fluid_habit.fluid_streak_current == 0
    session.commit.assert_awaited()
