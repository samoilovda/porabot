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
from bot.database.dao.user import UserDAO
from bot.database.models import Reminder, User

logger = logging.getLogger(__name__)

# Without this window, the very first deploy would generate a "missed" event
# for every habit's ancient habit_active_due_at.
RETROSPECTION_WINDOW = timedelta(days=7)
# For FREQ=DAILY habits, due + 24h is exactly the next scheduled notification —
# the same grace period apply_habit_streak_completion uses.
GRACE = timedelta(hours=24)


async def _sweep_fixed_habits(session, reminder_dao: ReminderDAO, habit_event_dao: HabitEventDAO, user: User) -> None:
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    result = await session.execute(
        select(Reminder).where(
            Reminder.user_id == user.id,
            Reminder.is_habit.is_(True),
            Reminder.is_fluid_habit.is_(False),
            Reminder.status == "pending",
        )
    )
    for reminder in result.scalars().all():
        due = reminder.habit_active_due_at
        if due is None:
            continue
        if reminder.habit_last_completed_due_at == due:
            continue  # already counted as done
        created_at = reminder.created_at
        if created_at is not None and due < created_at:
            continue  # don't invent cycles predating the habit
        if now_utc_naive < due + GRACE:
            continue  # grace period hasn't elapsed yet
        if now_utc_naive >= due + GRACE + RETROSPECTION_WINDOW:
            continue  # too old — don't backfill ancient misses

        recorded = await habit_event_dao.record(
            reminder=reminder,
            user_tz=user.timezone,
            outcome="missed",
            source="auto",
            due_at_utc_naive=due,
        )
        if recorded:
            reminder.habit_streak_current = 0


async def _sweep_fluid_habits(session, reminder_dao: ReminderDAO, habit_event_dao: HabitEventDAO, user: User) -> None:
    try:
        tz = pytz.timezone(user.timezone)
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


async def _sweep_user(session, user_id: int) -> None:
    user_dao = UserDAO(session)
    user = await user_dao.get_by_id(user_id)
    if not user:
        return

    reminder_dao = ReminderDAO(session)
    habit_event_dao = HabitEventDAO(session)

    await _sweep_fixed_habits(session, reminder_dao, habit_event_dao, user)
    await _sweep_fluid_habits(session, reminder_dao, habit_event_dao, user)


async def sweep_habit_cycles() -> None:
    """Detect and record auto-skipped habit cycles for every user."""
    from bot.services.scheduler import _instance
    if not _instance:
        logger.error("Failed to sweep habit cycles: SchedulerService not initialized")
        return

    session_pool_factory = _instance.session_pool

    try:
        async with session_pool_factory() as session:
            result = await session.execute(
                select(User.id)
                .distinct()
                .join(Reminder)
                .where(
                    Reminder.is_habit.is_(True),
                    Reminder.status == "pending",
                )
            )
            user_ids = result.scalars().all()

        for uid in user_ids:
            async with session_pool_factory() as session:
                try:
                    await _sweep_user(session, uid)
                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    logger.error("Error sweeping habits for user %s: %s", uid, e, exc_info=True)

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
    )
