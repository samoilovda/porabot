"""4.4/4.6: aiohttp HTTP server run as an asyncio task alongside the bot's
long-polling loop (bot/__main__.py) — the bot has never had an HTTP surface
before this. One aiohttp.web.Application, one server task, shared by:

  - 4.4: GET /ics/<token>.ics — a user's read-only calendar feed.
  - 4.6: GET /miniapp/*        — Mini App static frontend.
        GET /api/miniapp/*    — Mini App JSON endpoints, authenticated via
                                 Telegram's initData HMAC scheme.

aiohttp is already a transitive dependency via aiogram — no new package.
Deliberately NOT deployed publicly by this change: no domain, no TLS, no
reverse proxy. See bot/config.py's PUBLIC_BASE_URL/MINI_APP_URL for what a
human still has to set up before any of this is reachable from the
internet.
"""

import logging

from aiohttp import web

from bot.database.dao.reminder import ReminderDAO
from bot.database.dao.user import UserDAO
from bot.services.ics_feed import build_ics_calendar

logger = logging.getLogger(__name__)


async def handle_healthz(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def handle_ics_feed(request: web.Request) -> web.Response:
    """GET /ics/{token}.ics — the token IS the auth: anyone holding a valid,
    non-revoked feed token can read that one user's calendar, same trust
    model as a Google/Apple Calendar "secret address" subscription link."""
    token = request.match_info.get("token", "")
    session_pool = request.app["session_pool"]
    async with session_pool() as session:
        user_dao = UserDAO(session)
        user = await user_dao.get_by_ics_feed_token(token)
        if user is None:
            return web.Response(status=404, text="Not found")

        reminder_dao = ReminderDAO(session)
        reminders = await reminder_dao.get_active_for_ics(user.id)
        ics_text = build_ics_calendar(user, reminders)

    return web.Response(
        text=ics_text,
        content_type="text/calendar",
        charset="utf-8",
        headers={"Content-Disposition": "inline; filename=porabot.ics"},
    )


def create_app(session_pool) -> web.Application:
    """Build the shared aiohttp Application. *session_pool* is the same
    async_sessionmaker bot/__main__.py hands to DatabaseMiddleware — routes
    open their own short-lived session per request, same pattern as
    background jobs (see bot/services/*.py)."""
    app = web.Application()
    app["session_pool"] = session_pool
    app.router.add_get("/healthz", handle_healthz)
    app.router.add_get("/ics/{token}.ics", handle_ics_feed)
    return app


async def start_web_server(app: web.Application, host: str, port: int) -> web.AppRunner:
    """Start serving *app* and return the AppRunner so the caller can
    `await runner.cleanup()` on shutdown."""
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Web server listening on %s:%d", host, port)
    return runner
