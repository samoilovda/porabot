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
    
    # N7: WhitelistMiddleware is intentionally disabled (see bot/__main__.py) —
    # access is open regardless of ADMIN_ID/ALLOWED_USERS, so warn about that
    # directly instead of a conditional check those settings can't fix.
    logger.warning(
        "Access control is disabled: WhitelistMiddleware is commented out in "
        "bot/__main__.py, so ADMIN_ID/ALLOWED_USERS have no effect and anyone "
        "can use this bot. Re-enable it there if that's not intended."
    )

    # Check if DATABASE_URL uses default value (production warning)
    if config.DATABASE_URL == "sqlite+aiosqlite:///porabot.db":
        logger.warning(
            "Using default SQLite database URL! For production, set DATABASE_URL in .env file."
        )

# Export for use in __main__.py
__all__ = ["config", "validate_config"]