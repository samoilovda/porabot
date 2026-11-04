"""3.4: /find search and today/week/overdue/recurring filters over the task
list — ReminderDAO queries plus the /find command and filter callbacks."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.handlers.experience import list_items
from bot.handlers.reminders import cmd_find
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


async def test_search_treats_percent_as_a_literal_character(session) -> None:
    """Regression (fix 2.2): "%" is a LIKE wildcard. Unescaped, a query for
    "скидка 50%" would ALSO match "скидка 500 рублей" (the % matching zero
    or more characters) — a literal % in the search text must only match a
    literal %."""
    dao = ReminderDAO(session)
    session.add(User(id=1, username="u", timezone="UTC"))
    await session.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
    await dao.create_reminder(user_id=1, text="скидка 50%", execution_time=now + timedelta(hours=1))
    await dao.create_reminder(user_id=1, text="скидка 500 рублей", execution_time=now + timedelta(hours=2))

    results = await dao.search_user_reminders(1, "скидка 50%")

    texts = {r.reminder_text for r in results}
    assert texts == {"скидка 50%"}


async def test_search_for_bare_percent_does_not_match_everything(session) -> None:
    """A query of just "%" must not act as "match all" — it should be
    escaped to a literal percent sign, matching only tasks that contain one."""
    dao = ReminderDAO(session)
    session.add(User(id=1, username="u", timezone="UTC"))
    await session.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
    await dao.create_reminder(user_id=1, text="скидка 50%", execution_time=now + timedelta(hours=1))
    await dao.create_reminder(user_id=1, text="no percent here", execution_time=now + timedelta(hours=2))

    results = await dao.search_user_reminders(1, "%")

    texts = {r.reminder_text for r in results}
    assert texts == {"скидка 50%"}


async def test_tag_filter_treats_underscore_as_a_literal_character(session) -> None:
    """Same LIKE-escaping fix (2.2) applied to get_reminders_by_tag's SQL
    narrowing. Note this one does NOT regress on returned results even
    without the fix — get_reminders_by_tag already re-checks exact tag
    membership in Python after the SQL scan (see its docstring), so an
    unescaped "_" only ever widened the *scan*, not the final result set.
    Kept as a correctness pin, not a behavioral regression test."""
    dao = ReminderDAO(session)
    session.add(User(id=1, username="u", timezone="UTC"))
    await session.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
    await dao.create_reminder(user_id=1, text="task one", execution_time=now + timedelta(hours=1), tags="a_b")
    await dao.create_reminder(user_id=1, text="task two", execution_time=now + timedelta(hours=2), tags="axb")

    results = await dao.get_reminders_by_tag(1, "a_b")

    texts = {r.reminder_text for r in results}
    assert texts == {"task one"}


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


def _fake_task(i: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=i, reminder_text=f"Task {i}", execution_time=datetime(2026, 5, 1, 9, 0, 0),
        is_recurring=False, is_nagging=False,
    )




@pytest.mark.parametrize(
    "kind, expected",
    [
        ("today", {"Buy milk", "Daily standup", "Old task"}),
        ("week", {"Buy milk", "Buy bread", "Daily standup"}),
        ("recurring", {"Daily standup"}),
        ("overdue", {"Old task"}),
    ],
)
async def test_live_quick_filters_pick_the_right_tasks(session, kind, expected) -> None:
    """The quick filters the user actually reaches (experience.list_items;
    the legacy filter handlers were unreachable and removed in the
    2026-11-04 audit fixes)."""
    dao = await _seed(session)
    user = await session.get(User, 1)
    items = await list_items(dao, user, {"kind": kind, "query": None})
    texts = {r.reminder_text for r in items}
    if kind in {"today", "week"}:
        # Both depend on the clock: +1h/+2h may cross midnight, and the
        # 1-hour-overdue task counts as "this week" when it's still today.
        assert texts <= expected | {"Old task"}
        assert ("Old task" in texts) if kind == "today" else ("Buy bread" in texts)
    else:
        assert texts == expected
