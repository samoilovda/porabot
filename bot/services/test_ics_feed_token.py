"""4.4: UserDAO token lifecycle + Settings handlers for the calendar feed."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.engine import Base
from bot.database.dao.user import UserDAO
from bot.database.models import User
from bot.handlers.settings import callback_ics_feed, callback_ics_feed_regenerate
from bot.lexicon.ru import RU


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_ensure_ics_feed_token_creates_once(session) -> None:
    session.add(User(id=1, username="u", timezone="UTC"))
    await session.flush()
    dao = UserDAO(session)

    token1 = await dao.ensure_ics_feed_token(1)
    token2 = await dao.ensure_ics_feed_token(1)

    assert token1 is not None
    assert token1 == token2


async def test_regenerate_ics_feed_token_changes_value(session) -> None:
    session.add(User(id=1, username="u", timezone="UTC"))
    await session.flush()
    dao = UserDAO(session)

    token1 = await dao.ensure_ics_feed_token(1)
    token2 = await dao.regenerate_ics_feed_token(1)

    assert token1 != token2


async def test_get_by_ics_feed_token_round_trip(session) -> None:
    session.add(User(id=42, username="u", timezone="UTC"))
    await session.flush()
    dao = UserDAO(session)
    token = await dao.ensure_ics_feed_token(42)

    found = await dao.get_by_ics_feed_token(token)
    assert found is not None
    assert found.id == 42

    assert await dao.get_by_ics_feed_token("wrong-token") is None
    assert await dao.get_by_ics_feed_token("") is None


async def test_settings_ics_feed_shows_url() -> None:
    user_dao = SimpleNamespace(ensure_ics_feed_token=AsyncMock(return_value="abc123"))
    user = SimpleNamespace(id=1)
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await callback_ics_feed(callback, user, user_dao, RU)

    user_dao.ensure_ics_feed_token.assert_awaited_once_with(1)
    message.edit_text.assert_awaited_once()
    text = message.edit_text.await_args.args[0]
    assert "abc123" in text


async def test_settings_ics_feed_regenerate_issues_new_token() -> None:
    user_dao = SimpleNamespace(regenerate_ics_feed_token=AsyncMock(return_value="new-token"))
    user = SimpleNamespace(id=1)
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await callback_ics_feed_regenerate(callback, user, user_dao, RU)

    user_dao.regenerate_ics_feed_token.assert_awaited_once_with(1)
    text = message.edit_text.await_args.args[0]
    assert "new-token" in text
    callback.answer.assert_awaited_once()
