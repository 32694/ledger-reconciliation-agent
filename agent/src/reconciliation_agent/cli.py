"""Command-line entry points for database setup and demo data."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from reconciliation_agent.domain.seed import seed_demo_data
from reconciliation_agent.rag.embeddings import EmbeddingService, get_embedding_settings
from reconciliation_agent.rag.ingest import ingest_rule_directory
from reconciliation_agent.settings import get_database_settings


def _alembic_config(database_url: str) -> Config:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _migrate() -> None:
    database_url = get_database_settings().database_url_for_sqlalchemy
    upgrade(_alembic_config(database_url), "head")


def _seed() -> None:
    database_url = get_database_settings().database_url_for_sqlalchemy
    engine = create_engine(database_url)
    try:
        session = Session(engine)
        try:
            seed_demo_data(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    finally:
        engine.dispose()


def _ingest_rules() -> None:
    database_url = get_database_settings().database_url_for_sqlalchemy
    rules_directory = Path(__file__).resolve().parents[2] / "knowledge" / "rules"
    embedding_service = EmbeddingService(get_embedding_settings().embedding_model)
    engine = create_engine(database_url)
    try:
        session = Session(engine)
        try:
            ingest_rule_directory(session, rules_directory, embedding_service)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    """Run database and deterministic data initialization commands."""
    parser = argparse.ArgumentParser(prog="reconciliation-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate").set_defaults(handler=_migrate)
    subparsers.add_parser("seed").set_defaults(handler=_seed)
    subparsers.add_parser("ingest-rules").set_defaults(handler=_ingest_rules)
    arguments = parser.parse_args(argv)
    arguments.handler()


if __name__ == "__main__":
    main()
