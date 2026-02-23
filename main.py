import asyncio
import sys

from loader import bot, dp, scheduler, async_session_maker, logger
from database import init_db
from middleware import DbSessionMiddleware
import handlers  # Регистрируем роутеры (импорт модуля выполняет код регистрации, если там есть include_router? Нет)
# В handlers.py создан 'router'. Нам нужно его импортировать и подключить к dp.
from handlers import router as main_router

async def main():
    try:
        # 1. Init DB
        logger.info("Initializing database...")
        await init_db()

        # 2. Middleware
        dp.update.middleware(DbSessionMiddleware(session_pool=async_session_maker))

        # 3. Routers
        # Важно: сначала middleware, потом routers
        dp.include_router(main_router)

        # 4. Start Scheduler
        scheduler.start()
        logger.info("Scheduler started.")

        # 5. Start Polling
        if bot:
            logger.info("Starting polling...")
            await dp.start_polling(bot)
        else:
            logger.critical("Bot not initialized. Check BOT_TOKEN.")

    except Exception as e:
        logger.critical(f"Unhandled exception in main: {e}", exc_info=True)
    finally:
        if bot:
            await bot.session.close()
        # Останавливаем планировщик? Не обязательно, процесс умрет.

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
