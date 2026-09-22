"""docs/audits/2026-09-22-audit.md#a-23 — callback_data is client-
controlled: Telegram doesn't cryptographically bind it to the keyboard
actually shown, so a forged "set_lang_<garbage>" callback must not persist
an unsupported language code. Same defense-in-depth already applied to
set_tz_<zone> in bot/handlers/settings.py.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.commands import callback_set_lang
from bot.lexicon import get_l10n


async def test_forged_language_code_is_rejected_not_persisted() -> None:
    user = SimpleNamespace(id=101, language="en")
    user_dao = SimpleNamespace(update_language=AsyncMock())
    state = SimpleNamespace(update_data=AsyncMock())
    message = SimpleNamespace(delete=AsyncMock(), answer=AsyncMock())
    callback = SimpleNamespace(
        data="set_lang_' OR 1=1",
        message=message,
        from_user=SimpleNamespace(first_name="Alice"),
        answer=AsyncMock(),
    )

    await callback_set_lang(callback=callback, user_dao=user_dao, user=user, state=state, l10n=get_l10n("en"))

    user_dao.update_language.assert_not_awaited()
    assert user.language == "en"  # unchanged
    message.delete.assert_not_awaited()
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True


async def test_supported_language_code_still_works() -> None:
    user = SimpleNamespace(id=101, language=None)
    user_dao = SimpleNamespace(update_language=AsyncMock())
    state = SimpleNamespace(update_data=AsyncMock())
    message = SimpleNamespace(delete=AsyncMock(), answer=AsyncMock())
    callback = SimpleNamespace(
        data="set_lang_es",
        message=message,
        from_user=SimpleNamespace(first_name="Alice"),
        answer=AsyncMock(),
    )

    await callback_set_lang(callback=callback, user_dao=user_dao, user=user, state=state, l10n=get_l10n("es"))

    user_dao.update_language.assert_awaited_once_with(101, "es")
    assert user.language == "es"
