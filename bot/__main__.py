"""
Porabot — Composition Root.

Wires:  config → database → middleware → routers → scheduler → polling.
No business logic lives here.
"""

import asyncio
import logging
import signal
import sys
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

from bot.config import config, validate_config

validate_config()

from bot.database.engine import create_engine, create_session_maker, init_db, dispose_engine
from bot.middlewares.database import DatabaseMiddleware
from bot.middlewares.whitelist import WhitelistMiddleware
from bot.middlewares.rate_limit import RateLimitMiddleware
from bot.handlers import all_routers
from bot.services.scheduler import SchedulerService
from bot.services.daily_briefs import setup_daily_briefs
from bot.services.missed_recovery import setup_missed_task_recovery
from bot.services.habit_sweeper import setup_habit_sweeper
from bot.services.habit_reports import setup_habit_reports
from bot.services.delete_cleanup import setup_delete_cleanup
from bot.services.webserver import create_app, start_web_server
from bot.handlers.reminders import _cleanup_stale_timers


def _write_heartbeat() -> None:
    """Touch config.HEARTBEAT_FILE with the current time (4.4).

    docker-compose's `restart: always` only reacts to the process exiting —
    a polling loop that's alive but hung (deadlocked, stuck on a network
    call that never times out) looks identical to a healthy container from
    the outside. Written at startup and then every minute by a scheduler
    job; docker-compose's healthcheck fails once this file goes stale.
    """
    try:
        with open(config.HEARTBEAT_FILE, "w") as f:
            f.write(str(int(time.time())))
    except OSError as e:
        logger.warning("Failed to write heartbeat file %s: %s", config.HEARTBEAT_FILE, e)


