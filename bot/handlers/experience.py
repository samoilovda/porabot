"""Daily lists, durable navigation, task rename and explicit recovery confirmation."""

import json
from datetime import datetime, timezone
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import UiContext, User
from bot.keyboards.inline import callback_button as _button
from bot.services.habit_pause import is_paused
from bot.services.scheduler import SchedulerService
from bot.services.ui_context import create_context, get_context
from bot.utils.time_ext import format_time, to_utc_naive
from bot.utils.timezone import resolve_timezone

router = Router(name="experience")
PAGE_SIZE = 8


class ExperienceState(StatesGroup):
    searching = State()
    renaming = State()


def callbacks(context, page, action, rid):
    return f"ux:{context.token}:{page}:{action}:{rid}"


async def open_list(message, reminder_dao, user, l10n, *, kind="all", query=None, edit=False):
    context = await create_context(reminder_dao.session, user.id, "list", {"kind": kind, "query": query})
    await render_list(message, reminder_dao, user, l10n, context, 0, edit=edit)


async def list_items(dao, user, payload):
    kind = payload["kind"]
    if kind == "find":
        return list(await dao.search_user_reminders(user.id, payload["query"]))
    if kind == "habits":
        return list(await dao.get_active_habits(user.id))
    if kind == "today":
        tasks = list(await dao.get_user_reminders_today(user.id, user.timezone))
        overdue = list(await dao.get_overdue_pending_tasks(user.id, min_minutes_overdue=0, limit=200))
        habits = list(await dao.get_active_habits(user.id))
        today = datetime.now(resolve_timezone(user.timezone)).date()
        included = {r.id: r for r in tasks + overdue if not r.is_habit and not r.is_fluid_habit}
        for h in habits:
            if h.is_fluid_habit or is_paused(h) or daily_cycle(h, user, today) is not None:
                included[h.id] = h
        return list(included.values())
    if kind == "tag":
        return list(await dao.get_reminders_by_tag(user.id, payload["query"]))
    if kind == "overdue":
        return list(await dao.get_overdue_pending_tasks(user.id, min_minutes_overdue=0))
    if kind == "week":
        return list(await dao.get_user_reminders_this_week(user.id, user.timezone))
    if kind == "recurring":
        return list(await dao.get_user_reminders_recurring(user.id))
    return list(await dao.get_user_reminders(user.id))


