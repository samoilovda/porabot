"""5.1: voluntary Telegram Stars tip jar.

Deliberately NOT a paywall — the product owner's framing: "Telegram Stars
is meant for donations and voice input (if ever be implemented)." Nothing
in the product (reminders, habits, export, the 4.4 ICS feed, the 4.6 Mini
App) is gated behind payment; this is a "buy me a coffee" flow only,
reachable via /donate or the "☕ Support Porabot" Settings button.

Telegram Stars (currency="XTR") need no external payment provider —
provider_token is the empty string, unlike a real-money invoice.
"""

import logging
from typing import Any

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from bot.keyboards.inline import DONATION_PRESETS, get_donate_keyboard

router = Router(name="donate")
logger = logging.getLogger(__name__)


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
        payload=f"donate:{amount}",
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=amount)],
        provider_token="",  # Stars need no external payment provider.
    )
    await callback.answer()


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    # A fixed-price digital tip has nothing to reserve or validate server
    # side (no stock, no shipping, no external order) — always approve.
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message, l10n: dict[str, Any]) -> None:
    payment = message.successful_payment
    logger.info(
        "Received Stars donation: user=%s amount=%d currency=%s",
        message.from_user.id,
        payment.total_amount,
        payment.currency,
    )
    await message.answer(
        l10n.get("donate_thanks", "☕ Thank you so much for supporting Porabot! 💛").format(
            amount=payment.total_amount
        )
    )
