"""2.6: reject any update from a non-private chat before it reaches a handler.

No handler in this codebase filters on chat.type — a group the bot is
added to (or one with Telegram's privacy mode off, seeing every message)
reached the exact same free-text-parses-as-task catch-all a DM does.
Concretely: any text typed in a group by ANY member created a reminder
addressed to chat_id=user_id — that member's own private chat, not the
group — so unless that member had already started a DM with the bot, the
eventual notification hit TelegramForbiddenError (never having opened a
chat with the bot means Telegram refuses to deliver to it) three times in
a row and the reminder silently gave up (see forbidden_strikes in
bot/services/scheduler.py). Meanwhile the group itself accumulated
edit/delete/done inline keyboards that any other member could tap —
get_owned(reminder_id, user_id) already stops them from acting on someone
else's reminder, but the button sitting there answering "not found" to
everyone except its creator is still noise nobody asked for.

Registered FIRST in bot/__main__.py, before RateLimitMiddleware — a
rejected-outright update should not count toward that user's rate-limit
window at all.
"""

import logging
import time
from typing import Any, Awaitable, Callable, Optional

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Chat, Message, TelegramObject, Update

from bot.lexicon import get_l10n
from bot.utils.telegram import inner_event

logger = logging.getLogger(__name__)

# A-25: a group with Telegram's privacy mode off (or where the bot was
# briefly added) sees every single message, and used to get this reply on
# EVERY one of them, forever — the module docstring already claimed "answer
# once", which the code never actually did. Notify at most once per chat
# per this cooldown instead.
_RENOTIFY_AFTER_SECONDS = 3600.0


def _chat_of(target) -> Optional[Chat]:
    """A Message has .chat directly; a CallbackQuery has it via .message.chat
    (which can be an InaccessibleMessage for a callback on an old message —
    still has .chat, just not full content)."""
    chat = getattr(target, "chat", None)
    if chat is not None:
        return chat
    message = getattr(target, "message", None)
    return getattr(message, "chat", None) if message is not None else None


class PrivateChatOnlyMiddleware(BaseMiddleware):
    """Silently pass through update kinds with no chat to check (my_chat_member,
    poll_answer, ...) — no handler in this codebase acts on those anyway.
    For a Message/CallbackQuery from a non-private chat, reject it — but
    only actually reply with the "private only" notice at most once per
    _RENOTIFY_AFTER_SECONDS per chat (A-25), not on every single message a
    busy group generates.
    """

    def __init__(self) -> None:
        super().__init__()
        # chat_id -> monotonic time of the last notice sent to it.
        self._last_notified: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        target = inner_event(event)
        if target is None:
            return await handler(event, data)

        chat = _chat_of(target)
        if chat is None or chat.type == "private":
            return await handler(event, data)

        logger.info("Ignored update from non-private chat %s (type=%s).", chat.id, chat.type)

        now = time.monotonic()
        last_notified = self._last_notified.get(chat.id)
        if last_notified is not None and now - last_notified < _RENOTIFY_AFTER_SECONDS:
            return None
        self._last_notified[chat.id] = now

        from_user = getattr(target, "from_user", None)
        l10n = get_l10n(getattr(from_user, "language_code", None))
        text = l10n.get(
            "private_only",
            "🔒 Porabot only works in a private chat. Message me directly instead.",
        )
        try:
            if isinstance(target, Message):
                await target.answer(text)
            elif isinstance(target, CallbackQuery):
                await target.answer(text, show_alert=True)
        except Exception as e:
            logger.warning("Could not notify chat %s that Porabot is private-only: %s", chat.id, e)
        return None

    def cleanup_expired(self) -> None:
        """Drop chats whose cooldown has already elapsed — same "don't
        grow forever" shape as RateLimitMiddleware.cleanup_expired,
        registered as a periodic job in bot/__main__.py. A chat that
        renotifies again later just gets a fresh entry."""
        now = time.monotonic()
        expired = [
            chat_id
            for chat_id, last_notified in self._last_notified.items()
            if now - last_notified >= _RENOTIFY_AFTER_SECONDS
        ]
        for chat_id in expired:
            del self._last_notified[chat_id]
