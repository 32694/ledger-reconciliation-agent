# Agent Delivery and Runtime Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有对账分析 Agent 整理为可独立克隆、无 Key 验证、配置真实模型后启动、演示、备份和迁移的项目，同时保持 Java 仓库完全不变。

**Architecture:** 保留 `agent/` LangGraph 后端与 `web/` Next.js 前端，在仓库根增加配置、Docker Compose、运维脚本、根 CI 和正式文档。PostgreSQL 17 + pgvector 保存合成证据、规则向量和 Agent 审计数据；scripted model 只用于自动化验证，正式工作台通过 OpenAI 兼容配置调用真实模型。

**Tech Stack:** Python 3.12、LangGraph、LangChain、SQLAlchemy、Alembic、PostgreSQL 17、pgvector、sentence-transformers、Next.js 15、TypeScript、Docker Compose、GitHub Actions

---

## Guardrails

- Agent 仓库：`/Users/fanyi32694/Documents/Codex/2026-08-07/wo-x/outputs/ledger-reconciliation-agent`
- 实施 worktree：`/Users/fanyi32694/Documents/Codex/2026-08-07/wo-x/outputs/ledger-reconciliation-agent/.worktrees/agent-foundation`
- 分支：`feature/agent-delivery`
- 基线：`ffc217b`
- Java 仓库只读核验：`/Users/fanyi32694/Documents/Codex/2026-08-07/wo-x/outputs/ledger-reconciliation-platform`，必须保持 `main=4729213` 且工作区干净。
- 使用 `apply_patch` 手工编辑；锁文件只通过 `uv lock`、`uv sync` 或 pnpm 的标准命令更新。
- 不在命令、日志、提交、截图或聊天中输出真实 `MODEL_API_KEY`。

## File Map

- Create: `.env.example`, `compose.yaml`, `.github/workflows/ci.yml`, `.github/dependabot.yml`
- Create: `scripts/start.sh`, `scripts/stop.sh`, `scripts/verify.sh`, `scripts/backup.sh`, `scripts/restore.sh`
- Create: `agent/Dockerfile`, `agent/.dockerignore`, `agent/docker-entrypoint.sh`
- Create: `web/Dockerfile`, `web/.dockerignore`
- Create: `README.md`, `docs/USER_GUIDE.md`, `docs/MIGRATION.md`, `docs/images/*.png`
- Modify: `.gitignore`, `agent/.env.example`, `agent/pyproject.toml`, `agent/uv.lock`, `agent/README.md`
- Modify: `web/package.json`, `web/next.config.mjs`, `web/README.md`
- Modify: `agent/tests/unit/test_settings.py`
- Create: `agent/tests/unit/test_delivery_contract.py`
- Delete after replacement: `agent/.github/workflows/*.yml`, `web/.github/workflows/ci.yml`, `web/.github/dependabot.yml`

### Task 1: Align Runtime Configuration and Project Metadata

**Files:**
- Create: `.env.example`
- Modify: `agent/.env.example`
- Modify: `agent/pyproject.toml`
- Modify: `agent/uv.lock`
- Modify: `agent/tests/unit/test_settings.py`
- Create: `agent/tests/unit/test_delivery_contract.py`

- [ ] **Step 1: Add failing delivery configuration tests**

在 `agent/tests/unit/test_delivery_contract.py` 中建立稳定根路径并验证配置和元数据：

```python
from pathlib import Path
import tomllib


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_root_environment_example_matches_runtime_settings() -> None:
    content = (REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8")
    for name in (
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
    ):
        assert f"{name}=" in content
    assert "TAVILY_API_KEY" not in content
    assert "ANTHROPIC_API_KEY" not in content
    assert "FIREWORKS_API_KEY" not in content


def test_python_project_uses_agent_identity() -> None:
    pyproject = tomllib.loads(
        (REPOSITORY_ROOT / "agent/pyproject.toml").read_text(encoding="utf-8")
    )
    assert pyproject["project"]["name"] == "ledger-reconciliation-agent"
    assert "reconciliation" in pyproject["project"]["description"].lower()
```

