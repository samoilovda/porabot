"""
Daily Briefs Service — Sends Morning/Evening Summary Messages.

Runs as an hourly APScheduler cron job. For each user with active tasks,
checks if it is 09:00 (morning) or 23:00 (evening) in their local timezone
and sends the appropriate summary.
"""

import logging
from datetime import datetime

import pytz
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from sqlalchemy import or_, select, update

from bot.database.dao.habit_event import HabitEventDAO, cycle_key_for_fluid
from bot.database.dao.reminder import ReminderDAO
from bot.database.dao.user import UserDAO
from bot.database.models import Reminder, User
from bot.keyboards.inline import (
    get_evening_wrapup_keyboard,
    get_fluid_completion_keyboard,
    get_fluid_pick_time_keyboard,
)
from bot.utils.markdown import escape_markdown, strip_markdown_escapes
from bot.utils.time_ext import format_time, is_quiet_hours

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_morning_text(tasks, user, l10n: dict) -> str:
    lines = [l10n.get("brief_morning", "🌅 **Доброе утро! План на сегодня:**\n")]
    for t in tasks:
        time_str = format_time(t.execution_time, user.timezone, user.show_utc_offset, "%H:%M")
        lines.append(f"▫️ `{time_str}`: {escape_markdown(t.reminder_text)}")
    return "\n".join(lines)


def _build_evening_text(completed, pending, user, l10n: dict) -> str:
    lines = [
        l10n.get("brief_evening_title", "🌙 **Итоги дня:**"),
        l10n.get("brief_evening_done", "✅ Выполнено: {count}").format(count=len(completed)),
        l10n.get("brief_evening_pending", "⏳ Осталось/Пропущено: {count}\n").format(count=len(pending)),
    ]
    for t in completed:
        time_str = format_time(t.execution_time, user.timezone, user.show_utc_offset, "%H:%M")
        lines.append(f"✅ ~{escape_markdown(t.reminder_text)}~ ({time_str})")
    for t in pending:
        time_str = format_time(t.execution_time, user.timezone, user.show_utc_offset, "%H:%M")
        lines.append(f"❌ {escape_markdown(t.reminder_text)} ({time_str})")  # BUG-4 fixed: closing paren added
    return "\n".join(lines)


async def _claim_brief_slot(session, *, user, column, today_str: str) -> bool:
    """Atomically mark today's brief slot as sent, returning False if lost.

    A plain read-then-write (check the date, send, then persist the flag)
    has a TOCTOU window: if this job ever runs concurrently — an overlapping
    deploy, a stray second process, anything not ruled out by APScheduler's
    own single-process max_instances=1 — two ticks can both read "not sent
    yet" before either commits, and both send the identical brief. A single
    conditional UPDATE makes the claim atomic: SQLite serializes writers, so
    only one caller's UPDATE can match the WHERE clause and actually change
    a row; a loser sees rowcount 0 and must not send.
    """
    result = await session.execute(
        update(User)
        .where(User.id == user.id, or_(column.is_(None), column != today_str))
        .values(**{column.key: today_str})
    )
    await session.commit()
    claimed = result.rowcount == 1
    if claimed:
        setattr(user, column.key, today_str)
    return claimed


async def _send_safe(bot: Bot, user_id: int, text: str, reply_markup=None) -> None:
    """Send a message, silently suppressing bot-blocked and bad-request errors.

    Falls back to plain text if Markdown entities fail to parse, so a stray
    unescaped character never causes the whole brief to be silently dropped.
    """
    try:
        await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown", reply_markup=reply_markup)
    except TelegramForbiddenError:
        logger.warning("User %s has blocked the bot — skipping brief.", user_id)
    except TelegramBadRequest as e:
        logger.error("Bad request sending brief to %s: %s — retrying without Markdown.", user_id, e)
        try:
            await bot.send_message(
                chat_id=user_id,
                text=strip_markdown_escapes(text),
                parse_mode=None,
                reply_markup=reply_markup,
            )
        except Exception as retry_e:
            logger.error("Retry without Markdown also failed for %s: %s", user_id, retry_e, exc_info=True)
    except Exception as e:
        logger.error("Failed to send brief to %s: %s", user_id, e, exc_info=True)


