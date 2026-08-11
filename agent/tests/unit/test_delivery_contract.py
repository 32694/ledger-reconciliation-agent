"""Delivery metadata and configuration contract tests."""

import json
import os
import stat
import tomllib
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _read_environment_template(path: Path) -> dict[str, str]:
    values = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", maxsplit=1)
            values[key] = value
    return values


def _read_dockerfile_instructions(path: Path) -> list[str]:
    instructions = []
    fragments: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not fragments and (not line or line.startswith("#")):
            continue
        continued = line.endswith("\\")
        fragment = line[:-1].rstrip() if continued else line
        if fragment and not fragment.startswith("#"):
            fragments.append(fragment)
        if not continued:
            instructions.append(" ".join(fragments))
            fragments = []

    assert not fragments, "unterminated Dockerfile instruction"
    return instructions


def _dockerfile_stage(instructions: list[str], name: str) -> list[str]:
    start = next(
        index
        for index, instruction in enumerate(instructions)
        if instruction.startswith("FROM ") and instruction.endswith(f" AS {name}")
    )
    end = next(
        (
            index
            for index in range(start + 1, len(instructions))
            if instructions[index].startswith("FROM ")
        ),
        len(instructions),
    )
    return instructions[start + 1 : end]


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
    instructions = _read_dockerfile_instructions(dockerfile)

    assert instructions[0] == (
        "FROM ghcr.io/astral-sh/uv:0.9.26-python3.12-bookworm-slim AS base"
    )
    assert "COPY --from=ghcr.io/astral-sh/uv" not in contents
    assert "uv sync --frozen" in contents
    assert "USER app" in contents
    assert "EXPOSE 2024" in contents
    assert "HEALTHCHECK" in contents


def test_backend_dockerfile_does_not_depend_on_docker_hub() -> None:
    dockerfile = REPOSITORY_ROOT / "agent" / "Dockerfile"
    contents = dockerfile.read_text(encoding="utf-8")

    assert "docker.io/" not in contents
    assert "docker/dockerfile:" not in contents


def test_backend_dockerfile_caches_dependencies_before_copying_project() -> None:
    dockerfile = REPOSITORY_ROOT / "agent" / "Dockerfile"
    instructions = _read_dockerfile_instructions(dockerfile)

    for stage_name in ("development", "runtime"):
        stage = _dockerfile_stage(instructions, stage_name)
        metadata_position = next(
            (
                index
                for index, instruction in enumerate(stage)
                if instruction.startswith("COPY ")
                and all(
                    filename in instruction
                    for filename in ("pyproject.toml", "uv.lock", "README.md")
                )
            ),
            None,
        )
        dependency_sync_positions = [
            index
            for index, instruction in enumerate(stage)
            if instruction.startswith("RUN ")
            and "uv sync" in instruction
            and "--frozen" in instruction
            and "--no-install-project" in instruction
        ]
        project_copy_positions = [
            index
            for index, instruction in enumerate(stage)
            if instruction.startswith("COPY ") and index != metadata_position
        ]

        assert metadata_position is not None
        assert len(dependency_sync_positions) == 1
        assert project_copy_positions
        assert metadata_position < dependency_sync_positions[0]
        assert all(
            dependency_sync_positions[0] < position
            for position in project_copy_positions
        )


def test_backend_dockerfile_uses_ephemeral_cache_for_every_sync() -> None:
    dockerfile = REPOSITORY_ROOT / "agent" / "Dockerfile"
    instructions = _read_dockerfile_instructions(dockerfile)
    syncs = [
        instruction
        for instruction in instructions
        if instruction.startswith("RUN ") and "uv sync" in instruction
    ]

    assert syncs
    assert all("--mount=type=cache" in sync for sync in syncs)


def test_backend_runtime_installs_locked_editable_project() -> None:
    dockerfile = REPOSITORY_ROOT / "agent" / "Dockerfile"
    instructions = _read_dockerfile_instructions(dockerfile)
    runtime = _dockerfile_stage(instructions, "runtime")
    syncs = [
        instruction
        for instruction in runtime
        if instruction.startswith("RUN ") and "uv sync" in instruction
    ]

    assert len(syncs) == 2
    assert "--frozen" in syncs[0]
    assert "--no-dev" in syncs[0]
    assert "--no-install-project" in syncs[0]
    assert "--frozen" in syncs[1]
    assert "--no-dev" in syncs[1]
    assert "--no-editable" not in syncs[1]
    assert all(
        "--mount=type=cache,target=/home/app/.cache/uv,uid=10001,gid=10001" in sync
        for sync in syncs
    )


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


