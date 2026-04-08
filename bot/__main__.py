"""
Porabot — Composition Root (Application Entry Point)
=====================================================

This file is the **composition root** of the application. It wires all layers together:
config → database engine → middleware → routers → scheduler → polling.

⚠️ NO BUSINESS LOGIC lives here! This is purely infrastructure/setup code.

Architecture Overview:
----------------------
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│   Config    │────▶│  Database    │────▶│ Middleware Chain │
│ (pydantic)  │     │ (engine + DB) │     │ whitelist → DI  │
└─────────────┘     └──────────────┘     └─────────────────┘
                                              ↓
                                    ┌─────────────────────────┐
                                    │   Dispatcher + Routers  │
                                    │   (command handlers)     │
                                    └─────────────────────────┘
                                              ↓
                                    ┌─────────────────────────┐
                                    │   Scheduler Service     │
                                    │   (APScheduler jobs)    │
                                    └─────────────────────────┘

Usage:
  python -m bot          # Recommended entry point
  python bot/__main__.py # Alternative

Environment Variables (.env file):
  BOT_TOKEN              Telegram Bot API token (required)
  ADMIN_ID               Your Telegram user ID (whitelisted admin)
  ALLOWED_USERS          List of additional whitelisted user IDs
  TZ                     User timezone default (e.g., "Europe/Moscow")
  DATABASE_URL           SQLite connection string for task data
  SCHEDULER_DB_URL       SQLite connection for APScheduler jobs

Author: Porabot Team
"""

import asyncio
import logging
import signal
import sys
from typing import Any, Dict

# aiogram is the async Telegram Bot API framework (v3.x)
from aiogram import Bot, Dispatcher
# DefaultBotProperties sets default behavior for all bot messages
from aiogram.client.default import DefaultBotProperties
# ParseMode.MARKDOWN allows rich text formatting in messages
from aiogram.enums import ParseMode

# APScheduler is a job scheduling library (cron-like tasks)
import pickle
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore

# Configuration loaded from .env file via pydantic-settings
from bot.config import config, validate_config

# Validate configuration at startup (BUG FIX APPLIED)
validate_config()

# Database engine and session management (FIXED: Added cleanup imports)
from bot.database.engine import (
    create_engine,
    create_session_maker,
    init_db,
    dispose_engine,
    close_session_pool,
)

# Middleware classes that run before every handler
from bot.middlewares.database import DatabaseMiddleware
from bot.middlewares.whitelist import WhitelistMiddleware

# All route handlers (commands, reminders, settings, etc.)
from bot.handlers import all_routers

# Scheduler service manages APScheduler job lifecycle
from bot.services.scheduler import SchedulerService

# Daily briefs service for morning/evening summaries
from bot.services.daily_briefs import setup_daily_briefs


# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================

# Configure logging with timestamp, logger name, and log level
logging.basicConfig(
    level=logging.INFO,  # Show INFO and above (DEBUG, WARNING, ERROR, CRITICAL)
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",  # Human-readable format
)

# Get the root logger for this module
logger = logging.getLogger(__name__)


# =============================================================================
# SIGNAL HANDLERS FOR GRACEFUL SHUTDOWN
# =============================================================================

# Set up signal handlers for graceful shutdown (Ctrl+C, container termination)
signal.signal(signal.SIGINT, lambda signum, frame: sys.exit(0))
signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))


# =============================================================================
# MAIN APPLICATION LIFECYCLE FUNCTION
# =============================================================================