在 `test_settings.py` 增加测试，确认 `Settings` 从 `MODEL_*`、`DATABASE_URL` 读取值，且 `SecretStr` 不在 repr 中泄露。

- [ ] **Step 2: Verify the red state**

Run from `agent/`:

```sh
./.venv/bin/python -m pytest tests/unit/test_delivery_contract.py tests/unit/test_settings.py -q
```

Expected: FAIL，因为根 `.env.example` 不存在且项目名仍为 `react-agent`；不得是导入错误。

- [ ] **Step 3: Add authoritative environment examples**

根 `.env.example` 包含本地 PostgreSQL 默认值、空模型凭据、真实 Settings 名称、前端 URL 和默认关闭的 LangSmith 配置。`DATABASE_URL` 示例指向宿主机 `localhost:5433`；Compose 在容器内覆盖为 `postgres:5432`。

`agent/.env.example` 删除 Tavily、Anthropic 和 Fireworks 模板项，保留后端本地开发所需的 `DATABASE_URL` 和 `MODEL_*`，并注明根 `.env.example` 是 Docker 启动的权威配置。

- [ ] **Step 4: Correct Python package metadata**

修改 `agent/pyproject.toml`：

- `name = "ledger-reconciliation-agent"`
- 描述改为证据约束的对账分析 Agent。
- 作者改为 GitHub 账号 `32694`；上游作者保留在 `THIRD_PARTY_NOTICES.md`。
- 将运行 LangGraph API 所需的 `langgraph-cli[inmem]` 从测试 dependency group 移入运行依赖；pytest、httpx、mypy、ruff 和类型 stub 保持开发依赖。

Run:

```sh
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e UV_CACHE_DIR=/tmp/uv-cache \
  -v "$PWD:/workspace" \
  -w /workspace \
  ghcr.io/astral-sh/uv:python3.12-bookworm-slim \
  uv lock
```

当前本机 `.venv` 没有 uv 可执行文件，因此默认使用上面的官方 uv Docker 镜像；若执行环境已经安装同版本 uv，也可以直接运行 `uv lock`。不得手工编辑 `uv.lock`。现有虚拟环境继续用于聚焦测试，后续 Docker build 的 `uv sync --frozen` 负责验证锁文件与运行依赖一致。

- [ ] **Step 5: Verify and commit**

```sh
./.venv/bin/python -m pytest tests/unit/test_delivery_contract.py tests/unit/test_settings.py -q
git diff --check
git add .env.example agent/.env.example agent/pyproject.toml agent/uv.lock agent/tests/unit/test_settings.py agent/tests/unit/test_delivery_contract.py
git commit -m "chore: 对齐 Agent 配置与项目元数据"
```

### Task 2: Containerize the LangGraph Backend

**Files:**
- Create: `agent/Dockerfile`
- Create: `agent/.dockerignore`
- Create: `agent/docker-entrypoint.sh`
- Modify: `agent/tests/unit/test_delivery_contract.py`

- [ ] **Step 1: Add failing backend container contracts**

新增测试验证三个文件存在，Dockerfile 包含 Python 3.12、锁定依赖、非 root `USER`、`2024` 端口和健康检查；entrypoint 按顺序包含：

```text
python -m reconciliation_agent.cli migrate
python -m reconciliation_agent.cli seed
python -m reconciliation_agent.cli ingest-rules
exec "$@"
```

测试还要验证 entrypoint 是常规文件并带可执行位。

- [ ] **Step 2: Verify the red state**

```sh
cd agent
./.venv/bin/python -m pytest tests/unit/test_delivery_contract.py -q
```

Expected: FAIL，指出 `agent/Dockerfile` 缺失。

- [ ] **Step 3: Implement a multi-stage backend image**

Dockerfile 使用 `ghcr.io/astral-sh/uv` 的 Python 3.12 slim 基础镜像：