def test_frontend_container_delivery_files_and_metadata_exist() -> None:
    web_root = REPOSITORY_ROOT / "web"

    for filename in ("Dockerfile", ".dockerignore"):
        assert (web_root / filename).exists(), f"missing web/{filename}"

    package = json.loads((web_root / "package.json").read_text(encoding="utf-8"))
    assert package["name"] == "ledger-reconciliation-agent-web"
    assert package["scripts"]["test"] == "vitest run"
    assert package["scripts"]["typecheck"] == "tsc --noEmit"

    next_config = (web_root / "next.config.mjs").read_text(encoding="utf-8")
    assert 'output: "standalone"' in next_config


def test_frontend_dockerfile_uses_locked_node_and_pnpm_build_stages() -> None:
    dockerfile = REPOSITORY_ROOT / "web" / "Dockerfile"
    instructions = _read_dockerfile_instructions(dockerfile)

    node_image = "public.ecr.aws/docker/library/node:22.14.0-alpine3.21"
    assert instructions[0] == f"FROM {node_image} AS base"
    assert f"FROM {node_image} AS runner" in instructions
    assert not any(
        instruction.startswith("FROM node:")
        or instruction.startswith("FROM docker.io/")
        for instruction in instructions
    )

    base = _dockerfile_stage(instructions, "base")
    assert any("pnpm@10.5.1" in instruction for instruction in base)

    deps = _dockerfile_stage(instructions, "deps")
    install = next(
        instruction
        for instruction in deps
        if instruction.startswith("RUN ") and "pnpm install" in instruction
    )
    assert "--frozen-lockfile" in install
    assert any(
        instruction.startswith("COPY ")
        and "package.json" in instruction
        and "pnpm-lock.yaml" in instruction
        for instruction in deps
    )

    test_stage = _dockerfile_stage(instructions, "test")
    assert "COPY . ." in test_stage
    for command in ("pnpm test", "pnpm typecheck", "pnpm build"):
        assert any(
            instruction.startswith("RUN ") and command in instruction
            for instruction in test_stage
        )


def test_frontend_builder_accepts_only_public_runtime_identifiers() -> None:
    dockerfile = REPOSITORY_ROOT / "web" / "Dockerfile"
    contents = dockerfile.read_text(encoding="utf-8")
    instructions = _read_dockerfile_instructions(dockerfile)
    builder = _dockerfile_stage(instructions, "builder")

    for variable in ("NEXT_PUBLIC_API_URL", "NEXT_PUBLIC_ASSISTANT_ID"):
        assert f"ARG {variable}" in builder
        assert any(
            instruction.startswith("ENV ") and variable in instruction
            for instruction in builder
        )
    assert any(
        instruction.startswith("RUN ") and "pnpm build" in instruction
        for instruction in builder
    )

    uppercase_contents = contents.upper()
    for forbidden_name in ("API_KEY", "SECRET", "PASSWORD", "TOKEN"):
        assert forbidden_name not in uppercase_contents


def test_frontend_runner_is_a_nonroot_standalone_node_server() -> None:
    web_root = REPOSITORY_ROOT / "web"
    dockerfile = web_root / "Dockerfile"
    instructions = _read_dockerfile_instructions(dockerfile)
    runner = _dockerfile_stage(instructions, "runner")

    assert any(
        instruction.startswith("COPY ") and ".next/standalone" in instruction
        for instruction in runner
    )
    assert any(
        instruction.startswith("COPY ") and ".next/static" in instruction
        for instruction in runner
    )
    assert not any(
        instruction.startswith("COPY ") and "public" in instruction
        for instruction in runner
    )
    assert "USER nextjs" in runner
    assert "EXPOSE 3000" in runner
    assert 'CMD ["node", "server.js"]' in runner

    dockerignore = (web_root / ".dockerignore").read_text(encoding="utf-8")
    for excluded in (
        "node_modules",
        ".next",
        ".env*",
        ".npmrc",
        "*.key",
        "*.pem",
    ):
        assert excluded in dockerignore.splitlines()


