"""Delivery metadata and configuration contract tests."""

from pathlib import Path
import tomllib


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_root_environment_template_contains_delivery_configuration() -> None:
    environment_template = REPOSITORY_ROOT / ".env.example"

    assert environment_template.exists()
    contents = environment_template.read_text(encoding="utf-8")
    required_variables = (
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "DATABASE_URL",
        "MODEL_BASE_URL",
        "MODEL_API_KEY",
        "MODEL_NAME",
        "MODEL_TIMEOUT_SECONDS",
        "MODEL_MAX_RETRIES",
        "MAX_TOOL_CALLS",
        "EMBEDDING_MODEL",
        "NEXT_PUBLIC_API_URL",
        "NEXT_PUBLIC_ASSISTANT_ID",
    )

    for variable in required_variables:
        assert f"{variable}=" in contents

    for removed_variable in (
        "TAVILY_API_KEY",
        "ANTHROPIC_API_KEY",
        "FIREWORKS_API_KEY",
    ):
        assert removed_variable not in contents


def test_project_metadata_identifies_the_reconciliation_agent() -> None:
    pyproject = REPOSITORY_ROOT / "agent" / "pyproject.toml"
    metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]

    assert metadata["name"] == "ledger-reconciliation-agent"
    assert "reconciliation" in metadata["description"].lower()
