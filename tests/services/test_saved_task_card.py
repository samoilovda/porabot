"""Step 4 of the 2026-09-26 audit remediation: the "✅ saved" card must
show a compact Edit/Delete/Close keyboard with no "cancel_wizard" escape
hatch (there's no wizard in progress any more once a task is saved — that
button used to delete this very confirmation and claim the already-
persisted task was cancelled), and Delete on that card must go through the
existing soft-delete/undo mechanism rather than something new.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.reminders_listing import callback_delete_task, callback_undo_delete
from bot.handlers.reminders_shared import _save_and_show_edit
from bot.lexicon.ru import RU


def _make_state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


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


def _all_callback_data(keyboard) -> list[str]:
    return [btn.callback_data for row in keyboard.inline_keyboard for btn in row]


def _callback(data: str, chat_id: int = 1):
    message = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        message_id=1,
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        answer=AsyncMock(),
        delete=AsyncMock(),
    )
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message)


async def test_saved_card_keyboard_has_no_cancel_wizard() -> None:
    reminder_dao, engine = await _seeded_dao()
    try:
        state = _make_state()
        future_dt = datetime.now(timezone.utc) + timedelta(hours=2)
        await state.update_data(text="buy milk", execution_time=future_dt.isoformat())
        user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
        source_message = SimpleNamespace(chat=SimpleNamespace(id=1), answer=AsyncMock())
        scheduler_service = SimpleNamespace(schedule_reminder=lambda *a, **k: None)

        await _save_and_show_edit(source_message, state, RU, user, reminder_dao, scheduler_service)

        keyboard = source_message.answer.await_args.kwargs["reply_markup"]
        callback_data_values = _all_callback_data(keyboard)
        assert "cancel_wizard" not in callback_data_values
        assert any(cd.startswith("del_task_") for cd in callback_data_values)
        assert any(cd.startswith("task_settings_") for cd in callback_data_values)
        assert "done_close" in callback_data_values
    finally:
        await engine.dispose()


async def test_delete_from_saved_card_removes_job_and_undo_restores_both() -> None:
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
            remove_nagging_job=lambda rid: None,
        )

        await _save_and_show_edit(source_message, state, RU, user, reminder_dao, scheduler_service)
        keyboard = source_message.answer.await_args.kwargs["reply_markup"]
        del_callback_data = next(cd for cd in _all_callback_data(keyboard) if cd.startswith("del_task_"))
        reminder_id = int(del_callback_data.split("del_task_")[1])
        assert scheduled_ids == [reminder_id]

        await callback_delete_task(
            _callback(del_callback_data), reminder_dao=reminder_dao,
            scheduler_service=scheduler_service, user=user, l10n=RU,
        )
        assert removed_ids == [reminder_id]  # job torn down
        reminder = await reminder_dao.get_owned(reminder_id, user.id, include_pending_delete=True)
        assert reminder.pending_delete_at is not None  # soft-deleted, not gone yet

        await callback_undo_delete(
            _callback(f"undo_del_{reminder_id}"), reminder_dao=reminder_dao,
            scheduler_service=scheduler_service, user=user, l10n=RU,
        )
        assert scheduled_ids == [reminder_id, reminder_id]  # job re-created
        restored = await reminder_dao.get_owned(reminder_id, user.id)
        assert restored is not None
        assert restored.pending_delete_at is None
    finally:
        await engine.dispose()
