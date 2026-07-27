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
