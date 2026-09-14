"""1.3: schedule_reminder() writes a job into jobs.sqlite and the "✅
saved" reply/edit-keyboard message goes out to the user BEFORE
DatabaseMiddleware's implicit commit() (which only runs once the handler
returns). If that later commit fails — a collision with one of the
per-minute cron jobs sharing the same SQLite file, a disk error — the
user would have been told it worked while the DB row never actually
existed, with an orphaned job left ticking in the background.

_save_and_show_edit must commit explicitly BEFORE replying, and on a
commit failure must leave the scheduler in sync with whatever actually
persisted: remove the job entirely for a brand-new reminder (its INSERT
was rolled back too), or restore the PRIOR job for an edit (schedule_reminder
already replaced it with one pointing at the now-rolled-back new time).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.reminders_shared import _save_and_show_edit
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
    await session.commit()
    return ReminderDAO(session), engine


def _make_failing_commit():
    async def _fail():
        raise OperationalError("COMMIT", {}, Exception("database is locked"))
    return _fail


async def test_commit_failure_on_create_removes_the_orphaned_job() -> None:
    reminder_dao, engine = await _seeded_dao()
    try:
        state = _make_state()
        future_dt = datetime.now(timezone.utc) + timedelta(hours=2)
        await state.update_data(text="buy milk", execution_time=future_dt.isoformat())
        user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
        source_message = SimpleNamespace(chat=SimpleNamespace(id=1), answer=AsyncMock())

        scheduled_ids: list[int] = []
        removed_ids: list[int] = []
        scheduler_service = SimpleNamespace(
            schedule_reminder=lambda rid, dt, is_nagging=False: scheduled_ids.append(rid),
            remove_reminder_job=lambda rid: removed_ids.append(rid),
        )

        reminder_dao.session.commit = _make_failing_commit()

        await _save_and_show_edit(source_message, state, RU, user, reminder_dao, scheduler_service)

        assert scheduled_ids  # schedule_reminder was called before the commit attempt
        assert removed_ids == scheduled_ids  # ...and its job was removed once commit failed
        source_message.answer.assert_awaited()
        # No reminder row should have survived the rollback of its own INSERT.
        reminders = await reminder_dao.get_user_reminders(1)
        assert reminders == []
    finally:
        await engine.dispose()


async def test_commit_failure_on_edit_restores_the_prior_job() -> None:
    reminder_dao, engine = await _seeded_dao()
    try:
        old_time = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        reminder = await reminder_dao.create_reminder(user_id=1, text="orig", execution_time=old_time)
        await reminder_dao.session.commit()

        state = _make_state()
        new_time = datetime.now(timezone.utc) + timedelta(hours=5)
        await state.update_data(
            text="orig", execution_time=new_time.isoformat(), edit_reminder_id=reminder.id
        )
        user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
        source_message = SimpleNamespace(chat=SimpleNamespace(id=1), answer=AsyncMock())

        scheduled_calls: list[datetime] = []
        removed_ids: list[int] = []
        scheduler_service = SimpleNamespace(
            schedule_reminder=lambda rid, dt, is_nagging=False: scheduled_calls.append(dt),
            remove_reminder_job=lambda rid: removed_ids.append(rid),
        )

        reminder_dao.session.commit = _make_failing_commit()

        await _save_and_show_edit(source_message, state, RU, user, reminder_dao, scheduler_service)

        # Called once with the new (now rolled-back) time, once more to
        # restore the prior job — never removed outright, since the
        # reminder itself still exists in the DB at its old time.
        assert len(scheduled_calls) == 2
        assert removed_ids == []
        restored_dt = scheduled_calls[-1]
        assert abs((restored_dt.replace(tzinfo=None) - old_time).total_seconds()) < 5
        source_message.answer.assert_awaited()
    finally:
        await engine.dispose()


async def test_successful_save_commits_before_replying() -> None:
    """Happy-path sanity check: the explicit commit actually persists the
    row (not just a no-op alongside a later implicit one)."""
    reminder_dao, engine = await _seeded_dao()
    try:
        state = _make_state()
        future_dt = datetime.now(timezone.utc) + timedelta(hours=2)
        await state.update_data(text="buy milk", execution_time=future_dt.isoformat())
        user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
        source_message = SimpleNamespace(chat=SimpleNamespace(id=1), answer=AsyncMock())
        scheduler_service = SimpleNamespace(schedule_reminder=lambda *a, **k: None)

        await _save_and_show_edit(source_message, state, RU, user, reminder_dao, scheduler_service)

        # A second, fully independent session/connection must see the
        # committed row — proves it's durable, not just flushed.
        reminders = await reminder_dao.get_user_reminders(1)
        assert len(reminders) == 1
        assert reminders[0].reminder_text == "buy milk"
    finally:
        await engine.dispose()
