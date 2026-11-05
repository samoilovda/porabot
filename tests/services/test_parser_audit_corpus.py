"""Parser corpus from the 2026-11-04 audit (B-01, B-02, B-03): numeric dates,
recurrence phrases, day+time merging and leftover words. Each case is a
phrase a real user would type; expectations are relative so the test does
not depend on today's date."""

from datetime import datetime

import pytest
import pytz

pytest.importorskip("natasha")
from bot.services.parser import InputParser

TZ = "Europe/Moscow"
P = InputParser()


def parse(text, language="ru"):
    return P._parse_sync(text, TZ, language)


def local(result):
    return result.parsed_datetime.astimezone(pytz.timezone(TZ))


# --- B-01: numeric dates -----------------------------------------------------

@pytest.mark.parametrize(
    "text, day, month, hour, minute, clean",
    [
        ("встреча 12.11 в 14:30", 12, 11, 14, 30, "встреча"),
        ("встреча 12.11.2027 в 14:30", 12, 11, 14, 30, "встреча"),
        ("встреча 05.12 в 10:00", 5, 12, 10, 0, "встреча"),
        ("встреча 12/11 в 14:30", 12, 11, 14, 30, "встреча"),
        ("30.11 в 9 сдать отчёт", 30, 11, 9, 0, "сдать отчёт"),
        ("4.1 в 18:00 день рождения", 4, 1, 18, 0, "день рождения"),
    ],
)
def test_numeric_dates_day_first(text, day, month, hour, minute, clean) -> None:
    r = parse(text)
    dt = local(r)
    assert (dt.day, dt.month, dt.hour, dt.minute) == (day, month, hour, minute)
    assert dt > datetime.now(pytz.timezone(TZ))
    assert r.clean_text == clean
    assert not r.date_only


@pytest.mark.parametrize("text, day, month, clean", [("15.11 врач", 15, 11, "врач"), ("дедлайн 30.11", 30, 11, "дедлайн")])
def test_numeric_date_without_time_asks_for_hour(text, day, month, clean) -> None:
    r = parse(text)
    dt = local(r)
    assert (dt.day, dt.month) == (day, month)
    assert r.date_only
    assert r.clean_text == clean


def test_slash_date_is_month_first_for_english() -> None:
    dt = local(parse("meeting on 11/12 at 3pm", language="en"))
    assert (dt.month, dt.day, dt.hour) == (11, 12, 15)


def test_explicit_year_is_kept() -> None:
    dt = local(parse("встреча 12.11.2027 в 14:30"))
    assert dt.year == 2027


@pytest.mark.parametrize("text", ["в 14.30 позвонить", "через 1.5 часа выключить", "купить молоко за 1.20 руб"])
def test_dotted_numbers_that_are_not_dates(text) -> None:
    r = parse(text)
    if r.parsed_datetime is not None:
        assert not (r.parsed_datetime.month in (1, 5) and r.parsed_datetime.day in (14, 1))


def test_dotted_clock_time_is_still_a_time() -> None:
    dt = local(parse("в 14.30 позвонить банку"))
    assert (dt.hour, dt.minute) == (14, 30)


# --- B-02: recurrence ----------------------------------------------------------

