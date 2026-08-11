"""5.1: Telegram Stars tip jar — bot/handlers/donate.py.

Deliberately NOT a paywall: these tests assert the flow sends an invoice
and thanks the user, never that it gates any other feature."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.donate import (
    DONATION_PRESETS,
    callback_donate_amount,
    callback_donate_open,
    cmd_donate,
    process_pre_checkout,
    process_pre_checkout_unknown,
    process_successful_payment,
    router,
)
from bot.lexicon.ru import RU


async def test_cmd_donate_shows_preset_amounts() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    await cmd_donate(message, RU)
    message.answer.assert_awaited_once()
    markup = message.answer.await_args.kwargs["reply_markup"]
    callback_datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    for amount in DONATION_PRESETS:
        assert f"donate_amount_{amount}" in callback_datas


async def test_callback_donate_open_edits_message() -> None:
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())
    await callback_donate_open(callback, RU)
    message.edit_text.assert_awaited_once()
    callback.answer.assert_awaited_once()


async def test_callback_donate_amount_sends_stars_invoice() -> None:
    bot = SimpleNamespace(send_invoice=AsyncMock())
    callback = SimpleNamespace(
        data=f"donate_amount_{DONATION_PRESETS[0]}",
        bot=bot,
        message=SimpleNamespace(chat=SimpleNamespace(id=42)),
        answer=AsyncMock(),
    )
    await callback_donate_amount(callback, RU)

    bot.send_invoice.assert_awaited_once()
    kwargs = bot.send_invoice.await_args.kwargs
    assert kwargs["chat_id"] == 42
    assert kwargs["currency"] == "XTR"
    assert kwargs["provider_token"] == ""
    assert kwargs["prices"][0].amount == DONATION_PRESETS[0]
    callback.answer.assert_awaited_once()


async def test_callback_donate_amount_rejects_unknown_amount() -> None:
    bot = SimpleNamespace(send_invoice=AsyncMock())
    callback = SimpleNamespace(
        data="donate_amount_999999",
        bot=bot,
        message=SimpleNamespace(chat=SimpleNamespace(id=42)),
        answer=AsyncMock(),
    )
    await callback_donate_amount(callback, RU)

    bot.send_invoice.assert_not_awaited()
    callback.answer.assert_awaited_once_with(RU["invalid_action"], show_alert=True)


async def test_callback_donate_amount_rejects_malformed_data() -> None:
    bot = SimpleNamespace(send_invoice=AsyncMock())
    callback = SimpleNamespace(
        data="donate_amount_notanumber",
        bot=bot,
        message=SimpleNamespace(chat=SimpleNamespace(id=42)),
        answer=AsyncMock(),
    )
    await callback_donate_amount(callback, RU)
    bot.send_invoice.assert_not_awaited()
    callback.answer.assert_awaited_once_with(RU["invalid_action"], show_alert=True)


async def test_pre_checkout_always_approved_for_fixed_price_tip() -> None:
    pre_checkout_query = SimpleNamespace(answer=AsyncMock())
    await process_pre_checkout(pre_checkout_query)
    pre_checkout_query.answer.assert_awaited_once_with(ok=True)


def _handler_filters(func_name: str) -> list:
    handler = next(h for h in router.pre_checkout_query.handlers if h.callback.__name__ == func_name)
    return list(handler.filters or [])


def test_pre_checkout_handler_is_scoped_to_donate_payload() -> None:
    """Regression (fix 2.4): @router.pre_checkout_query() had no filter at
    all — it intercepted ANY pre-checkout, for any future payment flow,
    and unconditionally approved it. process_pre_checkout must only match
    payloads this donation flow itself creates (donate:<amount>,
    bot/handlers/donate.py's callback_donate_amount)."""
    filters = _handler_filters("process_pre_checkout")
    assert len(filters) == 1

    magic_filter = filters[0].magic
    assert magic_filter.resolve(SimpleNamespace(invoice_payload="donate:50")) is True
    assert magic_filter.resolve(SimpleNamespace(invoice_payload="something_else")) is False


async def test_pre_checkout_unknown_payload_is_rejected() -> None:
    pre_checkout_query = SimpleNamespace(invoice_payload="something_else", answer=AsyncMock())
    await process_pre_checkout_unknown(pre_checkout_query, RU)
    pre_checkout_query.answer.assert_awaited_once_with(
        ok=False, error_message=RU["donate_unknown_payment_error"]
    )


async def test_successful_payment_sends_thank_you() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        successful_payment=SimpleNamespace(total_amount=50, currency="XTR"),
        answer=AsyncMock(),
    )
    await process_successful_payment(message, RU)
    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    assert "Porabot" in text
