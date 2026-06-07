"""
Application configuration via pydantic-settings v2.

All environment variables are validated and typed at import time.
Usage anywhere in the project:
    from bot.config import config
    
BUG FIX APPLIED (Phase 1):
  ✅ Added runtime validation for missing ADMIN_ID/ALLOWED_USERS
  ✅ Added warning if DATABASE_URL uses default value
"""

from pydantic import SecretStr, field_validator
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
    
    # "open" = anyone can use; "whitelist" = only ADMIN_ID + ALLOWED_USERS
    ACCESS_MODE: str = "open"

    ADMIN_ID: int = 0
    ALLOWED_USERS: list[int] = []

    # --- Database ---
    DATABASE_URL: str = "sqlite+aiosqlite:///porabot.db"
    SCHEDULER_DB_URL: str = "sqlite:///jobs.sqlite"

    # --- Locale ---
    TZ: str = "UTC"


# Module-level singleton
config = Settings()


# =============================================================================
# RUNTIME VALIDATION (Added in Phase 1)
# =============================================================================

import logging as _logging
_cfg_logger = _logging.getLogger(__name__)


def validate_config() -> None:
    """Validate configuration at startup and log actionable warnings."""
    if config.ACCESS_MODE == "whitelist" and config.ADMIN_ID == 0 and not config.ALLOWED_USERS:
        _cfg_logger.warning(
            "ACCESS_MODE=whitelist but neither ADMIN_ID nor ALLOWED_USERS is set — "
            "nobody will be able to use the bot."
        )
    if config.ACCESS_MODE not in ("open", "whitelist"):
        _cfg_logger.warning("Unknown ACCESS_MODE=%r; defaulting to open access.", config.ACCESS_MODE)
    if config.DATABASE_URL == "sqlite+aiosqlite:///porabot.db":
        _cfg_logger.info("Using default SQLite database path (porabot.db).")

# Export for use in __main__.py
__all__ = ["config", "validate_config"]