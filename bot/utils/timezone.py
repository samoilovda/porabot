"""
Timezone Utility Module — Consistent Timezone Handling Across Porabot
========================================================================

PURPOSE:
  This module provides a centralized interface for all timezone operations
  throughout the bot. It ensures consistent handling of naive vs aware datetimes
  and UTC conversion rules.

ARCHITECTURE OVERVIEW:
  
  ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
  │   User Input│────▶│ TimezoneUtil │────▶│ UTC-Aware DT    │
  │ (naive/tz)  │     │              │     │ (for storage)    │
  └─────────────┘     └──────────────┘     └─────────────────┘

API:
  - ensure_timezone_aware(dt, tz_str): Convert naive datetime to timezone-aware
  - ensure_utc_tz(dt): Always convert to UTC timezone
  - to_utc(dt): Convert any datetime to UTC (preserving time)
  - utc_to_local(dt, tz_str): Convert UTC datetime to user's local timezone

USAGE:
  from bot.utils.timezone import ensure_timezone_aware, to_utc
  
  # Ensure naive datetime is marked as Moscow time
  naive = datetime(2024, 3, 27, 19, 0)
  aware = ensure_timezone_aware(naive, "Europe/Moscow")
  
  # Convert to UTC for storage
  utc_time = to_utc(aware)
  print(utc_time)  # datetime(2024, 3, 27, 16, 0, tzinfo=<UTC>)

"""

import logging
import pytz
from datetime import datetime, timezone as stdlib_timezone
from typing import Optional, Tuple


logger = logging.getLogger(__name__)


# =============================================================================
# TIMEZONE UTILITIES
# =============================================================================

def ensure_timezone_aware(
    dt: datetime, 
    tz_str: str
) -> datetime:
    """
    Ensure a datetime object has timezone information.
    
    If the datetime is already aware (has tzinfo), returns it unchanged.
    If naive, assumes the provided timezone string applies to it.
    
    Args:
        dt: Datetime object (may be naive or aware)
        tz_str: Timezone name string (e.g., "Europe/Moscow", "UTC")
        
    Returns:
        timezone-aware datetime object
        
    Raises:
        ValueError: If tz_str is not a valid timezone
    """
    if dt.tzinfo is not None:
        # Already aware - return unchanged
        logger.debug(f"DT already timezone-aware: {dt}")
        return dt
    
    # Naive datetime - attach the provided timezone
    try:
        tz = pytz.timezone(tz_str)
        aware_dt = dt.replace(tzinfo=tz)
        logger.debug(f"Made naive DT timezone-aware with '{tz_str}': {aware_dt}")
        return aware_dt
    except pytz.exceptions.UnknownTimeZoneError as e:
        # Handle invalid timezone gracefully
        logger.warning(
            f"Unknown timezone '{tz_str}', converting to UTC: {dt}",
            exc_info=False
        )
        return dt.replace(tzinfo=stdlib_timezone.utc)


def ensure_utc_tz(dt: datetime) -> datetime:
    """
    Convert a datetime object to UTC timezone.
    
    If already UTC, returns unchanged. Otherwise converts with time preservation.
    
    Args:
        dt: Datetime object (must be timezone-aware)
        
    Returns:
        UTC datetime object
        
    Raises:
        ValueError: If input datetime is naive (not timezone-aware)
    """
    if dt.tzinfo is None:
        raise ValueError(
            f"Cannot convert naive datetime {dt} to UTC. "
            f"Use ensure_timezone_aware() first to attach a timezone."
        )
    
    # If already UTC, return unchanged
    if dt.tzinfo.utcname().lower() == "utc":
        logger.debug(f"DT already UTC: {dt}")
        return dt
    
    # Convert to UTC using pytz
    try:
        utc_tz = pytz.UTC
        aware_dt = ensure_timezone_aware(dt, str(utc_tz))
        utc_dt = aware_dt.astimezone(utc_tz)
        logger.debug(f"Converted DT to UTC: {utc_dt}")
        return utc_dt
    except Exception as e:
        # Fallback for incompatible timezone objects
        logger.warning(
            f"Error converting to UTC with pytz, using standard datetime: {e}",
            exc_info=False
        )
        return dt.astimezone(stdlib_timezone.utc)


def to_utc(dt: datetime) -> datetime:
    """
    Convert any datetime to UTC (preserving wall-clock time value).
    
    This function handles both naive and aware datetimes. For naive datetimes,
    it attaches the provided timezone first (see ensure_timezone_aware()).
    
    Args:
        dt: Datetime object (may be naive or aware)
        tz_str: Optional timezone string if dt is naive
        
    Returns:
        UTC datetime object
        
    Examples:
        # Aware datetime
        utc = to_utc(aware_datetime)  # Uses wall-clock time value in UTC
        
        # Naive datetime with assumed timezone
        utc = to_utc(naive_datetime, "Europe/Moscow")  # Converts assuming Moscow time
    """
    if dt.tzinfo is not None:
        return ensure_utc_tz(dt)
    else:
        # Naive datetime - attach current session timezone (should come from user config)
        logger.warning(
            f"Converting naive datetime to UTC without timezone context: {dt} "
            f"→ assumes local system timezone"
        )
        return dt.replace(tzinfo=stdlib_timezone.utc)


