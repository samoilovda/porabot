"""Time formatting utilities."""

from datetime import datetime
import pytz

def format_time(dt: datetime, tz_str: str, show_utc_offset: bool = False, fmt: str = "%H:%M") -> str:
    """Format a datetime and optionally append its UTC offset."""
    try:
        user_tz = pytz.timezone(tz_str)
    except Exception:
        user_tz = pytz.UTC

    # Ensure dt is aware
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)
        
    dt_local = dt.astimezone(user_tz)
    base_str = dt_local.strftime(fmt)
    
    if not show_utc_offset:
        return base_str
        
    offset_str = dt_local.strftime('%z')
    if not offset_str:
        return base_str
        
    sign = offset_str[0]
    hours = int(offset_str[1:3])
    mins = int(offset_str[3:5])
    
    if mins == 0:
        offset_formatted = f"UTC{sign}{hours}"
    else:
        offset_formatted = f"UTC{sign}{hours}:{mins:02d}"
        
    return f"{base_str} ({offset_formatted})"
