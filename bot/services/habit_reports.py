"""
Weekly / monthly habit reports.

Runs as a minutely APScheduler cron job. For each user with reports enabled,
fires exactly once at their configured weekday+time, sends the weekly
summary, and — on the last occurrence of that weekday in the month — a
monthly summary right after it.
"""

import logging
from datetime import date, datetime, timedelta

import pytz
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select

from bot.database.dao.habit_event import HabitEventDAO
from bot.database.dao.user import UserDAO
from bot.database.models import Reminder, User
from bot.utils.markdown import escape_markdown_legacy

logger = logging.getLogger(__name__)


async def _send_safe(bot: Bot, user_id: int, text: str) -> None:
    """Send a report message, suppressing bot-blocked / bad-request errors."""
    try:
        await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
    except TelegramForbiddenError:
        logger.warning("User %s has blocked the bot — skipping habit report.", user_id)
    except TelegramBadRequest as e:
        logger.error("Bad request sending habit report to %s: %s", user_id, e)
    except Exception as e:
        logger.error("Failed to send habit report to %s: %s", user_id, e, exc_info=True)


def _aggregate(events) -> list[dict]:
    """Group events by reminder_id into per-habit done/not_today/rate stats."""
    grouped: dict[int, dict] = {}
    for event in events:
        bucket = grouped.setdefault(
            event.reminder_id,
            {"habit_text": event.habit_text, "done": 0, "not_today": 0, "last_local_date": event.local_date},
        )
        if event.outcome == "done":
            bucket["done"] += 1
        elif event.outcome in ("not_today", "missed"):
            bucket["not_today"] += 1
        # Habit name is a snapshot per event; keep the most recent one.
        if event.local_date >= bucket["last_local_date"]:
            bucket["habit_text"] = event.habit_text
            bucket["last_local_date"] = event.local_date

    rows = []
    for reminder_id, bucket in grouped.items():
        total = bucket["done"] + bucket["not_today"]
        if total == 0:
            continue
        rows.append(
            {
                "reminder_id": reminder_id,
                "habit_text": bucket["habit_text"],
                "done": bucket["done"],
                "not_today": bucket["not_today"],
                "total": total,
                "rate": round(bucket["done"] / total * 100),
            }
        )
    # Best weeks read as a reason to keep going, not a list of failures.
    rows.sort(key=lambda r: (-r["rate"], -r["done"]))
    return rows


def _current_streak_label(reminder: Reminder | None) -> int:
    if reminder is None:
        return 0  # habit was deleted since — no streak to show
    if getattr(reminder, "is_fluid_habit", False):
        return max(0, int(reminder.fluid_streak_current or 0))
    return max(0, int(reminder.habit_streak_current or 0))


def _build_report_text(
    *,
    title: str,
    start: date,
    end: date,
    rows: list[dict],
    reminders_by_id: dict[int, Reminder],
    l10n: dict,
) -> str:
    header = title.format(start=start.strftime("%d.%m"), end=end.strftime("%d.%m"))
    lines = [header, ""]

    total_done = 0
    total_not_today = 0
    for row in rows:
        total_done += row["done"]
        total_not_today += row["not_today"]
        streak = _current_streak_label(reminders_by_id.get(row["reminder_id"]))
        streak_suffix = f" · 🔥 {streak}" if streak > 0 else ""
        lines.append(
            l10n.get("habit_report_line", "🫧 {habit} — {done}/{total} ({rate}%){streak}").format(
                habit=escape_markdown_legacy(row["habit_text"]),
                done=row["done"],
                total=row["total"],
                rate=row["rate"],
            )
            + streak_suffix
        )

    total_all = total_done + total_not_today
    total_rate = round(total_done / total_all * 100) if total_all else 0
    lines.append("")
    lines.append(
        l10n.get("habit_report_total", "**Итого:** ✅ {done} · ❌ {not_done} ({rate}%)").format(
            done=total_done,
            not_done=total_not_today,
            rate=total_rate,
        )
    )
    return "\n".join(lines)


