from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.database.dao.user import UserDAO


def _make_dao(existing_user):
    session = SimpleNamespace(add=lambda obj: None, flush=AsyncMock())
    dao = UserDAO(session)  # type: ignore[arg-type]
    dao.get_by_id = AsyncMock(return_value=existing_user)  # type: ignore[method-assign]
    return dao, session


async def test_get_or_create_updates_changed_username_for_existing_user() -> None:
    user = SimpleNamespace(id=1, username="old_handle")
    dao, session = _make_dao(user)

    result = await dao.get_or_create(user_id=1, username="new_handle")

    assert result is user
    assert user.username == "new_handle"
    session.flush.assert_awaited()


async def test_get_or_create_clears_username_when_user_removes_it() -> None:
    user = SimpleNamespace(id=1, username="had_a_handle")
    dao, session = _make_dao(user)

    result = await dao.get_or_create(user_id=1, username=None)

    assert result is user
    assert user.username is None
    session.flush.assert_awaited()


async def test_get_or_create_does_not_flush_when_username_unchanged() -> None:
    user = SimpleNamespace(id=1, username="same_handle")
    dao, session = _make_dao(user)

    await dao.get_or_create(user_id=1, username="same_handle")

    session.flush.assert_not_awaited()
