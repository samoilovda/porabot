"""3.1: request.remote is the raw TCP peer address — behind a reverse
proxy (the normal deployment shape per config.PUBLIC_BASE_URL's own
docstring), that's always the proxy itself, so the per-IP rate limiter
effectively shared one key across every real visitor. With
TRUSTED_PROXY=True, the key comes from X-Forwarded-For's first entry
instead; left False (the default), a client-supplied header must never be
trusted, since any client could just claim to BE any IP.
"""

from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database import models  # noqa: F401
from bot.database.engine import Base
from bot.services.webserver import HTTP_RATE_LIMITER_KEY, HttpRateLimiter, create_app

BOT_TOKEN = "123456:AAFake-Bot-Token-For-Tests"


async def _make_client(*, trusted_proxy: bool):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_pool = async_sessionmaker(engine, expire_on_commit=False)

    app = create_app(session_pool, bot_token=BOT_TOKEN, trusted_proxy=trusted_proxy)
    app[HTTP_RATE_LIMITER_KEY] = HttpRateLimiter(max_requests=2, window_seconds=60.0)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    return client, engine


async def test_distinct_forwarded_for_ips_get_separate_limits_when_trusted() -> None:
    client, engine = await _make_client(trusted_proxy=True)
    try:
        headers_a = {"X-Forwarded-For": "1.1.1.1"}
        headers_b = {"X-Forwarded-For": "2.2.2.2"}

        # Exhaust client A's 2-request budget.
        for _ in range(2):
            resp = await client.get("/ics/nonexistent.ics", headers=headers_a)
            assert resp.status == 404
        blocked = await client.get("/ics/nonexistent.ics", headers=headers_a)
        assert blocked.status == 429

        # Client B, a different forwarded IP, is unaffected.
        still_ok = await client.get("/ics/nonexistent.ics", headers=headers_b)
        assert still_ok.status == 404
    finally:
        await client.close()
        await engine.dispose()


async def test_client_cannot_spoof_the_limit_by_prefixing_a_fake_chain_entry() -> None:
    """docs/audits/2026-09-22-audit.md#a-15 — nginx/Caddy's own
    proxy_add_x_forwarded_for APPENDS the real peer address to whatever
    chain the client already sent; a client sitting in front of the proxy
    controls every entry except that last, proxy-appended one. Trusting
    the FIRST entry (the old behavior) let a client dodge the limit
    outright by sending a fresh fake value up front on every request —
    only the LAST entry is the proxy's own and actually trustworthy."""
    client, engine = await _make_client(trusted_proxy=True)
    try:
        for i in range(2):
            # A different claimed client IP up front every time, but the
            # SAME real address in the position the trusted proxy itself
            # would have appended.
            resp = await client.get(
                "/ics/nonexistent.ics", headers={"X-Forwarded-For": f"{i}.{i}.{i}.{i}, 5.5.5.5"}
            )
            assert resp.status == 404
        blocked = await client.get(
            "/ics/nonexistent.ics", headers={"X-Forwarded-For": "9.9.9.9, 5.5.5.5"}
        )
        assert blocked.status == 429
    finally:
        await client.close()
        await engine.dispose()


async def test_forwarded_for_is_ignored_when_proxy_not_trusted() -> None:
    """The default (TRUSTED_PROXY=False): a client-supplied
    X-Forwarded-For must not let it dodge the limit by claiming a fresh
    IP on every request — the real TCP peer (the same one every time,
    from the test client) is what's actually keyed on."""
    client, engine = await _make_client(trusted_proxy=False)
    try:
        for i in range(2):
            resp = await client.get("/ics/nonexistent.ics", headers={"X-Forwarded-For": f"{i}.{i}.{i}.{i}"})
            assert resp.status == 404
        # A THIRD request, with yet another claimed IP, is still blocked —
        # proving the header was never actually consulted.
        blocked = await client.get("/ics/nonexistent.ics", headers={"X-Forwarded-For": "9.9.9.9"})
        assert blocked.status == 429
    finally:
        await client.close()
        await engine.dispose()
