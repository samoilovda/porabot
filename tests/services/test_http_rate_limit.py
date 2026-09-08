"""Regression (fix 3.1): /ics/ and /api/miniapp/* had no rate limit at all
— handle_ics_feed opens a DB session and runs a SELECT on every request,
including ones with an invalid token, so unauthenticated traffic could load
the DB directly. Uses aiohttp's own test client, same as test_webserver.py."""

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.engine import Base
from bot.services.webserver import HttpRateLimiter, create_app

BOT_TOKEN = "123456:AAFake-Bot-Token-For-Tests"


@pytest.fixture
async def session_pool():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture
async def tight_client(session_pool):
    """Same app as production, but with a 2-requests-per-window limiter so
    the test doesn't need to fire 31+ requests or fake elapsed time."""
    app = create_app(session_pool, bot_token=BOT_TOKEN)
    app["http_rate_limiter"] = HttpRateLimiter(max_requests=2, window_seconds=60.0)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    yield test_client
    await test_client.close()


async def test_ics_feed_is_rate_limited_per_ip(tight_client) -> None:
    for _ in range(2):
        resp = await tight_client.get("/ics/nonexistent-token.ics")
        assert resp.status == 404  # under the limit: normal 404, not throttled

    resp = await tight_client.get("/ics/nonexistent-token.ics")
    assert resp.status == 429


async def test_miniapp_api_is_rate_limited_per_ip(tight_client) -> None:
    for _ in range(2):
        resp = await tight_client.get("/api/miniapp/scores")
        assert resp.status == 401  # under the limit: normal auth rejection

    resp = await tight_client.get("/api/miniapp/scores")
    assert resp.status == 429


async def test_healthz_and_miniapp_static_are_not_rate_limited(session_pool) -> None:
    """The rate limit is scoped to the routes that touch the DB /
    authenticate on every hit — healthz and the static Mini App frontend
    don't, so they must not be throttled by the same counter."""
    app = create_app(session_pool, bot_token=BOT_TOKEN)
    app["http_rate_limiter"] = HttpRateLimiter(max_requests=1, window_seconds=60.0)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        for _ in range(5):
            resp = await client.get("/healthz")
            assert resp.status == 200
    finally:
        await client.close()


def test_http_rate_limiter_cleanup_expired_drops_stale_keys() -> None:
    limiter = HttpRateLimiter(max_requests=5, window_seconds=0.01)
    assert limiter.allow("1.2.3.4") is True
    assert "1.2.3.4" in limiter._hits

    import time

    time.sleep(0.02)
    limiter.cleanup_expired()

    assert "1.2.3.4" not in limiter._hits
