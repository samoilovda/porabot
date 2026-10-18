"""Offer notification preferences after the first successful task creation."""

from aiogram import BaseMiddleware


class NotificationOfferMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        result = await handler(event, data)
        message = getattr(event, "message", None) or getattr(getattr(event, "callback_query", None), "message", None)
        dao, user, l10n = data.get("reminder_dao"), data.get("user"), data.get("l10n")
        if message is not None and dao is not None and user is not None and l10n is not None:
            from bot.handlers.experience import offer_notifications

            await offer_notifications(message, dao, user, l10n)
        return result
