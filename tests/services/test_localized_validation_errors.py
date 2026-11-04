"""B-13 (2026-11-04 audit): DAO validation errors (quota, text length)
reached the user as raw English exception text."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.dao.errors import (
    ActiveHabitsLimitError,
    ActiveRemindersLimitError,
    ReminderTextTooLongError,
    user_message,
)
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.lexicon import get_l10n


@pytest.fixture
async def dao():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        s.add(User(id=1, username="u", timezone="UTC"))
        await s.flush()
        yield ReminderDAO(s)
    await engine.dispose()


def _later():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)


async def test_text_too_long_is_localized(dao) -> None:
    with pytest.raises(ReminderTextTooLongError) as exc:
        await dao.create_reminder(1, "x" * 3001, _later())
    text = user_message(exc.value, get_l10n("ru"))
    assert "3001" in text and "3000" in text and "Текст" in text


async def test_reminder_quota_is_localized(dao, monkeypatch) -> None:
    monkeypatch.setattr(ReminderDAO, "MAX_ACTIVE_REMINDERS", 1)
    await dao.create_reminder(1, "one", _later())
    with pytest.raises(ActiveRemindersLimitError) as exc:
        await dao.create_reminder(1, "two", _later())
    assert user_message(exc.value, get_l10n("ru")).startswith("❌ У тебя уже 1 активных задач")
    assert "limit 1" in user_message(exc.value, get_l10n("en"))


async def test_habit_quota_is_localized(dao, monkeypatch) -> None:
    monkeypatch.setattr(ReminderDAO, "MAX_ACTIVE_HABITS", 1)
    await dao.create_reminder(1, "h1", _later(), is_recurring=True, rrule_string="FREQ=DAILY", is_habit=True)
    with pytest.raises(ActiveHabitsLimitError) as exc:
        await dao.create_reminder(1, "h2", _later(), is_recurring=True, rrule_string="FREQ=DAILY", is_habit=True)
    assert "hábitos" in user_message(exc.value, get_l10n("es"))


def test_unknown_errors_never_leak_raw_text() -> None:
    l10n = get_l10n("ru")
    assert user_message(ValueError("internal detail"), l10n) == l10n["generic_error"]


@pytest.mark.parametrize("lang", ["en", "ru", "es", "pt"])
def test_keys_exist_in_every_language(lang) -> None:
    from bot.lexicon import _LEXICONS

    for key in ("limit_reminders_reached", "limit_habits_reached", "text_too_long", "generic_error"):
        assert key in _LEXICONS[lang]
