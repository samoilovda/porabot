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
    
    # Check if ADMIN_ID is 0 and ALLOWED_USERS is empty
    if config.ADMIN_ID == 0 and not config.ALLOWED_USERS:
        print("[WARNING] No admin or allowed users configured!")
        print("   The bot will have no access control - only you can use it.")
        print(f"   Set ADMIN_ID in .env file to enable proper access control.")
    
    # Check if DATABASE_URL uses default value (production warning)
    if config.DATABASE_URL == "sqlite+aiosqlite:///porabot.db":
        print("[WARNING] Using default SQLite database URL!")
        print("   For production, set DATABASE_URL in .env file.")

# Export for use in __main__.py
__all__ = ["config", "validate_config"]