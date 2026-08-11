from types import SimpleNamespace

import pytest
from alembic.config import Config

from reconciliation_agent import cli


class FakeSession:
    def __init__(self, engine: "FakeEngine") -> None:
        self.engine = engine
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class CloseFailingSession(FakeSession):
    def close(self) -> None:
        self.closed = True
        raise RuntimeError("close failed")


def test_migrate_uses_database_only_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upgrades: list[tuple[Config, str]] = []
    monkeypatch.setattr(
        cli,
        "get_database_settings",
        lambda: SimpleNamespace(database_url_for_sqlalchemy="postgresql://test"),
    )
    monkeypatch.setattr(
        cli, "upgrade", lambda config, target: upgrades.append((config, target))
    )

    cli.main(["migrate"])

    config, target = upgrades[0]
    assert config.get_main_option("sqlalchemy.url") == "postgresql://test"
    assert target == "head"


@pytest.mark.parametrize("seed_fails", [False, True])
def test_seed_owns_transaction_and_disposes_resources(
    monkeypatch: pytest.MonkeyPatch, seed_fails: bool
) -> None:
    engine = FakeEngine()
    sessions = []

    def make_session(fake_engine: FakeEngine) -> FakeSession:
        session = FakeSession(fake_engine)
        sessions.append(session)
        return session

    def seed_data(session: FakeSession) -> None:
        if seed_fails:
            raise RuntimeError("seed failed")

    monkeypatch.setattr(
        cli,
        "get_database_settings",
        lambda: SimpleNamespace(database_url_for_sqlalchemy="postgresql://test"),
    )
    monkeypatch.setattr(cli, "create_engine", lambda url: engine)
    monkeypatch.setattr(cli, "Session", make_session)
    monkeypatch.setattr(cli, "seed_demo_data", seed_data)

    if seed_fails:
        with pytest.raises(RuntimeError, match="seed failed"):
            cli.main(["seed"])
    else:
        cli.main(["seed"])

    session = sessions[0]
    assert session.committed is not seed_fails
    assert session.rolled_back is seed_fails
    assert session.closed
    assert engine.disposed


def test_seed_disposes_engine_when_session_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()

    def fail_session(fake_engine: FakeEngine) -> FakeSession:
        raise RuntimeError("session failed")

    monkeypatch.setattr(
        cli,
        "get_database_settings",
        lambda: SimpleNamespace(database_url_for_sqlalchemy="postgresql://test"),
    )
    monkeypatch.setattr(cli, "create_engine", lambda url: engine)
    monkeypatch.setattr(cli, "Session", fail_session)

    with pytest.raises(RuntimeError, match="session failed"):
        cli.main(["seed"])

    assert engine.disposed


def test_seed_disposes_engine_when_session_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    session = CloseFailingSession(engine)
    monkeypatch.setattr(
        cli,
        "get_database_settings",
        lambda: SimpleNamespace(database_url_for_sqlalchemy="postgresql://test"),
    )
    monkeypatch.setattr(cli, "create_engine", lambda url: engine)
    monkeypatch.setattr(cli, "Session", lambda fake_engine: session)
    monkeypatch.setattr(cli, "seed_demo_data", lambda fake_session: None)

    with pytest.raises(RuntimeError, match="close failed"):
        cli.main(["seed"])

    assert session.committed
    assert engine.disposed


@pytest.mark.parametrize("ingest_fails", [False, True])
def test_ingest_rules_owns_transaction_and_disposes_resources(
    monkeypatch: pytest.MonkeyPatch, ingest_fails: bool
) -> None:
    engine = FakeEngine()
    session = FakeSession(engine)
    embedding_service = object()
    calls = []

    def ingest(fake_session, rules_directory, fake_embedding_service) -> None:
        calls.append((fake_session, rules_directory.name, fake_embedding_service))
        if ingest_fails:
            raise RuntimeError("ingest failed")

    monkeypatch.setattr(
        cli,
        "get_database_settings",
        lambda: SimpleNamespace(database_url_for_sqlalchemy="postgresql://test"),
    )
    monkeypatch.setattr(
        cli,
        "get_embedding_settings",
        lambda: SimpleNamespace(embedding_model="demo-embedding"),
    )
    monkeypatch.setattr(
        cli,
        "EmbeddingService",
        lambda model_name: embedding_service,
    )
    monkeypatch.setattr(cli, "create_engine", lambda url: engine)
    monkeypatch.setattr(cli, "Session", lambda fake_engine: session)
    monkeypatch.setattr(cli, "ingest_rule_directory", ingest)

    if ingest_fails:
        with pytest.raises(RuntimeError, match="ingest failed"):
            cli.main(["ingest-rules"])
    else:
        cli.main(["ingest-rules"])

    assert calls == [(session, "rules", embedding_service)]
    assert session.committed is not ingest_fails
    assert session.rolled_back is ingest_fails
    assert session.closed
    assert engine.disposed
