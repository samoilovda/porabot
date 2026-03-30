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
import sys
from typing import Any, Dict

# aiogram is the async Telegram Bot API framework (v3.x)
from aiogram import Bot, Dispatcher
# DefaultBotProperties sets default behavior for all bot messages
from aiogram.client.default import DefaultBotProperties
# ParseMode.MARKDOWN allows rich text formatting in messages
from aiogram.enums import ParseMode

# APScheduler is a job scheduling library (cron-like tasks)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
# SQLAlchemyJobStore stores scheduler jobs in database for persistence (production-ready)
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

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
    # NOTE: Pydantic-settings validates all env vars on module import,
    # so we don't need to validate again here. This is a common pattern!

    # --------------------------------------------------------------------------
    # PHASE 2: Database Initialization
    # --------------------------------------------------------------------------
    
    # Create async SQLAlchemy engine from URL (e.g., "sqlite+aiosqlite:///porabot.db")
    # The 'aiosqlite' dialect enables async operations with SQLite
    engine = create_engine(config.DATABASE_URL)
    
    # Create session factory bound to the engine
    # This is used throughout the app for database access
    session_pool = create_session_maker(engine)
    
    # Initialize database: creates all tables defined in models.py
    # IMPORTANT: Call this ONCE at startup, not on every request!
    await init_db(engine)
    logger.info("Database initialized.")

    # --------------------------------------------------------------------------
    # PHASE 3: Create Telegram Bot Instance
    # --------------------------------------------------------------------------
    
    # Create Bot object with token from environment variable
    bot = Bot(
        token=config.BOT_TOKEN.get_secret_value(),  # SecretStr requires .get_secret_value()
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),  # Default message format
    )
    
    # Create Dispatcher - the central hub for all incoming updates
    dp = Dispatcher()

    # --------------------------------------------------------------------------
    # PHASE 4: Scheduler Setup (for recurring tasks)
    # --------------------------------------------------------------------------
    
    # Create AsyncIOScheduler with SQLAlchemy job store (production-ready)
    # Jobs are persisted in separate SQLite database for durability across restarts
    scheduler = AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=config.SCHEDULER_DB_URL)}
    )
    
    # Create SchedulerService - our facade over APScheduler
    # This service handles all scheduling operations with proper error handling
    scheduler_service = SchedulerService(scheduler, bot, session_pool)

    # --------------------------------------------------------------------------
    # PHASE 4.5: Daily Briefs Setup (for morning/evening summaries)
    # --------------------------------------------------------------------------
    
    # Register hourly cron job for daily briefs (morning at 09:00, evening at 23:00)
    setup_daily_briefs(scheduler, bot, session_pool)
    logger.info("Daily briefs scheduler registered.")

    # --------------------------------------------------------------------------
    # PHASE 5: Middleware Registration (Access Control & Dependency Injection)
    # --------------------------------------------------------------------------
    
    # ⚠️ CRITICAL: Middleware order MATTERS!
    # We register Whitelist BEFORE DatabaseMiddleware to avoid DB overhead for unauthorized users.
    # This is a performance optimization - checking whitelist first saves database queries.
    
    # Register WhitelistMiddleware first (access control)
    dp.update.middleware(
        WhitelistMiddleware(allowed_users=config.ALLOWED_USERS, admin_id=config.ADMIN_ID)
    )
    
    # Then register DatabaseMiddleware (dependency injection for handlers)
    # This injects 'user' and 'session' into every handler's workflow_data dict
    dp.update.middleware(DatabaseMiddleware(session_pool=session_pool))

    # --------------------------------------------------------------------------
    # PHASE 6: Register All Route Handlers
    # --------------------------------------------------------------------------
    
    # Include all routers in the dispatcher
    # Order matters! More specific handlers should come first.
    for router in all_routers:
        dp.include_router(router)

    # --------------------------------------------------------------------------
    # PHASE 7: Inject Services into Workflow Data (Dependency Injection)
    # --------------------------------------------------------------------------
    
    # Make scheduler_service and config available to ALL handlers via workflow_data
    # Handlers can access these without passing them as parameters
    dp.workflow_data.update(
        {
            "scheduler_service": scheduler_service,  # For scheduling new tasks
            "config": config,                        # For accessing configuration values
        }
    )

    # --------------------------------------------------------------------------
    # PHASE 8: Start the Bot
    # --------------------------------------------------------------------------
    
    # Start the scheduler (required for recurring tasks to work)
    scheduler.start()
    logger.info("Scheduler started.")

    try:
        # Start polling Telegram API for incoming updates
        # This is a blocking call that runs until interrupted
        logger.info("Starting polling...")
        await dp.start_polling(bot)
        
    finally:
        # CLEANUP: Always close resources on shutdown (even if error occurred)
        # FIXED Phase 1: Added proper resource cleanup to prevent leaks
        try:
            await bot.session.close()  # Close Telegram API connection
            logger.info("Telegram session closed.")
        except Exception as e:
            logger.warning(f"Error closing Telegram session: {e}")
        
        scheduler.shutdown(wait=False)  # Stop accepting new jobs but let running ones finish
        logger.info("Scheduler shutdown complete.")
        
        try:
            await close_session_pool(session_pool)  # Close all database sessions
            logger.info("Database sessions closed.")
        except Exception as e:
            logger.warning(f"Error closing session pool: {e}")
        
        try:
            await dispose_engine(engine)  # Dispose engine and close connections
            logger.info("Database engine disposed.")
        except Exception as e:
            logger.warning(f"Error disposing engine: {e}")
        
        logger.info("Bot stopped cleanly.")


# =============================================================================
# ENTRY POINT FOR DIRECT EXECUTION
# =============================================================================

if __name__ == "__main__":
    # Production: Skip Windows-specific event loop policy (not needed in Docker/Linux)
    try:
        # Run the async main() function
        asyncio.run(main())
        
    except (KeyboardInterrupt, SystemExit):
        # Handle graceful shutdown on Ctrl+C or explicit exit
        logger.info("Bot stopped by user.")
