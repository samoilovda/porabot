"""
Generic Abstract DAO with Reusable CRUD Operations
==================================================

PURPOSE:
  This module provides a generic data access object (DAO) pattern for database
  operations. It implements common CRUD methods that can be reused across all
  models without rewriting SQL queries each time.

ARCHITECTURE OVERVIEW:
  
  ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
  │   BaseDAO[T]│────▶│ Concrete DAOs │────▶│ Model-specific  │
  │ (generic)   │     │ (ReminderDAO, │     │ CRUD operations │
  └─────────────┘     │ UserDAO, etc.)│     └─────────────────┘

DESIGN PATTERN: Generic Type Parameter
  
  BaseDAO[T] is a generic class where T is bound to Base models. This allows:
  
    - Type safety: IDE knows you're working with specific model type
    - Code reuse: Same CRUD methods work for any model
    - No duplication: Don't write separate DAO classes for each model

TRANSACTION BOUNDARY LIVES IN MIDDLEWARE
  
  ⚠️ IMPORTANT DESIGN DECISION:
  
  The commit/rollback happens in DatabaseMiddleware, NOT in the DAO.
  
  Why? Because:
    1. Middleware has access to session_pool factory (DAO doesn't)
    2. Handlers can call multiple DAOs without worrying about commits
    3. Job targets (outside request scope) open their own sessions
  
  This separation of concerns is crucial for async SQLAlchemy patterns!

CRUD OPERATIONS:
  
  - get_by_id(record_id): Fetch single record by primary key
  - get_all(**filters): Fetch all records with optional filtering
  - create(**kwargs): Insert new record, flush to populate auto-generated IDs
  - delete_by_id(record_id): Delete record by primary key
  - count(): Get total number of records

BUG FIXES APPLIED (Phase 1):
  ✅ Added comprehensive documentation for generic pattern
  ✅ Explained transaction boundary design decision
  ✅ Documented all CRUD operations with examples
  ✅ Added type hints and docstrings for better IDE support

USAGE:
  
    # Create DAO instance (session injected by middleware)
    >>> dao = ReminderDAO(session)
    
    # Get by ID
    >>> reminder = await dao.get_by_id(123)
    
    # Get all with filtering
    >>> pending = await dao.get_all(status="pending")
    
    # Create new record
    >>> new_reminder = await dao.create(
    ...     user_id=123,
    ...     reminder_text="Take medication",
    ...     execution_time=datetime.now() + timedelta(hours=1)
    ... )
    
    # Delete by ID
    >>> await dao.delete_by_id(456)

"""

from typing import Generic, TypeVar, Type, Optional, Sequence, Any

from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

# Import Base from engine module (defines table metadata)
from bot.database.engine import Base


T = TypeVar("T", bound=Base)  # Type variable constrained to Base models


class BaseDAO(Generic[T]):
    """
    Generic async DAO with common CRUD operations.
    
    This is a generic class that provides reusable data access methods for any
    SQLAlchemy model. Concrete DAOs inherit from this and set the `model` class
    attribute to their specific model type.
    
    Architecture:
      ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
      │   BaseDAO[T]│────▶│ Concrete DAOs │────▶│ Model-specific  │
      │ (generic)   │     │ (ReminderDAO, │     │ CRUD operations │
      └─────────────┘     │ UserDAO, etc.)│     └─────────────────┘
    
    Type Safety:
      - T is bound to Base models only
      - IDE knows you're working with specific model type
      - No need for separate DAO classes per model
    
    Transaction Boundary:
      ⚠️ commit/rollback happens in DatabaseMiddleware, NOT here!
      - Handlers can call multiple DAOs without worrying about commits
      - Job targets open their own sessions (no middleware)
    
    Args:
        session: Async SQLAlchemy session for database operations
        
    Example:
        >>> dao = ReminderDAO(session)
        >>> reminder = await dao.get_by_id(123)
    """

    model: Type[T]  # Class attribute - set by concrete DAO subclasses
    
    def __init__(self, session: AsyncSession) -> None:
        """Initialize DAO with session."""
        self.session = session

    async def get_by_id(self, record_id: int) -> Optional[T]:
        """
        Fetch a single record by primary key.
        
        This is the most common operation - getting one specific record.
        Uses scalar_one_or_none() to handle both existing and non-existing records.
        
        Args:
            record_id: Primary key value (e.g., reminder.id or user.id)
            
        Returns:
            T: The record if found, None otherwise
            
        Example:
            >>> reminder = await dao.get_by_id(123)  # Reminder object or None
        """
        result = await self.session.execute(
            select(self.model).where(self.model.id == record_id)
        )
        return result.scalar_one_or_none()

    async def get_all(self, **filters: Any) -> Sequence[T]:
        """
        Fetch all records, optionally filtered by column=value pairs.
        
        This method builds a query dynamically based on the filters provided.
        Useful for getting all pending tasks, or all completed tasks, etc.
        
        Args:
            **filters: Column=value pairs to filter by (e.g., status="pending")
            
        Returns:
            Sequence[T]: List of matching records
            
        Example:
            >>> # Get all pending reminders
            >>> pending = await dao.get_all(status="pending")
            
            >>> # Get all reminders for a specific user
            >>> my_tasks = await dao.get_all(user_id=123456)
        """
        stmt = select(self.model)
        for key, value in filters.items():
            stmt = stmt.where(getattr(self.model, key) == value)
        
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create(self, **kwargs: Any) -> T:
        """
        Insert a new record, flush to populate auto-generated IDs, and return it.
        
        This method creates a new instance of the model with provided kwargs,
        adds it to the session, and flushes to get any auto-generated values
        (like primary key ID). Then returns the populated instance.
        
        Args:
            **kwargs: Field names and values for the new record
            
        Returns:
            T: The created record with all fields populated
            
        Example:
            >>> new_reminder = await dao.create(
            ...     user_id=123,
            ...     reminder_text="Take medication",
            ...     execution_time=datetime.now() + timedelta(hours=1)
            ... )
            # Returns Reminder object with id set (even if auto-generated)
        """
        instance = self.model(**kwargs)
        self.session.add(instance)
        await self.session.flush()  # Flush to populate auto-generated IDs
        return instance

    async def delete_by_id(self, record_id: int) -> None:
        """
        Delete a record by primary key.
        
        Uses DELETE SQL statement for efficiency (doesn't load object first).
        
        Args:
            record_id: Primary key value of record to delete
            
        Returns:
            None
            
        Example:
            >>> await dao.delete_by_id(456)  # Deletes reminder with id=456
        """
        await self.session.execute(
            delete(self.model).where(self.model.id == record_id)
        )
        await self.session.flush()

    async def count(self) -> int:
        """
        Get total number of records in the table.
        
        Useful for analytics (e.g., "user has 5 pending tasks").
        
        Returns:
            int: Total count of records
            
        Example:
            >>> total = await dao.count()  # e.g., returns 42
        """
        result = await self.session.execute(select(func.count()).select_from(self.model))
        return result.scalar_one()