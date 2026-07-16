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


async def test_forwarded_prefix_uses_users_own_language_not_hardcoded_russian() -> None:
    reminders_module = _load_module("bot/handlers/reminders.py")

    reminders_module.parser.parse = AsyncMock(
        return_value=SimpleNamespace(clean_text="llamar a mama", parsed_datetime=None, confidence=1.0)
    )

    forward_origin = SimpleNamespace(type="user", sender_user=SimpleNamespace(full_name="Juan"))
    message = SimpleNamespace(
        text="llamar a mama",
        caption=None,
        forward_origin=forward_origin,
        chat=SimpleNamespace(id=1),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock())
    user = SimpleNamespace(language="es", timezone="UTC", show_utc_offset=False)
    l10n = get_l10n("es")

    await reminders_module.handle_forwarded_task(
        message, state, user, l10n, reminder_dao=None, scheduler_service=None
    )

    reminders_module.parser.parse.assert_awaited_once()
    parsed_text = reminders_module.parser.parse.await_args.args[0]
    assert parsed_text.startswith("👤 Reenviado de Juan:\n")
    assert "Переслано" not in parsed_text
