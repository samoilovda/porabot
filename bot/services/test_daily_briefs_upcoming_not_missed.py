import importlib.util
from datetime import datetime, timezone
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


async def test_evening_brief_does_not_mark_not_yet_due_task_as_missed() -> None:
    """Regression (REWORK_PLAN_3 2.3): get_today_pending_tasks returns every
    pending task for the local day, including ones whose time hasn't come
    yet — e.g. a task at 23:30 when the evening brief checks at 23:00 (the
    default evening_brief_time). The old code rendered ALL of them as ❌
    "missed" and put ALL of them in the wrap-up keyboard's Done/Not-done
    choice, even though nothing had fired yet for the 23:30 one.
    """
    daily_briefs = _load_module("bot/services/daily_briefs.py")

    now = datetime(2026, 5, 1, 23, 0, 0, tzinfo=timezone.utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    fake_user = SimpleNamespace(
        id=7,
        timezone="UTC",
        language="en",
        briefs_enabled=True,
        show_utc_offset=False,
        quiet_hours_enabled=False,
        morning_brief_time="09:00",
        evening_brief_time="23:00",
        last_morning_brief_date="2026-05-01",  # morning already handled today
        last_evening_brief_date=None,
    )
    overdue_task = SimpleNamespace(
        id=1,
        execution_time=now.replace(tzinfo=None).replace(hour=20, minute=0),
        reminder_text="Overdue task",
    )
    not_yet_due_task = SimpleNamespace(
        id=2,
        execution_time=now.replace(tzinfo=None).replace(hour=23, minute=30),
        reminder_text="Not yet due task",
    )

    class _FakeReminderDAO:
        def __init__(self, session):
            self.session = session

        async def get_active_fluid_habits(self, user_id):
            return []

        async def get_today_pending_tasks(self, user_id, tz):
            return [overdue_task, not_yet_due_task]

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
    send_message = AsyncMock(return_value=SimpleNamespace(message_id=1, chat=SimpleNamespace(id=7)))
    daily_briefs.ReminderDAO = _FakeReminderDAO
    daily_briefs.UserDAO = _FakeUserDAO
    daily_briefs.datetime = _FrozenDatetime
    real_scheduler_module._instance = SimpleNamespace(
        bot=SimpleNamespace(send_message=send_message),
        session_pool=lambda: session,
    )

    try:
        await daily_briefs.process_daily_briefs()

        send_message.assert_awaited_once()
        sent_text = send_message.await_args.kwargs.get("text") or send_message.await_args.args[1]

        assert "❌ Overdue task" in sent_text
        assert "❌ Not yet due task" not in sent_text
        assert "Not yet due task" in sent_text  # still shown, just not as missed
        assert "Still today" in sent_text

        keyboard = send_message.await_args.kwargs.get("reply_markup")
        callback_data = [
            button.callback_data for row in keyboard.inline_keyboard for button in row
        ]
        assert "wrap_done_1" in callback_data
        assert "wrap_done_2" not in callback_data
    finally:
        real_scheduler_module._instance = None
