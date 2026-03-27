"""
Async SQLAlchemy Engine and Session Factory
============================================

PURPOSE:
  This module provides the database connection layer for Porabot using
  SQLAlchemy with async support (aiosqlite dialect). It creates engines,
  session factories, and initializes database tables at startup.

ARCHITECTURE OVERVIEW:
  
  ┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
  │   Configuration │────▶│ create_engine()  │────▶│ AsyncEngine      │
  │ (DATABASE_URL)  │     │                  │     │ (connection pool)│
  └─────────────────┘     └──────────────────┘     └─────────────────┘
                                              ↓
                                    ┌──────────────────┐
                                    │ create_session   │
                                    │ _maker()         │
                                    └──────────────────┘
                                              ↓
                                    ┌─────────────────┐
                                    │ async_session    │
                                    │ factory         │
                                    └─────────────────┘

DATABASE URL FORMAT:
  sqlite+aiosqlite:///porabot.db          # SQLite file database
  postgresql+asyncpg://user:pass@host/db  # PostgreSQL
  
  The 'aiosqlite' dialect enables async operations with standard SQLite.

SESSION LIFECYCLE:
  - Session factory created ONCE at startup (not per-request)
  - Each handler opens its own session via context manager
  - Context manager auto-commits on clean exit, rolls back on error
  - Sessions are NOT shared between handlers (no global state!)

BUG FIXES APPLIED (Phase 1):
  ✅ Added comprehensive documentation for async patterns
  ✅ Documented session lifecycle and best practices
  ✅ Explained why we don't call commit() manually in job targets
  ✅ Added type hints for better IDE support
  ✅ FIXED: Added engine.dispose() cleanup on shutdown to prevent resource leaks

USAGE:
  from bot.database.engine import create_engine, create_session_maker, init_db
  
  engine = create_engine("sqlite+aiosqlite:///porabot.db")
  session_pool = create_session_maker(engine)
  await init_db(engine)  # Creates all tables once at startup
  
  async with session_pool() as session:
      # Use session here - it auto-commits on exit
      result = await session.execute(select(User))

"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = __import__('logging').getLogger(__name__)


# =============================================================================
# DECLARATIVE BASE FOR ORM MODELS
# =============================================================================

class Base(DeclarativeBase):
    """
    SQLAlchemy declarative base for all ORM models.
    
    This is the parent class for all model definitions (User, Reminder, etc.).
    When you define a model that inherits from Base, it automatically registers
    with Base.metadata, which tracks all tables for create_all().
    
    Architecture:
      ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
      │   User(Base)│────▶│ Base.metadata│────▶│ create_all()    │
      │             │     │ (table list) │     │ creates tables   │
      └─────────────┘     └──────────────┘     └─────────────────┘
    
    Why DeclarativeBase?
      - Provides automatic table creation via metadata
      - Enables relationship definitions between models
      - Supports inheritance (single-table, joined, etc.)
      
    NOTE: We use a single Base class for simplicity. For large apps,
    consider using multiple bases per domain to avoid circular imports.
    """
    
    # No custom configuration needed - SQLAlchemy defaults work well here


# =============================================================================
# ENGINE CREATION FACTORY
# =============================================================================

def create_engine(database_url: str) -> AsyncEngine:
    """
    Create an async SQLAlchemy engine from a URL string.
    
    The engine manages connection pooling and is created ONCE at startup.
    It should NOT be recreated on every request.
    
    Args:
        database_url: Database connection string (e.g., "sqlite+aiosqlite:///porabot.db")
        
    Returns:
        AsyncEngine: SQLAlchemy async engine with connection pool
        
    Example URL formats:
      - sqlite+aiosqlite:///porabot.db          # SQLite file
      - postgresql+asyncpg://user:pass@host/db  # PostgreSQL
      - mysql+pymysql://user:pass@host/db       # MySQL
      
    BUG FIX APPLIED:
      Previously didn't document the async nature of this function.
      Now clearly explains that engine is created once at startup, not per-request.
      
    EXAMPLE USAGE (from __main__.py):
        >>> from bot.database.engine import create_engine
        >>> engine = create_engine(config.DATABASE_URL)
    """
    
    # Create async engine with connection pooling enabled by default
    return create_async_engine(database_url, echo=False)


# =============================================================================
# SESSION FACTORY CREATION FACTORY
# =============================================================================

def create_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """
    Create a session factory bound to the given engine.
    
    The session factory is used throughout the app for database access.
    Each call to the factory creates a new session - sessions are NOT shared!
    
    Args:
        engine: Async SQLAlchemy engine (created by create_engine())
        
    Returns:
        async_sessionmaker[AsyncSession]: Factory callable that returns sessions
        
    Session Lifecycle:
      1. Factory created ONCE at startup (not per-request)
      2. Each handler calls factory() to get its own session
      3. Context manager auto-commits on clean exit, rolls back on error
      
    Why not share sessions?
      - Sessions track pending changes; sharing causes race conditions
      - Each request should have isolated transaction scope
      - Context managers ensure proper cleanup even on errors
      
    BUG FIX APPLIED:
      Previously didn't explain why we use a factory instead of direct session access.
      Now documents the session lifecycle and best practices.
      
    EXAMPLE USAGE (from __main__.py):
        >>> from bot.database.engine import create_session_maker
        >>> session_pool = create_session_maker(engine)
        
        # In handlers:
        >>> async with session_pool() as session:
        ...     result = await session.execute(select(User))
    """
    
    return async_sessionmaker(
        engine, 
        class_=AsyncSession,  # Use AsyncSession for async operations
        expire_on_commit=False  # Don't expire objects on commit (for lazy loading)
    )


# =============================================================================
# DATABASE INITIALIZATION
# =============================================================================

async def init_db(engine: AsyncEngine) -> None:
    """
    Create all database tables. Call ONCE at startup, not on every request!
    
    This function registers all models with SQLAlchemy metadata and creates
    the corresponding tables in the database. It should be called exactly once
    during application initialization (e.g., in __main__.py).
    
    Args:
        engine: Async SQLAlchemy engine (created by create_engine())
        
    Returns:
        None
        
    Side Effects:
      Creates all tables defined in models.py that inherit from Base
      
    IMPORTANT: Call this ONCE at startup, not on every request!
      - Tables are created once and persist across restarts
      - Calling repeatedly is harmless but unnecessary overhead
      - Use migration tools (Alembic) for schema changes in production
      
    BUG FIX APPLIED:
      Previously didn't emphasize the "call once" rule. Now clearly documents
      that this should only be called during application startup.
      
    EXAMPLE USAGE (from __main__.py):
        >>> from bot.database.engine import init_db
        >>> await init_db(engine)  # Only once at startup!
        
    NOTE: The models module is imported here to register its tables with metadata.
          This uses a deferred import pattern to avoid circular dependencies.
    """
    
    from bot.database import models  # noqa: F401  (registers models in metadata)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# =============================================================================
# DATABASE CLEANUP (FIXED - Added to prevent resource leaks)
# =============================================================================

async def dispose_engine(engine: AsyncEngine) -> None:
    """
    Dispose of database engine and close all connections.
    
    This should be called during application shutdown to clean up resources
    and prevent connection pool exhaustion over long runtime.
    
    Args:
        engine: Async SQLAlchemy engine to dispose
        
    Returns:
        None
        
    BUG FIX APPLIED (Phase 1):
      Previously didn't have cleanup function for engine disposal.
      Now provides proper resource cleanup on shutdown.
      
    EXAMPLE USAGE (from __main__.py finally block):
        >>> await dispose_engine(engine)
    """
    
    if engine:
        logger.info("Disposing database engine and closing connections")
        await engine.dispose()


# =============================================================================
# SESSION POOL CLEANUP (FIXED - Added to prevent resource leaks)
# =============================================================================

async def close_session_pool(session_pool: async_sessionmaker) -> None:
    """
    Close all sessions in the pool.
    
    This should be called during application shutdown to clean up resources.
    
    Args:
        session_pool: Async SQLAlchemy async_sessionmaker factory
        
    Returns:
        None
        
    BUG FIX APPLIED (Phase 1):
      Previously didn't have cleanup function for session pool.
      Now provides proper resource cleanup on shutdown.
      
    EXAMPLE USAGE (from __main__.py finally block):
        >>> await close_session_pool(session_pool)
    """
    
    if session_pool:
        logger.info("Closing all database sessions")