"""1.3: habit-creation handlers must commit BEFORE telling the user "created"
— a fixed-time habit's scheduler job (created before this point) must be
removed if the commit that makes the row durable fails, and a fluid
habit's "created" message must not be sent for a row that didn't persist.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import bot.handlers.habits as habits_module


class _FakeParser:
    def __init__(self, parsed_datetime):
        self._parsed_datetime = parsed_datetime

    async def parse(self, text, tz):
        return SimpleNamespace(clean_text=text, parsed_datetime=self._parsed_datetime, confidence=1.0)


async def test_fixed_habit_commit_failure_removes_the_job() -> None:
    from datetime import datetime, timedelta, timezone

    future_local = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)
    orig_parser = habits_module.InputParser
    habits_module.InputParser = lambda: _FakeParser(future_local)
    try:
        created_reminder = SimpleNamespace(id=1, is_nagging=True)
        reminder_dao = SimpleNamespace(
            create_reminder=AsyncMock(return_value=created_reminder),
            session=SimpleNamespace(
                commit=AsyncMock(side_effect=RuntimeError("database is locked")),
                rollback=AsyncMock(),
            ),
            get_habit_motivation_stats=AsyncMock(return_value={}),
        )
        removed_ids: list[int] = []
        scheduler_service = SimpleNamespace(
            schedule_reminder=Mock(),
            remove_reminder_job=lambda rid: removed_ids.append(rid),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"habit_text": "Stretch"}), clear=AsyncMock())
        user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
        l10n = {
            "habit_default_name": "Habit",
            "habit_created": "Created {habit} at {time}",
            "habit_time_retry": "retry",
            "habit_create_failed_internal": "internal error",
        }
        message = SimpleNamespace(text="in 2 hours", answer=AsyncMock())

        await habits_module.state_habit_time(message, state, user, reminder_dao, scheduler_service, l10n)

        assert removed_ids == [1]
        state.clear.assert_not_awaited()
        message.answer.assert_awaited_with("internal error")
    finally:
        habits_module.InputParser = orig_parser


async def test_fluid_habit_commit_failure_does_not_report_success() -> None:
    reminder_dao = SimpleNamespace(
        create_reminder=AsyncMock(return_value=SimpleNamespace(id=2)),
        session=SimpleNamespace(
            flush=AsyncMock(),
            commit=AsyncMock(side_effect=RuntimeError("database is locked")),
            rollback=AsyncMock(),
        ),
    )
    state = SimpleNamespace(get_data=AsyncMock(return_value={"habit_text": "Drink water"}), clear=AsyncMock())
    user = SimpleNamespace(id=1)
    l10n = {
        "habit_default_name": "Habit",
        "invalid_mode": "invalid",
        "habit_fluid_created": "Created {habit} ({mode})",
        "habit_create_failed_internal": "internal error",
        "habit_fluid_mode_brief_only": "brief only",
        "habit_fluid_mode_ask_time": "ask time",
    }
    callback = SimpleNamespace(
        data="habit_fluid_mode_brief_only",
        message=SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()),
        answer=AsyncMock(),
    )

    await habits_module.cb_fluid_habit_mode(callback, state, user, reminder_dao, l10n)

    callback.message.edit_text.assert_not_awaited()
    callback.message.answer.assert_awaited_with("internal error")
    state.clear.assert_not_awaited()
