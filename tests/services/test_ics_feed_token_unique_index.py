"""docs/audits/2026-09-22-audit.md#a-26 — User.ics_feed_token had no
database-level uniqueness guarantee, just the low collision probability of
secrets.token_urlsafe(24). A real UNIQUE index now enforces the
one-token-per-lookup contract UserDAO.get_by_ics_feed_token relies on, and
init_db's soft migration adds it to a pre-existing table that predates the
index (same shape as _add_column_if_missing for columns).
"""

from sqlalchemy import inspect, text

from bot.database.engine import create_engine, dispose_engine, init_db


async def test_unique_index_is_created_on_a_legacy_table_missing_it() -> None:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            # A legacy `users` table predating the unique index entirely
            # (but with the column already present, so this exercises the
            # index-only migration path, not the column one).
            await conn.execute(
                text(
                    "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR, "
                    "timezone VARCHAR DEFAULT 'UTC', language VARCHAR, "
                    "ics_feed_token VARCHAR, created_at DATETIME)"
                )
            )
            await conn.execute(
                text("INSERT INTO users (id, timezone, ics_feed_token) VALUES (1, 'UTC', 'tok-a')")
            )
            await conn.execute(
                text("INSERT INTO users (id, timezone, ics_feed_token) VALUES (2, 'UTC', NULL)")
            )
            await conn.execute(
                text("INSERT INTO users (id, timezone, ics_feed_token) VALUES (3, 'UTC', NULL)")
            )

        await init_db(engine)

        async with engine.connect() as conn:
            indexes = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_indexes("users"))
        by_name = {ix["name"]: ix for ix in indexes}
        assert "uq_users_ics_feed_token" in by_name
        assert bool(by_name["uq_users_ics_feed_token"]["unique"])

        # Multiple NULL tokens (never-opened-the-feed-screen users) must
        # not have conflicted with the newly-added unique index.
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT COUNT(*) FROM users"))
            assert result.scalar_one() == 3
    finally:
        await dispose_engine(engine)


async def test_duplicate_token_actually_violates_the_index() -> None:
    """Sanity check the index is REAL, not a no-op: a second insert with
    an already-used, non-NULL token must fail."""
    from sqlalchemy.exc import IntegrityError

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_db(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO users (id, timezone, ics_feed_token) VALUES (1, 'UTC', 'dup-token')")
            )
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("INSERT INTO users (id, timezone, ics_feed_token) VALUES (2, 'UTC', 'dup-token')")
                )
        except IntegrityError:
            pass
        else:
            raise AssertionError("expected a UNIQUE constraint violation")
    finally:
        await dispose_engine(engine)
