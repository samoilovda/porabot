"""
Porabot — Composition Root.

Wires all layers together: config → engine → middleware → routers → scheduler → polling.
No business logic lives here.

Usage:
    python -m bot
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from bot.config import config
from bot.database.engine import create_engine, create_session_maker, init_db
from bot.middlewares.database import DatabaseMiddleware
from bot.handlers import all_routers
from bot.services.scheduler import SchedulerService
from bot.services.daily_briefs import setup_daily_briefs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # 1. Config — already validated at import time by pydantic-settings
    logger.info(f"Starting Porabot (TZ={config.TZ})")

    # 2. Database
    engine = create_engine(config.DATABASE_URL)
    session_pool = create_session_maker(engine)
    await init_db(engine)
    logger.info("Database initialized.")

    # 3. Bot & Dispatcher
    bot = Bot(
        token=config.BOT_TOKEN.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher()

    # 4. Scheduler
    scheduler = AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=config.SCHEDULER_DB_URL)}
    )
    scheduler_service = SchedulerService(scheduler, bot, session_pool)

    # 4.1 Daily Briefs Cron Check
    setup_daily_briefs(scheduler, bot, session_pool)

    # 5. Middleware (DI of session + DAOs + user)
    dp.update.middleware(DatabaseMiddleware(session_pool=session_pool))

    # 6. Register routers
    for router in all_routers:
        dp.include_router(router)

    # 7. Inject services into workflow_data (available to all handlers)
    dp.workflow_data.update(
        {
            "scheduler_service": scheduler_service,
            "config": config,
        }
    )

    # 8. Start
    scheduler.start()
    logger.info("Scheduler started.")

    try:
        logger.info("Starting polling...")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        scheduler.shutdown(wait=False)
        logger.info("Bot stopped cleanly.")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
