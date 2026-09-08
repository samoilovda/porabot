from bot.keyboards.inline import get_edit_keyboard
from bot.lexicon.ru import RU as RU_LEXICON


def test_edit_keyboard_has_change_time_button() -> None:
    markup = get_edit_keyboard(
        reminder_id=42,
        l10n=RU_LEXICON,
        is_recurring=False,
        is_nagging=False,
        nagging_max_repeats=3,
        rrule_text="Нет",
    )

    callback_datas = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]

    assert "edit_edit_42" in callback_datas


def test_edit_keyboard_shows_repeat_button_for_a_plain_non_recurring_task() -> None:
    """P0-2 regression: every new task is created with is_recurring=False, so
    gating the repeat button on is_recurring made recurrence unreachable
    through the UI for ordinary (non-habit) tasks. The button must show
    regardless, with the "none" label, so the user can turn recurrence on."""
    markup = get_edit_keyboard(
        reminder_id=42,
        l10n=RU_LEXICON,
        is_recurring=False,
        is_nagging=False,
        nagging_max_repeats=3,
        rrule_text=RU_LEXICON["repeat_none"],
    )

    callback_datas = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]

    assert "edit_repeat_menu_42" in callback_datas
