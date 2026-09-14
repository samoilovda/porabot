"""Regression test for 1.1: FSM state must survive a process restart.

The bug: `Dispatcher()` without an explicit `storage=` falls back to
aiogram's `MemoryStorage`, which is a plain in-process dict — nothing about
it is written to disk. A restart (deploy, crash, `docker compose up -d
--build`) wipes every in-progress wizard state. This test proves the fix
(`SQLAlchemyFSMStorage`) by simulating "restart" the only way that's
meaningful for a storage class: constructing a SECOND, independent
instance over the same underlying database and confirming it sees what the
first instance wrote — a real MemoryStorage would fail this immediately,
since a fresh instance starts with an empty dict.
"""

from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.context import AppContext, clear_context, set_context
from bot.database.engine import Base, create_engine
from bot.services.fsm_storage import SQLAlchemyFSMStorage, cleanup_stale_fsm_state


async def _make_session_pool(db_path):
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def test_state_and_data_survive_a_fresh_storage_instance(tmp_path) -> None:
    db_path = tmp_path / "fsm_test.db"
    session_pool, engine = await _make_session_pool(db_path)
    key = StorageKey(bot_id=1, chat_id=42, user_id=42)

    storage_before_restart = SQLAlchemyFSMStorage(session_pool)
    await storage_before_restart.set_state(key, "ReminderWizard:choosing_time")
    await storage_before_restart.set_data(key, {"text": "buy milk", "edit_reminder_id": 7})
    await storage_before_restart.close()

    # "Restart": brand-new storage instance, same session_pool (~= same DB
    # file) — nothing is shared in-process between the two.
    storage_after_restart = SQLAlchemyFSMStorage(session_pool)
    assert await storage_after_restart.get_state(key) == "ReminderWizard:choosing_time"
    assert await storage_after_restart.get_data(key) == {"text": "buy milk", "edit_reminder_id": 7}

    await engine.dispose()


async def test_a_real_memory_storage_would_fail_the_same_check(tmp_path) -> None:
    """Sanity check that the test above actually distinguishes durable from
    in-memory storage — proves this isn't a tautology."""
    key = StorageKey(bot_id=1, chat_id=42, user_id=42)
    storage_before_restart = MemoryStorage()
    await storage_before_restart.set_state(key, "ReminderWizard:choosing_time")

    storage_after_restart = MemoryStorage()
    assert await storage_after_restart.get_state(key) is None


async def test_missing_key_returns_defaults() -> None:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_pool = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    storage = SQLAlchemyFSMStorage(session_pool)
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    assert await storage.get_state(key) is None
    assert await storage.get_data(key) == {}
    await engine.dispose()


async def test_clear_resets_state_and_data() -> None:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_pool = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    storage = SQLAlchemyFSMStorage(session_pool)
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    await storage.set_state(key, "SomeState:step")
    await storage.set_data(key, {"a": 1})

    # Mirrors aiogram's FSMContext.clear(): set_state(None) then set_data({}).
    await storage.set_state(key, None)
    await storage.set_data(key, {})

    assert await storage.get_state(key) is None
    assert await storage.get_data(key) == {}
    await engine.dispose()


async def _row_exists(session_pool, key: StorageKey) -> bool:
    from sqlalchemy import select

    from bot.database.models import FsmState

    async with session_pool() as session:
        result = await session.execute(
            select(FsmState).where(
                FsmState.bot_id == key.bot_id,
                FsmState.chat_id == key.chat_id,
                FsmState.user_id == key.user_id,
                FsmState.destiny == key.destiny,
            )
        )
        return result.scalar_one_or_none() is not None


async def test_clear_deletes_the_row_instead_of_leaving_an_empty_placeholder() -> None:
    """3.5: FSMContext.clear() (state.clear(), called on nearly every
    successful flow completion/cancel) used to leave the row behind with
    state=None, data_json="{}" — a permanent placeholder for every chat
    that ever started a wizard, only ever cleaned up by the 24-hour
    cleanup_stale_fsm_state sweep. It should be dropped immediately
    once both halves of clear() (set_state(None), then set_data({}))
    have run.
    """
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_pool = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    storage = SQLAlchemyFSMStorage(session_pool)
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    await storage.set_state(key, "SomeState:step")
    await storage.set_data(key, {"a": 1})
    assert await _row_exists(session_pool, key)

    # Mirrors aiogram's FSMContext.clear(): set_state(None) then set_data({}).
    await storage.set_state(key, None)
    await storage.set_data(key, {})

    assert not await _row_exists(session_pool, key)
    # The public API still reads back the same defaults either way.
    assert await storage.get_state(key) is None
    assert await storage.get_data(key) == {}
    await engine.dispose()


async def test_setting_state_again_after_clear_recreates_the_row() -> None:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_pool = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    storage = SQLAlchemyFSMStorage(session_pool)
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    await storage.set_state(key, "SomeState:step")
    await storage.set_data(key, {})
    await storage.set_state(key, None)
    assert not await _row_exists(session_pool, key)

    await storage.set_state(key, "NewWizard:entering_text")

    assert await _row_exists(session_pool, key)
    assert await storage.get_state(key) == "NewWizard:entering_text"
    await engine.dispose()


async def test_clearing_data_alone_does_not_delete_a_row_with_active_state() -> None:
    """set_data({}) must only delete the row when state is ALSO already
    None — a wizard step that happens to clear its own scratch data
    mid-flow must not lose its FSM state."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_pool = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    storage = SQLAlchemyFSMStorage(session_pool)
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    await storage.set_state(key, "SomeState:step")
    await storage.set_data(key, {"a": 1})

    await storage.set_data(key, {})

    assert await _row_exists(session_pool, key)
    assert await storage.get_state(key) == "SomeState:step"
    await engine.dispose()


async def test_cleanup_drops_only_stale_rows(tmp_path) -> None:
    from datetime import datetime, timedelta

    db_path = tmp_path / "fsm_cleanup.db"
    session_pool, engine = await _make_session_pool(db_path)

    storage = SQLAlchemyFSMStorage(session_pool)
    fresh_key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    stale_key = StorageKey(bot_id=1, chat_id=2, user_id=2)
    await storage.set_state(fresh_key, "Wizard:step")
    await storage.set_state(stale_key, "Wizard:step")

    # Backdate the stale row's updated_at directly, past the door aiogram's
    # own API doesn't expose.
    from sqlalchemy import update

    from bot.database.models import FsmState

    async with session_pool() as session:
        await session.execute(
            update(FsmState)
            .where(FsmState.chat_id == stale_key.chat_id)
            .values(updated_at=datetime.utcnow() - timedelta(hours=25))
        )
        await session.commit()

    set_context(AppContext(bot=None, session_pool=session_pool, scheduler=None))
    try:
        await cleanup_stale_fsm_state()
    finally:
        clear_context()

    assert await storage.get_state(fresh_key) == "Wizard:step"
    assert await storage.get_state(stale_key) is None
    await engine.dispose()
