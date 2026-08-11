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

    # --- Web server (4.4/4.6) ---
    # aiohttp server run as an asyncio task alongside the long-polling loop
    # (see bot/services/webserver.py, wired up in bot/__main__.py). Serves
    # the per-user .ics calendar feed (4.4) and the Mini App's static
    # frontend + JSON API (4.6) from one aiohttp.web.Application.
    # Off by default: opening an HTTP port is an opt-in decision, not a
    # side effect of upgrading. Set to True once WEB_SERVER_HOST/PORT,
    # PUBLIC_BASE_URL and (if used) MINI_APP_URL are actually configured.
    WEB_SERVER_ENABLED: bool = False
    # Bind to localhost only by default — this process is meant to sit
    # behind a reverse proxy that terminates TLS and does the actual public
    # listening. Set to "0.0.0.0" only when the process itself runs inside a
    # container/network namespace where that's already the isolation
    # boundary (e.g. Docker, with the port published deliberately).
    WEB_SERVER_HOST: str = "127.0.0.1"
    WEB_SERVER_PORT: int = 8080
    # Publicly reachable origin for links the bot sends to the user (the
    # .ics feed URL, the Mini App web_app button). Deliberately empty by
    # default: there is no public domain/TLS cert for this out of the box.
    # A human must set this (and actually put something with a real
    # certificate in front of WEB_SERVER_HOST:WEB_SERVER_PORT — a reverse
    # proxy, a tunnel, whatever) before these links work outside localhost.
    # Example: "https://porabot.example.com".
    PUBLIC_BASE_URL: str = ""
    # web_app buttons require an https:// URL Telegram clients can load in
    # their in-app WebView. Left empty by default (see PUBLIC_BASE_URL
    # above) — until it's set, the "Open Mini App" button is not shown at
    # all rather than shipping a button that can never work.
    MINI_APP_URL: str = ""


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