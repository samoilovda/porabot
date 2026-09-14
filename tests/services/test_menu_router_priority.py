"""Full-dispatch regression tests for the menu-router-ordering fix.

Before the fix, "My Tasks" / "Settings" / "Habits" / "New Task" button
handlers lived on the reminders/settings/habits routers themselves. Since
those routers also register stateful FSM handlers with no text filter (e.g.
settings.SettingsState.waiting_for_brief_time, habits.HabitState.waiting_for_name),
whichever router happened to run first for a given update could swallow a
menu button tap meant for a later router. These tests drive the REAL
Dispatcher with the REAL router chain from bot.handlers.all_routers to prove
a menu button tap reaches its handler regardless of what FSM state another
router's flow left behind.
"""

import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser

# bot.handlers (via bot.handlers.admin -> bot.config) requires BOT_TOKEN to be
# set at import time; the value itself is never used by this test.
os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN-NOT-REAL-ABCDEFGHIJKLMNOPQRS")

import bot.handlers as handlers_pkg
import bot.handlers.reminders_wizard as reminders_wizard_module
from bot.handlers.habits import HabitState
from bot.handlers.settings import SettingsState
from bot.lexicon import get_l10n

_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _make_bot() -> Bot:
    return Bot(token=_TOKEN)


def _make_update(text: str, user_id: int) -> Update:
    tg_user = TgUser(id=user_id, is_bot=False, first_name="Test")
    chat = Chat(id=user_id, type="private")
    msg = Message.model_construct(message_id=1, date=datetime.now(), chat=chat, from_user=tg_user, text=text)
    return Update(update_id=1, message=msg)


# aiogram Router objects can only ever be attached to one parent Dispatcher —
# bot.handlers.all_routers are module-level singletons, so the Dispatcher
# wiring them together must be built exactly once and shared across tests in
# this file (each test uses its own user_id/StorageKey to avoid FSM bleed).
_shared_storage = MemoryStorage()
_shared_dispatcher = Dispatcher(storage=_shared_storage)
for _router in handlers_pkg.all_routers:
    _shared_dispatcher.include_router(_router)


async def test_my_tasks_button_not_swallowed_by_settings_brief_time_state() -> None:
    user_id = 555
    telegram_bot = _make_bot()
    try:
        key = StorageKey(bot_id=telegram_bot.id, chat_id=user_id, user_id=user_id)
        await _shared_storage.set_state(key, SettingsState.waiting_for_brief_time)
        await _shared_storage.set_data(key, {"brief_target": "morning"})

        reminder_dao = SimpleNamespace(get_user_reminders=AsyncMock(return_value=[]))
        user = SimpleNamespace(id=user_id, timezone="UTC", show_utc_offset=False, language="ru")
        l10n = get_l10n("ru")

        with patch.object(Bot, "__call__", AsyncMock(return_value=SimpleNamespace(message_id=99))) as mock_call:
            await _shared_dispatcher.feed_update(
                telegram_bot, _make_update("📅 Мои задачи", user_id), reminder_dao=reminder_dao, user=user, l10n=l10n
            )

        sent = mock_call.await_args.args[0]
        assert type(sent).__name__ == "SendMessage"
        # The "no tasks" branch of menu.btn_my_tasks fired — proof dispatch
        # reached the menu router, not settings' brief-time text validator
        # (which would have replied with a "invalid format" error instead).
        assert sent.text == l10n["no_tasks"]
        # btn_my_tasks calls state.clear() — the stale settings wizard state
        # must not survive the menu button tap.
        assert await _shared_storage.get_state(key) is None
        assert await _shared_storage.get_data(key) == {}
    finally:
        await telegram_bot.session.close()


async def test_settings_button_not_swallowed_by_habits_waiting_for_name_state() -> None:
    user_id = 556
    telegram_bot = _make_bot()
    try:
        key = StorageKey(bot_id=telegram_bot.id, chat_id=user_id, user_id=user_id)
        await _shared_storage.set_state(key, HabitState.waiting_for_name)

        user = SimpleNamespace(
            id=user_id,
            timezone="UTC",
            show_utc_offset=False,
            language="ru",
            quiet_hours_enabled=False,
            quiet_hours_start="23:00",
            quiet_hours_end="07:00",
        )
        l10n = get_l10n("ru")

        with patch.object(Bot, "__call__", AsyncMock(return_value=SimpleNamespace(message_id=99))) as mock_call:
            await _shared_dispatcher.feed_update(
                telegram_bot, _make_update("⚙️ Настройки", user_id), user=user, l10n=l10n
            )

        sent = mock_call.await_args.args[0]
        # menu.btn_settings renders the settings screen — if the habits
        # waiting_for_name handler had swallowed this instead, a habit named
        # "⚙️ Настройки" would have been created and no settings text sent.
        assert l10n["settings_text"].split("{")[0] in sent.text or "Настройки" in sent.text
        assert await _shared_storage.get_state(key) is None
    finally:
        await telegram_bot.session.close()


