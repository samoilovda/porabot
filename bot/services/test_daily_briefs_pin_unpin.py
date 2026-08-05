import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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


async def test_morning_brief_merges_fluid_habits_into_one_message_and_pins_it(monkeypatch) -> None:
    """Regression for the merge: previously the task list and the
    "fluid habits for today" list were two separate send_message calls.
    They must now be a single message, and that message must get pinned.
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
        evening_brief_time="23:59",
        last_morning_brief_date=None,
        last_evening_brief_date=datetime.now(timezone.utc).date().isoformat(),
        pinned_brief_message_id=None,
    )
    pending_task = SimpleNamespace(
        id=1, execution_time=datetime.now(timezone.utc).replace(tzinfo=None), reminder_text="Standup"
    )
    fluid_habit = SimpleNamespace(
        id=99,
        reminder_text="Drink water",
        fluid_mode="brief_only",
        fluid_planned_date=None,
    )

    class _FakeReminderDAO:
        def __init__(self, session):
            self.session = session

        async def get_active_fluid_habits(self, user_id):
            return [fluid_habit]

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

    class _FakeHabitEventDAO:
        def __init__(self, session):
            self.session = session

        async def has_event_for_cycle(self, reminder_id, cycle_key):
            return False

    session = _FakeSession()
    sent_message = MagicMock(spec=daily_briefs.Message, message_id=555)
    send_message = AsyncMock(return_value=sent_message)
    pin_chat_message = AsyncMock()
    daily_briefs.ReminderDAO = _FakeReminderDAO
    daily_briefs.UserDAO = _FakeUserDAO
    daily_briefs.HabitEventDAO = _FakeHabitEventDAO
    real_scheduler_module._instance = SimpleNamespace(
        bot=SimpleNamespace(send_message=send_message, pin_chat_message=pin_chat_message),
        session_pool=lambda: session,
    )

    try:
        await daily_briefs.process_daily_briefs()

        # Single merged message: both the task and the fluid habit show up
        # in the one send_message call, not two separate calls.
        assert send_message.await_count == 1
        sent_text = send_message.await_args.kwargs.get("text") or send_message.await_args.args[1]
        assert "Standup" in sent_text
        assert "Drink water" in sent_text

        pin_chat_message.assert_awaited_once_with(chat_id=7, message_id=555, disable_notification=True)
        assert fake_user.pinned_brief_message_id == 555
    finally:
        real_scheduler_module._instance = None


async def test_evening_brief_unpins_stored_morning_brief_message(monkeypatch) -> None:
    """When the evening summary is actually sent, the morning brief pinned
    earlier in the day must be unpinned and the stored id cleared.
    """
    daily_briefs = _load_module("bot/services/daily_briefs.py")

    now_utc = datetime.now(timezone.utc)
    fake_user = SimpleNamespace(
        id=7,
        timezone="UTC",
        language="en",
        briefs_enabled=True,
        show_utc_offset=False,
        quiet_hours_enabled=False,
        morning_brief_time="00:00",
        evening_brief_time=now_utc.strftime("%H:%M"),
        last_morning_brief_date=now_utc.date().isoformat(),
        last_evening_brief_date=None,
        pinned_brief_message_id=555,
    )
    completed_task = SimpleNamespace(
        id=1, execution_time=now_utc.replace(tzinfo=None), reminder_text="Standup"
    )

    class _FakeReminderDAO:
        def __init__(self, session):
            self.session = session

        async def get_active_fluid_habits(self, user_id):
            return []

        async def get_today_pending_tasks(self, user_id, tz):
            return []

        async def get_today_completed_tasks(self, user_id, tz):
            return [completed_task]

        async def reset_stale_fluid_streak_if_needed(self, reminder_id, tz):
            return None

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
    send_message = AsyncMock(return_value=MagicMock(spec=daily_briefs.Message, message_id=777))
    unpin_chat_message = AsyncMock()
    daily_briefs.ReminderDAO = _FakeReminderDAO
    daily_briefs.UserDAO = _FakeUserDAO
    daily_briefs.HabitEventDAO = _FakeHabitEventDAO
    real_scheduler_module._instance = SimpleNamespace(
        bot=SimpleNamespace(send_message=send_message, unpin_chat_message=unpin_chat_message),
        session_pool=lambda: session,
    )

    try:
        await daily_briefs.process_daily_briefs()

        unpin_chat_message.assert_awaited_once_with(chat_id=7, message_id=555)
        assert fake_user.pinned_brief_message_id is None
    finally:
        real_scheduler_module._instance = None
