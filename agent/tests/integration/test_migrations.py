from sqlalchemy import create_engine, inspect, text


def test_initial_migration_creates_required_tables(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {
            "reconciliation_case",
            "payment",
            "ledger_entry",
            "audit_event",
            "rule_chunk",
            "case_resolution",
            "confirmation_token",
            "agent_run",
        } <= tables
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
                ).scalar_one()
                == "vector"
            )
    finally:
        engine.dispose()
