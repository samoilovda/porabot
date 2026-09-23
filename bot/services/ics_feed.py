"""4.4: hand-rolled iCalendar (RFC 5545) feed builder.

No new dependency — Reminder.rrule_string is already stored in the exact
RRULE value-list syntax iCalendar expects (e.g. "FREQ=DAILY;INTERVAL=1",
"FREQ=WEEKLY;BYDAY=MO,WE,FR"), see bot/utils/time_ext.py's
next_occurrence_utc and the 3.1 repeat builder that produces these strings.
So building a VEVENT is mostly string assembly, not translation.

All Reminder.execution_time values are naive UTC (repo-wide invariant, see
bot/database/models.py's module docstring).

A-16: DTSTART for a RECURRING event is emitted as a LOCAL wall-clock time
with a bare TZID parameter (e.g. "DTSTART;TZID=Europe/Moscow:20260811T090000"),
not "...Z" UTC — this codebase's whole recurrence model (next_occurrence_utc,
see its own docstring) exists specifically because a daily/weekly rule
anchored to a UTC instant drifts the wall-clock time a user sees by an hour
across every DST transition; emitting UTC into the exported .ics feed would
reintroduce exactly that drift for anyone actually subscribed to it. A bare
TZID naming a real IANA zone (no embedded VTIMEZONE component) is not
strictly RFC 5545 §3.2.19-conformant — that requires either UTC or a
VTIMEZONE the calendar defines TZID against — but it is exactly how Google
Calendar documents accepting a TZID, and Apple/Outlook resolve one against
their own bundled IANA tzdata too; hand-rolling a fully correct VTIMEZONE
(the historical STANDARD/DAYLIGHT transition rules per zone) was judged
not worth the added complexity here for a personal-scale reminders export.
A one-off (non-recurring) event's DTSTART, and DTSTAMP always, are UTC:
DTSTAMP is REQUIRED to be UTC per RFC 5545, and a one-off has no recurrence
to drift, so its exact stored instant is fine as-is.

RRULE's own UNTIL= (see reminders_repeat.py's state_rrb_end_until) is
already stored as a naive local date-time with no "Z" suffix — RFC 5545
requires a local (TZID) DTSTART's UNTIL to match that same local-time value
type, so this pairs correctly with the change here without needing its own
adjustment.
"""

from datetime import datetime, timezone
from typing import Sequence

import pytz

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


def _format_local(dt_utc_naive: datetime, tz: pytz.BaseTzInfo) -> str:
    """Naive-UTC datetime -> local wall-clock ICS form (no "Z"), e.g.
    20260811T090000 — paired with a DTSTART;TZID=... parameter, never used
    bare (a floating/no-timezone local time means something different in
    RFC 5545 — always ambiguous "whatever zone the viewer is in")."""
    dt_local = dt_utc_naive.replace(tzinfo=timezone.utc).astimezone(tz)
    return dt_local.strftime("%Y%m%dT%H%M%S")


def _build_vevent(reminder, tz: pytz.BaseTzInfo) -> list[str]:
    uid = f"reminder-{reminder.id}@porabot"
    dtstamp = _format_utc(datetime.now(timezone.utc))  # DTSTAMP is REQUIRED to be UTC (RFC 5545 §3.8.7.2)
    summary = _escape_ics_text(reminder.reminder_text)

    is_recurring = bool(reminder.is_recurring and reminder.rrule_string)
    if is_recurring:
        # A-16/A-02: the series' own fixed anchor, not whichever occurrence
        # execution_time currently points at — same anchor
        # next_occurrence_utc's other callers use, so a COUNT=/UNTIL=-
        # limited rule exports the correct TOTAL occurrence count instead
        # of one restarted from "now".
        dtstart_utc_naive = getattr(reminder, "rrule_dtstart", None) or reminder.execution_time
        dtstart_line = f"DTSTART;TZID={tz.zone}:{_format_local(dtstart_utc_naive, tz)}"
    else:
        dtstart_line = f"DTSTART:{_format_utc(reminder.execution_time)}"

    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        dtstart_line,
        f"SUMMARY:{summary}",
    ]
    if is_recurring:
        lines.append(f"RRULE:{reminder.rrule_string}")
    lines.append("END:VEVENT")
    return lines


def build_ics_calendar(user, reminders: Sequence) -> str:
    """Render *reminders* (already filtered/ordered by the caller — see
    ReminderDAO.get_active_for_ics) into a full VCALENDAR document."""
    try:
        tz = pytz.timezone(user.timezone)
    except Exception:
        tz = pytz.UTC

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Porabot",
    ]
    for reminder in reminders:
        lines.extend(_build_vevent(reminder, tz))
    lines.append("END:VCALENDAR")

    return _CRLF.join(_fold_line(line) for line in lines) + _CRLF
