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


async def test_stale_morning_window_is_suppressed_only_evening_brief_sent(monkeypatch) -> None:
    """If both the morning and evening windows have already passed today
    (e.g. the bot was down, or briefs were just enabled late), only the
    evening brief should fire — a "good morning" brief this late is stale
    and confusing.
    """
    daily_briefs = _load_module("bot/services/daily_briefs.py")

    fake_user = SimpleNamespace(
        id=7,
        timezone="UTC",
        language="en",
        briefs_enabled=True,
        show_utc_offset=False,
        quiet_hours_enabled=False,
        morning_brief_time="00:00",
        # Both windows are already in the past relative to "now" (unless the
        # test happens to run in the first minute of the UTC day).
        evening_brief_time="00:01",
        last_morning_brief_date=None,
        last_evening_brief_date=None,
    )
    pending_task = SimpleNamespace(id=1, execution_time=datetime.now(), reminder_text="Standup")

    class _FakeReminderDAO:
        def __init__(self, session):
            self.session = session

        async def get_active_fluid_habits(self, user_id):
            return []

        async def get_today_pending_tasks(self, user_id, tz):
            return [pending_task]

        async def get_today_completed_tasks(self, user_id, tz):
            return []

        async def reset_stale_fluid_streak_if_needed(self, reminder_id, tz):
            return None

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
        await daily_briefs.process_daily_briefs()

        # Exactly one send: the evening brief. No stale morning brief.
        assert send_message.await_count == 1
        sent_text = send_message.await_args.kwargs.get("text") or send_message.await_args.args[1]
        assert "Standup" in sent_text  # evening text lists pending tasks

        today_str = datetime.now().date().isoformat()
        assert fake_user.last_morning_brief_date == today_str
        assert fake_user.last_evening_brief_date == today_str
    finally:
        real_scheduler_module._instance = None


async def test_morning_brief_still_sent_when_evening_time_misconfigured_before_morning(monkeypatch) -> None:
    """If a user sets evening_brief_time <= morning_brief_time, the morning
    window (morning..evening) is empty by construction. Regression: this must
    not permanently suppress the morning brief every day — the bug fell back
    to an always-empty window that a stale-morning cleanup then marked as
    handled before it ever got a chance to fire.
    """
    daily_briefs = _load_module("bot/services/daily_briefs.py")

    fake_user = SimpleNamespace(
        id=7,
        timezone="UTC",
        language="en",
        briefs_enabled=True,
        show_utc_offset=False,
        quiet_hours_enabled=False,
        morning_brief_time="09:00",
        evening_brief_time="08:00",  # misconfigured: before morning_brief_time
        last_morning_brief_date=None,
        last_evening_brief_date=None,
    )
    pending_task = SimpleNamespace(id=1, execution_time=datetime.now(), reminder_text="Standup")

    class _FakeReminderDAO:
        def __init__(self, session):
            self.session = session

        async def get_active_fluid_habits(self, user_id):
            return []

        async def get_today_pending_tasks(self, user_id, tz):
            return [pending_task]

        async def get_today_completed_tasks(self, user_id, tz):
            return []

        async def reset_stale_fluid_streak_if_needed(self, reminder_id, tz):
            return None

    class _FakeUserDAO:
        def __init__(self, session):
            self.session = session

        async def get_by_id(self, uid):
            return fake_user

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 5, 1, 10, 0, 0, tzinfo=tz)

    session = _FakeSession()
    send_message = AsyncMock()
    daily_briefs.ReminderDAO = _FakeReminderDAO
    daily_briefs.UserDAO = _FakeUserDAO
    daily_briefs.datetime = _FrozenDatetime
    real_scheduler_module._instance = SimpleNamespace(
        bot=SimpleNamespace(send_message=send_message),
        session_pool=lambda: session,
    )

    try:
        await daily_briefs.process_daily_briefs()

        assert send_message.await_count >= 1
        assert fake_user.last_morning_brief_date == "2026-05-01"
    finally:
        real_scheduler_module._instance = None
