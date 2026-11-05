"""B-05/B-06 (2026-11-04 audit): which replied-to bot message counts as the
card of ONE task. A task list (many tasks) must never be mistaken for the
card of its first task, and a fired reminder's notification must be a card
whether or not the task is recurring."""

from types import SimpleNamespace

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.handlers.reminders_wizard import _reminder_id_from_card
from bot.keyboards.inline import get_task_done_keyboard
from bot.lexicon import get_l10n

L10N = get_l10n("en")


def _msg(rows):
    return SimpleNamespace(
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="x", callback_data=d) for d in row] for row in rows]
        )
    )


def test_task_list_is_not_a_card() -> None:
    listing = _msg([
        ["ux:abcdef123456:0:done:41:1767225600", "ux:abcdef123456:0:detail:41"],
        ["ux:abcdef123456:0:done:57:1767229200", "ux:abcdef123456:0:detail:57"],
        ["uxlist:today", "uxlist:all", "ux_search"],
    ])
    assert _reminder_id_from_card(listing) is None


def test_single_task_list_is_not_a_card_either() -> None:
    listing = _msg([["ux:abcdef123456:0:done:41:1767225600", "ux:abcdef123456:0:detail:41"], ["uxlist:today"]])
    assert _reminder_id_from_card(listing) is None


def test_task_detail_screen_is_a_card() -> None:
    detail = _msg([
        ["ux:abcdef123456:0:rename:41"], ["ux:abcdef123456:0:time:41"], ["ux:abcdef123456:0:delete:41"],
        ["nav:abcdef123456:0"],
    ])
    assert _reminder_id_from_card(detail) == 41


def test_recurring_notification_is_a_card() -> None:
    kb = get_task_done_keyboard(41, L10N, cycle_due_ts=1767225600, show_not_done=True, is_recurring=True)
    assert _reminder_id_from_card(SimpleNamespace(reply_markup=kb)) == 41


def test_habit_notification_is_a_card() -> None:
    kb = get_task_done_keyboard(41, L10N, cycle_due_ts=1767225600, show_not_today=True, is_recurring=True)
    assert _reminder_id_from_card(SimpleNamespace(reply_markup=kb)) == 41


def test_one_off_notification_is_a_card() -> None:
    kb = get_task_done_keyboard(41, L10N, show_not_done=True)
    assert _reminder_id_from_card(SimpleNamespace(reply_markup=kb)) == 41


def test_buttons_for_different_tasks_are_ambiguous() -> None:
    assert _reminder_id_from_card(_msg([["del_task_1"], ["del_task_2"]])) is None
