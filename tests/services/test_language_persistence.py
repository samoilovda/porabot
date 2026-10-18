"""Language selection must release SQLite's write lock before durable FSM writes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import User as TgUser
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.database.dao.user import UserDAO
from bot.database.engine import Base, create_engine
from bot.database.models import User
from bot.handlers.commands import callback_set_lang
from bot.lexicon import get_l10n
from bot.middlewares.database import DatabaseMiddleware
from bot.services.fsm_storage import SQLAlchemyFSMStorage


@pytest.fixture
async def session_pool(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'language.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def short_busy_timeout(connection, _):
        # Fail a regression quickly instead of waiting production's 30 seconds.
        connection.execute("PRAGMA busy_timeout=100")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("initial_language", [None, "ru", "en"])
async def test_spanish_selection_persists_and_localizes_next_update(session_pool, initial_language):
    async with session_pool() as session:
        session.add(User(id=101, language=initial_language))
        session.add(User(id=202, language="ru"))
        await session.commit()

    middleware = DatabaseMiddleware(session_pool)
    state = FSMContext(SQLAlchemyFSMStorage(session_pool), StorageKey(bot_id=1, chat_id=101, user_id=101))
    callback = SimpleNamespace(
        data="set_lang_es",
        message=SimpleNamespace(delete=AsyncMock(), answer=AsyncMock()),
        from_user=SimpleNamespace(first_name="Alice"),
        answer=AsyncMock(),
    )
    tg_user = TgUser(id=101, is_bot=False, first_name="Alice", language_code="ru")

    async def select_language(_, data):
        await callback_set_lang(callback, data["user_dao"], data["user"], state, data["l10n"])

    await middleware(select_language, callback, {"event_from_user": tg_user})

    es = get_l10n("es")
    success = callback.message.answer.await_args_list[0]
    assert success.args[0] == es["lang_set"]
    menu = success.kwargs["reply_markup"]
    if initial_language is None:
        assert menu is None  # Keep the first-run timezone step ahead of the main menu.
    else:
        assert [button.text for row in menu.keyboard for button in row] == [
            es["btn_new_task"], es["btn_my_tasks"], es["btn_habits"], es["btn_settings"],
        ]
    if initial_language is None:
        assert (await state.get_data())["onboarding_timezone"] is True
        assert callback.message.answer.await_args_list[1].args[0] == es["choose_tz"]

    async def next_update(_, data):
        assert data["user"].language == "es"
        assert data["l10n"]["tasks_header"] == es["tasks_header"]
        assert data["l10n"]["btn_settings"] == es["btn_settings"]

    await middleware(next_update, callback, {"event_from_user": tg_user})
    async with session_pool() as session:
        assert (await UserDAO(session).get_by_id(202)).language == "ru"


async def test_language_survives_later_reply_failure(session_pool):
    middleware = DatabaseMiddleware(session_pool)
    state = FSMContext(SQLAlchemyFSMStorage(session_pool), StorageKey(bot_id=1, chat_id=101, user_id=101))
    callback = SimpleNamespace(
        data="set_lang_es",
        message=SimpleNamespace(delete=AsyncMock(), answer=AsyncMock(side_effect=RuntimeError("send failed"))),
        from_user=SimpleNamespace(first_name="Alice"),
        answer=AsyncMock(),
    )

    async def select_language(_, data):
        await callback_set_lang(callback, data["user_dao"], data["user"], state, data["l10n"])

    with pytest.raises(RuntimeError, match="send failed"):
        await middleware(
            select_language, callback,
            {"event_from_user": TgUser(id=101, is_bot=False, first_name="Alice")},
        )
    async with session_pool() as session:
        assert (await UserDAO(session).get_by_id(101)).language == "es"


async def test_commit_failure_does_not_report_success():
    callback = SimpleNamespace(
        data="set_lang_es",
        message=SimpleNamespace(delete=AsyncMock(), answer=AsyncMock()),
        answer=AsyncMock(),
    )
    user_dao = SimpleNamespace(
        update_language=AsyncMock(),
        session=SimpleNamespace(commit=AsyncMock(side_effect=RuntimeError("commit failed"))),
    )
    with pytest.raises(RuntimeError, match="commit failed"):
        await callback_set_lang(callback, user_dao, SimpleNamespace(id=101, language=None), None, get_l10n("ru"))
    callback.message.delete.assert_not_awaited()
    callback.message.answer.assert_not_awaited()
