"""4.4: hand-rolled iCalendar (RFC 5545) feed builder.

No new dependency — Reminder.rrule_string is already stored in the exact
RRULE value-list syntax iCalendar expects (e.g. "FREQ=DAILY;INTERVAL=1",
"FREQ=WEEKLY;BYDAY=MO,WE,FR"), see bot/utils/time_ext.py's
next_occurrence_utc and the 3.1 repeat builder that produces these strings.
So building a VEVENT is mostly string assembly, not translation.

All Reminder.execution_time values are naive UTC (repo-wide invariant, see
bot/database/models.py's module docstring) — DTSTART is emitted as
"...Z" (UTC) directly from that, no timezone conversion needed.
"""

from datetime import datetime, timezone
from typing import Sequence

_CRLF = "\r\n"
_PRODID = "-//Porabot//Reminders//EN"


def _escape_ics_text(value: str) -> str:
    """Escape a plain-text ICS property value per RFC 5545 §3.3.11."""
    value = value or ""
    value = value.replace("\\", "\\\\")
    value = value.replace(";", "\\;")
    value = value.replace(",", "\\,")
    value = value.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return value


def _fold_line(line: str) -> str:
    """Fold a single unfolded content line to <=75 octets per RFC 5545
    §3.1, continuation lines prefixed with a single space."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts = []
    start = 0
    limit = 75
    while start < len(encoded):
        # Avoid splitting a multi-byte UTF-8 sequence in half.
        end = min(start + limit, len(encoded))
        while end < len(encoded) and (encoded[end] & 0xC0) == 0x80:
            end -= 1
        parts.append(encoded[start:end].decode("utf-8"))
        start = end
        limit = 74  # continuation lines lose one octet to the leading space
    return (_CRLF + " ").join(parts)


def _format_utc(dt: datetime) -> str:
    """Naive-UTC datetime -> basic ICS UTC format, e.g. 20260811T090000Z."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _build_vevent(reminder) -> list[str]:
    uid = f"reminder-{reminder.id}@porabot"
    dtstamp = _format_utc(datetime.now(timezone.utc))
    dtstart = _format_utc(reminder.execution_time)
    summary = _escape_ics_text(reminder.reminder_text)

    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}",
        f"SUMMARY:{summary}",
    ]
    if reminder.is_recurring and reminder.rrule_string:
        lines.append(f"RRULE:{reminder.rrule_string}")
    lines.append("END:VEVENT")
    return lines


def build_ics_calendar(user, reminders: Sequence) -> str:
    """Render *reminders* (already filtered/ordered by the caller — see
    ReminderDAO.get_active_for_ics) into a full VCALENDAR document."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Porabot",
    ]
    for reminder in reminders:
        lines.extend(_build_vevent(reminder))
    lines.append("END:VCALENDAR")

    return _CRLF.join(_fold_line(line) for line in lines) + _CRLF
