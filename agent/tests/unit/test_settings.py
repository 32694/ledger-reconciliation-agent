import pytest
from pydantic import ValidationError

from reconciliation_agent.settings import Settings


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
