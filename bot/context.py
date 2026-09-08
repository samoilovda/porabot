"""Process-wide AppContext (4.2 — replaces the old bot.services.scheduler
``_instance`` convention).

APScheduler job targets must be top-level functions, not bound methods, so
the module-level job target in scheduler.py and the standalone cron job
functions in the bot.services.* modules all need a way to reach the running
bot/session_pool/scheduler without it being passed through every call. That
used to be a private ``_instance`` attribute on bot.services.scheduler,
which meant every unrelated module reading it had to reach into scheduler's
internals and pretend to know its shape. AppContext is the same singleton,
given its own name and file so it reads as a real seam rather than an
implementation detail of one service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram import Bot
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from bot.services.scheduler import SchedulerService


@dataclass
class AppContext:
    bot: "Bot"
    session_pool: "async_sessionmaker"
    scheduler: "SchedulerService"


_context: AppContext | None = None


def set_context(context: AppContext) -> None:
    global _context
    _context = context


def get_context() -> AppContext:
    """Return the process AppContext, or raise if startup hasn't set one yet.

    Raises RuntimeError (not e.g. AttributeError on None) so a caller that
    forgets to handle "not started yet" fails with a message that says what
    went wrong, instead of a bare None crashing somewhere downstream.
    """
    if _context is None:
        raise RuntimeError(
            "AppContext not set. set_context() is called by SchedulerService.__init__ "
            "during startup — this means a job or handler ran before startup finished, "
            "or in a test that didn't set a context."
        )
    return _context


def clear_context() -> None:
    """Test-only: reset the context so state doesn't leak between tests."""
    global _context
    _context = None
