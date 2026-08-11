"""4.4/4.6: aiohttp routes (bot/services/webserver.py) — uses aiohttp's own
test client (aiohttp.test_utils) rather than hand-rolled HTTP mocking."""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.engine import Base
from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.dao.user import UserDAO
from bot.database.models import User
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
    yield maker
    await engine.dispose()


@pytest.fixture
async def client(session_pool):
    app = create_app(session_pool, bot_token=BOT_TOKEN)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    yield test_client
    await test_client.close()


async def test_healthz_returns_ok(client) -> None:
    resp = await client.get("/healthz")
    assert resp.status == 200
    assert await resp.text() == "ok"


async def test_ics_feed_unknown_token_returns_404(client) -> None:
    resp = await client.get("/ics/nonexistent-token.ics")
    assert resp.status == 404


async def test_ics_feed_valid_token_returns_calendar(session_pool, client) -> None:
    async with session_pool() as session:
        session.add(User(id=1, username="u", timezone="UTC"))
        await session.flush()
        user_dao = UserDAO(session)
        token = await user_dao.ensure_ics_feed_token(1)

        reminder_dao = ReminderDAO(session)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        await reminder_dao.create_reminder(user_id=1, text="Buy milk", execution_time=now + timedelta(hours=1))
        await session.commit()

    resp = await client.get(f"/ics/{token}.ics")
    assert resp.status == 200
    assert resp.content_type == "text/calendar"
    body = await resp.text()
    assert "BEGIN:VCALENDAR" in body
    assert "SUMMARY:Buy milk" in body


async def test_ics_feed_regenerated_token_invalidates_old_link(session_pool, client) -> None:
    async with session_pool() as session:
        session.add(User(id=1, username="u", timezone="UTC"))
        await session.flush()
        user_dao = UserDAO(session)
        old_token = await user_dao.ensure_ics_feed_token(1)
        new_token = await user_dao.regenerate_ics_feed_token(1)
        await session.commit()

    assert old_token != new_token
    old_resp = await client.get(f"/ics/{old_token}.ics")
    assert old_resp.status == 404
    new_resp = await client.get(f"/ics/{new_token}.ics")
    assert new_resp.status == 200


# ---------------------------------------------------------------------------
# 4.6: Mini App static frontend + JSON API
# ---------------------------------------------------------------------------

async def test_miniapp_index_serves_html(client) -> None:
    resp = await client.get("/miniapp")
    assert resp.status == 200
    assert "text/html" in resp.content_type
    body = await resp.text()
    assert "<title>Porabot</title>" in body


async def test_miniapp_static_assets_served_with_correct_content_type(client) -> None:
    js_resp = await client.get("/miniapp/app.js")
    assert js_resp.status == 200
    assert "javascript" in js_resp.content_type

    css_resp = await client.get("/miniapp/style.css")
    assert css_resp.status == 200
    assert "css" in css_resp.content_type


async def test_miniapp_scores_rejects_missing_init_data(client) -> None:
    resp = await client.get("/api/miniapp/scores")
    assert resp.status == 401


async def test_miniapp_scores_rejects_invalid_init_data(client) -> None:
    resp = await client.get("/api/miniapp/scores", headers={"X-Telegram-Init-Data": "garbage"})
    assert resp.status == 401


async def test_miniapp_scores_returns_active_habits_for_authenticated_user(session_pool, client) -> None:
    async with session_pool() as session:
        session.add(User(id=7, username="u", timezone="UTC"))
        await session.flush()
        reminder_dao = ReminderDAO(session)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        habit = await reminder_dao.create_reminder(
            user_id=7, text="Meditate", execution_time=now + timedelta(hours=1),
            is_habit=True, is_recurring=True, rrule_string="FREQ=DAILY",
        )
        await session.commit()
        habit_id = habit.id

    init_data = _signed_init_data(7)
    resp = await client.get("/api/miniapp/scores", headers={"X-Telegram-Init-Data": init_data})
    assert resp.status == 200
    payload = await resp.json()
    assert payload["habits"] == [
        {"id": habit_id, "text": "Meditate", "score": 0, "streak_current": 0, "streak_best": 0}
    ]


async def test_miniapp_heatmap_rejects_missing_init_data(client) -> None:
    resp = await client.get("/api/miniapp/heatmap")
    assert resp.status == 401


async def test_miniapp_heatmap_returns_day_range_for_authenticated_user(session_pool, client) -> None:
    async with session_pool() as session:
        session.add(User(id=8, username="u", timezone="UTC"))
        await session.flush()
        await session.commit()

    init_data = _signed_init_data(8)
    resp = await client.get("/api/miniapp/heatmap?days=7", headers={"X-Telegram-Init-Data": init_data})
    assert resp.status == 200
    payload = await resp.json()
    assert len(payload["days"]) == 7