async def _run_until_stopped(polling_coro, stop_event: asyncio.Event) -> None:
    """Run *polling_coro* until either *stop_event* is set or it ends on its own.

    Waiting on stop_event alone (the earlier version of this function) left
    an unhandled exception from polling silently un-retrieved: the process
    would just hang forever with dead polling, Docker would never see a
    non-zero exit, and `restart: always` would never kick in. Waiting on
    FIRST_COMPLETED between the two means a crash surfaces immediately.
    """
    polling_task = asyncio.ensure_future(polling_coro)
    stop_task = asyncio.ensure_future(stop_event.wait())
    done, pending = await asyncio.wait({polling_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in pending:
        try:
            await task
        except asyncio.CancelledError:
            pass

    if polling_task in done and not stop_event.is_set():
        # Polling ended on its own, not via a shutdown signal — surface
        # whatever it raised (or a plain error if it just returned) so the
        # process exits non-zero instead of quietly staying alive but dead.
        exc = polling_task.exception()
        if exc is not None:
            raise exc
        raise RuntimeError("Polling stopped unexpectedly without a shutdown signal.")


async def _set_bot_commands(bot: Bot) -> None:
    """Populate Telegram's command menu (N2) — otherwise /help and /cancel are
    invisible unless a user already knows to type them."""
    from bot.lexicon import get_l10n

    for lang in (None, "en", "ru", "es"):
        l10n = get_l10n(lang)
        commands = [
            BotCommand(command="start", description=l10n.get("cmd_desc_start", "Main menu")),
            BotCommand(command="help", description=l10n.get("cmd_desc_help", "Show help")),
            BotCommand(command="cancel", description=l10n.get("cmd_desc_cancel", "Cancel current action")),
            BotCommand(command="find", description=l10n.get("cmd_desc_find", "Search your tasks")),
            BotCommand(command="donate", description=l10n.get("cmd_desc_donate", "Support Porabot")),
        ]
        await bot.set_my_commands(commands, language_code=lang)


async def _start_web_server_if_enabled(session_pool):
    """fix(1.1): opening an HTTP port is opt-in (WEB_SERVER_ENABLED), not a
    side effect of upgrading an existing install. Returns the runner if
    started, None otherwise — callers must skip cleanup() when None."""
    if not config.WEB_SERVER_ENABLED:
        logger.info("Web server disabled (WEB_SERVER_ENABLED=False)")
        return None
    logger.info(
        "Web server enabled, binding %s:%s", config.WEB_SERVER_HOST, config.WEB_SERVER_PORT
    )
    web_app = create_app(session_pool, bot_token=config.BOT_TOKEN.get_secret_value())
    return await start_web_server(web_app, config.WEB_SERVER_HOST, config.WEB_SERVER_PORT)


async def main() -> None:
    logger.info("Starting Porabot (TZ=%s)", config.TZ)

    # Database
    engine = create_engine(config.DATABASE_URL)
    session_pool = create_session_maker(engine)
    await init_db(engine)
    logger.info("Database initialised.")

    # Telegram bot
    bot = Bot(
        token=config.BOT_TOKEN.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher()
    await _set_bot_commands(bot)

    # Scheduler
    # Note: SQLAlchemyJobStore's own pickle_protocol already defaults to the
    # highest available protocol, so nothing needs to be set here explicitly.
    scheduler = AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=config.SCHEDULER_DB_URL)},
        # Default is 1 second — any downtime (deploy, restart, crash) would make
        # APScheduler silently discard every job whose run_date fell during it.
        job_defaults={"misfire_grace_time": 3600, "coalesce": True},
    )
    scheduler_service = SchedulerService(scheduler, bot, session_pool)
    setup_daily_briefs(scheduler)
    setup_missed_task_recovery(scheduler)
    setup_habit_sweeper(scheduler)
    setup_habit_reports(scheduler)
    setup_delete_cleanup(scheduler)
    scheduler.add_job(
        _cleanup_stale_timers,
        "interval",
        minutes=10,
        id="cleanup_stale_timers",
        replace_existing=True,
    )
    # 4.4: written now so the healthcheck doesn't see a stale/missing file
    # during startup (Dockerfile's schema init, etc.), then kept fresh by
    # the periodic job below.
    _write_heartbeat()
    scheduler.add_job(
        _write_heartbeat,
        "interval",
        minutes=1,
        id="write_heartbeat",
        replace_existing=True,
    )
    logger.info("Scheduler configured.")

    # Middleware — whitelist intentionally disabled (open access is a
    # deliberate product choice), but P1-10: open access with literally no
    # cap meant one user could flood the DB and the CPU-heavy, globally
    # locked NLP parser. RateLimitMiddleware goes first so a throttled
    # update never reaches the DB or the parser.
    # To re-enable the whitelist, restore WhitelistMiddleware registration
    # before DatabaseMiddleware too.
    # dp.update.middleware(WhitelistMiddleware(allowed_users=config.ALLOWED_USERS, admin_id=config.ADMIN_ID))
    rate_limit_middleware = RateLimitMiddleware()
    dp.update.middleware(rate_limit_middleware)
    dp.update.middleware(DatabaseMiddleware(session_pool=session_pool))
    # 3.1: RateLimitMiddleware's per-user hit dict never dropped an entry on
    # its own — a user who sent a handful of messages and never returned
    # left a permanent entry behind. Same cadence as cleanup_stale_timers.
    scheduler.add_job(
        rate_limit_middleware.cleanup_expired,
        "interval",
        minutes=10,
        id="cleanup_rate_limit_hits",
        replace_existing=True,
    )

    # Routers
    for router in all_routers:
        dp.include_router(router)

    # Inject services into handler kwargs
    dp.workflow_data.update({"scheduler_service": scheduler_service, "config": config})

    scheduler.start()
    await scheduler_service.reconcile_jobs_with_db()

    # 4.4/4.6: aiohttp server for the .ics feed and (once MINI_APP_URL is
    # configured) the Mini App — runs alongside long polling, not instead
    # of it. See bot/services/webserver.py's module docstring for scope.
    # Opt-in via WEB_SERVER_ENABLED (see bot/config.py) — opening an HTTP
    # port is a deliberate deployment decision, not a side effect of
    # upgrading an existing install.
    web_runner = await _start_web_server_if_enabled(session_pool)

    logger.info("Starting polling…")

    # Docker sends SIGTERM on `docker compose up -d --build` recreate / `stop`.
    # Python's default disposition for SIGTERM terminates the process
    # immediately — no `finally` blocks, no chance to finish an in-flight
    # brief/reminder send before its "already sent" flag is committed. Convert
    # it into a normal asyncio shutdown instead, so a deploy landing mid-tick
    # can't race with the next tick and send a duplicate.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # add_signal_handler isn't supported on Windows event loops —
            # Ctrl+C there still falls through to the KeyboardInterrupt
            # handler at the bottom of this file.
            pass

    try:
        await _run_until_stopped(dp.start_polling(bot), stop_event)
    finally:
        try:
            await bot.session.close()
        except Exception as e:
            logger.warning("Error closing Telegram session: %s", e)

        if web_runner is not None:
            try:
                await web_runner.cleanup()
            except Exception as e:
                logger.warning("Error shutting down web server: %s", e)

        scheduler.shutdown(wait=False)

        try:
            await dispose_engine(engine)
        except Exception as e:
            logger.warning("Error disposing engine: %s", e)

        logger.info("Bot stopped cleanly.")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