@pytest.mark.parametrize(
    "text, rrule, hour, clean",
    [
        ("каждый понедельник в 9 планёрка", "FREQ=WEEKLY;BYDAY=MO", 9, "планёрка"),
        ("по понедельникам в 9 планёрка", "FREQ=WEEKLY;BYDAY=MO", 9, "планёрка"),
        ("каждый вторник и четверг в 19 спортзал", "FREQ=WEEKLY;BYDAY=TU,TH", 19, "спортзал"),
        ("по вторникам и четвергам в 19 спортзал", "FREQ=WEEKLY;BYDAY=TU,TH", 19, "спортзал"),
        ("каждую среду в 10 созвон", "FREQ=WEEKLY;BYDAY=WE", 10, "созвон"),
        ("каждое утро медитация", "FREQ=DAILY", 9, "медитация"),
        ("каждый вечер читать", "FREQ=DAILY", 19, "читать"),
        ("каждый день в 9 тренировка", "FREQ=DAILY", 9, "тренировка"),
        ("по будням в 7:30 зарядка", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", 7, "зарядка"),
    ],
)
def test_recurrence_phrases(text, rrule, hour, clean) -> None:
    r = parse(text)
    assert r.rrule_string == rrule
    assert local(r).hour == hour
    assert r.clean_text == clean


@pytest.mark.parametrize(
    "text, rrule, clean",
    [
        ("каждые 2 дня поливать цветы", "FREQ=DAILY;INTERVAL=2", "поливать цветы"),
        ("каждые 3 недели стрижка", "FREQ=WEEKLY;INTERVAL=3", "стрижка"),
        ("оплатить интернет 5 числа каждого месяца", "FREQ=MONTHLY;BYMONTHDAY=5", "оплатить интернет"),
        ("ежемесячно 10 числа платёж", "FREQ=MONTHLY;BYMONTHDAY=10", "платёж"),
        ("каждый месяц 1 числа аренда", "FREQ=MONTHLY;BYMONTHDAY=1", "аренда"),
    ],
)
def test_recurrence_without_time_asks_for_time(text, rrule, clean) -> None:
    r = parse(text)
    assert r.rrule_string == rrule
    assert r.parsed_datetime is None
    assert r.clean_text == clean


@pytest.mark.parametrize(
    "text, rrule, clean, language",
    [
        ("every monday at 9 standup", "FREQ=WEEKLY;BYDAY=MO", "standup", "en"),
        ("every tuesday and thursday at 7pm gym", "FREQ=WEEKLY;BYDAY=TU,TH", "gym", "en"),
        ("pay rent on the 1st of every month", "FREQ=MONTHLY;BYMONTHDAY=1", "pay rent", "en"),
        ("every 2 weeks at 10 review", "FREQ=WEEKLY;INTERVAL=2", "review", "en"),
        ("cada lunes a las 9 reunión", "FREQ=WEEKLY;BYDAY=MO", "reunión", "es"),
        ("toda segunda às 9 reunião", "FREQ=WEEKLY;BYDAY=MO", "reunião", "pt"),
    ],
)
def test_recurrence_other_languages(text, rrule, clean, language) -> None:
    r = parse(text, language)
    assert r.rrule_string == rrule
    assert r.clean_text == clean


def test_unrecognized_recurrence_word_lowers_confidence() -> None:
    r = parse("каждый раз в 9 проверять почту")
    assert r.rrule_string is None
    assert r.confidence < 0.7


# --- B-03: merging and leftovers --------------------------------------------

def test_day_word_before_period_time_is_kept_and_stripped() -> None:
    r = parse("завтра в 10 утра позвонить маме")
    dt = local(r)
    tomorrow = datetime.now(pytz.timezone(TZ)).date().toordinal() + 1
    assert dt.date().toordinal() == tomorrow
    assert dt.hour == 10
    assert r.clean_text == "позвонить маме"


def test_period_time_before_weekday_is_merged() -> None:
    r = parse("в 10 утра в понедельник встреча")
    dt = local(r)
    assert dt.weekday() == 0 and dt.hour == 10
    assert not r.date_only
    assert r.clean_text == "встреча"


def test_spanish_afternoon_period() -> None:
    r = parse("llamar a mamá mañana a las 5 de la tarde", "es")
    assert local(r).hour == 17
    assert r.clean_text == "llamar a mamá"


def test_decimal_duration() -> None:
    r = parse("через 1.5 часа выключить духовку")
    delta = r.parsed_datetime - datetime.now(pytz.UTC)
    assert 85 <= delta.total_seconds() / 60 <= 95
    assert r.clean_text == "выключить духовку"


@pytest.mark.parametrize("text, hour", [("в полдень обед с Олей", 12), ("в обед забрать посылку", 13)])
def test_noon_and_lunch(text, hour) -> None:
    assert local(parse(text)).hour == hour


def test_next_weekday_modifier_is_not_left_in_text() -> None:
    r = parse("в следующий вторник в 15 стоматолог")
    assert local(r).weekday() == 1
    assert r.clean_text == "стоматолог"


@pytest.mark.parametrize(
    "text, clean",
    [
        ("напомни через полчаса выключить плиту", "выключить плиту"),
        ("напомни мне завтра в 9 купить хлеб", "купить хлеб"),
        ("remind me to call mom tomorrow at 5pm", "call mom"),
    ],
)
def test_leading_reminder_verb_is_stripped(text, clean) -> None:
    assert parse(text, "en" if text.startswith("remind") else "ru").clean_text == clean
