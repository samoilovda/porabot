from datetime import datetime, timedelta

from sqlalchemy import text

from bot.database.engine import create_engine, dispose_engine, init_db


async def test_backfill_marks_legacy_overdue_pending_one_offs_as_already_delivered() -> None:
    """Regression: pre-existing overdue pending one-off reminders (from before
    last_fired_at existed) must not look "never delivered" after the column
    is added — reconcile_jobs_with_db would otherwise resend a notification
    the user already received, once for every such row, on first restart.
    """
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_db(engine)

        past = datetime.utcnow() - timedelta(days=1)
        future = datetime.utcnow() + timedelta(days=1)
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO users (id, timezone) VALUES (1, 'UTC')"))
            await conn.execute(
                text(
                    """
                    INSERT INTO reminders
                        (user_id, reminder_text, execution_time, status, is_recurring, is_nagging)
                    VALUES
                        (1, 'legacy overdue one-off', :past, 'pending', 0, 0),
                        (1, 'legacy future one-off', :future, 'pending', 0, 0),
                        (1, 'legacy overdue recurring', :past, 'pending', 1, 0)
                    """
                ),
                {"past": past, "future": future},
            )

        # Re-running init_db simulates the deploy that introduces the
        # last_fired_at column onto a DB that already has these legacy rows.
        await init_db(engine)

        async with engine.begin() as conn:
            result = await conn.execute(
                text("SELECT reminder_text, last_fired_at FROM reminders ORDER BY id")
            )
            rows = {r[0]: r[1] for r in result.fetchall()}

        assert rows["legacy overdue one-off"] is not None
        assert rows["legacy future one-off"] is None
        assert rows["legacy overdue recurring"] is None
    finally:
        await dispose_engine(engine)
