"""Regression: a locked sqlite file must not be reported to the user as a
text-format problem.

The bot's several per-minute cron jobs (daily_briefs, habit_sweeper,
missed_recovery, habit_reports) share one sqlite file with the FSM-state
table (bot/database/engine.py's WAL/busy_timeout comment documents this).
Under contention that outlasts busy_timeout, InputParser.parse() or
_handle_parsed_result() can raise sqlalchemy.exc.OperationalError
("database is locked") — previously handle_task_text's except Exception
caught this the same as an actual parser bug and told the user "Error
parsing text. Check the format.", which is simply wrong: nothing about
their message was malformed. It should get the honest, actionable
"db_busy" message instead.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.exc import OperationalError

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_state() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    return FSMContext(storage=storage, key=key)


def _locked_error() -> OperationalError:
    return OperationalError("UPDATE fsm_state ...", {}, Exception("database is locked"))


async def test_locked_db_during_parse_shows_db_busy_not_parse_error() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")

    state = _make_state()
    original_parse = reminders_module.parser.parse
    reminders_module.parser.parse = AsyncMock(side_effect=_locked_error())

    message = SimpleNamespace(text="Сделать анонс в органике для курса Олега в 12", answer=AsyncMock())
    user = SimpleNamespace(id=1, timezone="UTC")
    l10n = {
        "parse_error": "Error parsing text. Check the format.",
        "db_busy": "The database is busy — please try again in a few seconds.",
    }

    try:
        await reminders_module.handle_task_text(
            message, state, user, l10n, reminder_dao=None, scheduler_service=None
        )
    finally:
        reminders_module.parser.parse = original_parse

    message.answer.assert_awaited_once_with(l10n["db_busy"])


async def test_locked_db_during_save_shows_db_busy_not_parse_error() -> None:
    import bot.handlers.reminders_wizard as reminders_wizard_module

    reminders_module = _load_module("bot/handlers/reminders.py")

    state = _make_state()
    original_parse = reminders_module.parser.parse
    reminders_module.parser.parse = AsyncMock(
        return_value=SimpleNamespace(clean_text="call mom", parsed_datetime=None, confidence=1.0)
    )
    # handle_task_text looks up _handle_parsed_result via reminders_wizard's
    # own module globals (that's where it's defined), not reminders.py's —
    # patch the real singleton module, not the freshly-loaded reminders.py.
    original_handler = reminders_wizard_module._handle_parsed_result
    reminders_wizard_module._handle_parsed_result = AsyncMock(side_effect=_locked_error())

    message = SimpleNamespace(text="call mom", answer=AsyncMock())
    user = SimpleNamespace(id=1, timezone="UTC")
    l10n = {
        "parse_error": "Error parsing text. Check the format.",
        "db_busy": "The database is busy — please try again in a few seconds.",
    }

    try:
        await reminders_module.handle_task_text(
            message, state, user, l10n, reminder_dao=None, scheduler_service=None
        )
    finally:
        reminders_module.parser.parse = original_parse
        reminders_wizard_module._handle_parsed_result = original_handler

    message.answer.assert_awaited_once_with(l10n["db_busy"])
