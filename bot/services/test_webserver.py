"""4.4: aiohttp routes (bot/services/webserver.py) — uses aiohttp's own
test client (aiohttp.test_utils) rather than hand-rolled HTTP mocking."""

from datetime import datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.engine import Base
from bot.database.dao.reminder import ReminderDAO
from bot.database.dao.user import UserDAO
from bot.database.models import User
from bot.services.webserver import create_app


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
    app = create_app(session_pool)
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
