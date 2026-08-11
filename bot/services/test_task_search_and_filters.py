"""3.4: /find search and today/week/overdue/recurring filters over the task
list — ReminderDAO queries plus the /find command and filter callbacks."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.engine import Base
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.handlers.reminders import (
    callback_tasks_filter_by_tag,
    callback_tasks_filter_overdue,
    callback_tasks_filter_recurring,
    callback_tasks_filter_today,
    callback_tasks_filter_week,
    callback_tasks_tags_menu,
    cmd_find,
)
from bot.keyboards.inline import get_filtered_tasks_keyboard
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


async def _seed(session) -> ReminderDAO:
    user = User(id=1, username="u", timezone="UTC")
    session.add(user)
    await session.flush()
    dao = ReminderDAO(session)
    now = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)

    await dao.create_reminder(user_id=1, text="Buy milk", execution_time=now + timedelta(hours=2))
    await dao.create_reminder(user_id=1, text="Buy bread", execution_time=now + timedelta(days=3))
    await dao.create_reminder(
        user_id=1, text="Daily standup", execution_time=now + timedelta(hours=1),
        is_recurring=True, rrule_string="FREQ=DAILY",
    )
    overdue = await dao.create_reminder(user_id=1, text="Old task", execution_time=now + timedelta(hours=5))
    overdue.execution_time = now - timedelta(hours=1)
    await session.flush()
    return dao


async def test_search_user_reminders_matches_substring_case_insensitively(session) -> None:
    dao = await _seed(session)
    results = await dao.search_user_reminders(1, "buy")
    texts = {r.reminder_text for r in results}
    assert texts == {"Buy milk", "Buy bread"}


async def test_search_user_reminders_no_match(session) -> None:
    dao = await _seed(session)
    results = await dao.search_user_reminders(1, "nonexistent")
    assert results == []


async def test_get_user_reminders_today_excludes_future_days(session) -> None:
    dao = await _seed(session)
    results = await dao.get_user_reminders_today(1, "UTC")
    texts = {r.reminder_text for r in results}
    assert "Buy bread" not in texts  # 3 days out


async def test_get_user_reminders_this_week_includes_next_few_days(session) -> None:
    dao = await _seed(session)
    results = await dao.get_user_reminders_this_week(1, "UTC")
    texts = {r.reminder_text for r in results}
    assert "Buy bread" in texts
    assert "Buy milk" in texts


async def test_get_user_reminders_recurring_only_returns_recurring(session) -> None:
    dao = await _seed(session)
    results = await dao.get_user_reminders_recurring(1)
    texts = {r.reminder_text for r in results}
    assert texts == {"Daily standup"}


async def test_find_command_renders_results() -> None:
    reminder_dao = SimpleNamespace(
        search_user_reminders=AsyncMock(
            return_value=[
                SimpleNamespace(
                    id=1, reminder_text="Buy milk", execution_time=datetime.now(timezone.utc).replace(tzinfo=None),
                    is_recurring=False, is_nagging=False,
                )
            ]
        )
    )
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    state = SimpleNamespace(clear=AsyncMock())
    message = SimpleNamespace(text="/find milk", answer=AsyncMock())

    await cmd_find(message, state, reminder_dao, user, RU)

    message.answer.assert_awaited_once()
    reminder_dao.search_user_reminders.assert_awaited_once_with(1, "milk")


async def test_find_command_without_query_shows_usage() -> None:
    reminder_dao = SimpleNamespace(search_user_reminders=AsyncMock())
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    state = SimpleNamespace(clear=AsyncMock())
    message = SimpleNamespace(text="/find", answer=AsyncMock())

    await cmd_find(message, state, reminder_dao, user, RU)

    reminder_dao.search_user_reminders.assert_not_awaited()
    message.answer.assert_awaited_once()


@pytest.mark.parametrize(
    "handler,dao_attr",
    [
        (callback_tasks_filter_today, "get_user_reminders_today"),
        (callback_tasks_filter_week, "get_user_reminders_this_week"),
        (callback_tasks_filter_recurring, "get_user_reminders_recurring"),
    ],
)
async def test_filter_callbacks_call_expected_dao_method(handler, dao_attr) -> None:
    task = SimpleNamespace(
        id=1, reminder_text="Task", execution_time=datetime.now(timezone.utc).replace(tzinfo=None),
        is_recurring=False, is_nagging=False,
    )
    reminder_dao = SimpleNamespace(**{dao_attr: AsyncMock(return_value=[task])})
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await handler(callback, reminder_dao, user, RU)

    getattr(reminder_dao, dao_attr).assert_awaited_once()
    message.edit_text.assert_awaited_once()


async def test_filter_overdue_uses_zero_minute_threshold() -> None:
    reminder_dao = SimpleNamespace(get_overdue_pending_tasks=AsyncMock(return_value=[]))
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await callback_tasks_filter_overdue(callback, reminder_dao, user, RU)

    reminder_dao.get_overdue_pending_tasks.assert_awaited_once_with(1, min_minutes_overdue=0)


async def test_tags_menu_shows_no_tags_alert_when_empty() -> None:
    reminder_dao = SimpleNamespace(get_distinct_tags=AsyncMock(return_value=[]))
    user = SimpleNamespace(id=1)
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await callback_tasks_tags_menu(callback, reminder_dao, user, RU)

    callback.answer.assert_awaited_once()
    message.edit_text.assert_not_awaited()


async def test_tags_menu_renders_tag_buttons() -> None:
    reminder_dao = SimpleNamespace(get_distinct_tags=AsyncMock(return_value=["дом", "работа"]))
    user = SimpleNamespace(id=1)
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await callback_tasks_tags_menu(callback, reminder_dao, user, RU)

    message.edit_text.assert_awaited_once()
    markup = message.edit_text.await_args.kwargs["reply_markup"]
    callback_datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "tasks_tag:дом" in callback_datas
    assert "tasks_tag:работа" in callback_datas


async def test_filter_by_tag_renders_results() -> None:
    task = SimpleNamespace(
        id=1, reminder_text="Buy milk", execution_time=datetime.now(timezone.utc).replace(tzinfo=None),
        is_recurring=False, is_nagging=False, tags="дом", priority=None,
    )
    reminder_dao = SimpleNamespace(get_reminders_by_tag=AsyncMock(return_value=[task]))
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(data="tasks_tag:дом", message=message, answer=AsyncMock())

    await callback_tasks_filter_by_tag(callback, reminder_dao, user, RU)

    reminder_dao.get_reminders_by_tag.assert_awaited_once_with(1, "дом")
    message.edit_text.assert_awaited_once()


async def test_filter_by_tag_no_results() -> None:
    reminder_dao = SimpleNamespace(get_reminders_by_tag=AsyncMock(return_value=[]))
    user = SimpleNamespace(id=1, timezone="UTC", show_utc_offset=False)
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(data="tasks_tag:nonexistent", message=message, answer=AsyncMock())

    await callback_tasks_filter_by_tag(callback, reminder_dao, user, RU)

    message.edit_text.assert_awaited_once_with(RU.get("find_no_results_filter"), reply_markup=None)


def test_filtered_tasks_keyboard_has_close_and_back_buttons() -> None:
    task = SimpleNamespace(id=5, reminder_text="Task")
    markup = get_filtered_tasks_keyboard([task], RU)
    callback_datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "tasks_page_0" in callback_datas
    assert "close_tasks" in callback_datas
    assert "done_task_5" in callback_datas
