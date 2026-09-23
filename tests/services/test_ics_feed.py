"""4.4: ICS calendar feed — bot/services/ics_feed.py's calendar builder."""

from datetime import datetime
from types import SimpleNamespace

from bot.services.ics_feed import build_ics_calendar


def _reminder(**kwargs):
    defaults = dict(
        id=1,
        reminder_text="Buy milk",
        execution_time=datetime(2026, 8, 12, 9, 0, 0),
        is_recurring=False,
        rrule_string=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_empty_calendar_has_valid_envelope() -> None:
    ics = build_ics_calendar(SimpleNamespace(id=1), [])
    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.rstrip("\r\n").endswith("END:VCALENDAR")
    assert "VERSION:2.0" in ics
    assert "BEGIN:VEVENT" not in ics


def test_one_off_reminder_becomes_a_vevent_without_rrule() -> None:
    r = _reminder()
    ics = build_ics_calendar(SimpleNamespace(id=1), [r])
    assert "BEGIN:VEVENT" in ics
    assert "UID:reminder-1@porabot" in ics
    assert "DTSTART:20260812T090000Z" in ics
    assert "SUMMARY:Buy milk" in ics
    assert "RRULE" not in ics


def test_recurring_reminder_includes_rrule_verbatim() -> None:
    r = _reminder(id=2, is_recurring=True, rrule_string="FREQ=WEEKLY;BYDAY=MO,WE,FR")
    ics = build_ics_calendar(SimpleNamespace(id=1), [r])
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR" in ics


def test_special_characters_are_escaped() -> None:
    r = _reminder(reminder_text="Buy milk, eggs; call mom\nurgent!")
    ics = build_ics_calendar(SimpleNamespace(id=1), [r])
    assert "SUMMARY:Buy milk\\, eggs\\; call mom\\nurgent!" in ics


def test_multiple_reminders_produce_multiple_vevents() -> None:
    reminders = [_reminder(id=1), _reminder(id=2, reminder_text="Call dad")]
    ics = build_ics_calendar(SimpleNamespace(id=1), reminders)
    assert ics.count("BEGIN:VEVENT") == 2
    assert ics.count("END:VEVENT") == 2


def test_long_summary_is_folded_under_75_octets_per_line() -> None:
    r = _reminder(reminder_text="x" * 200)
    ics = build_ics_calendar(SimpleNamespace(id=1), [r])
    for line in ics.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75


# ---------------------------------------------------------------------------
# docs/audits/2026-09-22-audit.md#a-16 — a recurring event's DTSTART must be
# a local wall-clock time under a TZID parameter, not a bare UTC instant:
# this codebase's whole recurrence model exists specifically because a
# UTC-anchored daily/weekly rule drifts the displayed wall-clock time by an
# hour across every DST transition (see next_occurrence_utc's docstring) —
# exporting UTC into the .ics feed would reintroduce exactly that drift for
# calendar subscribers. A one-off event is unaffected (no recurrence to
# drift) and keeps a plain UTC DTSTART.
# ---------------------------------------------------------------------------

def test_recurring_reminder_uses_local_tzid_dtstart_not_utc() -> None:
    r = _reminder(
        id=3,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        execution_time=datetime(2026, 8, 12, 6, 0, 0),  # 09:00 MSK (UTC+3)
    )
    ics = build_ics_calendar(SimpleNamespace(id=1, timezone="Europe/Moscow"), [r])

    assert "DTSTART;TZID=Europe/Moscow:20260812T090000" in ics
    assert "DTSTART:20260812T060000Z" not in ics


def test_one_off_reminder_still_uses_utc_dtstart_with_a_local_timezone_set() -> None:
    r = _reminder(execution_time=datetime(2026, 8, 12, 6, 0, 0))
    ics = build_ics_calendar(SimpleNamespace(id=1, timezone="Europe/Moscow"), [r])

    assert "DTSTART:20260812T060000Z" in ics
    assert "TZID" not in ics


def test_recurring_dtstart_uses_the_series_anchor_not_execution_time() -> None:
    """A-02/A-16: for a COUNT=/UNTIL=-limited series, the exported DTSTART
    must be the series' TRUE anchor (rrule_dtstart) — using the current,
    already-advanced execution_time instead would export a series that
    restarts its occurrence count from "now" instead of the original
    total."""
    r = _reminder(
        id=4,
        is_recurring=True,
        rrule_string="FREQ=DAILY;COUNT=5",
        execution_time=datetime(2026, 8, 15, 6, 0, 0),  # already advanced by a few fires
        rrule_dtstart=datetime(2026, 8, 12, 6, 0, 0),  # the true series start
    )
    ics = build_ics_calendar(SimpleNamespace(id=1, timezone="Europe/Moscow"), [r])

    assert "DTSTART;TZID=Europe/Moscow:20260812T090000" in ics
    assert "20260815" not in ics


def test_recurring_dtstart_falls_back_to_execution_time_without_an_anchor() -> None:
    """A legacy row (or a SimpleNamespace fixture) with no rrule_dtstart
    attribute at all must not crash — falls back to execution_time, same
    as every other rrule_dtstart call site in this codebase."""
    r = _reminder(id=5, is_recurring=True, rrule_string="FREQ=DAILY")
    ics = build_ics_calendar(SimpleNamespace(id=1, timezone="Europe/Moscow"), [r])

    # Default fixture execution_time is 2026-08-12 09:00 UTC == 12:00 MSK.
    assert "DTSTART;TZID=Europe/Moscow:20260812T120000" in ics


def test_dst_transition_does_not_shift_the_local_wall_clock_time() -> None:
    """Europe/Berlin switches to summer time (CET, +01:00 -> CEST, +02:00)
    at 2026-03-29 02:00 local. A daily 09:00-local reminder anchored well
    before the transition must still render as 09:00 local after it — a
    bare UTC DTSTART would instead show 10:00 local (or a calendar app
    would resolve the fixed UTC instant back to a DIFFERENT local hour)
    once the viewer's own calendar app crosses the same transition.
    """
    r = _reminder(
        id=6,
        is_recurring=True,
        rrule_string="FREQ=DAILY",
        execution_time=datetime(2026, 3, 20, 8, 0, 0),  # 09:00 CET (+01:00)
        rrule_dtstart=datetime(2026, 3, 20, 8, 0, 0),
    )
    ics = build_ics_calendar(SimpleNamespace(id=1, timezone="Europe/Berlin"), [r])

    assert "DTSTART;TZID=Europe/Berlin:20260320T090000" in ics
