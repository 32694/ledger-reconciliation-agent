"""Delivery metadata and configuration contract tests."""

import json
import os
import re
import shutil
import stat
import struct
import subprocess
import tomllib
from pathlib import Path

import pytest
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
    for filename in ("start.sh", "stop.sh", "verify.sh", "backup.sh", "restore.sh"):
        script = scripts_root / filename
        assert script.is_file(), f"missing scripts/{filename}"
        assert os.access(script, os.X_OK), f"scripts/{filename} must be executable"

    start = (scripts_root / "start.sh").read_text(encoding="utf-8")
    stop = (scripts_root / "stop.sh").read_text(encoding="utf-8")
    verify = (scripts_root / "verify.sh").read_text(encoding="utf-8")
    restore = (scripts_root / "restore.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in start
    assert "docker compose --env-file .env" in start
    assert '-f "$ROOT_DIR/compose.yaml"' in start
    assert "docker info" in start
    assert "docker compose --env-file .env" in stop
    assert '-f "$ROOT_DIR/compose.yaml"' in stop
    assert "down -v" not in start + stop
    assert "--env-file /dev/null" in verify
    assert "--project-name ledger-reconciliation-agent-verify" in verify
    assert '-f "$ROOT_DIR/compose.yaml"' in verify
    assert "reconciliation_agent.evaluation" in verify
    assert "/workspace/artifacts/evaluation.json" in verify
    assert "MODEL_API_KEY" not in verify
    assert "docker inspect --format" in verify
    assert ".State.Health" in verify
    assert "docker inspect --format" in restore
    assert ".State.Health" in restore
    migration = "python -m reconciliation_agent.cli migrate"
    assert migration in verify
    assert verify.index(migration) < verify.index("python -m pytest -q")
    assert "python -m ruff check src tests" in verify
    assert "python -m mypy --strict src" in verify


def test_delivery_artifacts_are_ignored() -> None:
    gitignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "artifacts/" in gitignore


def test_root_ci_runs_the_complete_keyless_verification_contract() -> None:
    workflow_file = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow_file.exists(), "missing root CI workflow"

    contents = workflow_file.read_text(encoding="utf-8")
    workflow = yaml.load(contents, Loader=yaml.BaseLoader)
    triggers = workflow["on"]
    assert triggers["push"]["branches"] == ["main"]
    assert "pull_request" in triggers
    assert "workflow_dispatch" in triggers
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] == "true"
    assert "github.ref" in workflow["concurrency"]["group"]

    jobs = workflow["jobs"]
    assert len(jobs) == 1
    job = next(iter(jobs.values()))
    assert job["runs-on"] == "ubuntu-latest"
    steps = job["steps"]
    assert any(step.get("uses", "").startswith("actions/checkout@") for step in steps)
    assert [step.get("run") for step in steps if "run" in step] == [
        "./scripts/verify.sh"
    ]

    upload = next(
        step
        for step in steps
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert "always()" in upload["if"]
    assert set(upload["with"]["path"].splitlines()) == {
        "artifacts/python-verification.log",
        "artifacts/frontend-verification.log",
        "artifacts/evaluation.json",
    }
    assert upload["with"]["if-no-files-found"] == "error"

    for forbidden in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "TAVILY_API_KEY",
        "MODEL_API_KEY",
        "secrets.",
    ):
        assert forbidden not in contents

    verify = (REPOSITORY_ROOT / "scripts" / "verify.sh").read_text(
        encoding="utf-8"
    )
    for log_variable, log_name in (
        ("PYTHON_LOG", "artifacts/python-verification.log"),
        ("FRONTEND_LOG", "artifacts/frontend-verification.log"),
    ):
        assert log_name in verify
        assert f': > "${log_variable}"' in verify
        assert f'run_logged "${log_variable}"' in verify
    assert 'tee -a "$log_file"' in verify

    for command in (
        "postgres-test",
        "python -m reconciliation_agent.cli migrate",
        "python -m ruff check src tests",
        "python -m mypy --strict src",
        "python -m pytest -q",
        "reconciliation_agent.evaluation",
        "--mode scripted",
        "pnpm test",
        "pnpm typecheck",
        "pnpm build",
    ):
        assert command in verify


def test_delivery_automation_is_owned_only_by_the_repository_root() -> None:
    for obsolete_file in (
        "agent/.github/workflows/unit-tests.yml",
        "agent/.github/workflows/integration-tests.yml",
        "web/.github/workflows/ci.yml",
        "web/.github/dependabot.yml",
    ):
        assert not (REPOSITORY_ROOT / obsolete_file).exists()


