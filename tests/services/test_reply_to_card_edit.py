"""Step 12 of the 2026-09-26 audit remediation: replying to a task card
with a time phrase changes that task, only for its owner, and asks rather
than creating a new task when the phrase is ambiguous."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.lexicon import get_l10n

ROOT = Path(__file__).resolve().parents[2]
L10N = get_l10n("en")


def _load():
    spec = importlib.util.spec_from_file_location("wizard_reply_test", ROOT / "bot/handlers/reminders_wizard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _card(reminder_id: int | None):
    if reminder_id is None:
        return SimpleNamespace(reply_markup=None)
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="x", callback_data=f"del_task_{reminder_id}")]]
    )
    return SimpleNamespace(reply_markup=markup)


def _state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _message(text: str, card):
    return SimpleNamespace(
        text=text, chat=SimpleNamespace(id=1), reply_to_message=card,
        answer=AsyncMock(return_value=SimpleNamespace(message_id=9, chat=SimpleNamespace(id=1))),
    )


def _parsed(hour_known: bool):
    dt = datetime.now(timezone.utc) + timedelta(days=2)
    return SimpleNamespace(
        clean_text="", parsed_datetime=dt, confidence=0.95 if hour_known else 0.4,
        parse_source="dateparser", rrule_string=None, date_only=not hour_known,
    )


def test_reminder_id_read_from_card_keyboard() -> None:
    wizard = _load()
    assert wizard._reminder_id_from_card(_card(42)) == 42
    assert wizard._reminder_id_from_card(_card(None)) is None
    assert wizard._reminder_id_from_card(None) is None


async def test_reply_with_full_time_updates_the_existing_task() -> None:
    wizard = _load()
    reminder = SimpleNamespace(
        id=42, reminder_text="call mom", tags=None, priority=None, is_recurring=False,
        is_nagging=False, nagging_max_repeats=3, rrule_string=None, snooze_count=0,
        execution_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )
    dao = SimpleNamespace(
        get_owned=AsyncMock(return_value=reminder), create_reminder=AsyncMock(),
        session=SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
    )
    scheduler = SimpleNamespace(schedule_reminder=MagicMock())
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    state = _state()
    wizard.parser.parse = AsyncMock(return_value=_parsed(hour_known=True))

    await wizard.handle_reply_to_card(_message("friday 18:00", _card(42)), state, user, L10N, dao, scheduler)

    dao.get_owned.assert_awaited_with(42, 1)
    dao.create_reminder.assert_not_awaited()  # edited, not created
    scheduler.schedule_reminder.assert_called_once()


async def test_reply_to_someone_elses_card_is_refused() -> None:
    wizard = _load()
    dao = SimpleNamespace(get_owned=AsyncMock(return_value=None), create_reminder=AsyncMock())
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    message = _message("friday 18:00", _card(42))

    await wizard.handle_reply_to_card(message, _state(), user, L10N, dao, SimpleNamespace())

    message.answer.assert_awaited_once_with(L10N["item_not_found"])
    dao.create_reminder.assert_not_awaited()


async def test_ambiguous_reply_asks_instead_of_creating_a_new_task() -> None:
    wizard = _load()
    reminder = SimpleNamespace(id=42, reminder_text="call mom", tags=None, priority=None)
    dao = SimpleNamespace(get_owned=AsyncMock(return_value=reminder), create_reminder=AsyncMock())
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    state = _state()
    wizard.parser.parse = AsyncMock(return_value=SimpleNamespace(
        clean_text="blah", parsed_datetime=None, confidence=0.0, parse_source="none",
        rrule_string=None, date_only=False,
    ))
    message = _message("asdf", _card(42))

    await wizard.handle_reply_to_card(message, state, user, L10N, dao, SimpleNamespace())

    dao.create_reminder.assert_not_awaited()
    assert (await state.get_data())["edit_reminder_id"] == 42
    assert await state.get_state() == wizard.ReminderWizard.choosing_time.state


async def test_reply_to_a_non_card_message_falls_back_to_creating_a_task() -> None:
    wizard = _load()
    wizard.handle_task_text = AsyncMock()
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)

    await wizard.handle_reply_to_card(_message("buy milk", _card(None)), _state(), user, L10N, SimpleNamespace(), SimpleNamespace())

    wizard.handle_task_text.assert_awaited_once()
