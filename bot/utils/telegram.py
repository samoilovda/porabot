"""2.1: helpers for editing an already-sent Telegram message where the new
content may be IDENTICAL to what's already there.

Telegram's Bot API rejects edit_text/edit_reply_markup with
`TelegramBadRequest: Bad Request: message is not modified` whenever the
new content is byte-for-byte the same as the current one — tapping
"Refresh" on an unchanged list, or re-tapping a filter/back button that
lands on the same screen twice in a row, are both ordinary, frequent user
actions that trigger this. Before this helper, that exception propagated
uncaught to bot/__main__.py's handle_dispatcher_error, which shows the
user a generic "❌ Something went wrong" alert for what is, from their
point of view, nothing going wrong at all.
"""

import logging
from typing import Optional, Union

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, Update

logger = logging.getLogger(__name__)


def inner_event(update: Update) -> Optional[Union[Message, CallbackQuery]]:
    """2.3: the Message or CallbackQuery INSIDE an Update.

    Middleware registered via ``dp.update.middleware(...)`` (as
    RateLimitMiddleware and DatabaseMiddleware are, in bot/__main__.py, so
    a throttled/DB-erroring update never reaches a handler at all) runs at
    the Update level: its ``event`` argument is always an
    ``aiogram.types.Update``, never the ``Message``/``CallbackQuery``
    inside it, regardless of what kind of update actually arrived.
    ``isinstance(event, Message)`` there is consequently always False —
    the exact bug this helper exists to stop being repeated. Returns None
    for an update kind this bot doesn't otherwise act on (a poll answer,
    a chat-member update, ...).
    """
    return update.message or update.callback_query

_NOT_MODIFIED_MARKER = "message is not modified"


def _is_not_modified(error: TelegramBadRequest) -> bool:
    return _NOT_MODIFIED_MARKER in str(error).lower()


async def safe_edit_text(message: Message, text: str, **kwargs) -> bool:
    """message.edit_text(text, **kwargs), swallowing ONLY a "message is not
    modified" TelegramBadRequest. Returns True if the message was actually
    edited, False if Telegram rejected it as unchanged. Any other
    TelegramBadRequest (message too old to edit, message deleted, ...) is
    re-raised — those are real failures, not a no-op refresh."""
    try:
        await message.edit_text(text, **kwargs)
        return True
    except TelegramBadRequest as e:
        if _is_not_modified(e):
            logger.debug("edit_text no-op (unchanged content) for message %s.", message.message_id)
            return False
        raise


async def safe_edit_reply_markup(message: Message, **kwargs) -> bool:
    """message.edit_reply_markup(**kwargs), same "not modified" handling as
    safe_edit_text above."""
    try:
        await message.edit_reply_markup(**kwargs)
        return True
    except TelegramBadRequest as e:
        if _is_not_modified(e):
            logger.debug("edit_reply_markup no-op (unchanged markup) for message %s.", message.message_id)
            return False
        raise