def test_root_dependabot_covers_all_package_ecosystems() -> None:
    dependabot_file = REPOSITORY_ROOT / ".github" / "dependabot.yml"
    assert dependabot_file.exists(), "missing root Dependabot configuration"

    configuration = yaml.safe_load(dependabot_file.read_text(encoding="utf-8"))
    assert configuration["version"] == 2
    updates = configuration["updates"]
    assert {
        (update["package-ecosystem"], update["directory"])
        for update in updates
    } == {
        ("npm", "/web"),
        ("pip", "/agent"),
        ("github-actions", "/"),
    }
    assert all(update["schedule"]["interval"] == "weekly" for update in updates)


def _copy_delivery_script(script_name: str, temporary_root: Path) -> tuple[Path, Path]:
    repository = temporary_root / "repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (repository / ".env").write_text(
        "POSTGRES_DB=ledger_agent\nPOSTGRES_USER=ledger_agent\n",
        encoding="utf-8",
    )
    script = scripts / script_name
    source = REPOSITORY_ROOT / "scripts" / script_name
    shutil.copy2(source, script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return repository, script


def _fake_docker_bin(temporary_root: Path, body: str) -> tuple[Path, Path]:
    bin_dir = temporary_root / "bin"
    bin_dir.mkdir(parents=True)
    marker = temporary_root / "docker.args"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"${DOCKER_MARKER}\"\n"
        + body,
        encoding="utf-8",
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    return bin_dir, marker


def test_verify_logs_early_failures_without_swallowing_the_exit_status(
    tmp_path: Path,
) -> None:
    repository, script = _copy_delivery_script("verify.sh", tmp_path)
    artifacts = repository / "artifacts"
    artifacts.mkdir()
    python_log = artifacts / "python-verification.log"
    frontend_log = artifacts / "frontend-verification.log"
    python_log.write_text("stale python log\n", encoding="utf-8")
    frontend_log.write_text("stale frontend log\n", encoding="utf-8")
    bin_dir, marker = _fake_docker_bin(
        tmp_path,
        "printf 'build failed before tests\\n'\nexit 23\n",
    )

    result = subprocess.run(
        [str(script)],
        cwd=repository,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DOCKER_MARKER": str(marker),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 23
    assert python_log.read_text(encoding="utf-8") == "build failed before tests\n"
    assert frontend_log.read_text(encoding="utf-8") == ""


def test_backup_rejects_an_existing_directory_before_calling_docker(tmp_path: Path) -> None:
    repository, script = _copy_delivery_script("backup.sh", tmp_path)
    output_directory = repository / "backups"
    output_directory.mkdir()
    bin_dir, marker = _fake_docker_bin(tmp_path, "exit 99\n")

    result = subprocess.run(
        [str(script), str(output_directory)],
        cwd=repository,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "DOCKER_MARKER": str(marker)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 64
    assert "目录" in result.stderr
    assert not marker.exists()


def test_backup_failure_preserves_original_and_partial_dump(tmp_path: Path) -> None:
    repository, script = _copy_delivery_script("backup.sh", tmp_path)
    output = repository / "backups" / "agent.dump"
    output.parent.mkdir()
    output.write_text("original backup", encoding="utf-8")
    bin_dir, marker = _fake_docker_bin(
        tmp_path,
        "printf 'partial backup'\nexit 7\n",
    )

    result = subprocess.run(
        [str(script), str(output)],
        cwd=repository,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "DOCKER_MARKER": str(marker)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert output.read_text(encoding="utf-8") == "original backup"
    partials = [
        candidate
        for candidate in output.parent.iterdir()
        if candidate != output and candidate.is_file()
    ]
    assert any(candidate.read_text(encoding="utf-8") == "partial backup" for candidate in partials)
    assert marker.exists()


def test_backup_retries_create_distinct_partial_files_and_success_renames_atomically(
    tmp_path: Path,
) -> None:
    repository, script = _copy_delivery_script("backup.sh", tmp_path)
    output = repository / "backups" / "agent.dump"
    output.parent.mkdir()
    output.write_text("original backup", encoding="utf-8")
    bin_dir, marker = _fake_docker_bin(
        tmp_path,
        "printf 'partial backup'\nexit 7\n",
    )
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_MARKER": str(marker),
    }

    first = subprocess.run(
        [str(script), str(output)],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    second = subprocess.run(
        [str(script), str(output)],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert first.returncode != 0
    assert second.returncode != 0
    assert output.read_text(encoding="utf-8") == "original backup"
    partials = [
        candidate
        for candidate in output.parent.iterdir()
        if candidate != output and candidate.is_file()
    ]
    assert len(partials) == 2
    assert all(candidate.read_text(encoding="utf-8") == "partial backup" for candidate in partials)

    success_bin, success_marker = _fake_docker_bin(
        tmp_path / "success",
        "printf 'new backup'\n",
    )
    success_environment = {
        **os.environ,
        "PATH": f"{success_bin}:{os.environ['PATH']}",
        "DOCKER_MARKER": str(success_marker),
    }
    successful = subprocess.run(
        [str(script), str(output)],
        cwd=repository,
        env=success_environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert successful.returncode == 0, successful.stderr
    assert output.read_text(encoding="utf-8") == "new backup"
    assert len([
        candidate
        for candidate in output.parent.iterdir()
        if candidate != output and candidate.is_file()
    ]) == 2


def test_restore_missing_argument_returns_usage_exit_code_without_docker(
    tmp_path: Path,
) -> None:
    repository, script = _copy_delivery_script("restore.sh", tmp_path)
    bin_dir, marker = _fake_docker_bin(tmp_path, "exit 99\n")

    result = subprocess.run(
        [str(script)],
        cwd=repository,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "DOCKER_MARKER": str(marker)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 64
    assert "用法" in result.stderr
    assert not marker.exists()


def test_restore_missing_file_returns_not_found_before_docker(tmp_path: Path) -> None:
    repository, script = _copy_delivery_script("restore.sh", tmp_path)
    bin_dir, marker = _fake_docker_bin(tmp_path, "exit 99\n")
    missing = repository / "backups" / "missing.dump"

    result = subprocess.run(
        [str(script), str(missing)],
        cwd=repository,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "DOCKER_MARKER": str(marker)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 66
    assert not marker.exists()


@pytest.mark.parametrize(
    "arguments",
    (("--yes",), ("--unknown",), ("--yes", "--unknown"), ("--yes", "file", "extra")),
)
def test_restore_rejects_invalid_options_before_file_lookup_or_docker(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    repository, script = _copy_delivery_script("restore.sh", tmp_path)
    bin_dir, marker = _fake_docker_bin(tmp_path, "exit 99\n")

    result = subprocess.run(
        [str(script), *arguments],
        cwd=repository,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "DOCKER_MARKER": str(marker)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 64
    assert "用法" in result.stderr
    assert not marker.exists()


def test_restore_requires_fixed_confirmation_before_calling_docker(tmp_path: Path) -> None:
    repository, script = _copy_delivery_script("restore.sh", tmp_path)
    backup = repository / "agent.dump"
    backup.write_bytes(b"custom-format-backup")
    bin_dir, marker = _fake_docker_bin(tmp_path, "exit 99\n")

    result = subprocess.run(
        [str(script), str(backup)],
        cwd=repository,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "DOCKER_MARKER": str(marker)},
        input="取消\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "确认恢复" in result.stdout
    assert not marker.exists()


def test_restore_yes_checks_health_then_preserves_source_backup(tmp_path: Path) -> None:
    repository, script = _copy_delivery_script("restore.sh", tmp_path)
    backup = repository / "agent.dump"
    backup.write_bytes(b"custom-format-backup")
    bin_dir, marker = _fake_docker_bin(
        tmp_path,
        "if [[ \"$*\" == *\"ps -q postgres\"* ]]; then printf 'container-id\\n'; "
        "elif [[ \"$*\" == *inspect* ]]; then printf 'healthy\\n'; "
        "else cat >/dev/null; fi\n",
    )

    result = subprocess.run(
        [str(script), "--yes", str(backup)],
        cwd=repository,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "DOCKER_MARKER": str(marker)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert backup.read_bytes() == b"custom-format-backup"
    calls = marker.read_text(encoding="utf-8")
    assert " ps -q" in calls
    assert "inspect --format" in calls
    assert "pg_restore --clean --if-exists --no-owner" in calls


def test_restore_rejects_unhealthy_status_without_running_pg_restore(tmp_path: Path) -> None:
    repository, script = _copy_delivery_script("restore.sh", tmp_path)
    backup = repository / "agent.dump"
    backup.write_bytes(b"custom-format-backup")
    restore_marker = tmp_path / "pg-restore-called"
    bin_dir, marker = _fake_docker_bin(
        tmp_path,
        "if [[ \"$*\" == *\"ps -q postgres\"* ]]; then printf 'container-id\\n'; "
        "elif [[ \"$*\" == *inspect* ]]; then printf 'unhealthy\\n'; "
        "else touch \"${PG_RESTORE_MARKER}\"; fi\n",
    )
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_MARKER": str(marker),
        "PG_RESTORE_MARKER": str(restore_marker),
    }

    result = subprocess.run(
        [str(script), "--yes", str(backup)],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert not restore_marker.exists()
    assert backup.read_bytes() == b"custom-format-backup"


def test_restore_failure_preserves_source_and_does_not_export_dotenv_secrets(
    tmp_path: Path,
) -> None:
    repository, script = _copy_delivery_script("restore.sh", tmp_path)
    (repository / ".env").write_text(
        "POSTGRES_DB=ledger_agent\nMODEL_API_KEY=dotenv-secret\n",
        encoding="utf-8",
    )
    backup = repository / "agent.dump"
    backup.write_bytes(b"custom-format-backup")
    leaked = tmp_path / "leaked-secret"
    bin_dir, marker = _fake_docker_bin(
        tmp_path,
        "if [[ \"$*\" == *\"ps -q postgres\"* ]]; then printf 'container-id\\n'; "
        "elif [[ \"$*\" == *inspect* ]]; then printf 'healthy\\n'; "
        "else printf '%s' \"${MODEL_API_KEY:-}\" > \"${LEAK_MARKER}\"; cat >/dev/null; exit 9; fi\n",
    )
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_MARKER": str(marker),
        "LEAK_MARKER": str(leaked),
    }
    environment.pop("MODEL_API_KEY", None)

    result = subprocess.run(
        [str(script), "--yes", str(backup)],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert backup.read_bytes() == b"custom-format-backup"
    assert leaked.read_text(encoding="utf-8") == ""


def test_backup_and_restore_scripts_forbid_destructive_cleanup() -> None:
    scripts_root = REPOSITORY_ROOT / "scripts"
    contents = "\n".join(
        (scripts_root / filename).read_text(encoding="utf-8")
        for filename in ("backup.sh", "restore.sh")
        if (scripts_root / filename).exists()
    )
    assert "docker compose down -v" not in contents
    assert "rm -rf" not in contents


def test_backup_and_restore_pin_postgres_identity_inside_container() -> None:
    scripts_root = REPOSITORY_ROOT / "scripts"
    backup = (scripts_root / "backup.sh").read_text(encoding="utf-8")
    restore = (scripts_root / "restore.sh").read_text(encoding="utf-8")

    assert "sh -c" in backup
    assert "pg_dump --format=custom --username=\"$POSTGRES_USER\" --dbname=\"$POSTGRES_DB\"" in backup
    assert "sh -c" in restore
    assert "pg_restore --clean --if-exists --no-owner --username=\"$POSTGRES_USER\" --dbname=\"$POSTGRES_DB\"" in restore


def test_root_readme_documents_the_complete_delivery_contract_in_order() -> None:
    readme = REPOSITORY_ROOT / "README.md"

    assert readme.exists()
    contents = readme.read_text(encoding="utf-8")
    assert contents.strip()
    assert contents.startswith("# 智能对账分析 Agent\n")

    required_sections = (
        "## 项目定位",
        "## 核心能力",
        "## 架构",
        "## 三分钟演示",
        "## 界面预览",
        "## 技术栈",
        "## 快速启动",
        "## 无 Key 验证",
        "## 目录结构",
        "## 当前限制",
        "## 上游来源与许可证",
    )
    positions = [contents.index(section) for section in required_sections]
    assert positions == sorted(positions)

    for required_text in (
        "https://github.com/32694/ledger-reconciliation-agent/actions/workflows/ci.yml/badge.svg",
        "LangGraph",
        "pgvector",
        "BAAI/bge-small-zh-v1.5",
        "版本化 RAG",
        "scripted model",
        "人工确认",
        "./scripts/start.sh",
        "./scripts/verify.sh",
        "合成数据",
        "OpenAI 兼容模型",
        "Java",
        "真实资金",
        "langchain-ai/react-agent",
        "langchain-ai/agent-chat-ui",
        "THIRD_PARTY_NOTICES.md",
    ):
        assert required_text in contents

    for image_name in (
        "docs/images/analysis-workbench.png",
        "docs/images/resolution-confirmation.png",
        "docs/images/resolved-case.png",
    ):
        assert image_name in contents
    assert "截图待补充" not in contents
    assert "placeholder" not in contents.lower()


def test_readme_screenshots_are_nonempty_png_files() -> None:
    for image_name in (
        "analysis-workbench.png",
        "resolution-confirmation.png",
        "resolved-case.png",
    ):
        image = REPOSITORY_ROOT / "docs" / "images" / image_name
        contents = image.read_bytes()
        assert contents.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(contents) > 1024
        width, height = struct.unpack(">II", contents[16:24])
        assert width >= 1280
        assert height >= 720


def test_user_and_migration_guides_cover_safe_end_to_end_operations() -> None:
    user_guide = REPOSITORY_ROOT / "docs" / "USER_GUIDE.md"
    migration_guide = REPOSITORY_ROOT / "docs" / "MIGRATION.md"

    for guide in (user_guide, migration_guide):
        assert guide.exists()
        assert guide.read_text(encoding="utf-8").strip()

    user_contents = user_guide.read_text(encoding="utf-8")
    for required_text in (
        "cp .env.example .env",
        "MODEL_BASE_URL",
        "MODEL_API_KEY",
        "MODEL_NAME",
        "不要提交",
        "./scripts/start.sh",
        "http://localhost:3000",
        "CASE-0001",
        "证据面板",
        "证据引用",
        "证据不足",
        "确认执行",
        "拒绝执行",
        "./scripts/stop.sh",
        "常见错误",
    ):
        assert required_text in user_contents

    migration_contents = migration_guide.read_text(encoding="utf-8")
    for required_text in (
        "git clone",
        "Docker",
        "Git",
        "cp .env.example .env",
        "./scripts/backup.sh",
        "./scripts/restore.sh",
        "ledger-postgres-data",
        "ledger-huggingface-cache",
        "Hugging Face",
        "MODEL_API_KEY",
        "重新下载",
        "不要提交",
        "回滚边界",
    ):
        assert required_text in migration_contents


def test_migration_guide_keeps_upgrades_on_a_tracking_branch() -> None:
    migration_guide = REPOSITORY_ROOT / "docs" / "MIGRATION.md"
    contents = migration_guide.read_text(encoding="utf-8")

    for required_command in (
        "git switch main",
        "git branch --set-upstream-to=origin/main main",
        'migration_ref="v1.0.0"',
        'git switch -c migration-pinned "$migration_ref"',
    ):
        assert required_command in contents

    assert "git switch --detach" not in contents
    assert contents.index("git switch main") < contents.index("git pull --ff-only")
    assert "固定版本分支不要执行 `git pull`" in contents


def test_migration_shell_examples_are_copyable_without_angle_bracket_redirection() -> None:
    migration_guide = REPOSITORY_ROOT / "docs" / "MIGRATION.md"
    contents = migration_guide.read_text(encoding="utf-8")
    bash_blocks = re.findall(r"```bash\n(.*?)```", contents, flags=re.DOTALL)

    assert bash_blocks
    assert not any(re.search(r"<[^>\n]+>", block) for block in bash_blocks)
    assert 'backup_file="backups/before-20260812-120000.dump"' in contents
    assert './scripts/restore.sh --yes "$backup_file"' in contents


def test_subproject_readmes_are_project_specific_and_credit_upstream() -> None:
    readmes = {
        "agent": REPOSITORY_ROOT / "agent" / "README.md",
        "web": REPOSITORY_ROOT / "web" / "README.md",
    }

    for project, readme in readmes.items():
        contents = readme.read_text(encoding="utf-8")
        first_line = contents.splitlines()[0]
        assert contents.strip()
        assert first_line.startswith("# ")
        assert "../README.md" in contents
        assert "开发命令" in contents
        assert "目录职责" in contents
        assert "上游版权" in contents
        assert first_line not in {
            "# LangGraph ReAct Agent Template",
            "# Agent Chat UI",
        }
        assert "docs/images" not in contents
        assert "placeholder" not in contents.lower()

    assert "langchain-ai/react-agent" in readmes["agent"].read_text(encoding="utf-8")
    assert "langchain-ai/agent-chat-ui" in readmes["web"].read_text(encoding="utf-8")
