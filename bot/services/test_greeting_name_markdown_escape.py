"""P2-4: Telegram first_name is user-controlled and sent under the default
parse_mode=Markdown greeting — an unescaped "_"/"*"/"`"/"[" in it must not
break formatting or trip a TelegramBadRequest."""

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN-NOT-REAL-ABCDEFGHIJKLMNOPQRS")

from bot.lexicon import get_l10n

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_cmd_start_escapes_markdown_special_chars_in_first_name() -> None:
    commands_module = _load_module("bot/handlers/commands.py")

    l10n = get_l10n("en")
    user = SimpleNamespace(language="en")
    message = SimpleNamespace(
        from_user=SimpleNamespace(first_name="_evil_*name*"),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())

    await commands_module.cmd_start(message, state, user, l10n)

    sent_text = message.answer.await_args.args[0]
    assert "\\_evil\\_\\*name\\*" in sent_text
    # The raw, unescaped name must not appear — that's the injection surface.
    assert "_evil_*name*" not in sent_text
