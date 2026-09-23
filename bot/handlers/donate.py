"""5.1: voluntary Telegram Stars tip jar.

Deliberately NOT a paywall — the product owner's framing: "Telegram Stars
is meant for donations and voice input (if ever be implemented)." Nothing
in the product (reminders, habits, export, the 4.4 ICS feed, the 4.6 Mini
App) is gated behind payment; this is a "buy me a coffee" flow only,
reachable via /donate or the "☕ Support Porabot" Settings button.

Telegram Stars (currency="XTR") need no external payment provider —
provider_token is the empty string, unlike a real-money invoice.

A-19: Telegram requires a bot offering Stars payments to implement
/paysupport, and refundStarPayment needs the telegram_payment_charge_id —
a value this codebase didn't keep anywhere before the Payment model/
PaymentDAO this module now writes to on every completed donation.
"""

import logging
from typing import Any, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, LabeledPrice, Message, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.dao.payment import PaymentDAO
from bot.keyboards.inline import DONATION_PRESETS, get_donate_keyboard

router = Router(name="donate")
logger = logging.getLogger(__name__)

_DONATE_PAYLOAD_PREFIX = "donate:"


def _parse_donate_amount(payload: str) -> Optional[int]:
    """Extract the amount from a "donate:<amount>" invoice payload,
    or None if it isn't shaped that way at all."""
    if not payload.startswith(_DONATE_PAYLOAD_PREFIX):
        return None
    try:
        return int(payload[len(_DONATE_PAYLOAD_PREFIX):])
    except ValueError:
        return None


@router.message(Command("donate"))
async def cmd_donate(message: Message, l10n: dict[str, Any]) -> None:
    await message.answer(
        l10n.get(
            "donate_prompt",
            "☕ Enjoying Porabot? You can support development with Telegram Stars — pick an amount:",
        ),
        reply_markup=get_donate_keyboard(l10n),
    )


@router.callback_query(F.data == "donate_open")
async def callback_donate_open(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    await callback.message.edit_text(
        l10n.get(
            "donate_prompt",
            "☕ Enjoying Porabot? You can support development with Telegram Stars — pick an amount:",
        ),
        reply_markup=get_donate_keyboard(l10n),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("donate_amount_"))
async def callback_donate_amount(callback: CallbackQuery, l10n: dict[str, Any]) -> None:
    try:
        amount = int(callback.data.split("donate_amount_")[1])
    except (IndexError, ValueError):
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return
    if amount not in DONATION_PRESETS:
        await callback.answer(l10n["invalid_action"], show_alert=True)
        return

    title = l10n.get("donate_invoice_title", "Support Porabot")
    await callback.bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=title,
        description=l10n.get(
            "donate_invoice_description",
            "A voluntary tip — thank you for supporting development!",
        ),
        payload=f"{_DONATE_PAYLOAD_PREFIX}{amount}",
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=amount)],
        provider_token="",  # Stars need no external payment provider.
    )
    await callback.answer()


@router.pre_checkout_query(F.invoice_payload.startswith(_DONATE_PAYLOAD_PREFIX))
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery, l10n: dict[str, Any]) -> None:
    # A-19: a fixed-price digital tip has no stock/shipping/external order
    # to reserve — but the payload, currency, and amount are still worth
    # actually checking against each other and against the preset list
    # this bot itself generated the invoice from. Telegram's pre-checkout
    # step passes through client-influenced fields; approving blind would
    # confirm a payload/currency/amount combination this bot never
    # actually offered.
    amount = _parse_donate_amount(pre_checkout_query.invoice_payload)
    valid = (
        pre_checkout_query.currency == "XTR"
        and amount is not None
        and amount in DONATION_PRESETS
        and pre_checkout_query.total_amount == amount
    )
    if not valid:
        logger.warning(
            "Rejecting mismatched donate pre-checkout: payload=%r amount=%s currency=%s",
            pre_checkout_query.invoice_payload,
            pre_checkout_query.total_amount,
            pre_checkout_query.currency,
        )
        await pre_checkout_query.answer(
            ok=False,
            error_message=l10n.get("donate_unknown_payment_error", "Unrecognized payment. Please try again."),
        )
        return
    await pre_checkout_query.answer(ok=True)


