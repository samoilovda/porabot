"""
Habit auto-skip sweeper.

Writes `missed` habit events from live DB state instead of hooking into the
notification moment — pinning misses to "reminder fired" loses pending
cycles when the bot is down, the user has blocked the bot, or nagging is
disabled. This sweeper re-derives misses from Reminder rows every minute
regardless of whether any notification ever went out.
"""

import logging
from datetime import datetime, timedelta, timezone

import pytz
from sqlalchemy import select

from bot.database.dao.habit_event import HabitEventDAO, cycle_key_for_fluid
from bot.database.dao.reminder import ReminderDAO
from bot.database.models import Reminder, User
from bot.utils.time_ext import habit_cycle_grace, occurrences_between_utc
from bot.utils.timezone import resolve_timezone

logger = logging.getLogger(__name__)

# Without this window, the very first deploy would generate a "missed" event
# for every habit's ancient habit_active_due_at.
RETROSPECTION_WINDOW = timedelta(days=7)
# Minimum grace before a cycle can become a miss; the real grace runs until
# the rule's next occurrence (habit_cycle_grace) — exactly 24h for daily.
GRACE = timedelta(hours=24)


def _second(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _candidate_cycles(reminder, user_tz: str, now_utc_naive: datetime) -> list[datetime]:
    """Due times of this habit's cycles that may need a "missed" event.

    B-09 (2026-11-04 audit): only habit_active_due_at used to be checked —
    and that column only advances when a notification actually fires. So a
    bot outage of N days, or a user who blocked the bot (sends stop after
    FORBIDDEN_STRIKES_LIMIT), produced at most ONE missed event however
    many cycles went by. Now every occurrence of the habit's own rule inside
    the retrospection window is a candidate. Enumeration starts at the
    series anchor (rrule_dtstart), which moves when the habit's time is
    edited, so cycles from before an edit are never reinterpreted.
    """
    candidates: dict[int, datetime] = {}
    if reminder.habit_active_due_at is not None:
        candidates[_second(reminder.habit_active_due_at)] = reminder.habit_active_due_at
    anchor = reminder.rrule_dtstart or reminder.execution_time
    if reminder.is_recurring and reminder.rrule_string and anchor is not None:
        window_start = now_utc_naive - GRACE - RETROSPECTION_WINDOW
        try:
            for due in occurrences_between_utc(reminder.rrule_string, anchor, user_tz, window_start, now_utc_naive):
                candidates.setdefault(_second(due), due)
        except (ValueError, TypeError):
            logger.warning("Invalid rrule on habit %s — sweeping its active cycle only.", reminder.id)
    return sorted(candidates.values())


async def _sweep_fixed_habits(session, reminder_dao: ReminderDAO, habit_event_dao: HabitEventDAO, user: User) -> None:
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    result = await session.execute(
        select(Reminder).where(
            Reminder.user_id == user.id,
            Reminder.is_habit.is_(True),
            Reminder.is_fluid_habit.is_(False),
            Reminder.status == "pending",
            Reminder.pending_delete_at.is_(None),  # Hide soft-deleted (undo window)
        )
    )
    for reminder in result.scalars().all():
        rule = reminder.rrule_string if reminder.is_recurring else None
        anchor = reminder.rrule_dtstart or reminder.execution_time
        last_done = reminder.habit_last_completed_due_at
        last_done_s = last_done.replace(microsecond=0) if last_done is not None else None
        for due in _candidate_cycles(reminder, user.timezone, now_utc_naive):
            # rrule occurrences carry no microseconds; cycle keys are whole
            # seconds too — compare at that precision.
            if due.replace(microsecond=0) == last_done_s:
                continue  # already counted as done
            if reminder.paused_until is not None and due < reminder.paused_until:
                continue  # cycle fell inside a pause (step 13) — never a miss
            created_at = reminder.created_at
            if created_at is not None and due < created_at:
                continue  # don't invent cycles predating the habit
            # B-08: the cycle stays completable until the rule's next
            # occurrence (min 24h), so it only becomes a miss after that.
            grace = habit_cycle_grace(rule, anchor, user.timezone, due)
            if now_utc_naive < due + grace:
                continue  # grace period hasn't elapsed yet
            if now_utc_naive >= due + grace + RETROSPECTION_WINDOW:
                continue  # too old — don't backfill ancient misses

            recorded = await habit_event_dao.record(
                reminder=reminder,
                user_tz=user.timezone,
                outcome="missed",
                source="auto",
                due_at_utc_naive=due,
            )
            if recorded and (last_done_s is None or due.replace(microsecond=0) > last_done_s):
                reminder.habit_streak_current = 0


async def _sweep_fluid_habits(session, reminder_dao: ReminderDAO, habit_event_dao: HabitEventDAO, user: User) -> None:
    try:
        tz = resolve_timezone(user.timezone)
    except Exception:
        tz = pytz.UTC

    today_local = datetime.now(tz).date()
    yesterday_local = today_local - timedelta(days=1)
    yesterday_str = yesterday_local.isoformat()

    fluid_habits = await reminder_dao.get_active_fluid_habits(user.id)
    for habit in fluid_habits:
        # Independent of daily_briefs — a user with briefs disabled (or whose
        # evening brief lands inside quiet hours) would otherwise never reset
        # a stale streak, since that used to be the only call site.
        await reminder_dao.reset_stale_fluid_streak_if_needed(habit.id, user.timezone)

        if habit.fluid_last_completed_date == yesterday_str:
            continue

        if habit.paused_until is not None:
            paused_local_date = habit.paused_until.replace(tzinfo=timezone.utc).astimezone(tz).date()
            if yesterday_local < paused_local_date:
                continue  # yesterday fell inside a pause (step 13) — never a miss

        created_at = habit.created_at
        if created_at is not None:
            created_local_date = created_at.replace(tzinfo=timezone.utc).astimezone(tz).date()
            if yesterday_local < created_local_date:
                continue  # habit created today — yesterday isn't a real cycle

        cycle_key = cycle_key_for_fluid(yesterday_str)
        if await habit_event_dao.has_event_for_cycle(habit.id, cycle_key):
            continue

        await habit_event_dao.record(
            reminder=habit,
            user_tz=user.timezone,
            outcome="missed",
            source="auto",
            local_date=yesterday_str,
        )


async def _sweep_user(session, user) -> None:
    reminder_dao = ReminderDAO(session)
    habit_event_dao = HabitEventDAO(session)

    await _sweep_fixed_habits(session, reminder_dao, habit_event_dao, user)
    await _sweep_fluid_habits(session, reminder_dao, habit_event_dao, user)


async def sweep_habit_cycles() -> None:
    """Detect and record auto-skipped habit cycles for every user."""
    from bot.context import get_context
    try:
        ctx = get_context()
    except RuntimeError:
        logger.error("Failed to sweep habit cycles: AppContext not set")
        return

    session_pool_factory = ctx.session_pool

    try:
        # 2.3: fetch full User rows in the same broad query that already
        # narrows candidates to "has a pending habit" — _sweep_user used to
        # re-fetch the same row via UserDAO.get_by_id inside its own
        # session, an extra SELECT per candidate for data already sitting
        # in this result set. Detached rows from an expire_on_commit=False
        # session keep their loaded scalar attributes (id, timezone, ...),
        # which is all _sweep_fixed_habits/_sweep_fluid_habits ever read.
        async with session_pool_factory() as session:
            # 2.7: deliberately NOT filtering out User.bot_blocked_at here,
            # unlike daily_briefs/missed_recovery/habit_reports. This job
            # never sends a Telegram message — it only records habit_events
            # ("missed", ...) so streaks and reports stay accurate. A user
            # who blocked the bot can still unblock it later, or check
            # progress via the Mini App; freezing their history at the
            # moment they blocked the bot would be a real data-correctness
            # regression, not just a performance win.
            result = await session.execute(
                select(User)
                .distinct()
                .join(Reminder)
                .where(
                    Reminder.is_habit.is_(True),
                    Reminder.status == "pending",
                )
            )
            candidates = result.scalars().all()

        for user in candidates:
            async with session_pool_factory() as session:
                try:
                    await _sweep_user(session, user)
                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    logger.error("Error sweeping habits for user %s: %s", user.id, e, exc_info=True)

    except Exception as e:
        logger.error("Error in habit sweeper job: %s", e, exc_info=True)


def setup_habit_sweeper(scheduler) -> None:
    """Register the minutely cron job for habit auto-skip detection."""
    logger.info("Registering minutely habit sweeper cron job")
    scheduler.add_job(
        sweep_habit_cycles,
        "cron",
        minute="*",
        id="habit_sweeper",
        replace_existing=True,
        # 1.4: memory jobstore, not the default SQLAlchemyJobStore — this
        # job is re-registered with replace_existing=True on every single
        # startup anyway, so there is no benefit to persisting it, and the
        # persistent store is reserved for reminder jobs (see bot/__main__.py).
        jobstore="memory",
    )