- `development` stage 同步开发依赖并包含 tests，用于无 Key 验证。
- `runtime` stage 安装锁定运行依赖，复制 `src/`、`migrations/`、`knowledge/`、`alembic.ini`、`langgraph.json` 和 entrypoint。
- 创建固定 UID/GID 的非 root 用户并授予应用目录和 Hugging Face cache 目录权限。
- 设置 `PATH=/app/.venv/bin:$PATH`、`PYTHONUNBUFFERED=1` 和 embedding cache 环境。
- runtime 命令运行 `langgraph dev --host 0.0.0.0 --port 2024 --no-browser --no-reload`，并明确该镜像用于本地演示。

entrypoint 使用 `set -euo pipefail`，执行 migration、seed、规则导入后 `exec` 主命令。任何步骤失败都阻止 Agent API 启动。

- [ ] **Step 4: Build and inspect the backend image**

```sh
docker build --target runtime -t ledger-reconciliation-agent-backend:test agent
docker image inspect ledger-reconciliation-agent-backend:test --format '{{json .Config.User}} {{json .Config.ExposedPorts}}'
```

Expected: build 成功，用户非空且不是 `0`，暴露 `2024/tcp`。

- [ ] **Step 5: Verify and commit**

```sh
cd agent
./.venv/bin/python -m pytest tests/unit/test_delivery_contract.py -q
git diff --check
git add Dockerfile .dockerignore docker-entrypoint.sh tests/unit/test_delivery_contract.py
git commit -m "feat: 增加 Agent 后端容器"
```

### Task 3: Containerize the Next.js Workbench

**Files:**
- Create: `web/Dockerfile`
- Create: `web/.dockerignore`
- Modify: `web/next.config.mjs`
- Modify: `web/package.json`
- Modify: `agent/tests/unit/test_delivery_contract.py`

- [ ] **Step 1: Add failing frontend delivery contracts**

测试验证：

- `web/Dockerfile` 和 `.dockerignore` 存在。
- `next.config.mjs` 含 `output: "standalone"`。
- `package.json.name` 为 `ledger-reconciliation-agent-web`。
- scripts 包含 `test: "vitest run"` 和 `typecheck: "tsc --noEmit"`。

- [ ] **Step 2: Verify the red state**

```sh
cd agent
./.venv/bin/python -m pytest tests/unit/test_delivery_contract.py -q
```

Expected: FAIL，指出前端 Dockerfile 或 standalone 配置缺失。

- [ ] **Step 3: Implement frontend build stages**

Dockerfile 使用 Node 22 与 pnpm 10：

- `deps` stage 通过 `pnpm install --frozen-lockfile` 安装依赖。
- `test` stage 包含源代码，用于 Vitest、TypeScript 和 production build。
- `builder` stage 接收 `NEXT_PUBLIC_API_URL` 与 `NEXT_PUBLIC_ASSISTANT_ID` build args，执行 `pnpm build`。
- `runner` stage 复制 `.next/standalone` 和 `.next/static`，使用非 root Next.js 用户，暴露 `3000`。当前仓库没有 `web/public/`，不得无条件 COPY 一个不存在的目录；以后新增 public assets 时再显式加入。

最终镜像命令为 `node server.js`。不得在 build args 或镜像 layer 中加入模型 API Key。

- [ ] **Step 4: Run frontend checks and build**

```sh
cd web
PATH=/Users/fanyi32694/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH ./node_modules/.bin/vitest run
PATH=/Users/fanyi32694/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH ./node_modules/.bin/tsc --noEmit
docker build --target runner --build-arg NEXT_PUBLIC_API_URL=http://localhost:2024 --build-arg NEXT_PUBLIC_ASSISTANT_ID=reconciliation_agent -t ledger-reconciliation-agent-web:test .
```

- [ ] **Step 5: Verify and commit**

