from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, text

from reconciliation_agent.settings import get_database_settings

REQUIRED_TABLES = {
    "reconciliation_case",
    "payment",
    "ledger_entry",
    "audit_event",
    "rule_chunk",
    "case_resolution",
    "confirmation_token",
    "agent_run",
}

REQUIRED_UNIQUES = {
    "reconciliation_case": {("case_id",)},
    "payment": {("payment_id",)},
    "rule_chunk": {("chunk_id",)},
    "case_resolution": {("case_id",)},
    "confirmation_token": {("token_hash",)},
    "agent_run": {("run_id",)},
}

REQUIRED_CHECKS = {
    "reconciliation_case": {"case_status", "result_type"},
    "case_resolution": {"resolution_code"},
    "confirmation_token": {"resolution_code"},
}


def _vector_is_installed(engine: Engine) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
                    ")"
                )
            ).scalar_one()
        )


def test_initial_migration_manages_complete_schema(
    database_url: str,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    get_database_settings.cache_clear()

    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    engine = create_engine(database_url)

    try:
        vector_was_installed = _vector_is_installed(engine)
        command.downgrade(config, "base")

        assert REQUIRED_TABLES.isdisjoint(inspect(engine).get_table_names())
        if vector_was_installed:
            assert _vector_is_installed(engine)

        command.upgrade(config, "head")

        inspector = inspect(engine)
        assert REQUIRED_TABLES <= set(inspector.get_table_names())

        with engine.connect() as connection:
            current_revision = MigrationContext.configure(
                connection
            ).get_current_revision()
        assert current_revision == script.get_current_head()

        for table_name, expected_columns in REQUIRED_UNIQUES.items():
            unique_columns = {
                tuple(constraint["column_names"])
                for constraint in inspector.get_unique_constraints(table_name)
            }
            assert expected_columns <= unique_columns

        for table_name, expected_names in REQUIRED_CHECKS.items():
            check_names = {
                constraint["name"]
                for constraint in inspector.get_check_constraints(table_name)
            }
            assert expected_names <= check_names

        rule_chunk_columns = {
            column["name"]: column for column in inspector.get_columns("rule_chunk")
        }
        assert str(rule_chunk_columns["embedding"]["type"]).lower() == "vector(512)"
        assert _vector_is_installed(engine)
    finally:
        get_database_settings.cache_clear()
        try:
            command.upgrade(config, "head")
        finally:
            get_database_settings.cache_clear()
            engine.dispose()
