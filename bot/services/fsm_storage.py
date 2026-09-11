"""1.1: SQLite-backed aiogram FSM storage.

``Dispatcher()`` without an explicit ``storage=`` falls back to aiogram's
``MemoryStorage`` (bot/__main__.py used to do exactly this). Combined with
docker-compose's ``restart: always`` and a deploy that recreates the
container, every user mid-wizard (choosing a time, typing a timezone
offset, setting a nag limit) silently lost their FSM state on every
deploy: their next message no longer matched the state-scoped handler that
was waiting for it, fell through to the catch-all ``StateFilter(None)``
text handler, and got created as a brand-new task instead.

This storage persists state+data in the same database the rest of the app
already uses (see bot/database/models.py's ``FsmState``), so it survives a
restart the same way a Reminder row does. Deliberately NOT the scheduler's
jobs.sqlite — that's an APScheduler-owned file with its own lifecycle.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.database.models import FsmState

logger = logging.getLogger(__name__)

# An abandoned wizard (user started, never finished, never came back) is
# stale after this long — the periodic cleanup job below drops it so the
# table tracks "flows in progress", not "every flow ever started".
STALE_STATE_AGE = timedelta(hours=24)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SQLAlchemyFSMStorage(BaseStorage):
    """Durable counterpart to aiogram's MemoryStorage.

    Each method opens its own short-lived session, same pattern as the
    background jobs in bot/services/*.py — FSM reads/writes happen on
    (almost) every update, so holding one session open for the storage's
    whole lifetime would fight DatabaseMiddleware's per-update session.
    """

    def __init__(self, session_pool: async_sessionmaker) -> None:
        self.session_pool = session_pool

    async def _get_row(self, session, key: StorageKey) -> Optional[FsmState]:
        result = await session.execute(
            select(FsmState).where(
                FsmState.bot_id == key.bot_id,
                FsmState.chat_id == key.chat_id,
                FsmState.user_id == key.user_id,
                FsmState.destiny == key.destiny,
            )
        )
        return result.scalar_one_or_none()

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        state_str = state.state if isinstance(state, State) else state
        async with self.session_pool() as session:
            row = await self._get_row(session, key)
            if row is None:
                if state_str is None:
                    return
                row = FsmState(
                    bot_id=key.bot_id,
                    chat_id=key.chat_id,
                    user_id=key.user_id,
                    destiny=key.destiny,
                    data_json="{}",
                )
                session.add(row)
            row.state = state_str
            row.updated_at = _utcnow_naive()
            await session.commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        async with self.session_pool() as session:
            row = await self._get_row(session, key)
            return row.state if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        async with self.session_pool() as session:
            row = await self._get_row(session, key)
            if row is None:
                if not data:
                    return
                row = FsmState(
                    bot_id=key.bot_id,
                    chat_id=key.chat_id,
                    user_id=key.user_id,
                    destiny=key.destiny,
                    state=None,
                )
                session.add(row)
            row.data_json = json.dumps(dict(data), ensure_ascii=False)
            row.updated_at = _utcnow_naive()
            await session.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self.session_pool() as session:
            row = await self._get_row(session, key)
            if row is None or not row.data_json:
                return {}
            try:
                return json.loads(row.data_json)
            except (TypeError, ValueError):
                logger.warning("Corrupt FSM data_json for chat=%s user=%s — resetting.", key.chat_id, key.user_id)
                return {}

    async def close(self) -> None:
        pass


async def cleanup_stale_fsm_state() -> None:
    """Drop FSM rows untouched for STALE_STATE_AGE (1.1).

    Registered as a periodic job in bot/__main__.py, same cadence family as
    cleanup_stale_timers/cleanup_expired — an abandoned wizard must not pin
    a row in this table forever.

    Takes no args: APScheduler's SQLAlchemyJobStore pickles every job's
    args/kwargs to persist it, and a bound async_sessionmaker drags in its
    engine's connection-pool creator (an unpicklable local closure), which
    crashed scheduler.start() on every startup. Reads session_pool from the
    process AppContext instead, same as process_daily_briefs and friends.
    """
    from bot.context import get_context

    try:
        ctx = get_context()
    except RuntimeError:
        logger.error("Failed to clean up stale FSM state: AppContext not set")
        return

    cutoff = _utcnow_naive() - STALE_STATE_AGE
    async with ctx.session_pool() as session:
        await session.execute(delete(FsmState).where(FsmState.updated_at < cutoff))
        await session.commit()
