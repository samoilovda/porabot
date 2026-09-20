"""1: end-to-end wiring — a recurrence phrase the parser detects ("каждый
день"/"every weekday"/…) must actually create a recurring Reminder, not a
one-off whose title still contains the phrase (GPTaudit27.07.26.md #1)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.reminders import _handle_parsed_result
from bot.lexicon.ru import RU


def _make_state() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    return FSMContext(storage=storage, key=key)


async def _seeded_dao():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()
    session.add(User(id=1, username="u", timezone="UTC"))
    await session.flush()
    return ReminderDAO(session), engine


def _source_message():
    return SimpleNamespace(
        chat=SimpleNamespace(id=1),
        answer=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=1)),
    )


async def test_recurrence_phrase_creates_recurring_reminder() -> None:
    reminder_dao, engine = await _seeded_dao()
    try:
        state = _make_state()
        user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
        future_dt = datetime.now(timezone.utc) + timedelta(hours=2)
        result = SimpleNamespace(
            clean_text="тренировка",
            parsed_datetime=future_dt,
            confidence=1.0,
            rrule_string="FREQ=DAILY",
        )
        scheduler_service = SimpleNamespace(schedule_reminder=lambda *a, **k: None)

        await _handle_parsed_result(
            _source_message(), state, user, RU, result, reminder_dao, scheduler_service
        )

        reminders = await reminder_dao.get_user_reminders(1)
        assert len(reminders) == 1
        assert reminders[0].reminder_text == "тренировка"
        assert reminders[0].is_recurring is True
        assert reminders[0].rrule_string == "FREQ=DAILY"
    finally:
        await engine.dispose()


async def test_no_recurrence_phrase_still_creates_one_off_reminder() -> None:
    """Regression guard: plain text (no rrule_string on the parse result)
    must keep behaving exactly as before this feature existed."""
    reminder_dao, engine = await _seeded_dao()
    try:
        state = _make_state()
        user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
        future_dt = datetime.now(timezone.utc) + timedelta(hours=2)
        result = SimpleNamespace(clean_text="купить молоко", parsed_datetime=future_dt, confidence=1.0)
        scheduler_service = SimpleNamespace(schedule_reminder=lambda *a, **k: None)

        await _handle_parsed_result(
            _source_message(), state, user, RU, result, reminder_dao, scheduler_service
        )

        reminders = await reminder_dao.get_user_reminders(1)
        assert reminders[0].is_recurring is False
        assert reminders[0].rrule_string is None
    finally:
        await engine.dispose()


async def test_weekday_only_recurrence_snaps_past_an_excluded_day() -> None:
    """"по будням в 9" parsed on a Saturday must not create a job that first
    fires on a Saturday — the rule excludes it, so the first occurrence
    should snap forward to the next Monday at the same clock time."""
    reminder_dao, engine = await _seeded_dao()
    try:
        state = _make_state()
        user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)

        now = datetime.now(timezone.utc)
        days_until_saturday = (5 - now.weekday()) % 7 or 7
        saturday_future = (now + timedelta(days=days_until_saturday + 7)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        assert saturday_future.weekday() == 5

        result = SimpleNamespace(
            clean_text="зарядка",
            parsed_datetime=saturday_future,
            confidence=1.0,
            rrule_string="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        )
        scheduler_service = SimpleNamespace(schedule_reminder=lambda *a, **k: None)

        await _handle_parsed_result(
            _source_message(), state, user, RU, result, reminder_dao, scheduler_service
        )

        reminders = await reminder_dao.get_user_reminders(1)
        assert reminders[0].is_recurring is True
        assert reminders[0].execution_time.weekday() in (0, 1, 2, 3, 4)
        assert reminders[0].execution_time.hour == 9
        assert reminders[0].execution_time > saturday_future.replace(tzinfo=None)
    finally:
        await engine.dispose()


async def test_editing_existing_reminder_text_does_not_apply_stale_recurrence() -> None:
    """recurrence_rrule must only apply to brand-new reminders — re-typing
    the text of an existing one (edit_reminder_id set) must not silently
    change its repeat rule as a side effect."""
    reminder_dao, engine = await _seeded_dao()
    try:
        existing = await reminder_dao.create_reminder(
            user_id=1,
            text="old text",
            execution_time=(datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None),
            is_recurring=False,
        )
        await reminder_dao.session.commit()

        state = _make_state()
        await state.update_data(edit_reminder_id=existing.id)
        user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
        future_dt = datetime.now(timezone.utc) + timedelta(hours=2)
        result = SimpleNamespace(
            clean_text="new text",
            parsed_datetime=future_dt,
            confidence=1.0,
            rrule_string="FREQ=DAILY",
        )
        scheduler_service = SimpleNamespace(schedule_reminder=lambda *a, **k: None)

        await _handle_parsed_result(
            _source_message(), state, user, RU, result, reminder_dao, scheduler_service
        )

        reminders = await reminder_dao.get_user_reminders(1)
        assert len(reminders) == 1
        assert reminders[0].reminder_text == "new text"
        assert reminders[0].is_recurring is False
        assert reminders[0].rrule_string is None
    finally:
        await engine.dispose()
