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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pytz
from aiohttp import web

from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.dao.user import UserDAO
from bot.services.habit_reports import compute_habit_score
from bot.services.ics_feed import build_ics_calendar
from bot.services.miniapp import build_heatmap_payload, build_scores_payload, validate_init_data

logger = logging.getLogger(__name__)

_WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"
_MINIAPP_HEATMAP_DEFAULT_DAYS = 90
_MINIAPP_HEATMAP_MAX_DAYS = 365


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


# ---------------------------------------------------------------------------
# 4.6: Mini App — static frontend
# ---------------------------------------------------------------------------
# Hand-registered per file (not aiohttp's add_static) since there are only
# three and this keeps content-type + 404 behavior explicit and easy to
# test, no directory-traversal surface to think about.

_MINIAPP_STATIC_FILES = {
    "index.html": "text/html",
    "app.js": "application/javascript",
    "style.css": "text/css",
}


def _make_static_handler(filename: str, content_type: str):
    async def handler(request: web.Request) -> web.Response:
        path = _WEBAPP_DIR / filename
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            return web.Response(status=404, text="Not found")
        return web.Response(text=body, content_type=content_type, charset="utf-8")

    return handler


# ---------------------------------------------------------------------------
# 4.6: Mini App — JSON API, authenticated via Telegram initData
# ---------------------------------------------------------------------------

async def _authenticate_miniapp_request(request: web.Request, bot_token: str) -> Optional[int]:
    """Return the validated Telegram user id for this request, or None.

    initData travels in the X-Telegram-Init-Data header (this API's own
    contract, set by bot/webapp/app.js) — never trust a user id from the
    query string or a request body, only the id embedded in initData whose
    HMAC signature checks out against the bot's own token.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    validated = validate_init_data(init_data, bot_token)
    if validated is None:
        return None
    return validated["user_id"]


async def handle_miniapp_scores(request: web.Request) -> web.Response:
    user_id = await _authenticate_miniapp_request(request, request.app["bot_token"])
    if user_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    session_pool = request.app["session_pool"]
    async with session_pool() as session:
        reminder_dao = ReminderDAO(session)
        habit_event_dao = HabitEventDAO(session)
        habits = await reminder_dao.get_active_habits(user_id)
        habits_with_events = []
        for habit in habits:
            events = await habit_event_dao.get_events_for_reminder(habit.id)
            habits_with_events.append((habit, events))

    payload = build_scores_payload(habits_with_events, compute_habit_score)
    return web.json_response(payload)


async def handle_miniapp_heatmap(request: web.Request) -> web.Response:
    user_id = await _authenticate_miniapp_request(request, request.app["bot_token"])
    if user_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        days = int(request.query.get("days", str(_MINIAPP_HEATMAP_DEFAULT_DAYS)))
    except ValueError:
        days = _MINIAPP_HEATMAP_DEFAULT_DAYS
    days = max(1, min(days, _MINIAPP_HEATMAP_MAX_DAYS))

    session_pool = request.app["session_pool"]
    async with session_pool() as session:
        user_dao = UserDAO(session)
        user = await user_dao.get_by_id(user_id)
        if user is None:
            return web.json_response({"error": "unknown user"}, status=404)
        try:
            tz = pytz.timezone(user.timezone)
        except Exception:
            tz = pytz.UTC
        today_local = datetime.now(tz).date()
        start_local = today_local - timedelta(days=days - 1)

        habit_event_dao = HabitEventDAO(session)
        events = await habit_event_dao.get_events_in_range(
            user_id, start_local.isoformat(), today_local.isoformat()
        )

    payload = build_heatmap_payload(events, start_local, today_local)
    return web.json_response(payload)


def create_app(session_pool, bot_token: str = "") -> web.Application:
    """Build the shared aiohttp Application. *session_pool* is the same
    async_sessionmaker bot/__main__.py hands to DatabaseMiddleware — routes
    open their own short-lived session per request, same pattern as
    background jobs (see bot/services/*.py). *bot_token* signs/validates
    Mini App initData (4.6) — optional so 4.4-only callers (and tests that
    don't touch the Mini App routes) don't need to supply one."""
    app = web.Application()
    app["session_pool"] = session_pool
    app["bot_token"] = bot_token
    app.router.add_get("/healthz", handle_healthz)
    app.router.add_get("/ics/{token}.ics", handle_ics_feed)

    for filename, content_type in _MINIAPP_STATIC_FILES.items():
        route = "/miniapp" if filename == "index.html" else f"/miniapp/{filename}"
        app.router.add_get(route, _make_static_handler(filename, content_type))
    app.router.add_get("/miniapp/", _make_static_handler("index.html", "text/html"))

    app.router.add_get("/api/miniapp/scores", handle_miniapp_scores)
    app.router.add_get("/api/miniapp/heatmap", handle_miniapp_heatmap)
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
