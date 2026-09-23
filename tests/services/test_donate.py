"""5.1: Telegram Stars tip jar — bot/handlers/donate.py.

Deliberately NOT a paywall: these tests assert the flow sends an invoice
and thanks the user, never that it gates any other feature."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.donate import (
    DONATION_PRESETS,
    callback_donate_amount,
    callback_donate_open,
    callback_paysupport_refund,
    cmd_donate,
    cmd_paysupport,
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


async def test_pre_checkout_approved_when_payload_matches_amount_and_currency() -> None:
    pre_checkout_query = SimpleNamespace(
        invoice_payload=f"donate:{DONATION_PRESETS[0]}",
        total_amount=DONATION_PRESETS[0],
        currency="XTR",
        answer=AsyncMock(),
    )
    await process_pre_checkout(pre_checkout_query, RU)
    pre_checkout_query.answer.assert_awaited_once_with(ok=True)


async def test_pre_checkout_rejects_amount_mismatch() -> None:
    """docs/audits/2026-09-22-audit.md#a-19 — total_amount must match the
    payload's own amount; approving blind would confirm a payment whose
    reported amount doesn't match what this bot's own invoice said."""
    pre_checkout_query = SimpleNamespace(
        invoice_payload=f"donate:{DONATION_PRESETS[0]}",
        total_amount=999999,  # doesn't match the payload
        currency="XTR",
        answer=AsyncMock(),
    )
    await process_pre_checkout(pre_checkout_query, RU)
    pre_checkout_query.answer.assert_awaited_once_with(
        ok=False, error_message=RU["donate_unknown_payment_error"]
    )


async def test_pre_checkout_rejects_wrong_currency() -> None:
    pre_checkout_query = SimpleNamespace(
        invoice_payload=f"donate:{DONATION_PRESETS[0]}",
        total_amount=DONATION_PRESETS[0],
        currency="USD",  # Stars donations are XTR only
        answer=AsyncMock(),
    )
    await process_pre_checkout(pre_checkout_query, RU)
    pre_checkout_query.answer.assert_awaited_once_with(
        ok=False, error_message=RU["donate_unknown_payment_error"]
    )


async def test_pre_checkout_rejects_amount_outside_presets() -> None:
    pre_checkout_query = SimpleNamespace(
        invoice_payload="donate:1",  # not one of DONATION_PRESETS
        total_amount=1,
        currency="XTR",
        answer=AsyncMock(),
    )
    await process_pre_checkout(pre_checkout_query, RU)
    pre_checkout_query.answer.assert_awaited_once_with(
        ok=False, error_message=RU["donate_unknown_payment_error"]
    )


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


async def test_successful_payment_sends_thank_you_and_records_the_payment() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        successful_payment=SimpleNamespace(
            total_amount=50,
            currency="XTR",
            telegram_payment_charge_id="charge-123",
            invoice_payload="donate:50",
        ),
        answer=AsyncMock(),
    )
    payment_dao = SimpleNamespace(record_once=AsyncMock(return_value=True))

    await process_successful_payment(message, RU, payment_dao)

    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    assert "Porabot" in text
    payment_dao.record_once.assert_awaited_once_with(
        user_id=7,
        telegram_payment_charge_id="charge-123",
        amount=50,
        currency="XTR",
        invoice_payload="donate:50",
    )


async def test_successful_payment_still_thanks_user_if_recording_fails() -> None:
    """A DB hiccup (or a duplicate delivery hitting the unique constraint)
    recording the payment must never stop the user from being thanked —
    the Stars payment already genuinely happened regardless."""
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7),
        successful_payment=SimpleNamespace(
            total_amount=50,
            currency="XTR",
            telegram_payment_charge_id="charge-123",
            invoice_payload="donate:50",
        ),
        answer=AsyncMock(),
    )
    payment_dao = SimpleNamespace(record_once=AsyncMock(side_effect=RuntimeError("db locked")))

    await process_successful_payment(message, RU, payment_dao)

    message.answer.assert_awaited_once()


# ---------------------------------------------------------------------------
# A-19: /paysupport
# ---------------------------------------------------------------------------

async def test_paysupport_with_no_payments() -> None:
    message = SimpleNamespace(from_user=SimpleNamespace(id=7), answer=AsyncMock())
    payment_dao = SimpleNamespace(get_recent_for_user=AsyncMock(return_value=[]))

    await cmd_paysupport(message, RU, payment_dao)

    message.answer.assert_awaited_once_with(RU["paysupport_no_payments"])


async def test_paysupport_lists_recent_payments_with_refund_buttons() -> None:
    message = SimpleNamespace(from_user=SimpleNamespace(id=7), answer=AsyncMock())
    payments = [
        SimpleNamespace(id=1, amount=50, created_at=datetime(2026, 9, 1)),
        SimpleNamespace(id=2, amount=25, created_at=datetime(2026, 8, 15)),
    ]
    payment_dao = SimpleNamespace(get_recent_for_user=AsyncMock(return_value=payments))

    await cmd_paysupport(message, RU, payment_dao)

    message.answer.assert_awaited_once()
    markup = message.answer.await_args.kwargs["reply_markup"]
    callback_datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "paysupport_refund_1" in callback_datas
    assert "paysupport_refund_2" in callback_datas


async def test_refund_callback_calls_telegram_and_deletes_the_record() -> None:
    payment = SimpleNamespace(id=1, user_id=7, telegram_payment_charge_id="charge-123", amount=50)
    payment_dao = SimpleNamespace(
        get_owned=AsyncMock(return_value=payment),
        delete_by_id=AsyncMock(),
    )
    bot = SimpleNamespace(refund_star_payment=AsyncMock())
    callback = SimpleNamespace(
        data="paysupport_refund_1",
        from_user=SimpleNamespace(id=7),
        bot=bot,
        message=SimpleNamespace(edit_reply_markup=AsyncMock()),
        answer=AsyncMock(),
    )

    await callback_paysupport_refund(callback, RU, payment_dao)

    bot.refund_star_payment.assert_awaited_once_with(user_id=7, telegram_payment_charge_id="charge-123")
    payment_dao.delete_by_id.assert_awaited_once_with(1)
    callback.answer.assert_awaited_once_with(RU["paysupport_refund_success"], show_alert=True)


async def test_refund_callback_rejects_a_payment_owned_by_someone_else() -> None:
    """IDOR guard — same shape as ReminderDAO.get_owned."""
    payment_dao = SimpleNamespace(
        get_owned=AsyncMock(return_value=None),  # not owned by this user
        delete_by_id=AsyncMock(),
    )
    bot = SimpleNamespace(refund_star_payment=AsyncMock())
    callback = SimpleNamespace(
        data="paysupport_refund_1",
        from_user=SimpleNamespace(id=999),
        bot=bot,
        message=SimpleNamespace(edit_reply_markup=AsyncMock()),
        answer=AsyncMock(),
    )

    await callback_paysupport_refund(callback, RU, payment_dao)

    bot.refund_star_payment.assert_not_awaited()
    callback.answer.assert_awaited_once_with(RU["item_not_found"], show_alert=True)


async def test_refund_callback_handles_telegram_failure_gracefully() -> None:
    payment = SimpleNamespace(id=1, user_id=7, telegram_payment_charge_id="charge-123", amount=50)
    payment_dao = SimpleNamespace(
        get_owned=AsyncMock(return_value=payment),
        delete_by_id=AsyncMock(),
    )
    bot = SimpleNamespace(refund_star_payment=AsyncMock(side_effect=RuntimeError("already refunded")))
    callback = SimpleNamespace(
        data="paysupport_refund_1",
        from_user=SimpleNamespace(id=7),
        bot=bot,
        message=SimpleNamespace(edit_reply_markup=AsyncMock()),
        answer=AsyncMock(),
    )

    await callback_paysupport_refund(callback, RU, payment_dao)

    payment_dao.delete_by_id.assert_not_awaited()
    callback.answer.assert_awaited_once_with(RU["paysupport_refund_failed"], show_alert=True)
