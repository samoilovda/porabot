from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock
import importlib.util
from pathlib import Path

from bot.lexicon import get_l10n

ROOT = Path(__file__).resolve().parents[2]


def _load_handler(module_rel_path: str, fn_name: str):
    module_path = ROOT / module_rel_path
    spec = importlib.util.spec_from_file_location(f"test_{fn_name}", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, fn_name)


callback_set_lang = _load_handler("bot/handlers/commands.py", "callback_set_lang")
callback_set_tz = _load_handler("bot/handlers/settings.py", "callback_set_tz")


async def test_set_lang_onboarding_prompts_timezone_selection() -> None:
    user = SimpleNamespace(id=101, language=None)
    user_dao = SimpleNamespace(update_language=AsyncMock())
    state = SimpleNamespace(update_data=AsyncMock())
    message = SimpleNamespace(delete=AsyncMock(), answer=AsyncMock())
    callback = SimpleNamespace(
        data="set_lang_en",
        message=message,
        from_user=SimpleNamespace(first_name="Alice"),
        answer=AsyncMock(),
    )

    await callback_set_lang(callback=callback, user_dao=user_dao, user=user, state=state)

    l10n = get_l10n("en")
    user_dao.update_language.assert_awaited_once_with(101, "en")
    assert user.language == "en"
    message.delete.assert_awaited_once()
    state.update_data.assert_awaited_once_with(onboarding_timezone=True)
    callback.answer.assert_awaited_once()

    assert message.answer.await_count == 2
    assert message.answer.await_args_list[0].args[0] == l10n["lang_set"]
    assert message.answer.await_args_list[1].args[0] == l10n["choose_tz"]
    assert "reply_markup" in message.answer.await_args_list[1].kwargs


async def test_set_timezone_onboarding_finishes_with_main_menu() -> None:
    user = SimpleNamespace(id=202, timezone="UTC")
    user_dao = SimpleNamespace(update_timezone=AsyncMock())
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"onboarding_timezone": True}),
        clear=AsyncMock(),
    )
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
    callback = SimpleNamespace(
        data="set_tz_Europe/Moscow",
        message=message,
        from_user=SimpleNamespace(first_name="Bob"),
        answer=AsyncMock(),
    )
    l10n = get_l10n("en")

    await callback_set_tz(callback=callback, user_dao=user_dao, user=user, l10n=l10n, state=state)

    user_dao.update_timezone.assert_awaited_once_with(202, "Europe/Moscow")
    assert user.timezone == "Europe/Moscow"
    message.edit_text.assert_awaited_once_with(
        l10n["tz_success"].format(tz="Europe/Moscow"),
        reply_markup=None,
    )
    state.clear.assert_awaited_once()
    message.answer.assert_awaited_once_with(
        l10n["cmd_start"].format(name="Bob"),
        reply_markup=ANY,
    )
    callback.answer.assert_awaited_once()
