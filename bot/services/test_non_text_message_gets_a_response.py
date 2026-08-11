"""Regression (REWORK_PLAN_3 2.7): nothing responded to a photo/voice/video
sent outside any FSM flow — the bot silently swallowed the update, which for
a "just type a phrase" bot reads as broken. A forwarded photo/video with no
caption was in the exact same boat (handle_forwarded_task returned early
with no answer).
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.lexicon import get_l10n

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_bare_photo_outside_any_flow_gets_a_hint_not_silence() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")

    message = SimpleNamespace(answer=AsyncMock())
    l10n = get_l10n("en")

    await reminders_module.handle_non_text_message(message, l10n)

    message.answer.assert_awaited_once_with(l10n["text_only_hint"])


async def test_forwarded_photo_with_no_caption_gets_a_hint_not_silence() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")

    forward_origin = SimpleNamespace(type="user", sender_user=SimpleNamespace(full_name="Juan"))
    message = SimpleNamespace(
        text=None,
        caption=None,
        forward_origin=forward_origin,
        chat=SimpleNamespace(id=1),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())
    user = SimpleNamespace(language="en", timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")

    await reminders_module.handle_forwarded_task(
        message, state, user, l10n, reminder_dao=None, scheduler_service=None
    )

    message.answer.assert_awaited_once_with(l10n["text_only_hint"])
    # Must not have proceeded into the wizard with an empty task description.
    state.clear.assert_not_awaited()


async def test_forwarded_message_with_caption_is_unaffected() -> None:
    """Baseline: the 2.7 fix must not regress the normal forwarded-text path."""
    reminders_module = _load_module("bot/handlers/reminders.py")

    reminders_module.parser.parse = AsyncMock(
        return_value=SimpleNamespace(clean_text="call mom", parsed_datetime=None, confidence=1.0)
    )

    forward_origin = SimpleNamespace(type="user", sender_user=SimpleNamespace(full_name="Juan"))
    message = SimpleNamespace(
        text=None,
        caption="call mom",
        forward_origin=forward_origin,
        chat=SimpleNamespace(id=1),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock())
    user = SimpleNamespace(language="en", timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("en")

    await reminders_module.handle_forwarded_task(
        message, state, user, l10n, reminder_dao=None, scheduler_service=None
    )

    reminders_module.parser.parse.assert_awaited_once()
    state.clear.assert_awaited_once()
