"""
Application configuration via pydantic-settings v2.

All environment variables are validated and typed at import time.
Usage anywhere in the project:
    from bot.config import config
    
BUG FIX APPLIED (Phase 1):
  ✅ Added runtime validation for missing ADMIN_ID/ALLOWED_USERS
  ✅ Added warning if DATABASE_URL uses default value
"""

import logging

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Strict, validated application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Telegram ---
    BOT_TOKEN: SecretStr
    
    # Admin ID - if 0 and ALLOWED_USERS is empty, bot has no access control
    ADMIN_ID: int = 0
    
    # Whitelisted user IDs (empty list means only admin can use bot)
    ALLOWED_USERS: list[int] = []

    # --- Database ---
    DATABASE_URL: str = "sqlite+aiosqlite:///porabot.db"
    SCHEDULER_DB_URL: str = "sqlite:///jobs.sqlite"

    # --- Locale ---
    TZ: str = "UTC"

    # --- Health ---
    # Touched every minute by a periodic job (see bot/__main__.py) so
    # docker-compose's healthcheck can tell "process alive" apart from
    # "process alive but polling died/hung" — restart: always only reacts
    # to the former. Relative by default for local (non-Docker) runs;
    # docker-compose.yml points it at the same mounted volume as the DB
    # files so the directory is guaranteed to already exist and be writable.
    HEARTBEAT_FILE: str = "heartbeat"


# Module-level singleton
config = Settings()


# =============================================================================
# RUNTIME VALIDATION (Added in Phase 1)
# =============================================================================

def validate_config():
    """
    Validate configuration at runtime.
    
    This function checks for common misconfigurations and logs warnings.
    Call this after loading config but before starting the bot.
    
    BUG FIX APPLIED:
      Previously no validation was done - now warns about missing admin/users.
      
    EXAMPLE USAGE (from __main__.py):
        >>> from bot.config import validate_config
        >>> validate_config()  # Check for misconfigurations
    """
    
    # Check if ADMIN_ID is 0 and ALLOWED_USERS is empty
    if config.ADMIN_ID == 0 and not config.ALLOWED_USERS:
        logger.warning(
            "No admin or allowed users configured. WhitelistMiddleware is "
            "currently disabled in __main__.py, so the bot has NO access "
            "control — anyone can use it. Set ADMIN_ID in .env and re-enable "
            "WhitelistMiddleware to restrict access."
        )

    # Check if DATABASE_URL uses default value (production warning)
    if config.DATABASE_URL == "sqlite+aiosqlite:///porabot.db":
        logger.warning(
            "Using default SQLite database URL. For production, set "
            "DATABASE_URL in .env file."
        )

# Export for use in __main__.py
__all__ = ["config", "validate_config"]