```sh
git diff --check
git add Dockerfile .dockerignore next.config.mjs package.json ../agent/tests/unit/test_delivery_contract.py
git commit -m "feat: 增加 Agent 前端容器"
```

### Task 4: Add Compose and One-Command Operations

**Files:**
- Create: `compose.yaml`
- Create: `scripts/start.sh`
- Create: `scripts/stop.sh`
- Create: `scripts/verify.sh`
- Modify: `.gitignore`
- Modify: `agent/tests/unit/test_delivery_contract.py`

- [ ] **Step 1: Add failing Compose and script contracts**

测试验证根 Compose 与三个脚本存在且可执行，并读取 Compose YAML 断言：

- 长期服务精确为 `postgres`、`agent`、`web`。
- `postgres` 使用 PostgreSQL 17 + pgvector，绑定 `127.0.0.1:5433:5432`。
- `agent` 绑定 `127.0.0.1:2024:2024`。
- `web` 绑定 `127.0.0.1:3000:3000`。
- 验证 profile 包含不映射宿主端口的 `postgres-test`、`agent-verify`、`web-verify`。
- 数据库和 Hugging Face cache 使用命名 volume。

- [ ] **Step 2: Verify the red state**

```sh
cd agent
./.venv/bin/python -m pytest tests/unit/test_delivery_contract.py -q
```

- [ ] **Step 3: Implement Compose services**

`compose.yaml` 使用变量默认值，但不为 `MODEL_API_KEY` 提供可工作的假密钥。长期 Agent 服务读取根 `.env`，容器内覆盖：

```text
DATABASE_URL=postgresql+psycopg://<compose user>:<compose password>@postgres:5432/<compose db>
HF_HOME=/models
```

`agent-verify` 使用 backend `development` target、dummy 模型配置和 `TEST_DATABASE_URL`；`web-verify` 使用 frontend `test` target。验证 profile 不启动长期 `agent`/`web`。

- [ ] **Step 4: Implement start, stop, and verify scripts**

共同要求：`set -euo pipefail`，通过脚本路径定位仓库根，所有 Docker Compose 命令显式使用根 `compose.yaml`。

`start.sh`：

- 检查 Docker 可用和根 `.env` 存在。
- 读取变量名但不打印值，拒绝空 `MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_NAME`。
- 所有长期服务命令显式使用 `docker compose --env-file .env`，启动并轮询健康状态，超时返回非零。
- 输出 `http://localhost:3000` 与 `http://localhost:2024`。

`stop.sh`：使用根 `.env` 执行 `docker compose stop`，不带 `-v`，不删除数据。

`verify.sh`：

- 用 trap 确保验证结束后移除测试容器和 test volume。
- 所有 Compose 命令显式使用 `--env-file /dev/null`，禁止 Docker Compose 自动读取根 `.env`。
- 启动 `postgres-test`，运行 Alembic upgrade。
- 顺序运行 backend tests/Ruff/mypy/scripted evaluation 和 frontend Vitest/typecheck/build。
- 使用 `python -m reconciliation_agent.evaluation --mode scripted --output /workspace/artifacts/evaluation.json` 生成固定评测，把 JSON 挂载写到根 `artifacts/evaluation.json`；`artifacts/` 加入 `.gitignore`。
- 不读取根 `.env` 中的真实 API Key。

- [ ] **Step 5: Validate syntax and Compose resolution**

```sh
bash -n scripts/start.sh scripts/stop.sh scripts/verify.sh
docker compose --env-file .env.example config --profiles
./scripts/verify.sh
```

Expected: Compose 配置成功；验证脚本使数据库相关测试实际运行，不再出现因缺少 `TEST_DATABASE_URL` 造成的 42 项跳过。允许明确标注的非数据库平台跳过，但必须记录原因。

- [ ] **Step 6: Commit**

```sh
git diff --check
git add compose.yaml scripts/start.sh scripts/stop.sh scripts/verify.sh .gitignore agent/tests/unit/test_delivery_contract.py
git commit -m "feat: 增加 Agent 一键启动与无密钥验证"
```

