"""Recurrence utilities — thin wrapper around dateutil rrule."""

import logging
from datetime import datetime, timezone
from typing import Optional

from dateutil.rrule import rrulestr

logger = logging.getLogger(__name__)


def next_rrule_occurrence(
    rrule_string: str,
    execution_time: datetime,
    after: datetime,
) -> Optional[datetime]:
    """Return the first occurrence of *rrule_string* that falls after *after*.

    Both *execution_time* (used as ``dtstart``) and *after* should be
    timezone-aware; naive datetimes are treated as UTC.

    Returns ``None`` if the rule has no future occurrences or is malformed.
    """
    start_dt = execution_time
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    try:
        rule = rrulestr(rrule_string, dtstart=start_dt)
        return rule.after(after)
    except Exception:
        logger.warning("Invalid rrule %r — cannot compute next occurrence.", rrule_string)
        return None
