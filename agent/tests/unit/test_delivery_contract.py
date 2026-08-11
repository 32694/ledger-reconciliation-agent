"""Delivery metadata and configuration contract tests."""

import os
import stat
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _read_environment_template(path: Path) -> dict[str, str]:
    values = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", maxsplit=1)
            values[key] = value
    return values


def test_root_environment_template_contains_delivery_configuration() -> None:
    environment_template = REPOSITORY_ROOT / ".env.example"

    assert environment_template.exists()
    values = _read_environment_template(environment_template)
    required_variables = (
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "DATABASE_URL",
        "MODEL_BASE_URL",
        "MODEL_API_KEY",
        "MODEL_NAME",
        "MODEL_TIMEOUT_SECONDS",
        "MODEL_MAX_OUTPUT_TOKENS",
        "MODEL_MAX_RETRIES",
        "MAX_TOOL_CALLS",
        "EMBEDDING_MODEL",
        "NEXT_PUBLIC_API_URL",
        "NEXT_PUBLIC_ASSISTANT_ID",
    )

    for variable in required_variables:
        assert variable in values

    assert values["MODEL_API_KEY"] == ""
    assert values["MODEL_MAX_OUTPUT_TOKENS"] == "2048"
    assert values["MODEL_MAX_RETRIES"] == "2"

    for removed_variable in (
        "TAVILY_API_KEY",
        "ANTHROPIC_API_KEY",
        "FIREWORKS_API_KEY",
        "OPENAI_API_KEY",
    ):
        assert removed_variable not in values


def test_agent_environment_template_matches_local_backend_contract() -> None:
    environment_template = REPOSITORY_ROOT / "agent" / ".env.example"
    contents = environment_template.read_text(encoding="utf-8")
    values = _read_environment_template(environment_template)

    for variable in (
        "DATABASE_URL",
        "MODEL_BASE_URL",
        "MODEL_API_KEY",
        "MODEL_NAME",
        "MODEL_TIMEOUT_SECONDS",
        "MODEL_MAX_OUTPUT_TOKENS",
        "MODEL_MAX_RETRIES",
        "MAX_TOOL_CALLS",
        "EMBEDDING_MODEL",
        "LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
    ):
        assert variable in values

    assert "root .env.example" in contents
    assert values["MODEL_MAX_OUTPUT_TOKENS"] == "2048"
    assert values["MODEL_MAX_RETRIES"] == "2"
    for removed_variable in (
        "TAVILY_API_KEY",
        "ANTHROPIC_API_KEY",
        "FIREWORKS_API_KEY",
        "OPENAI_API_KEY",
    ):
        assert removed_variable not in values


def test_project_metadata_identifies_the_reconciliation_agent() -> None:
    pyproject = REPOSITORY_ROOT / "agent" / "pyproject.toml"
    configuration = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    metadata = configuration["project"]

    assert metadata["name"] == "ledger-reconciliation-agent"
    assert "reconciliation" in metadata["description"].lower()
    assert "evidence" in metadata["description"].lower()
    assert metadata["authors"] == [{"name": "32694"}]
    assert metadata["license"] == {"text": "MIT"}
    assert metadata["readme"] == "README.md"
    assert metadata["requires-python"] == ">=3.11,<4.0"
    assert "langgraph-cli[inmem]>=0.4.10" in metadata["dependencies"]

    assert (
        "langgraph-cli[inmem]>=0.4.10" not in configuration["dependency-groups"]["dev"]
    )
    for dependency in (
        "pytest>=9.0.3",
        "httpx>=0.28,<1",
        "anyio>=4.7.0",
        "types-pyyaml>=6,<7",
    ):
        assert dependency in configuration["dependency-groups"]["dev"]
    assert configuration["project"]["optional-dependencies"]["dev"] == [
        "mypy>=1.11.1",
        "ruff>=0.6.1",
    ]


def test_lockfile_tracks_the_runtime_cli_dependency_from_official_pypi() -> None:
    lockfile = REPOSITORY_ROOT / "agent" / "uv.lock"
    contents = lockfile.read_text(encoding="utf-8")
    lock = tomllib.loads(contents)
    project = next(
        package
        for package in lock["package"]
        if package["name"] == "ledger-reconciliation-agent"
    )

    assert "pypi.tuna" not in contents
    for package in lock["package"]:
        source = package.get("source", {})
        if "registry" in source:
            assert source["registry"] == "https://pypi.org/simple"
    for line in contents.splitlines():
        if "url =" in line:
            assert "https://files.pythonhosted.org/" in line

    runtime_dependency = next(
        dependency
        for dependency in project["dependencies"]
        if dependency["name"] == "langgraph-cli"
    )
    required_distribution = next(
        dependency
        for dependency in project["metadata"]["requires-dist"]
        if dependency["name"] == "langgraph-cli"
    )
    development_dependencies = project["metadata"]["requires-dev"]["dev"]

    assert runtime_dependency["extra"] == ["inmem"]
    assert required_distribution["extras"] == ["inmem"]
    assert required_distribution["specifier"] == ">=0.4.10"
    assert "langgraph-cli" not in {
        dependency["name"] for dependency in development_dependencies
    }
    assert {dependency["name"] for dependency in development_dependencies} >= {
        "anyio",
        "httpx",
        "pytest",
        "types-pyyaml",
    }


def test_backend_container_delivery_files_exist() -> None:
    agent_root = REPOSITORY_ROOT / "agent"

    for filename in ("Dockerfile", ".dockerignore", "docker-entrypoint.sh"):
        assert (agent_root / filename).exists(), f"missing agent/{filename}"


def test_backend_dockerfile_uses_locked_python_runtime_contract() -> None:
    dockerfile = REPOSITORY_ROOT / "agent" / "Dockerfile"
    contents = dockerfile.read_text(encoding="utf-8")
    base_image = contents.splitlines()[0]

    assert base_image.startswith("FROM ghcr.io/astral-sh/uv:python3.12-")
    assert base_image.endswith("-slim AS base")
    assert "COPY --from=ghcr.io/astral-sh/uv" not in contents
    assert "uv sync --frozen" in contents
    assert "USER app" in contents
    assert "EXPOSE 2024" in contents
    assert "HEALTHCHECK" in contents


def test_backend_entrypoint_prepares_data_before_starting_the_api() -> None:
    entrypoint = REPOSITORY_ROOT / "agent" / "docker-entrypoint.sh"
    contents = entrypoint.read_text(encoding="utf-8")
    commands = (
        "python -m reconciliation_agent.cli migrate",
        "python -m reconciliation_agent.cli seed",
        "python -m reconciliation_agent.cli ingest-rules",
        'exec "$@"',
    )

    positions = [contents.index(command) for command in commands]
    assert positions == sorted(positions)


def test_backend_entrypoint_is_a_regular_executable_file() -> None:
    entrypoint = REPOSITORY_ROOT / "agent" / "docker-entrypoint.sh"
    mode = entrypoint.lstat().st_mode

    assert stat.S_ISREG(mode)
    assert os.access(entrypoint, os.X_OK)