def _compose_document() -> dict:
    compose_file = REPOSITORY_ROOT / "compose.yaml"
    assert compose_file.exists(), "missing compose.yaml"
    return yaml.safe_load(compose_file.read_text(encoding="utf-8"))


def test_compose_defines_long_lived_services_and_local_ports() -> None:
    document = _compose_document()
    services = document["services"]

    assert {name for name, service in services.items() if not service.get("profiles")} == {
        "postgres",
        "agent",
        "web",
    }

    assert services["postgres"]["image"].startswith("pgvector/pgvector:")
    assert "pg17" in services["postgres"]["image"]
    assert services["postgres"]["ports"] == ["127.0.0.1:5433:5432"]
    assert services["agent"]["ports"] == ["127.0.0.1:2024:2024"]
    assert services["web"]["ports"] == ["127.0.0.1:3000:3000"]
    assert services["postgres"]["healthcheck"]
    assert services["agent"]["healthcheck"]
    assert services["web"]["healthcheck"]


def test_compose_long_lived_agent_and_web_use_runtime_targets_and_dependencies() -> None:
    services = _compose_document()["services"]
    agent = services["agent"]
    web = services["web"]

    assert agent["build"]["target"] == "runtime"
    assert agent["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert agent["environment"]["HF_HOME"] == "/models"
    assert "DATABASE_URL" in agent["environment"]
    assert "postgres:5432" in agent["environment"]["DATABASE_URL"]
    assert agent["volumes"]

    assert web["build"]["target"] == "runner"
    assert web["depends_on"]["agent"]["condition"] == "service_healthy"
    assert web["build"]["args"]["NEXT_PUBLIC_API_URL"] == "http://localhost:2024"
    assert "NEXT_PUBLIC_ASSISTANT_ID" in web["build"]["args"]


def test_compose_verification_profiles_are_isolated_and_keyless() -> None:
    services = _compose_document()["services"]
    for name in ("postgres-test", "agent-verify", "web-verify"):
        assert services[name]["profiles"]
        assert "ports" not in services[name]

    assert services["postgres-test"]["profiles"] == ["postgres-test"]
    assert services["agent-verify"]["profiles"] == ["agent-verify"]
    assert services["web-verify"]["profiles"] == ["web-verify"]
    assert services["agent-verify"]["build"]["target"] == "development"
    assert services["web-verify"]["build"]["target"] == "test"
    assert services["agent-verify"]["environment"]["MODEL_API_KEY"]
    assert "dummy" in services["agent-verify"]["environment"]["MODEL_API_KEY"].lower()
    assert "TEST_DATABASE_URL" in services["agent-verify"]["environment"]


def test_compose_uses_named_database_and_huggingface_volumes() -> None:
    document = _compose_document()
    services = document["services"]
    volumes = document["volumes"]

    assert "ledger-postgres-data" in volumes
    assert "ledger-postgres-test-data" in volumes
    assert any("ledger-postgres-data" in str(mount) for mount in services["postgres"]["volumes"])
    assert any("ledger-postgres-test-data" in str(mount) for mount in services["postgres-test"]["volumes"])
    assert "ledger-huggingface-cache" in volumes
    assert any("ledger-huggingface-cache" in str(mount) for mount in services["agent"]["volumes"])


def test_delivery_scripts_are_executable_and_preserve_data() -> None:
    scripts_root = REPOSITORY_ROOT / "scripts"
    for filename in ("start.sh", "stop.sh", "verify.sh"):
        script = scripts_root / filename
        assert script.is_file(), f"missing scripts/{filename}"
        assert os.access(script, os.X_OK), f"scripts/{filename} must be executable"

    start = (scripts_root / "start.sh").read_text(encoding="utf-8")
    stop = (scripts_root / "stop.sh").read_text(encoding="utf-8")
    verify = (scripts_root / "verify.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in start
    assert "docker compose --env-file .env" in start
    assert "docker compose --env-file .env stop" in stop
    assert "down -v" not in start + stop
    assert "--env-file /dev/null" in verify
    assert "reconciliation_agent.evaluation" in verify
    assert "/workspace/artifacts/evaluation.json" in verify
    assert "MODEL_API_KEY" not in verify


def test_delivery_artifacts_are_ignored() -> None:
    gitignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "artifacts/" in gitignore
