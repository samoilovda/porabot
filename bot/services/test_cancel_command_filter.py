import importlib.util
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from aiogram.types import Chat, Message

ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_rel_path: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location("test_module_" + module_rel_path, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_message(text: str) -> Message:
    chat = Chat(id=1, type="private")
    return Message.model_construct(message_id=1, date=datetime.now(), chat=chat, text=text)


def _cancel_filter():
    commands_module = _load_module("bot/handlers/commands.py")
    from aiogram.filters import Command

    handler = next(
        h for h in commands_module.router.message.handlers
        if h.callback.__name__ == "cmd_cancel"
    )
    command_filters = [f.callback for f in handler.filters if isinstance(f.callback, Command)]
    assert len(command_filters) == 1, "expected exactly one Command filter on cmd_cancel"
    return command_filters[0]


class _FakeBot:
    async def me(self):
        return SimpleNamespace(username="my_bot")


async def test_cancel_matches_plain_command() -> None:
    cancel_filter = _cancel_filter()
    result = await cancel_filter(_make_message("/cancel"), bot=_FakeBot())
    assert result is not False


async def test_cancel_matches_with_arguments() -> None:
    cancel_filter = _cancel_filter()
    result = await cancel_filter(_make_message("/cancel please"), bot=_FakeBot())
    assert result is not False


async def test_cancel_matches_with_bot_mention() -> None:
    cancel_filter = _cancel_filter()
    result = await cancel_filter(_make_message("/cancel@my_bot"), bot=_FakeBot())
    assert result is not False


async def test_cancel_does_not_match_other_text() -> None:
    cancel_filter = _cancel_filter()
    result = await cancel_filter(_make_message("hello"), bot=_FakeBot())
    assert result is False