# ---------------------------------------------------------------------------
# 2.5: unknown-command / /find routing, against this same real router tree
# ---------------------------------------------------------------------------
# Two related bugs, both in ROUTER ORDERING (not catchable by a
# function-level test of handle_task_text alone):
#
# 1. Any unregistered slash command ("/settings", "/stats", a typo) used to
#    be silently parsed as a task — reminders_wizard.py's handle_task_text
#    is a StateFilter(None), F.text catch-all with no exclusion for
#    command-shaped text.
#
# 2. More severe: a REGISTERED command not living on one of the six
#    top-level routers checked before reminders_router (admin/commands/
#    donate/menu/settings/habits) — concretely, reminders_listing.py's own
#    Command("find") — never reached its handler at all.
#    reminders_wizard's catch-all sits on the FIRST reminders_* sub-router
#    in inclusion order, so it claimed "/find milk" before cmd_find (three
#    sub-routers later, within the same reminders_router) ever got a
#    chance. Verified empirically against this exact router tree before
#    the fix.
#
# Fix: handle_task_text's idle-state registration excludes anything
# starting with "/" (~F.text.startswith("/")), letting a command fall
# through the whole router tree to wherever it's actually registered.
# reminders_snooze.py's handle_unknown_command is the true last resort.

async def test_unregistered_slash_command_gets_the_unknown_command_reply() -> None:
    user_id = 557
    telegram_bot = _make_bot()
    try:
        reminder_dao = SimpleNamespace(search_user_reminders=AsyncMock(return_value=[]))
        user = SimpleNamespace(id=user_id, timezone="UTC", show_utc_offset=False, language="ru")
        l10n = get_l10n("ru")

        with patch.object(Bot, "__call__", AsyncMock(return_value=object())) as mock_call:
            await _shared_dispatcher.feed_update(
                telegram_bot, _make_update("/nonexistent", user_id), reminder_dao=reminder_dao, user=user, l10n=l10n
            )

        sent = mock_call.await_args.args[0]
        assert sent.text == l10n["unknown_command"]
    finally:
        await telegram_bot.session.close()


async def test_find_command_reaches_cmd_find_not_the_task_wizard() -> None:
    user_id = 558
    telegram_bot = _make_bot()
    try:
        reminder_dao = SimpleNamespace(search_user_reminders=AsyncMock(return_value=[]))
        user = SimpleNamespace(id=user_id, timezone="UTC", show_utc_offset=False, language="ru")
        l10n = get_l10n("ru")

        with patch.object(Bot, "__call__", AsyncMock(return_value=object())) as mock_call:
            await _shared_dispatcher.feed_update(
                telegram_bot, _make_update("/find milk", user_id), reminder_dao=reminder_dao, user=user, l10n=l10n
            )

        reminder_dao.search_user_reminders.assert_awaited_once_with(user_id, "milk")
        sent = mock_call.await_args.args[0]
        # cmd_find's own "nothing found" reply, not the unknown-command or
        # task-creation-prompt text.
        assert sent.text == l10n["find_no_results"].format(query="milk")
    finally:
        await telegram_bot.session.close()


async def test_ordinary_text_is_unaffected_and_still_starts_the_task_wizard() -> None:
    """This test's purpose is proving ROUTING (ordinary text still reaches
    handle_task_text), not NLP parsing quality — the real InputParser
    singleton reminders_wizard_module.parser is stubbed with a fixed
    future datetime, saved/restored around the test, rather than relying
    on the real parser's exact behavior for "in 15 minutes" (which some
    other test file — test_manual_time_and_reparse_flow.py — is also
    known to leave monkeypatched without restoring, being a module-level
    singleton shared for the whole test session)."""
    user_id = 559
    telegram_bot = _make_bot()
    original_parse = reminders_wizard_module.parser.parse
    try:
        future_dt = datetime.now() + timedelta(hours=1)
        reminders_wizard_module.parser.parse = AsyncMock(
            return_value=SimpleNamespace(clean_text="buy milk", parsed_datetime=future_dt, confidence=1.0)
        )

        created = SimpleNamespace(
            id=1, reminder_text="buy milk", is_recurring=False, is_nagging=False,
            nagging_max_repeats=3, rrule_string=None,
        )
        reminder_dao = SimpleNamespace(
            create_reminder=AsyncMock(return_value=created),
            session=SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
        )
        user = SimpleNamespace(id=user_id, timezone="UTC", show_utc_offset=False, language="ru")
        l10n = get_l10n("ru")
        scheduler_service = SimpleNamespace(schedule_reminder=lambda *a, **k: None)

        with patch.object(Bot, "__call__", AsyncMock(return_value=object())) as mock_call:
            await _shared_dispatcher.feed_update(
                telegram_bot,
                _make_update("buy milk in 15 minutes", user_id),
                reminder_dao=reminder_dao,
                user=user,
                l10n=l10n,
                scheduler_service=scheduler_service,
            )

        reminder_dao.create_reminder.assert_awaited_once()
        sent = mock_call.await_args.args[0]
        assert sent.text != l10n["unknown_command"]
    finally:
        reminders_wizard_module.parser.parse = original_parse
        await telegram_bot.session.close()
