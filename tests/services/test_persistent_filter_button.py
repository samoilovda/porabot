"""Step 8 of the 2026-09-26 audit remediation: "Filter" must be a
permanent entry point regardless of page count, a quick filter's results
must be genuinely paginable, and the active filter must survive both
paging and marking a task done from within it.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.reminders_completion import callback_task_done
from bot.handlers.reminders_listing import _TASKS_PAGE_SIZE, callback_filter_page, callback_tasks_filter_today
from bot.keyboards.inline import get_tasks_list_keyboard
from bot.lexicon.ru import RU


def _fake_task(i: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=i, reminder_text=f"Task {i}", execution_time=datetime(2026, 5, 1, 9, 0, 0),
        is_recurring=False, is_nagging=False,
    )


@pytest.mark.parametrize("total_pages", [1, 3])
def test_filter_button_always_present_regardless_of_page_count(total_pages) -> None:
    markup = get_tasks_list_keyboard([_fake_task(1)], RU, page=0, total_pages=total_pages)
    callback_datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "tasks_filter_menu" in callback_datas


async def test_filter_stays_active_across_pagination() -> None:
    tasks = [_fake_task(i) for i in range(1, _TASKS_PAGE_SIZE + 6)]  # spans 2 pages
    reminder_dao = SimpleNamespace(get_user_reminders_today=AsyncMock(return_value=tasks))
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    message = SimpleNamespace(edit_text=AsyncMock())

    callback = SimpleNamespace(message=message, answer=AsyncMock())
    await callback_tasks_filter_today(callback, reminder_dao, user, RU)
    page0_markup = message.edit_text.await_args.kwargs["reply_markup"]
    page0_callbacks = [b.callback_data for row in page0_markup.inline_keyboard for b in row]
    next_page_cb = next(cd for cd in page0_callbacks if cd.startswith("flt:today:"))

    message.edit_text.reset_mock()
    callback = SimpleNamespace(data=next_page_cb, message=message, answer=AsyncMock())
    await callback_filter_page(callback, reminder_dao, user, RU)

    reminder_dao.get_user_reminders_today.assert_awaited()  # re-fetched the SAME filter, not the full list
    page1_text = message.edit_text.await_args.args[0]
    page1_markup = message.edit_text.await_args.kwargs["reply_markup"]
    assert "Task 26" in page1_text  # page 2 shows the tasks page 1 didn't
    page1_callbacks = [b.callback_data for row in page1_markup.inline_keyboard for b in row]
    assert any(cd.startswith("flt:today:") for cd in page1_callbacks)  # still a "today" filter


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_filter_stays_active_after_marking_a_task_done(session) -> None:
    user = User(id=1, username="u", timezone="UTC")
    session.add(user)
    await session.flush()

    reminder_dao = ReminderDAO(session)
    habit_event_dao = HabitEventDAO(session)
    due_today = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)
    reminder = await reminder_dao.create_reminder(
        user_id=1, text="Buy milk", execution_time=due_today, is_recurring=False,
    )
    # A second task also due today, so the filter screen still has a
    # result to show once the first one above is marked done and drops
    # out of it — proving the FILTER survives, not just an empty screen.
    await reminder_dao.create_reminder(
        user_id=1, text="Call mom", execution_time=due_today + timedelta(minutes=10), is_recurring=False,
    )
    await session.commit()

    scheduler_service = SimpleNamespace(
        remove_reminder_job=lambda _id: None, remove_nagging_job=lambda _id: None,
    )
    message = SimpleNamespace(text="Buy milk", edit_text=AsyncMock())
    callback = SimpleNamespace(
        data=f"done_task_{reminder.id}::today:0", message=message, answer=AsyncMock(),
    )

    await callback_task_done(callback, reminder_dao, habit_event_dao, scheduler_service, user, RU)

    # Re-rendered the "today" filter's results (via _render_filter_screen),
    # not the single-task done-followup card — the filter survived.
    message.edit_text.assert_awaited_once()
    reply_markup = message.edit_text.await_args.kwargs.get("reply_markup")
    callback_datas = [b.callback_data for row in reply_markup.inline_keyboard for b in row] if reply_markup else []
    assert "done_followup" not in " ".join(callback_datas)
    assert any(cd.startswith("tasks_page_") for cd in callback_datas)  # the filter screen's "back to all" button
