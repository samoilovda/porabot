"""Offer notification preferences after the first successful task creation."""

from aiogram import BaseMiddleware


class NotificationOfferMiddleware(BaseMiddleware):
    """B-17 (2026-11-04 audit): this used to run a COUNT(*) on reminders for
    EVERY update of every user, forever. Once a user has been offered (or
    is past their first task), remember that in-process and skip the
    queries; after a restart the first update re-checks once."""

    def __init__(self) -> None:
        super().__init__()
        self._settled: set[int] = set()

    async def __call__(self, handler, event, data):
        result = await handler(event, data)
        message = getattr(event, "message", None) or getattr(getattr(event, "callback_query", None), "message", None)
        dao, user, l10n = data.get("reminder_dao"), data.get("user"), data.get("l10n")
        if message is None or dao is None or user is None or l10n is None or user.id in self._settled:
            return result
        from bot.handlers.experience import offer_notifications

        if await offer_notifications(message, dao, user, l10n):
            self._settled.add(user.id)
        return result
