from bot.lexicon.en import EN
from bot.lexicon.es import ES
from bot.lexicon.pt import PT
from bot.lexicon.ru import RU


def test_ru_and_es_cover_all_en_keys() -> None:
    en_keys = set(EN.keys())
    assert not (en_keys - set(RU.keys()))
    assert not (en_keys - set(ES.keys()))
    assert not (en_keys - set(PT.keys()))


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
    for locale in (EN, RU, ES, PT):
        missing = required - set(locale.keys())
        assert not missing


def test_spanish_is_not_accidentally_falling_back_to_english() -> None:
    # Keys intentionally shared with English (names, offsets, universal label).
    allowed_equal = {
        "lang_ru",
        "lang_en",
        "lang_es",
        "lang_pt",
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
        # Pagination controls: arrows/page-number format are language-neutral.
        "btn_prev_page",
        "btn_next_page",
        "tasks_page_indicator",
        # A-19: a star amount + a date, no actual words to translate.
        "paysupport_item",
    }
    equal_keys = {k for k, v in EN.items() if ES.get(k) == v}
    assert equal_keys <= allowed_equal


def test_portuguese_is_not_accidentally_falling_back_to_english() -> None:
    allowed_equal = {
        "lang_ru", "lang_en", "lang_es", "lang_pt",
        "quiet_hours_summary", "tz_label_europe_kyiv", "tz_label_asia_dubai",
        "tz_label_asia_almaty", "tz_label_utc",
        "time_delta_15m", "time_delta_30m", "time_delta_1h", "time_delta_2h", "time_delta_3h",
        "reminder_prefix", "snooze_15m", "snooze_30m", "snooze_1h", "snooze_2h",
        "btn_prev_page", "btn_next_page", "tasks_page_indicator", "paysupport_item",
    }
    assert {k for k, v in EN.items() if PT.get(k) == v} <= allowed_equal


def test_portuguese_placeholders_match_english() -> None:
    import re

    pattern = re.compile(r"\{[a-z_]+\}")
    for key, en_value in EN.items():
        if isinstance(en_value, str):
            assert sorted(pattern.findall(PT[key])) == sorted(pattern.findall(en_value)), key


def test_regional_language_codes_resolve_to_their_lexicon() -> None:
    from bot.lexicon import get_l10n

    assert get_l10n("pt-br")["lang_set"] == PT["lang_set"]
    assert get_l10n("pt_PT")["lang_set"] == PT["lang_set"]
    assert get_l10n("es-419")["lang_set"] == ES["lang_set"]
    assert get_l10n("de")["lang_set"] == RU["lang_set"]
