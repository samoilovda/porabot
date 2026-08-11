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
    process_successful_payment,
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
