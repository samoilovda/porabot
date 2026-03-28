"""Time formatting utilities."""

from datetime import datetime, timezone
import pytz


def format_time(
    dt: datetime,
    tz_str: str,
    show_utc_offset: bool = False,
    fmt: str = "%H:%M",
) -> str:
    """Format a datetime string with optional UTC offset.

    Args:
        dt: Datetime object (will be converted to user's timezone if naive).
        tz_str: User's timezone string (e.g., 'Europe/Moscow'). Falls back to UTC if invalid.
        show_utc_offset: If True, append UTC offset in parentheses after the time.
        fmt: strftime format for the time portion (default: '%H:%M').

    Returns:
        Formatted time string, optionally with UTC offset.

    Example:
        >>> format_time(datetime(2024, 3, 27, 9, 0), "Europe/Moscow", False)
        '09:00'
        
        >>> format_time(datetime(2024, 3, 27, 9, 0), "Europe/Moscow", True)
        '09:00 (UTC+3)'
    """
    # Parse timezone string with specific exception handling
    try:
        user_tz = pytz.timezone(tz_str)
    except pytz.UnknownTimeZoneError:
        user_tz = pytz.UTC

    # Ensure dt is timezone-aware before formatting
    if dt.tzinfo is None or dt.tzinfo == timezone.utc:
        # Localize naive datetime to UTC first
        dt = dt.replace(tzinfo=timezone.utc)
        
    # Convert to user's local time for display
    dt_local = dt.astimezone(user_tz)
    
    # Format the base time string
    base_str = dt_local.strftime(fmt)
    
    if not show_utc_offset:
        return base_str
        
    # Extract UTC offset from the localized datetime
    offset_seconds = dt_local.utcoffset().total_seconds()
    
    sign = "-" if offset_seconds < 0 else "+"
    hours, mins = divmod(abs(offset_seconds), 3600)
    
    # Convert to integers for format string (divmod returns floats when input is float)
    hours_int = int(hours)
    mins_int = int(mins)
    
    offset_formatted = f"UTC{sign}{hours_int:02d}:{mins_int:02d}"
        
    return f"{base_str} ({offset_formatted})"


def format_datetime(dt: datetime, tz_str: str, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a full datetime string (date + time) in user's timezone.

    Args:
        dt: Datetime object.
        tz_str: User's timezone string. Falls back to UTC if invalid.
        fmt: strftime format for the full datetime (default: '%Y-%m-%d %H:%M').

    Returns:
        Formatted datetime string in user's local time.

    Example:
        >>> format_datetime(datetime(2024, 3, 27, 9, 0), "Europe/Moscow")
        '2024-03-27 12:00'
    """
    try:
        user_tz = pytz.timezone(tz_str)
    except pytz.UnknownTimeZoneError:
        user_tz = pytz.UTC

    if dt.tzinfo is None or dt.tzinfo == timezone.utc:
        dt = dt.replace(tzinfo=timezone.utc)
        
    dt_local = dt.astimezone(user_tz)
    
    return dt_local.strftime(fmt)


def format_duration(minutes: int, include_sign: bool = True) -> str:
    """Format a duration in human-readable form.

    Args:
        minutes: Duration in minutes (can be negative).
        include_sign: If True, prepend '+' for positive durations.

    Returns:
        Human-readable duration string.

    Examples:
        >>> format_duration(15)
        '+15m'
        
        >>> format_duration(-30)
        '-30m'
        
        >>> format_duration(90, include_sign=False)
        '1ч 30м'
    """
    if minutes == 0:
        return "0"
    
    sign = "+" if include_sign and minutes > 0 else ""
    
    hours, remainder = divmod(abs(minutes), 60)
    mins = remainder
    
    if hours > 0:
        if mins > 0:
            return f"{sign}{hours}ч {mins}м"
        return f"{sign}{hours}ч"
    return f"{sign}{mins}м"


def get_time_of_day_label(hour: int) -> str:
    """Get a descriptive label for the time of day.

    Args:
        hour: Hour in 24-hour format (0-23).

    Returns:
        Descriptive string like 'Morning', 'Afternoon', 'Evening', or 'Night'.

    Examples:
        >>> get_time_of_day_label(9)
        'Morning'
        
        >>> get_time_of_day_label(14)
        'Afternoon'
        
        >>> get_time_of_day_label(20)
        'Evening'
    """
    if 5 <= hour < 12:
        return "Morning"
    elif 12 <= hour < 17:
        return "Afternoon"
    elif 17 <= hour < 22:
        return "Evening"
    else:
        return "Night"