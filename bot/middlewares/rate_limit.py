"""Per-user rate limiting — P1-10.

The bot has open access (WhitelistMiddleware is disabled — see
bot/__main__.py) with no cap on how fast a single user can send updates.
Registered BEFORE DatabaseMiddleware so a rate-limited update never
touches the DB or the (CPU-heavy, globally-locked) NLP parser.

Deliberately simple: a fixed per-user sliding window, in-memory. Good
enough to blunt one user flooding the bot with reminder-creation text or
rapid-fire callbacks; not a distributed/persisted solution and not a
substitute for closing access entirely if that's ever needed instead.
"""

import logging
import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Optional

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User as TgUser

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseMiddleware):
    """Stop a user's updates once they exceed *max_updates* per *window_seconds*.

    Args:
        max_updates: Max updates allowed from one user within the window.
        window_seconds: Sliding window size, in seconds.
    """

    def __init__(self, max_updates: int = 20, window_seconds: float = 10.0) -> None:
        super().__init__()
        self.max_updates = max_updates
        self.window_seconds = window_seconds
        self._hits: dict[int, deque] = defaultdict(deque)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: Optional[TgUser] = data.get("event_from_user")
        if not tg_user:
            return await handler(event, data)

        now = time.monotonic()
        hits = self._hits[tg_user.id]
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()

        if len(hits) >= self.max_updates:
            logger.warning(
                "Rate limit hit: user %s sent %d+ updates within %.0fs",
                tg_user.id,
                self.max_updates,
                self.window_seconds,
            )
            # Silent drop rather than a reply on every single throttled
            # update — replying would itself be an unbounded-rate action
            # against the same flood.
            if isinstance(event, Message) and len(hits) == self.max_updates:
                await event.answer(
                    "⏳ Too many messages — please slow down and try again in a few seconds."
                )
            elif isinstance(event, CallbackQuery) and len(hits) == self.max_updates:
                await event.answer("⏳ Too many requests — slow down.", show_alert=False)
            hits.append(now)
            return None

        hits.append(now)
        return await handler(event, data)

    def cleanup_expired(self) -> None:
        """Drop dict entries for users with no hits left in the window (3.1).

        Without this, self._hits only ever grows: a user's deque is trimmed
        lazily, but only by that SAME user's own future events — a user who
        sends a handful of messages and never returns leaves their entry
        (dict key + whatever hits hadn't aged out yet) in memory forever.
        Every unique user_id ever seen accumulates a permanent entry in a
        long-running process. Registered as a periodic job in bot/__main__.py
        so the dict stays bounded to roughly "users active within the last
        window_seconds", not "every user ever".
        """
        now = time.monotonic()
        stale_user_ids = []
        for user_id, hits in self._hits.items():
            while hits and now - hits[0] > self.window_seconds:
                hits.popleft()
            if not hits:
                stale_user_ids.append(user_id)
        for user_id in stale_user_ids:
            del self._hits[user_id]
