# 对账 Agent 后端

项目定位、Docker 启动方式和使用边界见[根 README](../README.md)，完整操作见[用户指南](../docs/USER_GUIDE.md)。

## 目录职责

- `src/reconciliation_agent/`：LangGraph 工作流、领域模型、工具、RAG、安全确认和运行配置。
- `knowledge/rules/`：带版本元数据的合成对账规则。
- `migrations/`：PostgreSQL 与 pgvector schema migration。
- `evals/`、`tests/`：固定评测、单元测试和集成测试。

## 开发命令

先从仓库根目录启动 PostgreSQL，并在 `agent/.env` 填写本地模型配置：

```bash
docker compose --env-file .env -f compose.yaml up -d postgres
cd agent
cp .env.example .env
uv sync --frozen --all-extras
uv run python -m reconciliation_agent.cli migrate
uv run python -m reconciliation_agent.cli seed
uv run python -m reconciliation_agent.cli ingest-rules
uv run langgraph dev --host 127.0.0.1 --port 2024
```

质量检查：

```bash
uv run ruff check src tests
uv run mypy --strict src
uv run pytest -q
```

根目录的 `./scripts/verify.sh` 是包含数据库集成测试和固定评测的完整交付门禁。

## 上游版权

后端由 MIT 许可的 [langchain-ai/react-agent](https://github.com/langchain-ai/react-agent) 改造而来。上游许可证保留在 [LICENSE](LICENSE)，固定来源版本见[第三方声明](../THIRD_PARTY_NOTICES.md)。
