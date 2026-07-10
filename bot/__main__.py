"""
Porabot — Composition Root.

Wires:  config → database → middleware → routers → scheduler → polling.
No business logic lives here.
"""

import asyncio
import logging
import pickle
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from bot.config import config, validate_config

validate_config()

from bot.database.engine import create_engine, create_session_maker, init_db, dispose_engine
from bot.middlewares.database import DatabaseMiddleware
from bot.middlewares.whitelist import WhitelistMiddleware
from bot.handlers import all_routers
from bot.services.scheduler import SchedulerService
from bot.services.daily_briefs import setup_daily_briefs
from bot.services.missed_recovery import setup_missed_task_recovery

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


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

    # Scheduler
    scheduler = AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=config.SCHEDULER_DB_URL)},
        pickle_protocol=pickle.HIGHEST_PROTOCOL,      # Best pickling protocol for args
        job_defaults={"misfire_grace_time": 3600, "coalesce": True},
    )
    scheduler_service = SchedulerService(scheduler, bot, session_pool)
    setup_daily_briefs(scheduler)
    setup_missed_task_recovery(scheduler)
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

    try:
        await dp.start_polling(bot)
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