@router.pre_checkout_query()
async def process_pre_checkout_unknown(pre_checkout_query: PreCheckoutQuery, l10n: dict[str, Any]) -> None:
    # fix(2.4): the donation payload is the ONLY payment flow this bot has
    # today, so a filterless handler answering ok=True was harmless in
    # practice — but it would silently auto-confirm any future payment
    # flow too, without validating anything about it. Reject explicitly
    # instead of letting an unmatched pre-checkout hang unanswered.
    logger.warning(
        "Rejecting pre-checkout with unrecognized payload: %r", pre_checkout_query.invoice_payload
    )
    await pre_checkout_query.answer(
        ok=False,
        error_message=l10n.get("donate_unknown_payment_error", "Unrecognized payment. Please try again."),
    )


@router.message(F.successful_payment)
async def process_successful_payment(
    message: Message, l10n: dict[str, Any], payment_dao: PaymentDAO
) -> None:
    payment = message.successful_payment
    logger.info(
        "Received Stars donation: user=%s amount=%d currency=%s",
        message.from_user.id,
        payment.total_amount,
        payment.currency,
    )
    # A-19: record it — telegram_payment_charge_id is the ONLY handle
    # refundStarPayment accepts, and nothing kept it anywhere before this.
    # unique=True on the column means a duplicate delivery of the same
    # successful_payment update (Telegram redelivers on a missed ack) just
    # fails this insert instead of double-recording — best-effort: the
    # "thank you" reply below always sends either way, since a payment
    # already actually happened regardless of whether this insert was new.
    try:
        await payment_dao.create(
            user_id=message.from_user.id,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            amount=payment.total_amount,
            currency=payment.currency,
            invoice_payload=payment.invoice_payload,
        )
    except Exception as e:
        logger.warning(
            "Could not record payment charge_id=%s for user=%s: %s",
            payment.telegram_payment_charge_id,
            message.from_user.id,
            e,
        )
    await message.answer(
        l10n.get("donate_thanks", "☕ Thank you so much for supporting Porabot! 💛").format(
            amount=payment.total_amount
        )
    )


# ---------------------------------------------------------------------------
# A-19: /paysupport — required by Telegram for any bot offering Stars
# payments. Lets a user see (and self-service refund) their own recent
# donations instead of having nowhere to go for one.
# ---------------------------------------------------------------------------

_PAYSUPPORT_RECENT_LIMIT = 5


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message, l10n: dict[str, Any], payment_dao: PaymentDAO) -> None:
    payments = await payment_dao.get_recent_for_user(message.from_user.id, limit=_PAYSUPPORT_RECENT_LIMIT)
    if not payments:
        await message.answer(l10n.get("paysupport_no_payments", "You have no recorded donations to refund."))
        return

    builder = InlineKeyboardBuilder()
    lines = [l10n.get("paysupport_intro", "Your recent donations — tap one to request a refund:")]
    for p in payments:
        lines.append(
            l10n.get("paysupport_item", "⭐ {amount} — {date}").format(
                amount=p.amount, date=p.created_at.strftime("%d.%m.%Y")
            )
        )
        builder.row(
            InlineKeyboardButton(
                text=l10n.get("btn_paysupport_refund", "↩ Refund {amount} ⭐").format(amount=p.amount),
                callback_data=f"paysupport_refund_{p.id}",
            )
        )
    await message.answer("\n".join(lines), reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("paysupport_refund_"))
async def callback_paysupport_refund(
    callback: CallbackQuery, l10n: dict[str, Any], payment_dao: PaymentDAO
) -> None:
    try:
        payment_id = int(callback.data.split("paysupport_refund_")[1])
    except (IndexError, ValueError):
        return await callback.answer(l10n["invalid_action"], show_alert=True)

    payment = await payment_dao.get_owned(payment_id, callback.from_user.id)
    if not payment:
        return await callback.answer(l10n["item_not_found"], show_alert=True)

    try:
        await callback.bot.refund_star_payment(
            user_id=payment.user_id,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
        )
    except Exception as e:
        logger.error(
            "Refund failed for payment %s (charge_id=%s): %s", payment.id, payment.telegram_payment_charge_id, e
        )
        await callback.answer(
            l10n.get("paysupport_refund_failed", "❌ Refund failed. It may already be refunded."),
            show_alert=True,
        )
        return

    await payment_dao.delete_by_id(payment.id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer(l10n.get("paysupport_refund_success", "✅ Refunded."), show_alert=True)
