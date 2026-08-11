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