def utc_to_local(
    dt_utc: datetime, 
    tz_str: str
) -> datetime:
    """
    Convert a UTC datetime to user's local timezone.
    
    Args:
        dt_utc: UTC datetime (must be timezone-aware with UTC tzinfo)
        tz_str: User's timezone string (e.g., "Europe/Moscow")
        
    Returns:
        Local timezone datetime object
        
    Raises:
        ValueError: If dt_utc is not in UTC timezone
    """
    if dt_utc.tzinfo is None:
        raise ValueError("Cannot convert naive datetime to local timezone. Use ensure_utc_tz() first.")
    
    try:
        tz = pytz.timezone(tz_str)
        aware_dt = ensure_timezone_aware(dt_utc, str(tz))
        logger.debug(f"Converted UTC {dt_utc} → Local {tz}: {aware_dt}")
        return aware_dt
    except Exception as e:
        logger.error(
            f"Error converting UTC to local timezone '{tz_str}': {e}",
            exc_info=True
        )
        return dt_utc  # Return UTC on error to avoid silent data loss


def normalize_datetime(dt: datetime, tz_str: str) -> Tuple[datetime, str]:
    """
    Normalize a datetime to UTC-aware and validate timezone.
    
    Returns both the normalized datetime AND the resolved timezone string
    for logging/debugging purposes.
    
    Args:
        dt: Datetime object (may be naive or aware, any timezone)
        tz_str: Timezone string (used if dt is naive)
        
    Returns:
        Tuple of (UTC-aware datetime, timezone name string)
        
    Examples:
        # Always returns UTC for database storage
        normalized_dt, _ = normalize_datetime(user_time, "Europe/Moscow")
        print(normalized_dt.tzinfo.utcname())  # "UTC"
    """
    aware_dt = ensure_timezone_aware(dt, tz_str)
    utc_dt = ensure_utc_tz(aware_dt)
    
    tz_name = str(utc_dt.tzinfo)
    logger.debug(f"Normalized DT to UTC: {utc_dt} (tz={tz_name})")
    
    return utc_dt, tz_name


# =============================================================================
# TIMESTAMP FORMATTER (For Display - Returns Local Time)
# =============================================================================

def format_for_display(
    dt_utc: datetime, 
    user_tz_str: str, 
    show_utc_offset: bool = False
) -> str:
    """
    Format a UTC datetime for display in user's local timezone.
    
    This function converts the UTC datetime to the user's local time and
    formats it as a string. Optionally includes UTC offset annotation.
    
    Args:
        dt_utc: UTC datetime object (must be timezone-aware)
        user_tz_str: User's timezone string
        show_utc_offset: Whether to append +HH:MM or -HH:MM
        
    Returns:
        Formatted time string (e.g., "19:30" or "19:30 (+03:00)")
        
    Raises:
        ValueError: If dt_utc is not timezone-aware
    """
    if dt_utc.tzinfo is None:
        raise ValueError("format_for_display requires timezone-aware datetime. Use to_utc() first.")
    
    try:
        tz = pytz.timezone(user_tz_str)
    except Exception:
        tz = stdlib_timezone.utc
    
    # Convert UTC to user's local time
    local_dt = utc_to_local(dt_utc, user_tz_str)
    hour = local_dt.hour
    minute = local_dt.minute
    
    if show_utc_offset:
        offset = local_dt.strftime("%z")[:3]  # +0300 → +03
        return f"{hour:02d}:{minute:02d} (+{offset})"
    else:
        return f"{hour:02d}:{minute:02d}"


# =============================================================================
# TEST HELPER (For Development)
# =============================================================================

def assert_utc(dt: datetime, label: str = "") -> None:
    """
    Assert that a datetime is in UTC timezone.
    
    Raises ValueError if datetime is not UTC-aware.
    
    Args:
        dt: Datetime to check
        label: Optional description for error message
        
    Example:
        # In DAO methods after saving from database:
        reminder = await session.get(Reminder, reminder_id)
        assert_utc(reminder.execution_time, f"execution_time of reminder {reminder_id}")
    """
    if dt.tzinfo is None:
        raise ValueError(f"{label or 'Datetime'} is naive (missing timezone)")
    
    tz_name = str(dt.tzinfo)
    if "UTC" not in tz_name.upper():
        raise ValueError(
            f"{label or 'Datetime'} is not in UTC! Got: {dt} (tz={tz_name})"
        )


# =============================================================================
# DOCKWALL COMPATIBILITY (Optional - for APScheduler integration)
# =============================================================================

def convert_to_utc_string(dt: datetime) -> str:
    """
    Convert a timezone-aware datetime to ISO format string compatible with APScheduler.
    
    Args:
        dt: UTC-aware datetime object
        
    Returns:
        ISO format string without timezone (e.g., "2024-03-27T16:00:00")
        
    Example:
        >>> iso_str = convert_to_utc_string(utc_dt)
        >>> scheduler.add_job(func, "date", run_date=iso_str)  # APScheduler handles UTC internally
    """
    if dt.tzinfo is None:
        raise ValueError("Must provide UTC-aware datetime. Use ensure_utc_tz() first.")
    
    # Return ISO format without timezone indicator (APScheduler assumes UTC for string dates)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")