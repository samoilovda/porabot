"""2.1: tapping "Refresh" on an unchanged list, or re-tapping a filter/back
button that lands on the same screen twice, makes Telegram reject
edit_text with `TelegramBadRequest: Bad Request: message is not modified`.
Before this fix that propagated to bot/__main__.py's
handle_dispatcher_error, which shows the user a generic "❌ Something went
wrong" alert for something that isn't actually wrong.

Covers both layers: the local safe_edit_text/safe_edit_reply_markup
helper (bot/utils/telegram.py) used at the highest-traffic repeat-tap call
sites, and the global backstop in handle_dispatcher_error for every other
edit_text call site.
"""

from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText

from bot.utils.telegram import safe_edit_reply_markup, safe_edit_text


def _not_modified_error() -> TelegramBadRequest:
    return TelegramBadRequest(
        method=EditMessageText(text="x"), message="Bad Request: message is not modified"
    )


def _other_bad_request() -> TelegramBadRequest:
    return TelegramBadRequest(
        method=EditMessageText(text="x"), message="Bad Request: message to edit not found"
    )


async def test_safe_edit_text_swallows_not_modified() -> None:
    message = AsyncMock()
    message.edit_text.side_effect = _not_modified_error()

    result = await safe_edit_text(message, "same text")

    assert result is False
    message.edit_text.assert_awaited_once()


async def test_safe_edit_text_reraises_other_bad_request() -> None:
    message = AsyncMock()
    message.edit_text.side_effect = _other_bad_request()

    try:
        await safe_edit_text(message, "new text")
    except TelegramBadRequest:
        pass
    else:
        raise AssertionError("expected TelegramBadRequest to propagate")


async def test_safe_edit_text_returns_true_on_success() -> None:
    message = AsyncMock()

    result = await safe_edit_text(message, "new text")

    assert result is True


async def test_safe_edit_reply_markup_swallows_not_modified() -> None:
    message = AsyncMock()
    message.edit_reply_markup.side_effect = _not_modified_error()

    result = await safe_edit_reply_markup(message, reply_markup=None)

    assert result is False


async def test_dispatcher_error_handler_treats_not_modified_as_a_quiet_no_op() -> None:
    """The global backstop for the ~60 edit_text call sites not individually
    converted to safe_edit_text — must not show the generic error alert."""
    from types import SimpleNamespace

    from bot.__main__ import handle_dispatcher_error

    callback_query = AsyncMock()
    update = SimpleNamespace(update_id=1, callback_query=callback_query, message=None)
    event = SimpleNamespace(update=update, exception=_not_modified_error())

    await handle_dispatcher_error(event)

    callback_query.answer.assert_awaited_once_with()  # no text, no show_alert=True


async def test_dispatcher_error_handler_still_alerts_on_a_real_error() -> None:
    from types import SimpleNamespace

    from bot.__main__ import handle_dispatcher_error

    callback_query = AsyncMock()
    update = SimpleNamespace(update_id=1, callback_query=callback_query, message=None)
    event = SimpleNamespace(update=update, exception=RuntimeError("boom"))

    await handle_dispatcher_error(event)

    args, kwargs = callback_query.answer.call_args
    assert kwargs.get("show_alert") is True