### Task 5: Add Database Backup and Restore

**Files:**
- Create: `scripts/backup.sh`
- Create: `scripts/restore.sh`
- Modify: `.gitignore`
- Modify: `agent/tests/unit/test_delivery_contract.py`

- [ ] **Step 1: Add failing script behavior tests**

使用 Python `subprocess` 测试不需要 Docker 的前置失败行为：

- `backup.sh` 接受可选输出文件，输出目录不能是现有目录目标。
- `restore.sh` 缺少参数返回 `64` 并显示中文用法。
- `restore.sh` 文件不存在返回 `66`，且在调用 Docker 前失败。
- 两个脚本不得包含 `docker compose down -v`、`rm -rf` 或宽泛删除命令。

- [ ] **Step 2: Verify the red state**

```sh
cd agent
./.venv/bin/python -m pytest tests/unit/test_delivery_contract.py -q
```

- [ ] **Step 3: Implement recoverable operations**

`backup.sh` 默认写入 `backups/agent-YYYYMMDD-HHMMSS.dump`，先创建目标目录，再通过 `docker compose --env-file .env exec -T postgres pg_dump --format=custom` 写入临时文件，成功后原子重命名。

`restore.sh` 接受一个明确文件路径，验证常规文件后要求用户输入固定确认文本；支持 `--yes` 供已明确授权的自动化迁移。恢复前使用根 `.env` 检查 postgres 健康，执行 `pg_restore --clean --if-exists --no-owner`，失败保留原备份文件并返回非零。

- [ ] **Step 4: Verify syntax and safe failures**

```sh
bash -n scripts/backup.sh scripts/restore.sh
./scripts/restore.sh; test "$?" -eq 64
./scripts/restore.sh /tmp/agent-backup-does-not-exist.dump; test "$?" -eq 66
cd agent && ./.venv/bin/python -m pytest tests/unit/test_delivery_contract.py -q
```

- [ ] **Step 5: Commit**

```sh
git add scripts/backup.sh scripts/restore.sh .gitignore agent/tests/unit/test_delivery_contract.py
git commit -m "feat: 增加 Agent 数据备份与恢复"
```

### Task 6: Enable Root GitHub CI

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/dependabot.yml`
- Delete: `agent/.github/workflows/unit-tests.yml`
- Delete: `agent/.github/workflows/integration-tests.yml`
- Delete: `web/.github/workflows/ci.yml`
- Delete: `web/.github/dependabot.yml`
- Modify: `agent/tests/unit/test_delivery_contract.py`

- [ ] **Step 1: Add failing CI location contracts**

测试验证根 CI 存在，子目录 workflow 不存在，并且根 CI 包含：

- push `main`、pull request、workflow dispatch。
- PostgreSQL/pgvector 或调用根无 Key 验证脚本。
- Python、scripted evaluation、Ruff、mypy、Vitest、TypeScript、Next build。
- 上传 `artifacts/evaluation.json`。
- 不引用 `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、Tavily 或模型 secrets。

- [ ] **Step 2: Verify the red state**

```sh
cd agent
./.venv/bin/python -m pytest tests/unit/test_delivery_contract.py -q
```

- [ ] **Step 3: Implement one authoritative root workflow**

根 CI checkout 后运行 `./scripts/verify.sh`，失败时上传 Python、前端和 evaluation artifacts。设置 concurrency，取消同分支旧运行；权限只给 `contents: read`。Dependabot 的 pnpm 目录为 `/web`、uv/pip 目录为 `/agent`、GitHub Actions 目录为 `/`。

删除四个不会被 GitHub 识别的子目录配置，避免错误 badge 和双重维护。

- [ ] **Step 4: Verify and commit**

```sh
cd agent && ./.venv/bin/python -m pytest tests/unit/test_delivery_contract.py -q
cd .. && ./scripts/verify.sh
git diff --check
git add .github agent/.github web/.github agent/tests/unit/test_delivery_contract.py
git commit -m "ci: 启用 Agent 根目录全量质量门禁"
```

