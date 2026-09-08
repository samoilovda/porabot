"""Regression (fix 3.2): handle_miniapp_scores (bot/services/webserver.py)
used to call HabitEventDAO.get_events_for_reminder once per active habit
— an N+1 that turned "20 habits" into 20 SELECTs on habit_events per HTTP
request. Counts actual SELECTs on habit_events via a SQLAlchemy engine
event hook (the plan's own suggested verification method) rather than
guessing at call counts."""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.engine import Base
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import User
from bot.services.habit_reports import compute_habit_score
from bot.services.webserver import create_app

BOT_TOKEN = "123456:AAFake-Bot-Token-For-Tests"


def _signed_init_data(user_id: int, bot_token: str = BOT_TOKEN) -> str:
    fields = {
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
        "auth_date": str(int(time.time())),
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture
async def session_pool():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker, engine
    await engine.dispose()


@pytest.fixture
async def client(session_pool):
    maker, _ = session_pool
    app = create_app(maker, bot_token=BOT_TOKEN)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    yield test_client
    await test_client.close()


async def _seed_habits_with_events(session_pool, n_habits: int = 5) -> int:
    maker, _ = session_pool
    async with maker() as session:
        session.add(User(id=9, username="u", timezone="UTC"))
        await session.flush()
        reminder_dao = ReminderDAO(session)
        habit_event_dao = HabitEventDAO(session)
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        for i in range(n_habits):
            habit = await reminder_dao.create_reminder(
                user_id=9, text=f"Habit {i}", execution_time=now + timedelta(hours=1),
                is_habit=True, is_recurring=True, rrule_string="FREQ=DAILY",
            )
            for day in range(3):
                await habit_event_dao.record(
                    reminder=habit, user_tz="UTC", outcome="done", source="button",
                    local_date=(now.date() - timedelta(days=day)).isoformat(),
                )
        await session.commit()
    return n_habits


async def test_scores_endpoint_issues_one_select_on_habit_events_for_five_habits(
    session_pool, client
) -> None:
    await _seed_habits_with_events(session_pool, n_habits=5)
    _, engine = session_pool

    select_count = 0

    def _count_habit_event_selects(conn, cursor, statement, parameters, context, executemany):
        nonlocal select_count
        if "habit_events" in statement and statement.strip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count_habit_event_selects)
    try:
        init_data = _signed_init_data(9)
        resp = await client.get("/api/miniapp/scores", headers={"X-Telegram-Init-Data": init_data})
        assert resp.status == 200
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count_habit_event_selects)

    assert select_count == 1, f"expected exactly one SELECT on habit_events, got {select_count}"


async def test_scores_endpoint_values_unchanged_by_batching(session_pool, client) -> None:
    """The batched query must produce the same scores as the old per-habit
    loop — this is a pure perf change, not a behavior change."""
    await _seed_habits_with_events(session_pool, n_habits=3)
    maker, _ = session_pool

    # Compute expected scores the OLD (per-habit) way, directly against the DAO.
    async with maker() as session:
        reminder_dao = ReminderDAO(session)
        habit_event_dao = HabitEventDAO(session)
        habits = await reminder_dao.get_active_habits(9)
        expected = {}
        for habit in habits:
            events = await habit_event_dao.get_events_for_reminder(habit.id)
            expected[habit.id] = compute_habit_score(events)

    init_data = _signed_init_data(9)
    resp = await client.get("/api/miniapp/scores", headers={"X-Telegram-Init-Data": init_data})
    payload = await resp.json()

    actual = {h["id"]: h["score"] for h in payload["habits"]}
    assert actual == expected
