"""
Porabot — Composition Root.

Wires:  config → database → middleware → routers → scheduler → polling.
No business logic lives here.
"""

import asyncio
import logging
import signal
import sys

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
from bot.handlers import all_routers
from bot.services.scheduler import SchedulerService
from bot.services.daily_briefs import setup_daily_briefs
from bot.services.missed_recovery import setup_missed_task_recovery
from bot.services.habit_sweeper import setup_habit_sweeper
from bot.services.habit_reports import setup_habit_reports
from bot.handlers.reminders import _cleanup_stale_timers


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
        ]
        await bot.set_my_commands(commands, language_code=lang)


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
    scheduler.add_job(
        _cleanup_stale_timers,
        "interval",
        minutes=10,
        id="cleanup_stale_timers",
        replace_existing=True,
    )
    logger.info("Scheduler configured.")

    # Middleware — whitelist intentionally disabled (open access).
    # To re-enable, restore WhitelistMiddleware registration before DatabaseMiddleware.
    # dp.update.middleware(WhitelistMiddleware(allowed_users=config.ALLOWED_USERS, admin_id=config.ADMIN_ID))
    dp.update.middleware(DatabaseMiddleware(session_pool=session_pool))

    # Routers
    for router in all_routers:
        dp.include_router(router)

    # Inject services into handler kwargs
    dp.workflow_data.update({"scheduler_service": scheduler_service, "config": config})

    scheduler.start()
    await scheduler_service.reconcile_jobs_with_db()
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
        polling_task = asyncio.create_task(dp.start_polling(bot))
        await stop_event.wait()
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    finally:
        try:
            await bot.session.close()
        except Exception as e:
            logger.warning("Error closing Telegram session: %s", e)

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