# ---------------------------------------------------------------------------
# Main job function (canonical — registered by setup_daily_briefs)
# ---------------------------------------------------------------------------

async def process_daily_briefs() -> None:
    """Process morning/evening briefs for all users with active tasks."""
    from bot.services.scheduler import _instance
    if not _instance:
        logger.error("Failed to process daily briefs: SchedulerService not initialized")
        return

    bot = _instance.bot
    session_pool_factory = _instance.session_pool
    logger.info("Starting hourly daily briefs check...")

    try:
        async with session_pool_factory() as session:

            # BUG-C1 FIX: Only fetch users who have PENDING (active) reminders
            # AND have briefs enabled. Previously also included "completed" which
            # caused every past user to be processed every minute indefinitely.
            result = await session.execute(
                select(User.id)
                .distinct()
                .join(Reminder)
                .where(
                    Reminder.status == "pending",
                    User.briefs_enabled.is_(True),
                )
            )
            user_ids = result.scalars().all()

        for uid in user_ids:
            async with session_pool_factory() as session:
                user_dao = UserDAO(session)
                user = await user_dao.get_by_id(uid)
                if not user:
                    continue

                # BUG-D1 FIX: isolate each user in its own try/except (mirroring
                # habit_reports.py). Previously the whole batch shared a single
                # try/except at the function level, so an error anywhere in one
                # user's processing (e.g. a transient SQLite "database is locked"
                # during commit — likely here since several per-minute cron jobs
                # share one sqlite file) aborted the rest of the batch, and left
                # that user's last_morning/evening_brief_date uncommitted even
                # though the brief had already been sent — causing the identical
                # brief to resend on the next tick.
                try:
                    try:
                        tz = pytz.timezone(user.timezone)
                    except Exception:
                        logger.warning("Invalid timezone '%s' for user %s, using UTC", user.timezone, user.id)
                        tz = pytz.UTC

                    local_time_str = datetime.now(tz).strftime("%H:%M")
                    # W1: the morning/evening brief itself is exempt from quiet hours —
                    # it fires exactly at the time the user configured, so suppressing
                    # it (e.g. default evening_brief_time == default quiet_hours_start)
                    # would silently make the brief unreachable. Quiet hours still
                    # apply to the ancillary fluid-habit prompts below.
                    is_quiet = is_quiet_hours(user, datetime.now(tz))
                    reminder_dao = ReminderDAO(session)
                    habit_event_dao = HabitEventDAO(session)

                    from bot.lexicon import get_l10n
                    l10n = get_l10n(user.language)
                    fluid_habits = await reminder_dao.get_active_fluid_habits(user.id)
                    today_str = datetime.now(tz).date().isoformat()

                    morning_brief_time = getattr(user, 'morning_brief_time', "09:00")
                    evening_brief_time = getattr(user, 'evening_brief_time', "23:00")
                    # If the user configured evening_brief_time at or before
                    # morning_brief_time, there's no valid morning..evening window
                    # to bound — fall back to an open-ended morning check instead
                    # of one that can never be true, which would otherwise
                    # silently and permanently suppress the morning brief.
                    has_valid_window = evening_brief_time > morning_brief_time
                    morning_due = (
                        local_time_str >= morning_brief_time
                        and (not has_valid_window or local_time_str < evening_brief_time)
                        and getattr(user, 'last_morning_brief_date', None) != today_str
                    )
                    evening_due = (
                        local_time_str >= evening_brief_time
                        and getattr(user, 'last_evening_brief_date', None) != today_str
                    )

                    if (
                        has_valid_window
                        and local_time_str >= evening_brief_time
                        and getattr(user, 'last_morning_brief_date', None) != today_str
                    ):
                        # The morning window (morning_brief_time..evening_brief_time)
                        # has already fully passed today — e.g. the bot was down, or
                        # briefs got enabled after evening_brief_time. Sending a
                        # "good morning" brief this late would be confusing; mark
                        # it as handled instead of sending a stale one.
                        user.last_morning_brief_date = today_str

                    if morning_due:
                        # Atomically claim today's morning slot before sending —
                        # see _claim_brief_slot. A lost claim means another
                        # (possibly concurrent) run already handled this user
                        # today, so skip the brief and its extras entirely.
                        claimed = await _claim_brief_slot(
                            session, user=user, column=User.last_morning_brief_date, today_str=today_str
                        )
                        if claimed:
                            tasks = await reminder_dao.get_today_pending_tasks(user.id, user.timezone)
                            if tasks:
                                text = _build_morning_text(tasks, user, l10n)
                                await _send_safe(bot, user.id, text)
                                logger.info("Morning brief sent to user %s", user.id)

                            try:
                                fluid_brief_only = [h for h in fluid_habits if (h.fluid_mode or "brief_only") == "brief_only"]
                                if fluid_brief_only:
                                    lines = [l10n.get("fluid_morning_title", "🌊 **Fluid habits for today:**")]
                                    for h in fluid_brief_only:
                                        lines.append(f"▫️ {escape_markdown(h.reminder_text)}")
                                    await _send_safe(bot, user.id, "\n".join(lines))

                                for h in fluid_habits:
                                    if is_quiet:
                                        continue
                                    if (h.fluid_mode or "brief_only") != "ask_time":
                                        continue
                                    if h.fluid_planned_date == today_str:
                                        continue
                                    await _send_safe(
                                        bot,
                                        user.id,
                                        l10n.get("fluid_pick_time_prompt", "🌊 **Plan your fluid habit:**\nPick today’s reminder time for: **{habit}**").format(habit=escape_markdown(h.reminder_text)),
                                        reply_markup=get_fluid_pick_time_keyboard(h.id, l10n),
                                    )
                                    logger.info("Fluid pick-time prompt sent to user %s for habit %s", user.id, h.id)
                            except Exception as e:
                                logger.error("Error sending fluid-habit morning extras for user %s: %s", user.id, e, exc_info=True)

                    elif evening_due:
                        # Also reset in habit_sweeper.sweep_habit_cycles every minute
                        # (W3) — briefs_enabled=False or a suppressed brief must not
                        # leave a stale streak stuck until the user reopens the app.
                        # Kept here too so the reset isn't delayed until evening_due
                        # for users who do get briefs.
                        for h in fluid_habits:
                            await reminder_dao.reset_stale_fluid_streak_if_needed(h.id, user.timezone)

                        # Same atomic claim as the morning branch above.
                        claimed = await _claim_brief_slot(
                            session, user=user, column=User.last_evening_brief_date, today_str=today_str
                        )
                        if claimed:
                            completed = await reminder_dao.get_today_completed_tasks(user.id, user.timezone)
                            pending = await reminder_dao.get_today_pending_tasks(user.id, user.timezone)
                            if completed or pending:
                                text = _build_evening_text(completed, pending, user, l10n)
                                await _send_safe(
                                    bot,
                                    user.id,
                                    text,
                                    reply_markup=get_evening_wrapup_keyboard(pending, l10n) if pending else None,
                                )
                                logger.info("Evening brief sent to user %s", user.id)

                            try:
                                # "Cycle resolved today" is defined by the presence of a habit
                                # event, not by fluid_last_completed_date — otherwise a user who
                                # tapped "Not today" this morning would be asked again tonight.
                                pending_fluid = [
                                    h
                                    for h in fluid_habits
                                    if not await habit_event_dao.has_event_for_cycle(h.id, cycle_key_for_fluid(today_str))
                                ]
                                if pending_fluid and not is_quiet:
                                    await _send_safe(
                                        bot,
                                        user.id,
                                        l10n.get("fluid_evening_check", "🌙 **Before evening wrap-up:**\nMark completed fluid habits:"),
                                        reply_markup=get_fluid_completion_keyboard(pending_fluid, l10n),
                                    )
                            except Exception as e:
                                logger.error("Error sending fluid-completion prompt for user %s: %s", user.id, e, exc_info=True)

                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    logger.error("Error processing daily brief for user %s: %s", uid, e, exc_info=True)

    except Exception as e:
        logger.error("Error in daily briefs job: %s", e, exc_info=True)


# ---------------------------------------------------------------------------
# Scheduler registration
# ---------------------------------------------------------------------------

def setup_daily_briefs(scheduler) -> None:
    """Register the minutely cron job for daily briefs. Call once at startup."""
    logger.info("Registering minutely daily briefs cron job")
    scheduler.add_job(
        process_daily_briefs,
        "cron",
        minute="*",
        id="daily_briefs_minutely",
        replace_existing=True,
    )