### Task 7: Replace Template Documentation with Project Documentation

**Files:**
- Create: `README.md`
- Create: `docs/USER_GUIDE.md`
- Create: `docs/MIGRATION.md`
- Modify: `agent/README.md`
- Modify: `web/README.md`
- Modify: `agent/tests/unit/test_delivery_contract.py`

- [ ] **Step 1: Add failing documentation contracts**

测试验证：

- 根 README、用户手册、迁移手册存在且非空。
- 根 README 包含 CI badge、`LangGraph`、`pgvector`、`BAAI/bge-small-zh-v1.5`、版本化 RAG、`scripted model`、人工确认、`./scripts/start.sh`、`./scripts/verify.sh` 和上游来源。
- 用户手册包含模型配置、`CASE-0001`、证据不足、批准、拒绝和常见错误。
- 迁移手册包含 clone、`.env`、backup、restore、Docker volume、模型缓存和回滚边界。
- `agent/README.md` 与 `web/README.md` 链接 `../README.md`，不再以模板项目标题开头。

- [ ] **Step 2: Verify the red state**

```sh
cd agent
./.venv/bin/python -m pytest tests/unit/test_delivery_contract.py -q
```

- [ ] **Step 3: Write root README**

顺序：项目定位、核心能力、架构 Mermaid、三分钟演示、技术栈、快速启动、无 Key 验证、目录结构、限制、上游与许可证。明确：

- 只使用合成数据。
- RAG 规则向量使用本地 embedding，但 Agent 推理需要真实 OpenAI 兼容模型。
- scripted model 只用于测试，不是在线模型替代品。
- Agent 与 Java 仓库独立。
- 不宣称生产级部署或真实资金处理。

截图章节留到 Task 8 在真实模型验收后一次性加入；不得提交图片占位符。

- [ ] **Step 4: Write user and migration guides**

用户手册给出从复制 `.env`、填写模型三变量、启动、分析案件、查看引用、批准/拒绝到停止服务的完整命令。迁移手册区分代码、数据库 volume、Hugging Face cache 和模型 API Key，说明新电脑缓存会重新下载，不需要把缓存提交到 Git。

子项目 README 只保留开发命令、目录职责和根 README 链接；保留必要的上游版权链接，不复制大段模板营销文案。

- [ ] **Step 5: Verify and commit**

```sh
cd agent && ./.venv/bin/python -m pytest tests/unit/test_delivery_contract.py -q
cd .. && rg -n "LangGraph ReAct Agent Template|Agent Chat UI" README.md agent/README.md web/README.md
git diff --check
git add README.md docs/USER_GUIDE.md docs/MIGRATION.md agent/README.md web/README.md agent/tests/unit/test_delivery_contract.py
git commit -m "docs: 完善 Agent 使用与迁移文档"
```

上面的 `rg` 允许在“上游来源”段落命中项目名，但根/子 README 的一级标题不得仍是模板标题。

### Task 8: Validate a Real Model and Capture the Demonstration

**Checkpoint:** 此任务开始前暂停，请用户在根 `.env` 本地填写 `MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_NAME`。不得要求用户把 Key 发到聊天中。

**Files:**
- Create: `docs/images/analysis-workbench.png`
- Create: `docs/images/resolution-confirmation.png`
- Create: `docs/images/resolved-case.png`
- Modify: `README.md`
- Modify: `docs/USER_GUIDE.md`
- Modify: `agent/tests/unit/test_delivery_contract.py`

- [ ] **Step 1: Start the real stack without exposing secrets**

```sh
./scripts/start.sh
docker compose ps
```

只报告服务状态和模型名；不得打印 `.env` 或 `docker inspect` 的完整环境。

- [ ] **Step 2: Execute the real model acceptance flow**

在 `http://localhost:3000`：