async def _process_user_reports(session, bot: Bot, user: User, now_local: datetime) -> None:
    from bot.lexicon import get_l10n

    l10n = get_l10n(user.language)
    habit_event_dao = HabitEventDAO(session)

    result = await session.execute(select(Reminder).where(Reminder.user_id == user.id))
    reminders_by_id = {r.id: r for r in result.scalars().all()}

    today_local = now_local.date()

    week_start = today_local - timedelta(days=6)
    weekly_events = await habit_event_dao.get_events_in_range(
        user.id, week_start.isoformat(), today_local.isoformat()
    )
    if weekly_events:
        rows = _aggregate(weekly_events)
        if rows:
            text = _build_report_text(
                title=l10n.get("habit_report_weekly_title", "📊 **Итоги недели**"),
                start=week_start,
                end=today_local,
                rows=rows,
                reminders_by_id=reminders_by_id,
                l10n=l10n,
            )
            await _send_safe(bot, user.id, text)

    # "+7 days lands in a different month" is the only reliable way to detect
    # the last occurrence of this weekday in the month — day-number arithmetic
    # (25-31) breaks across months of different lengths.
    if (today_local + timedelta(days=7)).month != today_local.month:
        month_start = today_local.replace(day=1)
        monthly_events = await habit_event_dao.get_events_in_range(
            user.id, month_start.isoformat(), today_local.isoformat()
        )
        if monthly_events:
            rows = _aggregate(monthly_events)
            if rows:
                text = _build_report_text(
                    title=l10n.get("habit_report_monthly_title", "📊 **Итоги месяца**"),
                    start=month_start,
                    end=today_local,
                    rows=rows,
                    reminders_by_id=reminders_by_id,
                    l10n=l10n,
                )
                await _send_safe(bot, user.id, text)


async def process_habit_reports() -> None:
    """Send weekly/monthly habit reports to users whose scheduled slot is now."""
    from bot.services.scheduler import _instance
    if not _instance:
        logger.error("Failed to process habit reports: SchedulerService not initialized")
        return

    bot = _instance.bot
    session_pool_factory = _instance.session_pool

    try:
        async with session_pool_factory() as session:
            result = await session.execute(
                select(User.id)
                .distinct()
                .join(Reminder)
                .where(
                    User.habit_reports_enabled.is_(True),
                    Reminder.is_habit.is_(True),
                )
            )
            user_ids = result.scalars().all()

        for uid in user_ids:
            async with session_pool_factory() as session:
                try:
                    user_dao = UserDAO(session)
                    user = await user_dao.get_by_id(uid)
                    if not user:
                        continue

                    try:
                        tz = pytz.timezone(user.timezone)
                    except Exception:
                        tz = pytz.UTC
                    now_local = datetime.now(tz)

                    # Habit reports intentionally ignore quiet hours: the default
                    # report time (23:50) falls inside the default quiet window
                    # (23:00-07:00), so honoring it would mean a user who enabled
                    # quiet hours never receives the report they explicitly set up.
                    if now_local.strftime("%H:%M") != getattr(user, "habit_report_time", "23:50"):
                        continue
                    if now_local.weekday() != int(getattr(user, "habit_report_weekday", 6)):
                        continue

                    await _process_user_reports(session, bot, user, now_local)
                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    logger.error("Error building habit report for user %s: %s", uid, e, exc_info=True)

    except Exception as e:
        logger.error("Error in habit reports job: %s", e, exc_info=True)


def setup_habit_reports(scheduler) -> None:
    """Register the minutely cron job for weekly/monthly habit reports."""
    logger.info("Registering minutely habit reports cron job")
    scheduler.add_job(
        process_habit_reports,
        "cron",
        minute="*",
        id="habit_reports",
        replace_existing=True,
    )
