"""Regression test for 2.3: process_missed_task_recovery must not open one
DB session per candidate user just to find out whether their local time
has crossed missed_recovery_time — that used to cost a session-open plus a
SELECT on `users` per candidate, even for users this tick has nothing to
do for (the overwhelming majority, most minutes). Verifies the initial
candidate fetch is exactly one SELECT on `users` regardless of how many
users are enabled, and that behavior — who actually gets a digest — is
unchanged.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import bot.context as context_module
from bot.database import models  # noqa: F401
from bot.database.dao.reminder import ReminderDAO
from bot.database.engine import Base
from bot.database.models import User
from bot.services.missed_recovery import process_missed_task_recovery


@pytest.fixture
async def session_pool():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker, engine
    await engine.dispose()


async def _seed_users(session_pool, *, n_not_due: int, n_due_with_overdue: int) -> list[int]:
    maker, _ = session_pool
    now_utc = datetime.now(timezone.utc)
    due_ids = []
    async with maker() as session:
        # Not-due users: recovery time far in the future (local), so the
        # time-window check rejects them before ever touching a per-user
        # session.
        for i in range(n_not_due):
            uid = 1000 + i
            session.add(
                User(
                    id=uid, username="u", timezone="UTC",
                    missed_recovery_enabled=True, missed_recovery_time="23:59",
                )
            )
        await session.flush()

        # Due users: recovery time already passed today (local), with one
        # genuinely overdue pending task each — these must still receive
        # a digest exactly as before the refactor.
        for i in range(n_due_with_overdue):
            uid = 2000 + i
            session.add(
                User(
                    id=uid, username="u", timezone="UTC",
                    missed_recovery_enabled=True, missed_recovery_time="00:00",
                )
            )
            await session.flush()
            reminder_dao = ReminderDAO(session)
            await reminder_dao.create_reminder(
                user_id=uid, text="Overdue task",
                execution_time=(now_utc - timedelta(hours=2)).replace(tzinfo=None),
            )
            due_ids.append(uid)
        await session.commit()
    return due_ids


async def test_only_one_select_on_users_regardless_of_candidate_count(session_pool, monkeypatch) -> None:
    maker, engine = session_pool
    await _seed_users(session_pool, n_not_due=20, n_due_with_overdue=3)

    fake_bot = AsyncMock()
    fake_instance = type("FakeInstance", (), {})()
    fake_instance.bot = fake_bot
    fake_instance.session_pool = maker
    monkeypatch.setattr(context_module, "_context", fake_instance)

    select_count = 0

    def _count_user_selects(conn, cursor, statement, parameters, context, executemany):
        nonlocal select_count
        if '"users"' in statement.lower().replace("`", '"') or "from users" in statement.lower():
            if statement.strip().upper().startswith("SELECT"):
                select_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count_user_selects)
    try:
        await process_missed_task_recovery()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count_user_selects)

    # 1 for the broad candidate fetch. Before the fix this was
    # 1 + n_candidates (23) — one get_by_id SELECT per candidate.
    assert select_count == 1, f"expected exactly one SELECT on users, got {select_count}"

    # Behavior unchanged: exactly the 3 due-with-overdue users got a digest
    # and their claim recorded.
    assert fake_bot.send_message.await_count == 3
    async with maker() as session:
        result = await session.execute(select(User.id, User.last_missed_recovery_date).where(User.id >= 2000))
        rows = {uid: date for uid, date in result.all()}
    assert all(rows[uid] is not None for uid in rows)


async def test_not_due_users_receive_nothing(session_pool, monkeypatch) -> None:
    maker, engine = session_pool
    await _seed_users(session_pool, n_not_due=5, n_due_with_overdue=0)

    fake_bot = AsyncMock()
    fake_instance = type("FakeInstance", (), {})()
    fake_instance.bot = fake_bot
    fake_instance.session_pool = maker
    monkeypatch.setattr(context_module, "_context", fake_instance)

    await process_missed_task_recovery()

    fake_bot.send_message.assert_not_called()
