"""Step 3 of the 2026-09-26 audit remediation: a phrase that names only a
DAY ("в понедельник", "tomorrow") with no clock time must make the bot ask
for the hour — keeping that recognized date — instead of silently saving a
filler time (local midnight) or showing a raw "Confidence: N%" prompt.
Covers manual time entry, the quick-button follow-up, and the edit-time
flow, since all three now go through reminders_shared._resolve_time_and_respond.
"""

import importlib.util
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.lexicon import get_l10n

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _date_only_result(local_midnight: datetime, text: str = "приготовить обед"):
    """What InputParser.parse returns for a bare weekday/"tomorrow" phrase —
    see test_parser.py's test_date_only_phrase_gets_low_confidence_not_silently_saved."""
    return SimpleNamespace(
        clean_text=text,
        parsed_datetime=local_midnight,
        confidence=0.4,
        parse_source="dateparser",
        rrule_string=None,
        date_only=True,
    )


async def test_manual_date_only_input_asks_for_hour_instead_of_saving() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")
    ReminderWizard = reminders_module.ReminderWizard

    monday = datetime(2026, 9, 28, 0, 0)  # a Monday, local-midnight filler
    state = _make_state()
    await state.set_state(ReminderWizard.choosing_time)
    await state.update_data(text="приготовить обед")

    reminder_dao = SimpleNamespace(create_reminder=AsyncMock(), get_owned=AsyncMock())
    scheduler_service = SimpleNamespace(schedule_reminder=MagicMock())
    user = SimpleNamespace(id=1, timezone="Europe/Moscow", show_utc_offset=False)
    l10n = get_l10n("ru")
    message = SimpleNamespace(text="в понедельник", chat=SimpleNamespace(id=1), answer=AsyncMock())

    original_parse = reminders_module.parser.parse
    try:
        reminders_module.parser.parse = AsyncMock(return_value=_date_only_result(monday))
        await reminders_module.state_choosing_time_text_input(
            message, state, user, l10n, reminder_dao, scheduler_service,
        )
    finally:
        reminders_module.parser.parse = original_parse

    reminder_dao.create_reminder.assert_not_awaited()
    assert await state.get_state() == ReminderWizard.choosing_time.state
    message.answer.assert_awaited_once()
    prompt_text = message.answer.await_args.args[0]
    assert "%" not in prompt_text  # no raw confidence number shown to the user
    assert "0.4" not in prompt_text


async def test_quick_button_after_date_only_prompt_creates_task_on_the_recognized_date() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")
    ReminderWizard = reminders_module.ReminderWizard

    monday = datetime(2026, 9, 28, 0, 0)
    state = _make_state()
    await state.set_state(ReminderWizard.choosing_time)
    await state.update_data(text="приготовить обед")

    reminder_dao = SimpleNamespace(create_reminder=AsyncMock(), get_owned=AsyncMock())
    scheduler_service = SimpleNamespace(schedule_reminder=MagicMock())
    user = SimpleNamespace(id=1, timezone="Europe/Moscow", show_utc_offset=False)
    l10n = get_l10n("ru")
    message = SimpleNamespace(text="в понедельник", chat=SimpleNamespace(id=1), answer=AsyncMock())

    original_parse = reminders_module.parser.parse
    try:
        reminders_module.parser.parse = AsyncMock(return_value=_date_only_result(monday))
        await reminders_module.state_choosing_time_text_input(
            message, state, user, l10n, reminder_dao, scheduler_service,
        )
    finally:
        reminders_module.parser.parse = original_parse

    keyboard = message.answer.await_args.kwargs["reply_markup"]
    morning_button = keyboard.inline_keyboard[0][0]  # first time-of-day slot: 09:00
    assert morning_button.callback_data.startswith("time_fixed_")

    created_reminder = SimpleNamespace(
        id=7, reminder_text="приготовить обед", is_recurring=False, is_nagging=False,
        nagging_max_repeats=3, rrule_string=None,
    )
    reminder_dao.create_reminder = AsyncMock(return_value=created_reminder)
    reminder_dao.session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    callback = SimpleNamespace(
        data=morning_button.callback_data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            delete=AsyncMock(),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=1, chat=SimpleNamespace(id=1))),
        ),
    )

    await reminders_module.callback_time_selected(
        callback, state, user, l10n, reminder_dao, scheduler_service,
    )

    reminder_dao.create_reminder.assert_awaited_once()
    kwargs = reminder_dao.create_reminder.await_args.kwargs
    # 09:00 Europe/Moscow (UTC+3) on the recognized Monday == 06:00 UTC —
    # not "today"/"tomorrow", which is what the bug this fixes would have used.
    assert kwargs["execution_time"] == datetime(2026, 9, 28, 6, 0)


async def test_editing_an_existing_task_time_also_asks_for_hour_on_date_only_input() -> None:
    """Same routing for the edit-time flow (callback_edit_edit sets
    choosing_time on an existing reminder) — step 3 unifies this with task
    creation and manual entry, so it must not save at a filler midnight
    either."""
    reminders_module = _load_module("bot/handlers/reminders.py")
    ReminderWizard = reminders_module.ReminderWizard

    monday = datetime(2026, 9, 28, 0, 0)
    state = _make_state()
    await state.set_state(ReminderWizard.choosing_time)
    await state.update_data(text="call mom", edit_reminder_id=42)

    reminder_dao = SimpleNamespace(create_reminder=AsyncMock(), get_owned=AsyncMock())
    scheduler_service = SimpleNamespace(schedule_reminder=MagicMock())
    user = SimpleNamespace(id=1, timezone="Europe/Moscow", show_utc_offset=False)
    l10n = get_l10n("ru")
    message = SimpleNamespace(text="в понедельник", chat=SimpleNamespace(id=1), answer=AsyncMock())

    original_parse = reminders_module.parser.parse
    try:
        reminders_module.parser.parse = AsyncMock(return_value=_date_only_result(monday, text="call mom"))
        await reminders_module.state_choosing_time_text_input(
            message, state, user, l10n, reminder_dao, scheduler_service,
        )
    finally:
        reminders_module.parser.parse = original_parse

    reminder_dao.get_owned.assert_not_awaited()
    assert await state.get_state() == ReminderWizard.choosing_time.state
    data = await state.get_data()
    assert data["edit_reminder_id"] == 42  # not lost while asking for the hour