1. 分析 `CASE-0001`。
2. 确认 Agent 调用案件、支付、账本、审计和规则工具。
3. 检查结论引用与案件 `rule_version` 相同。
4. 在人工确认中先拒绝一次，验证案件状态未改变。
5. 重新发起解决并批准，验证案件只解决一次且运行/审计记录存在。
6. 记录模型名、总耗时、工具调用数和是否成功，不记录 Key 或完整原始请求。

如果模型不支持 tool calling、违反版本锁定或无法稳定完成流程，停止验收并报告，不通过手工写数据库伪造成功。

- [ ] **Step 3: Add screenshot contracts before capture**

测试要求三个 PNG 路径存在、非空且能通过 Python 图片解码；README 使用相对路径引用全部图片。先运行测试确认因图片缺失失败。

- [ ] **Step 4: Capture real Chinese UI screenshots**

固定 `1440x1000`，只截网页内容：

- 分析工作台：事实、工具调用和规则引用。
- 解决确认：拟执行动作、风险信息和批准/拒绝控件。
- 已解决案件：完成状态、运行记录或审计结果。

只使用合成数据；逐张检查无 Key、Cookie、本机路径、真实人员/公司信息、加载遮罩、截断或重叠。

- [ ] **Step 5: Complete README demonstration content**

加入 `## 界面预览`、三张相对路径图片和真实模型验收说明。只写实际验证的模型名称与结果，不写供应商密钥、账户或费用信息。

- [ ] **Step 6: Verify and commit**

```sh
cd agent && ./.venv/bin/python -m pytest tests/unit/test_delivery_contract.py -q
cd .. && file docs/images/*.png
git diff --check
git add README.md docs/USER_GUIDE.md docs/images agent/tests/unit/test_delivery_contract.py
git commit -m "docs: 增加 Agent 真实模型演示"
```

### Task 9: Full Verification, CI, Review, and Repository Isolation

**Files:**
- Verify all changed files

- [ ] **Step 1: Run the no-Key full verification**

```sh
./scripts/verify.sh
```

记录 Python 总数、数据库测试总数、skip 原因、前端测试总数、mypy/Ruff/build 和 scripted evaluation 指标。任何失败都先诊断根因，不重复盲跑。

- [ ] **Step 2: Run container and operations smoke checks**

```sh
docker compose --env-file .env.example config
./scripts/start.sh
docker compose ps
./scripts/backup.sh backups/final-smoke.dump
./scripts/stop.sh
```

真实启动使用本地 `.env`；命令输出不得包含密钥。备份完成后验证文件非空，并保持 `backups/` ignored。

- [ ] **Step 3: Verify Git scope and Java isolation**

```sh
git status --short --branch
git diff --check main...HEAD
git diff --name-only main...HEAD
git -C /Users/fanyi32694/Documents/Codex/2026-08-07/wo-x/outputs/ledger-reconciliation-platform status --short --branch
git -C /Users/fanyi32694/Documents/Codex/2026-08-07/wo-x/outputs/ledger-reconciliation-platform rev-parse --short HEAD
```

Expected: Agent worktree clean；变更只在 Agent 仓库；Java 输出 `main...origin/main` 且 SHA 为 `4729213`。

- [ ] **Step 4: Push the feature branch and validate root CI**

在用户确认推送方式后，推送 `feature/agent-delivery` 并创建 PR 或按用户要求合并。根 CI 必须出现真实运行并通过；没有远端运行记录不能声称 CI 已验收。

- [ ] **Step 5: Request final code review**

对 `main...feature/agent-delivery` 做规格与代码质量审查。Critical/Important 必须修复并重新验证；可选 Minor 单独记录，不无目的扩展范围。

- [ ] **Step 6: Finish the branch**

使用 `superpowers:finishing-a-development-branch`，由用户选择本地合并、PR、保留或放弃。合并后在 Agent `main` 重新运行 `./scripts/verify.sh`，核对远端 SHA，再删除 Agent 功能 worktree/分支；不得清理 Java worktree。
