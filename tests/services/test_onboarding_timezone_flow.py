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
resolve_timezone_candidate = _load_handler("bot/handlers/settings.py", "_resolve_timezone_candidate")


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
    reminder_dao = SimpleNamespace(get_active_habits=AsyncMock(return_value=[]))

    await callback_set_tz(
        callback=callback, user_dao=user_dao, user=user, l10n=l10n, state=state, reminder_dao=reminder_dao
    )

    user_dao.update_timezone.assert_awaited_once_with(202, "Europe/Moscow")
    assert user.timezone == "Europe/Moscow"
    assert message.edit_text.await_count == 1
    assert message.edit_text.await_args.kwargs == {"reply_markup": None}
    edit_text = message.edit_text.await_args.args[0]
    assert edit_text.startswith(l10n["tz_success"].split("{tz}")[0])
    assert "Europe/Moscow" in edit_text
    assert "UTC+" in edit_text
    state.clear.assert_awaited_once()
    message.answer.assert_awaited_once_with(
        l10n["cmd_start"].format(name="Bob"),
        reply_markup=ANY,
    )
    callback.answer.assert_awaited_once()


def test_manual_timezone_offset_resolution() -> None:
    assert resolve_timezone_candidate("+5") == "Etc/GMT-5"
    assert resolve_timezone_candidate("-6") == "Etc/GMT+6"
    assert resolve_timezone_candidate("0") == "UTC"


def test_manual_timezone_offset_resolution_half_hour() -> None:
    # W8: known half/quarter-hour offsets resolve to a real (DST-aware) zone,
    # not a fixed Etc/GMT offset — several accepted spellings for the same input.
    assert resolve_timezone_candidate("+5:30") == "Asia/Kolkata"
    assert resolve_timezone_candidate("+5.5") == "Asia/Kolkata"
    assert resolve_timezone_candidate("5:30") == "Asia/Kolkata"
    assert resolve_timezone_candidate("-3:30") == "America/St_Johns"


def test_manual_timezone_offset_resolution_unknown_half_hour_rejected() -> None:
    import pytz
    import pytest

    with pytest.raises(pytz.UnknownTimeZoneError):
        resolve_timezone_candidate("+1:15")
