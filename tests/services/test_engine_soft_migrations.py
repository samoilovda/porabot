from bot.database.engine import create_engine, dispose_engine, init_db


async def test_init_db_is_idempotent_when_columns_already_exist() -> None:
    """Soft-migrations must not blow up (or poison later ones) on a re-run."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_db(engine)
        # Second run hits "duplicate column" for every soft-migrated column —
        # each ALTER must be isolated so the rest of init_db still completes.
        await init_db(engine)
    finally:
        await dispose_engine(engine)
