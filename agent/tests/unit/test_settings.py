import pytest
from pydantic import SecretStr, ValidationError

from reconciliation_agent.settings import Settings, get_database_settings, get_settings


def test_model_key_is_required_outside_tests() -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+psycopg://agent:agent@localhost/agent",
            model_base_url="https://example.invalid/v1",
            model_name="demo-model",
            model_api_key="",
        )


def test_secret_is_not_rendered() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://agent:agent@localhost/agent",
        model_base_url="https://example.invalid/v1",
        model_name="demo-model",
        model_api_key="secret-value",
    )
    assert "secret-value" not in repr(settings)


def test_settings_load_model_and_database_values_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_api_key = "model-api-key-marker"
    monkeypatch.setenv("MODEL_BASE_URL", "https://model.example.test/v1")
    monkeypatch.setenv("MODEL_API_KEY", model_api_key)
    monkeypatch.setenv("MODEL_NAME", "ledger-model")
    monkeypatch.setenv("MODEL_MAX_RETRIES", "1")
    monkeypatch.setenv("MODEL_MAX_OUTPUT_TOKENS", "512")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://agent:password@localhost/agent"
    )
    get_settings.cache_clear()

    try:
        settings = get_settings()
        assert settings.model_base_url == "https://model.example.test/v1"
        assert settings.model_name == "ledger-model"
        assert settings.model_max_retries == 1
        assert settings.model_max_output_tokens == 512
        assert isinstance(settings.model_api_key, SecretStr)
        assert settings.model_api_key.get_secret_value() == model_api_key
        assert settings.database_url_for_sqlalchemy.endswith("@localhost/agent")
        assert model_api_key not in repr(settings)
    finally:
        get_settings.cache_clear()


def test_database_password_is_not_rendered_or_serialized() -> None:
    password = "database-password-marker"
    database_url = f"postgresql+psycopg://agent:{password}@localhost/agent"
    settings = Settings(
        database_url=database_url,
        model_base_url="https://example.invalid/v1",
        model_name="demo-model",
        model_api_key="secret-value",
    )

    assert [
        password in repr(settings),
        password in settings.model_dump_json(),
    ] == [False, False]
    assert settings.database_url_for_sqlalchemy == database_url


def test_database_settings_load_without_model_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = "postgresql+psycopg://agent:test-only@localhost/agent"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    get_database_settings.cache_clear()

    try:
        settings = get_database_settings()
        assert settings.database_url_for_sqlalchemy == database_url
    finally:
        get_database_settings.cache_clear()
