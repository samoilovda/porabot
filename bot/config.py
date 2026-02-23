"""
Application configuration via pydantic-settings v2.

All environment variables are validated and typed at import time.
Usage anywhere in the project:
    from bot.config import config
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strict, validated application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Telegram ---
    BOT_TOKEN: SecretStr
    ADMIN_ID: int = 0

    # --- Database ---
    DATABASE_URL: str = "sqlite+aiosqlite:///porabot.db"
    SCHEDULER_DB_URL: str = "sqlite:///jobs.sqlite"

    # --- Locale ---
    TZ: str = "UTC"


# Module-level singleton
config = Settings()
