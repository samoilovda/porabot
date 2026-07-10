from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.database.dao.reminder import ReminderDAO


def _make_dao(reminder):
    session = SimpleNamespace(flush=AsyncMock())
    dao = ReminderDAO(session)  # type: ignore[arg-type]
    dao.get_by_id = AsyncMock(return_value=reminder)  # type: ignore[method-assign]
    return dao


async def test_get_owned_returns_reminder_for_matching_user() -> None:
    reminder = SimpleNamespace(id=1, user_id=777)
    dao = _make_dao(reminder)

    result = await dao.get_owned(1, 777)

    assert result is reminder


async def test_get_owned_returns_none_for_other_user() -> None:
    reminder = SimpleNamespace(id=1, user_id=777)
    dao = _make_dao(reminder)

    result = await dao.get_owned(1, 999)

    assert result is None


async def test_get_owned_returns_none_when_reminder_missing() -> None:
    dao = _make_dao(None)

    result = await dao.get_owned(1, 777)

    assert result is None