async def main() -> None:
    """
    Main application lifecycle function.
    
    This orchestrates the entire bot startup and shutdown sequence.
    It follows a clear pattern: Initialize → Start → Handle Requests → Shutdown
    
    Lifecycle Phases:
      1. Load configuration (already validated at import time)
      2. Set up database connection and create tables
      3. Create Telegram Bot instance with token from config
      4. Configure APScheduler for recurring tasks
      5. Register middleware (access control → dependency injection)
      6. Mount all route handlers to dispatcher
      7. Start scheduler and begin polling Telegram API
      
    Args:
        None - reads configuration from global 'config' object
        
    Returns:
        None - runs until interrupted or bot stops cleanly
        
    Raises:
        Various exceptions that get logged and handled by finally block
    """
    
    # --------------------------------------------------------------------------
    # PHASE 1: Configuration (already validated at import time)
    # --------------------------------------------------------------------------
    logger.info(f"Starting Porabot (TZ={config.TZ})")

    # --------------------------------------------------------------------------
    # PHASE 2: Database Initialization
    # --------------------------------------------------------------------------
    
    engine = create_engine(config.DATABASE_URL)
    session_pool = create_session_maker(engine)
    await init_db(engine)
    logger.info("Database initialized.")

    # --------------------------------------------------------------------------
    # PHASE 3: Create Telegram Bot Instance
    # --------------------------------------------------------------------------
    
    bot = Bot(
        token=config.BOT_TOKEN.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    
    dp = Dispatcher()

    # --------------------------------------------------------------------------
    # PHASE 4: Scheduler Setup (for recurring tasks)
    # --------------------------------------------------------------------------
    
    scheduler = AsyncIOScheduler(
        jobstores={"default": MemoryJobStore()},   # In-memory storage (no pickle issues)
        pickle_protocol=pickle.HIGHEST_PROTOCOL,      # Best pickling protocol for args
    )
    
    scheduler_service = SchedulerService(
        scheduler, 
        bot, 
        session_pool, 
        config.BOT_TOKEN.get_secret_value()  # Pass token string (picklable), not Bot instance
    )

    # --------------------------------------------------------------------------
    # PHASE 4.5: Daily Briefs Setup (for morning/evening summaries)
    # --------------------------------------------------------------------------
    
    setup_daily_briefs(scheduler, config.BOT_TOKEN.get_secret_value(), session_pool)
    logger.info("Daily briefs scheduler registered.")

    # --------------------------------------------------------------------------
    # PHASE 4.5: Register Periodic Cleanup Job for Memory Management
    # --------------------------------------------------------------------------
    
    from bot.handlers.reminders import _cleanup_stale_timers
    
    scheduler.add_job(
        _cleanup_stale_timers,
        "interval",
        minutes=1,
        id="cleanup_active_delete_tasks",
        replace_existing=True,
    )
    logger.info("Registered periodic cleanup job for auto-delete task tracker.")

    # --------------------------------------------------------------------------
    # PHASE 5: Middleware Registration (Access Control & Dependency Injection)
    # --------------------------------------------------------------------------
    
    dp.update.middleware(
        WhitelistMiddleware(allowed_users=config.ALLOWED_USERS, admin_id=config.ADMIN_ID)
    )
    
    dp.update.middleware(DatabaseMiddleware(session_pool=session_pool))

    # --------------------------------------------------------------------------
    # PHASE 6: Register All Route Handlers
    # --------------------------------------------------------------------------
    
    for router in all_routers:
        dp.include_router(router)

    # --------------------------------------------------------------------------
    # PHASE 7: Inject Services into Workflow Data (Dependency Injection)
    # --------------------------------------------------------------------------
    
    dp.workflow_data.update(
        {
            "scheduler_service": scheduler_service,
            "config": config,
        }
    )

    # --------------------------------------------------------------------------
    # PHASE 8: Start the Bot
    # --------------------------------------------------------------------------
    
    scheduler.start()
    logger.info("Scheduler started.")

    try:
        logger.info("Starting polling...")
        await dp.start_polling(bot)
        
    finally:
        try:
            await bot.session.close()
            logger.info("Telegram session closed.")
        except Exception as e:
            logger.warning(f"Error closing Telegram session: {e}")
        
        scheduler.shutdown(wait=False)
        logger.info("Scheduler shutdown complete.")
        
        try:
            await close_session_pool(session_pool)
            logger.info("Database sessions closed.")
        except Exception as e:
            logger.warning(f"Error closing session pool: {e}")
        
        try:
            await dispose_engine(engine)
            logger.info("Database engine disposed.")
        except Exception as e:
            logger.warning(f"Error disposing engine: {e}")
        
        logger.info("Bot stopped cleanly.")


# =============================================================================
# ENTRY POINT FOR DIRECT EXECUTION
# =============================================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())
        
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")