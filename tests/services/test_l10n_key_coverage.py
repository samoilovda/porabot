import pytest

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


def test_lexicons_are_standalone_literals() -> None:
    """A-07 (2026-11-04 audit): es/pt used to start from dict(EN), so a
    missing translation silently showed English and the coverage test above
    could never fail; ru/en/es were also layered with .update() patches
    that redefined keys. Each file must now be one literal."""
    import ast
    import re
    from pathlib import Path

    for lang in ("en", "ru", "es", "pt"):
        source = (Path(__file__).resolve().parents[2] / "bot" / "lexicon" / f"{lang}.py").read_text()
        assert not re.search(r"^[A-Z]{2}\.update\(", source, re.MULTILINE), lang
        assert "from bot.lexicon" not in source, lang
        keys = [k.value for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Dict) for k in n.keys
                if isinstance(k, ast.Constant)]
        assert len(keys) == len(set(keys)), f"{lang}: duplicate keys"


@pytest.mark.parametrize(
    "lang, count, expected",
    [
        ("ru", 1, "1 задача"), ("ru", 2, "2 задачи"), ("ru", 5, "5 задач"), ("ru", 11, "11 задач"),
        ("ru", 21, "21 задача"), ("ru", 22, "22 задачи"), ("ru", 112, "112 задач"), ("ru", 0, "0 задач"),
        ("en", 1, "1 task"), ("en", 2, "2 tasks"), ("es", 1, "1 tarea"), ("pt", 3, "3 tarefas"),
    ],
)
def test_plural_forms(lang, count, expected) -> None:
    """U-07 (2026-11-04 audit): "Осталось: 1 задач" → correct plural forms."""
    from bot.lexicon import get_l10n, plural

    assert plural(get_l10n(lang), "plural_tasks", count) == expected
