import os
import logging
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# Load environment variables from .env
load_dotenv()

# --- CONFIGURATION (ENV) ---
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Allow overriding DB paths (essential for Docker volumes)
# Default: Store in current directory
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///porabot.db")
SCHEDULER_DB_URL = os.getenv("SCHEDULER_DB_URL", "sqlite:///jobs.sqlite")

# --- LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    logger.error("BOT_TOKEN is not set! Please check your environment variables.")
    # We don't exit here to allow importing other components, but runtime will fail.

# --- DATABASE SETUP ---
engine = create_async_engine(DATABASE_URL, echo=False)
async_session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

# --- BOT & DISPATCHER ---
# Initialize Bot and Dispatcher here to avoid circular imports.
# Handlers will import 'dp' to register themselves (or use Router and include in main).
# Using Router is better, but 'bot' instance is often needed globally.

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)) if BOT_TOKEN else None
dp = Dispatcher()

# --- SCHEDULER ---
jobstores = {
    'default': SQLAlchemyJobStore(url=SCHEDULER_DB_URL)
}
# Scheduler is global to be accessible from handlers
scheduler = AsyncIOScheduler(jobstores=jobstores)
