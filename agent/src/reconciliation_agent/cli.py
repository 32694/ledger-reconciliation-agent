"""Command-line entry points for database setup and demo data."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from reconciliation_agent.domain.seed import seed_demo_data
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
    session = Session(engine)
    try:
        seed_demo_data(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    """Run a database migration or seed command."""
    parser = argparse.ArgumentParser(prog="reconciliation-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate").set_defaults(handler=_migrate)
    subparsers.add_parser("seed").set_defaults(handler=_seed)
    arguments = parser.parse_args(argv)
    arguments.handler()


if __name__ == "__main__":
    main()