async def render_list(message, dao, user, l10n, context, page, *, edit=True, undo=None, undo_done=None):
    payload = json.loads(context.payload_json)
    tasks = await list_items(dao, user, payload)
    tasks = sorted(tasks, key=lambda r: (getattr(r, "priority", None) or 4, r.execution_time, r.id))
    pages = max(1, (len(tasks) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    shown = tasks[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    title = (
        l10n["habits_daily_title"]
        if payload["kind"] == "habits"
        else l10n["daily_title"]
        if payload["kind"] == "today"
        else l10n["tasks_header"].replace("*", "")
    )
    if payload["kind"] == "find":
        title = l10n["search_button"] + ": " + payload["query"]
    elif payload["kind"] == "tag":
        title = l10n["btn_filter_tags"] + ": #" + (payload["query"] or "")[:300]
    elif payload["kind"] in {"week", "overdue", "recurring"}:
        title = l10n["btn_filter_" + payload["kind"]]
    lines = [
        title,
        format_time(datetime.now(timezone.utc), user.timezone, True, "%d.%m.%Y %H:%M"),
        f"{page + 1}/{pages}",
    ]
    builder = InlineKeyboardBuilder()
    today = datetime.now(resolve_timezone(user.timezone)).date()
    if payload["kind"] in {"today", "habits"}:
        count_tasks = sum(not r.is_habit and not r.is_fluid_habit for r in tasks)
        count_habits = 0
        for r in tasks:
            if (r.is_habit or r.is_fluid_habit) and not is_paused(r):
                cycle = daily_cycle(r, user, today)
                done = (
                    (r.fluid_last_completed_date == today.isoformat())
                    if r.is_fluid_habit
                    else (r.habit_last_completed_due_at == cycle and not r.habit_undo_pending)
                )
                if (r.is_fluid_habit or cycle is not None) and not done:
                    count_habits += 1
        lines.append(l10n["daily_remaining"].format(tasks=count_tasks, habits=count_habits))

    from sqlalchemy import select

    from bot.database.dao.habit_event import cycle_key_for_fixed, cycle_key_for_fluid
    from bot.services.habit_completion import completion_token
    from bot.services.ui_context import now_utc

    undo_tokens = set(
        (
            await dao.session.scalars(
                select(UiContext.token).where(
                    UiContext.user_id == user.id,
                    UiContext.kind == "habitundo",
                    UiContext.result == "done",
                    UiContext.expires_at > now_utc(),
                )
            )
        ).all()
    )
    for index, task in enumerate(shown, start=page * PAGE_SIZE + 1):
        paused = is_paused(task)
        when = (
            l10n["habit_fluid_time_anytime"]
            if task.is_fluid_habit
            else format_time(task.execution_time, user.timezone, fmt="%d.%m %H:%M")
        )
        if task.execution_time < datetime.now(timezone.utc).replace(tzinfo=None) and not task.is_fluid_habit:
            when = l10n["btn_filter_overdue"] + " · " + when
        if paused:
            when = l10n["habit_paused_until"].format(date=format_time(task.paused_until, user.timezone, fmt="%d.%m"))
        lines.append(f"{index}. {task.reminder_text[:180]}\n{when}")
        if task.is_habit or task.is_fluid_habit:
            cycle = daily_cycle(task, user, today)
            if not task.is_fluid_habit and cycle is None:
                cycle = task.execution_time
            done = (
                (task.fluid_last_completed_date == today.isoformat())
                if task.is_fluid_habit
                else task.habit_last_completed_due_at == cycle and not task.habit_undo_pending
            )
            if not paused and (
                task.is_fluid_habit
                or to_utc_naive(cycle).replace(tzinfo=timezone.utc).astimezone(resolve_timezone(user.timezone)).date()
                == today
            ):
                suffix = (
                    today.isoformat()
                    if task.is_fluid_habit
                    else str(int(to_utc_naive(cycle).replace(tzinfo=timezone.utc).timestamp()))
                )
                key = cycle_key_for_fluid(today.isoformat()) if task.is_fluid_habit else cycle_key_for_fixed(cycle)
                can_undo = completion_token(user.id, task.id, key) in undo_tokens
                builder.button(
                    text=f"{'↩' if done and can_undo else '✅'} {index}",
                    callback_data=(
                        callbacks(context, page, "habitundo" if done else "habitdone", task.id) + f":{suffix}"
                    )
                    if not done or can_undo
                    else "noop",
                )
        else:
            builder.button(
                text=f"✅ {index}",
                callback_data=callbacks(context, page, "done", task.id)
                + f":{int(to_utc_naive(task.execution_time).replace(tzinfo=timezone.utc).timestamp())}",
            )
        builder.button(text=f"{l10n['btn_edit']} {index}", callback_data=callbacks(context, page, "detail", task.id))
        builder.adjust(2, repeat=True)
    if not shown:
        lines.append(
            l10n["find_no_results"].format(query=payload["query"])
            if payload["kind"] == "find"
            else l10n["no_tasks"]
            if payload["kind"] == "all"
            else l10n["habit_no_active"]
            if payload["kind"] == "habits"
            else l10n["find_no_results_filter"]
        )
    if page > 0:
        builder.row(_button("◀", f"nav:{context.token}:{page - 1}"))
    if page + 1 < pages:
        builder.row(_button("▶", f"nav:{context.token}:{page + 1}"))
    if undo_done:
        builder.row(_button(l10n["btn_done_undo"], callbacks(context, page, "undone", undo_done)))
    if undo:
        builder.row(_button(l10n["btn_done_undo"], callbacks(context, page, "undelete", undo)))
    builder.row(
        _button(l10n["btn_filter_today"], "uxlist:today"),
        _button(l10n["btn_filter_back"], "uxlist:all"),
        _button(l10n["search_button"], "ux_search"),
    )
    if payload["kind"] == "habits":
        builder.row(_button(l10n["add_habit"], "habit_back_dash"))
        from bot.config import config

        if config.MINI_APP_URL:
            from aiogram.types import InlineKeyboardButton, WebAppInfo

            builder.row(
                InlineKeyboardButton(text=l10n["btn_open_mini_app"], web_app=WebAppInfo(url=config.MINI_APP_URL))
            )
    else:
        builder.row(
            _button(l10n["btn_filter"], "tasks_filter_menu"),
            _button(l10n["btn_completed_tasks"], "show_completed"),
            _button(l10n["btn_refresh"], f"nav:{context.token}:{page}"),
        )
    send = message.edit_text if edit else message.answer
    await send("\n\n".join(lines), reply_markup=builder.as_markup(), parse_mode=None)


@router.callback_query(F.data.startswith("uxlist:"))
async def new_list(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any], state: FSMContext
) -> None:
    await state.clear()
    kind = callback.data.split(":", 1)[1]
    if kind not in {"all", "today", "habits"}:
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    await open_list(callback.message, reminder_dao, user, l10n, kind=kind, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("nav:"))
async def navigate(
    callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any], state: FSMContext
) -> None:
    try:
        _, token, raw = callback.data.split(":")
        page = int(raw)
    except ValueError:
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    context = await get_context(reminder_dao.session, token, user.id, "list")
    if not context:
        return await callback.answer(l10n["context_expired"], show_alert=True)
    await state.clear()
    await render_list(callback.message, reminder_dao, user, l10n, context, page)
    await callback.answer()


@router.callback_query(F.data == "ux_search")
async def search_prompt(callback: CallbackQuery, state: FSMContext, l10n: dict[str, Any]) -> None:
    await state.clear()
    await state.set_state(ExperienceState.searching)
    await callback.message.answer(l10n["search_prompt"])
    await callback.answer()


@router.message(ExperienceState.searching, F.text, ~F.text.startswith("/"))
async def search_input(
    message: Message, state: FSMContext, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    query = message.text.strip()
    if not query or len(query) > 300:
        await message.answer(l10n["search_prompt"])
        return
    await state.clear()
    await open_list(message, reminder_dao, user, l10n, kind="find", query=query)


async def return_to_list(message, state_data, dao, user, l10n):
    context = await get_context(dao.session, state_data.get("nav_token"), user.id, "list")
    if context:
        await render_list(message, dao, user, l10n, context, state_data.get("nav_page", 0), edit=False)


@router.callback_query(F.data.startswith("ux:"))
async def task_action(
    callback: CallbackQuery,
    state: FSMContext,
    reminder_dao: ReminderDAO,
    habit_event_dao: HabitEventDAO,
    scheduler_service: SchedulerService,
    user: User,
    l10n: dict[str, Any],
) -> None:
    try:
        _, token, raw_page, action, raw_id, *cycle = callback.data.split(":")
        page, rid = int(raw_page), int(raw_id)
    except ValueError:
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    context = await get_context(reminder_dao.session, token, user.id, "list")
    if not context:
        return await callback.answer(l10n["context_expired"], show_alert=True)
    task = await reminder_dao.get_owned(rid, user.id, include_pending_delete=action == "undelete")
    if not task:
        return await callback.answer(l10n["item_not_found"], show_alert=True)
    await state.clear()
    await state.update_data(nav_token=token, nav_page=page)
    if action == "detail":
        from bot.handlers.reminders_shared import _rrule_text

        builder = InlineKeyboardBuilder()
        actions = [("rename_button", "rename"), ("btn_delete", "delete")]
        if task.is_fluid_habit:
            actions.insert(1, ("fluid_mode_edit", "fluidmode"))
        else:
            actions[1:1] = [("btn_edit_time", "time"), ("btn_snooze", "snooze"), ("btn_task_settings", "settings")]
        for key, name in actions:
            builder.row(_button(l10n[key], callbacks(context, page, name, rid)))
        if task.is_habit or task.is_fluid_habit:
            builder.row(_button(l10n["habit_btn_pause"], f"habit_pause_{rid}"))
        builder.row(_button(l10n["close_settings"], f"nav:{token}:{page}"))
        schedule = (
            (
                l10n["habit_fluid_time_anytime"]
                + " · "
                + l10n["habit_fluid_mode_ask_time" if task.fluid_mode == "ask_time" else "habit_fluid_mode_brief_only"]
            )
            if task.is_fluid_habit
            else format_time(task.execution_time, user.timezone, True, "%d.%m.%Y %H:%M")
            + "\n"
            + _rrule_text(task, l10n)
        )
        progress = ""
        if task.is_habit or task.is_fluid_habit:
            from bot.services.habit_reports import compute_habit_score

            history = await habit_event_dao.get_events_for_reminder(task.id)
            current = task.fluid_streak_current if task.is_fluid_habit else task.habit_streak_current
            best = task.fluid_streak_best if task.is_fluid_habit else task.habit_streak_best
            progress = (
                "\n"
                + l10n["habit_detail_progress"].format(score=compute_habit_score(history), current=current, best=best)
                + "\n"
                + l10n["habit_score_explainer"]
            )
            if is_paused(task):
                progress += "\n" + l10n["habit_paused_until"].format(
                    date=format_time(task.paused_until, user.timezone, fmt="%d.%m.%Y")
                )
        await callback.message.edit_text(
            f"{task.reminder_text}\n{schedule}{progress}",
            reply_markup=builder.as_markup(),
            parse_mode=None,
        )
    elif action == "fluidmode" and task.is_fluid_habit:
        builder = InlineKeyboardBuilder()
        builder.row(_button(l10n["habit_fluid_mode_brief_only"], callbacks(context, page, "modebrief", rid)))
        builder.row(_button(l10n["habit_fluid_mode_ask_time"], callbacks(context, page, "modeask", rid)))
        builder.row(_button(l10n["close_settings"], f"nav:{token}:{page}"))
        await callback.message.edit_text(
            l10n["fluid_mode_change_note"], reply_markup=builder.as_markup(), parse_mode=None
        )
    elif action in {"modebrief", "modeask"} and task.is_fluid_habit:
        task.fluid_mode = "brief_only" if action == "modebrief" else "ask_time"
        await reminder_dao.session.commit()
        await render_list(callback.message, reminder_dao, user, l10n, context, page)
    elif action == "rename":
        await state.update_data(rename_id=rid)
        await state.set_state(ExperienceState.renaming)
        await callback.message.answer(l10n["rename_prompt"])
    elif action in {"time", "settings", "snooze"}:
        from bot.handlers.reminders_listing import callback_task_settings
        from bot.handlers.reminders_snooze import callback_snooze_show
        from bot.handlers.reminders_wizard import callback_edit_edit

        if action == "time":
            await callback_edit_edit(
                callback.model_copy(update={"data": f"edit_edit_{rid}"}), reminder_dao, state, l10n, user
            )
        elif action == "settings":
            await callback_task_settings(
                callback.model_copy(update={"data": f"task_settings_{rid}"}), reminder_dao, user, l10n
            )
        else:
            await callback_snooze_show(callback.model_copy(update={"data": f"snooze_show_{rid}"}), l10n)
        # Durable return remains available even while a nested legacy screen is open.
        await callback.message.answer(
            l10n["back_results"],
            reply_markup=InlineKeyboardBuilder()
            .row(_button(l10n["close_settings"], f"nav:{token}:{page}"))
            .as_markup(),
        )
    elif action in {"delete", "undelete"}:
        from bot.handlers.reminders_listing import callback_undo_delete
        from bot.handlers.reminders_shared import _soft_delete_reminder

        if action == "delete":
            if not await _soft_delete_reminder(task, reminder_dao, scheduler_service):
                return await callback.answer(l10n["schedule_error"], show_alert=True)
        else:
            await callback_undo_delete(
                callback.model_copy(update={"data": f"undo_del_{rid}"}), reminder_dao, scheduler_service, user, l10n
            )
        await render_list(
            callback.message, reminder_dao, user, l10n, context, page, undo=rid if action == "delete" else None
        )
    elif action == "done":
        if len(cycle) != 1 or cycle[0] != str(
            int(to_utc_naive(task.execution_time).replace(tzinfo=timezone.utc).timestamp())
        ):
            return await callback.answer(l10n["context_expired"], show_alert=True)
        if task.status == "completed":
            return await callback.answer(l10n["already_done"])
        await reminder_dao.mark_done(rid)
        if task.is_recurring:
            task.completed_for_execution_time = task.execution_time
        await reminder_dao.session.commit()
        if not task.is_recurring:
            scheduler_service.remove_reminder_job(rid)
        scheduler_service.remove_nagging_job(rid)
        await render_list(callback.message, reminder_dao, user, l10n, context, page, undo_done=rid)
    elif action == "undone":
        from bot.handlers.reminders_completion import callback_done_undo

        await callback_done_undo(
            callback.model_copy(update={"data": f"done_undo_{rid}"}),
            reminder_dao,
            habit_event_dao,
            scheduler_service,
            user,
            l10n,
        )
        await render_list(callback.message, reminder_dao, user, l10n, context, page)
    elif action in {"habitdone", "habitundo"}:
        await habit_action(callback, task, cycle, action, reminder_dao, habit_event_dao, scheduler_service, user, l10n)
        await render_list(callback.message, reminder_dao, user, l10n, context, page)
    else:
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    if action not in {"time", "settings", "snooze", "undelete", "undone", "habitdone", "habitundo"}:
        await callback.answer()


@router.message(ExperienceState.renaming, F.text, ~F.text.startswith("/"))
async def rename_input(
    message: Message, state: FSMContext, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    data = await state.get_data()
    name = message.text.strip()
    if not name or len(name) > 1000:
        return await message.answer(l10n["rename_prompt"])
    task = await reminder_dao.get_owned(data.get("rename_id"), user.id)
    if not task:
        await state.clear()
        return await message.answer(l10n["item_not_found"])
    task.reminder_text = name
    await reminder_dao.session.commit()
    await state.clear()
    await message.answer(l10n["rename_saved"])
    await return_to_list(message, data, reminder_dao, user, l10n)


async def habit_action(callback, task, cycle, action, dao, events, scheduler, user, l10n):
    from bot.services.habit_completion import set_daily_completion

    today = datetime.now(resolve_timezone(user.timezone)).date()
    if not cycle or is_paused(task):
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    if task.is_fluid_habit:
        if cycle[0] != today.isoformat():
            return await callback.answer(l10n["invalid_action"], show_alert=True)
        due = cycle[0]
    else:
        try:
            due = datetime.fromtimestamp(int(cycle[0]), timezone.utc).replace(tzinfo=None)
        except (ValueError, OverflowError):
            return await callback.answer(l10n["invalid_action"], show_alert=True)
        candidates = [
            v
            for v in (
                (task.habit_last_completed_due_at, task.habit_active_due_at, task.execution_time)
                if action == "habitundo"
                else (task.execution_time, task.habit_active_due_at, task.habit_last_completed_due_at)
            )
            if v is not None and int(to_utc_naive(v).replace(tzinfo=timezone.utc).timestamp()) == int(cycle[0])
        ]
        if candidates:
            due = to_utc_naive(candidates[0])
        if (
            due.replace(tzinfo=timezone.utc).astimezone(resolve_timezone(user.timezone)).date() != today
            or not candidates
        ):
            return await callback.answer(l10n["invalid_action"], show_alert=True)
    changed = await set_daily_completion(dao, events, task, user, due, undo=action == "habitundo")
    if changed:
        scheduler.remove_nagging_job(task.id)
        if task.is_fluid_habit:
            scheduler.remove_reminder_job(task.id)
        elif action == "habitundo" and due <= datetime.now(timezone.utc).replace(tzinfo=None):
            from datetime import timedelta

            scheduler.schedule_reminder(
                task.id, datetime.now(timezone.utc) + timedelta(minutes=15), is_nagging=task.is_nagging
            )
    await callback.answer(l10n["btn_done"] if changed else l10n["already_done"])


def daily_cycle(task, user, today):
    """The current local day's due cycle, including an early-completed slot."""
    tz = resolve_timezone(user.timezone)
    values = (
        (task.habit_last_completed_due_at, task.execution_time, task.habit_active_due_at)
        if not task.habit_undo_pending
        else (task.execution_time, task.habit_active_due_at)
    )
    for value in values:
        if value is not None and to_utc_naive(value).replace(tzinfo=timezone.utc).astimezone(tz).date() == today:
            return to_utc_naive(value)
    return None


@router.callback_query(
    F.data.in_({"tasks_filter_today", "tasks_filter_week", "tasks_filter_overdue", "tasks_filter_recurring"})
)
async def durable_filter(callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]) -> None:
    await open_list(
        callback.message, reminder_dao, user, l10n, kind=callback.data.removeprefix("tasks_filter_"), edit=True
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tasks_tag:"))
async def durable_tag(callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]) -> None:
    await open_list(
        callback.message, reminder_dao, user, l10n, kind="tag", query=callback.data.partition(":")[2], edit=True
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rename_task_"))
async def rename_saved(
    callback: CallbackQuery, state: FSMContext, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]
) -> None:
    try:
        rid = int(callback.data.removeprefix("rename_task_"))
    except ValueError:
        return await callback.answer(l10n["invalid_action"], show_alert=True)
    if not await reminder_dao.get_owned(rid, user.id):
        return await callback.answer(l10n["item_not_found"], show_alert=True)
    data = await state.get_data()
    context = (
        await get_context(reminder_dao.session, data.get("nav_token"), user.id, "list")
        if data.get("nav_token")
        else None
    )
    if not context:
        context = await create_context(reminder_dao.session, user.id, "list", {"kind": "all", "query": None})
    await state.update_data(nav_token=context.token, nav_page=data.get("nav_page", 0), rename_id=rid)
    await state.set_state(ExperienceState.renaming)
    await callback.message.answer(l10n["rename_prompt"])
    await callback.answer()


@router.callback_query(F.data == "tasks_page_0")
async def all_tasks_alias(callback: CallbackQuery, reminder_dao: ReminderDAO, user: User, l10n: dict[str, Any]) -> None:
    await open_list(callback.message, reminder_dao, user, l10n, edit=True)
    await callback.answer()


# Re-exports preserve the initial experience entry points while each feature owns its router.
from bot.handlers.notification_preferences import apply_notifications, offer_notifications  # noqa: E402, F401
from bot.handlers.notification_preferences import router as notification_router  # noqa: E402
from bot.handlers.recovery_review import batch_action, begin_batch  # noqa: E402, F401
from bot.handlers.recovery_review import router as recovery_router  # noqa: E402
from bot.handlers.tag_navigation import router as tag_router  # noqa: E402

router.include_router(recovery_router)
router.include_router(notification_router)
router.include_router(tag_router)
