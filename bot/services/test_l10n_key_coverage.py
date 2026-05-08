from bot.lexicon.en import EN
from bot.lexicon.es import ES
from bot.lexicon.ru import RU


def test_ru_and_es_cover_all_en_keys() -> None:
    en_keys = set(EN.keys())
    assert not (en_keys - set(RU.keys()))
    assert not (en_keys - set(ES.keys()))


def test_locales_define_core_ui_localization_keys() -> None:
    required = {
        "tz_manual_button",
        "time_delta_15m",
        "time_delta_30m",
        "time_delta_1h",
        "time_delta_2h",
        "time_delta_3h",
        "invalid_action",
        "invalid_mode",
        "unknown_snooze",
        "item_not_found",
        "task_not_found",
        "snoozed_toast",
        "task_untitled",
        "brief_time_invalid_format",
        "brief_time_invalid_value",
        "db_error",
    }
    for locale in (EN, RU, ES):
        missing = required - set(locale.keys())
        assert not missing


def test_spanish_is_not_accidentally_falling_back_to_english() -> None:
    # Keys intentionally shared with English (names, offsets, universal label).
    allowed_equal = {
        "lang_ru",
        "lang_en",
        "lang_es",
        "quiet_hours_summary",
        "tz_label_europe_kyiv",
        "tz_label_asia_almaty",
        "tz_label_utc",
        "time_delta_15m",
        "time_delta_30m",
        "time_delta_1h",
        "time_delta_2h",
        "time_delta_3h",
        "reminder_prefix",
        "snooze_15m",
        "snooze_30m",
        "snooze_1h",
        "snooze_2h",
    }
    equal_keys = {k for k, v in EN.items() if ES.get(k) == v}
    assert equal_keys <= allowed_equal
