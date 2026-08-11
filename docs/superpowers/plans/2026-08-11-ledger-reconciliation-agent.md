# 智能对账分析 Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 LangChain 官方模板交付一个可独立运行的智能对账分析 Agent，具备领域工具调用、版本化 RAG、证据拒答、人工确认、固定评测和中文管理界面。

**Architecture:** 仓库采用 `web`、`agent`、`postgres` 三部分。Next.js 前端通过 LangGraph SDK 连接 Python Agent；Agent 只访问自己的 PostgreSQL 17 + pgvector 合成数据，通过显式 LangGraph 节点编排受限 ReAct、规则检索、证据校验和人工确认，不读取或修改 Java 账本平台。

**Tech Stack:** Python 3.12、LangGraph 1.x、LangChain、Pydantic 2、SQLAlchemy 2、Alembic、PostgreSQL 17、pgvector、sentence-transformers、pytest、Next.js 15、React 19、TypeScript 5、Playwright、Docker Compose、GitHub Actions。

---

## 文件结构

```text
ledger-reconciliation-agent/
├── .env.example
├── .github/workflows/build.yml
├── .gitignore
├── compose.yaml
├── LICENSE
├── Makefile
├── README.md
├── THIRD_PARTY_NOTICES.md
├── agent/
│   ├── LICENSE
│   ├── Dockerfile
│   ├── alembic.ini
│   ├── langgraph.json
│   ├── pyproject.toml
│   ├── migrations/
│   │   ├── env.py
│   │   └── versions/0001_initial_schema.py
│   ├── knowledge/rules/
│   │   ├── alipay-v1.md
│   │   ├── unionpay-v1.md
│   │   └── wechat-v1.md
│   ├── evals/cases.json
│   ├── src/reconciliation_agent/
│   │   ├── __init__.py
│   │   ├── context.py
│   │   ├── settings.py
│   │   ├── model.py
│   │   ├── prompts.py
│   │   ├── state.py
│   │   ├── workflow.py
│   │   ├── cli.py
│   │   ├── domain/models.py
│   │   ├── domain/repository.py
│   │   ├── domain/schemas.py
│   │   ├── domain/seed.py
│   │   ├── rag/embeddings.py
│   │   ├── rag/ingest.py
│   │   ├── rag/retriever.py
│   │   ├── safety/confirmations.py
│   │   └── tools/{cases,payments,ledger,audit,rules,resolution}.py
│   └── tests/{unit,integration}/
└── web/
    ├── LICENSE
    ├── Dockerfile
    ├── package.json
    ├── pnpm-lock.yaml
    ├── playwright.config.ts
    ├── src/app/
    ├── src/components/cases/
    └── tests/
```

文件按领域职责拆分：数据库模型只描述持久化，repository 只提供确定性查询，tool 只负责参数校验和调用 repository，workflow 只编排状态，前端业务组件不持有模型密钥或数据库访问。

### Task 1: 导入并固定官方模板

**Files:**
- Create: `web/**`
- Create: `agent/**`
- Create: `LICENSE`
- Create: `THIRD_PARTY_NOTICES.md`
- Create: `.gitignore`

- [ ] **Step 1: 下载固定版本的上游源码**

Run:

```bash
mkdir -p .upstream
git clone https://github.com/langchain-ai/agent-chat-ui.git .upstream/agent-chat-ui
git -C .upstream/agent-chat-ui checkout fdc87e65307581b02898d33c62b3f285e56bd85b
git clone https://github.com/langchain-ai/react-agent.git .upstream/react-agent
git -C .upstream/react-agent checkout c93701b297e643c7f80926d78247e5dc06c87bfa
```

Expected: 两个仓库均处于 detached HEAD，提交哈希分别匹配计划。

- [ ] **Step 2: 导入模板并保留许可证**

Run:

```bash
mkdir -p web agent
rsync -a --exclude=.git .upstream/agent-chat-ui/ web/
rsync -a --exclude=.git .upstream/react-agent/ agent/
cp web/LICENSE LICENSE
```

Create `THIRD_PARTY_NOTICES.md`:

```markdown
# Third-party notices

This project uses and modifies these MIT-licensed projects:

- LangChain Agent Chat UI, pinned at `fdc87e65307581b02898d33c62b3f285e56bd85b`.
- LangGraph ReAct Agent Template, pinned at `c93701b297e643c7f80926d78247e5dc06c87bfa`.

The original licenses are preserved in `web/LICENSE` and `agent/LICENSE`.
Domain data, reconciliation tools, RAG, workflow, evaluations and Chinese product UI are implemented in this repository.
```

- [ ] **Step 3: 建立仓库忽略规则**

Create `.gitignore`:

```gitignore
.env
.DS_Store
.upstream/
__pycache__/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.venv/
agent/.langgraph_api/
agent/htmlcov/
web/.next/
web/node_modules/
playwright-report/
test-results/
*.pyc
```

- [ ] **Step 4: 运行上游基线构建**

Run:

```bash
cd agent && uv sync --all-groups && uv run pytest tests/unit_tests -q
cd web && corepack pnpm install --frozen-lockfile && corepack pnpm build
```

Expected: Python unit tests通过；Next.js构建成功。需要外部模型的上游集成测试不在此步骤运行。

- [ ] **Step 5: 提交上游基线**

```bash
git add .gitignore LICENSE THIRD_PARTY_NOTICES.md agent web
git commit -m "chore: 导入官方 Agent 工程底座"
```

### Task 2: 建立后端配置和数据库迁移

**Files:**
- Modify: `agent/pyproject.toml`
- Modify: `agent/langgraph.json`
- Create: `agent/src/reconciliation_agent/settings.py`
- Create: `agent/src/reconciliation_agent/domain/models.py`
- Create: `agent/src/reconciliation_agent/domain/schemas.py`
- Create: `agent/src/reconciliation_agent/domain/repository.py`
- Create: `agent/alembic.ini`
- Create: `agent/migrations/env.py`
- Create: `agent/migrations/versions/0001_initial_schema.py`
- Modify: `agent/tests/conftest.py`
- Test: `agent/tests/unit/test_settings.py`
- Test: `agent/tests/integration/test_migrations.py`

- [ ] **Step 1: 写配置失败测试**

Create `agent/tests/unit/test_settings.py`:

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent && uv run pytest tests/unit/test_settings.py -q`

Expected: FAIL，原因是 `reconciliation_agent.settings` 尚不存在。

- [ ] **Step 3: 添加依赖和配置对象**

Run:

```bash
cd agent
uv add 'pydantic-settings>=2.10,<3' 'sqlalchemy>=2.0,<3' 'psycopg[binary,pool]>=3.2,<4' 'alembic>=1.16,<2' 'pgvector>=0.4,<1' 'langgraph-checkpoint-postgres>=3,<4' 'sentence-transformers>=5,<6' 'tenacity>=9,<10'
uv add --dev 'httpx>=0.28,<1'
```

Create `agent/src/reconciliation_agent/settings.py`:

```python
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    model_base_url: str
    model_api_key: SecretStr = Field(min_length=1)
    model_name: str
    model_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    model_max_output_tokens: int = Field(default=2048, ge=256, le=8192)
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    confirmation_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    max_tool_calls: int = Field(default=8, ge=1, le=12)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

- [ ] **Step 4: 定义数据库模型和首次迁移**

Implement `domain/models.py` with SQLAlchemy 2 typed models for:

```python
class CaseStatus(str, enum.Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class ResultType(str, enum.Enum):
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    CHANNEL_ONLY = "CHANNEL_ONLY"
    INTERNAL_ONLY = "INTERNAL_ONLY"


class ResolutionCode(str, enum.Enum):
    INTERNAL_CONFIRMED = "INTERNAL_CONFIRMED"
    CHANNEL_CONFIRMED = "CHANNEL_CONFIRMED"
    IGNORED_TEST_DATA = "IGNORED_TEST_DATA"
    OTHER = "OTHER"
```

The migration must execute `CREATE EXTENSION IF NOT EXISTS vector` and create `reconciliation_case`, `payment`, `ledger_entry`, `audit_event`, `rule_chunk`, `case_resolution`, `confirmation_token`, and `agent_run`. `rule_chunk.embedding` uses `vector(512)`. Add unique constraints on case id, payment id, one resolution per case, and confirmation token hash.

Update `agent/tests/conftest.py` so integration tests require `TEST_DATABASE_URL`, create a SQLAlchemy session, begin one outer transaction per test, and always roll it back. Unit tests must not connect to PostgreSQL.

- [ ] **Step 5: 写并运行迁移集成测试**

Create `agent/tests/integration/test_migrations.py`:

```python
from sqlalchemy import create_engine, inspect, text


def test_initial_migration_creates_required_tables(database_url: str) -> None:
    engine = create_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    assert {
        "reconciliation_case",
        "payment",
        "ledger_entry",
        "audit_event",
        "rule_chunk",
        "case_resolution",
        "confirmation_token",
        "agent_run",
    } <= tables
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one() == "vector"
```

Run:

```bash
cd agent
uv run alembic upgrade head
uv run pytest tests/unit/test_settings.py tests/integration/test_migrations.py -q
```

Expected: 全部通过。

- [ ] **Step 6: 提交数据库基础**

```bash
git add agent
git commit -m "feat: 建立 Agent 数据库与安全配置"
```

### Task 3: 固定合成数据和领域查询

**Files:**
- Create: `agent/src/reconciliation_agent/domain/seed.py`
- Create: `agent/src/reconciliation_agent/cli.py`
- Modify: `agent/src/reconciliation_agent/domain/repository.py`
- Test: `agent/tests/integration/test_seed_repository.py`

- [ ] **Step 1: 写固定数据失败测试**

Create `agent/tests/integration/test_seed_repository.py`:

```python
from reconciliation_agent.domain.repository import ReconciliationRepository
from reconciliation_agent.domain.seed import seed_demo_data


def test_seed_is_repeatable(session) -> None:
    seed_demo_data(session)
    seed_demo_data(session)
    repository = ReconciliationRepository(session)
    assert [case.case_id for case in repository.list_cases()] == [
        "CASE-1001",
        "CASE-1002",
        "CASE-1003",
        "CASE-1004",
    ]


def test_case_1001_has_amount_mismatch_evidence(session) -> None:
    seed_demo_data(session)
    repository = ReconciliationRepository(session)
    case = repository.get_case("CASE-1001")
    payments = repository.find_payments(case)
    assert case.channel_amount_cents == 10_000
    assert [payment.amount_cents for payment in payments] == [9_900]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent && uv run pytest tests/integration/test_seed_repository.py -q`

Expected: FAIL，缺少 seed 和 repository 实现。

- [ ] **Step 3: 实现固定演示数据**

Implement `seed_demo_data(session)` as idempotent upserts for these exact scenarios:

```python
DEMO_CASES = (
    ("CASE-1001", "ALIPAY", "AMOUNT_MISMATCH", "RULE-ALIPAY-V1", 10_000),
    ("CASE-1002", "WECHAT", "CHANNEL_ONLY", "RULE-WECHAT-V1", 5_000),
    ("CASE-1003", "UNIONPAY", "INTERNAL_ONLY", "RULE-UNIONPAY-V1", 20_000),
    ("CASE-1004", "ALIPAY", "AMOUNT_MISMATCH", "RULE-MISSING-V9", 8_000),
)
```

`CASE-1001` has a 9,900-cent successful payment and balanced ledger entries. `CASE-1002` has no internal payment. `CASE-1003` has a 20,000-cent internal payment not present in the channel evidence. `CASE-1004` has structured facts but references a missing rule version to exercise refusal.

- [ ] **Step 4: 实现只读 repository**

`ReconciliationRepository` implements these exact deterministic queries:

```python
def list_cases(self) -> list[ReconciliationCase]:
    statement = select(ReconciliationCase).order_by(ReconciliationCase.case_id)
    return list(self._session.scalars(statement))

def get_case(self, case_id: str) -> ReconciliationCase:
    case = self._session.scalar(
        select(ReconciliationCase).where(ReconciliationCase.case_id == case_id)
    )
    if case is None:
        raise CaseNotFoundError(case_id)
    return case

def find_payments(self, case: ReconciliationCase) -> list[Payment]:
    statement = (
        select(Payment)
        .where(Payment.channel_transaction_id == case.channel_transaction_id)
        .order_by(Payment.payment_id)
    )
    return list(self._session.scalars(statement))

def find_ledger_entries(self, case: ReconciliationCase) -> list[LedgerEntry]:
    payment_ids = select(Payment.payment_id).where(
        Payment.channel_transaction_id == case.channel_transaction_id
    )
    statement = (
        select(LedgerEntry)
        .where(LedgerEntry.business_reference.in_(payment_ids))
        .order_by(LedgerEntry.posted_at, LedgerEntry.id)
    )
    return list(self._session.scalars(statement))

def get_audit_timeline(self, case_id: str) -> list[AuditEvent]:
    statement = (
        select(AuditEvent)
        .where(AuditEvent.business_reference == case_id)
        .order_by(AuditEvent.occurred_at, AuditEvent.id)
    )
    return list(self._session.scalars(statement))

def resolve_case(self, command: ResolveCaseCommand) -> CaseResolution:
    case = self._session.scalar(
        select(ReconciliationCase)
        .where(ReconciliationCase.case_id == command.case_id)
        .with_for_update()
    )
    if case is None:
        raise CaseNotFoundError(command.case_id)
    if case.status is not CaseStatus.OPEN:
        raise CaseAlreadyResolvedError(command.case_id)
    resolution = CaseResolution.from_command(command)
    case.status = CaseStatus.RESOLVED
    self._session.add_all([resolution, AuditEvent.for_resolution(command)])
    self._session.flush()
    return resolution
```

Missing rows raise `CaseNotFoundError`; duplicate resolution raises `CaseAlreadyResolvedError`. Queries order results by stable identifiers and timestamps so tests and demos are deterministic.

- [ ] **Step 5: 添加初始化命令并验证**

Implement CLI commands:

```bash
uv run python -m reconciliation_agent.cli migrate
uv run python -m reconciliation_agent.cli seed
```

Run: `cd agent && uv run pytest tests/integration/test_seed_repository.py -q`

Expected: PASS。

- [ ] **Step 6: 提交合成领域数据**

```bash
git add agent
git commit -m "feat: 增加可重复的合成对账数据"
```

### Task 4: 实现结构化只读工具

**Files:**
- Create: `agent/src/reconciliation_agent/tools/cases.py`
- Create: `agent/src/reconciliation_agent/tools/payments.py`
- Create: `agent/src/reconciliation_agent/tools/ledger.py`
- Create: `agent/src/reconciliation_agent/tools/audit.py`
- Create: `agent/src/reconciliation_agent/tools/__init__.py`
- Test: `agent/tests/unit/test_read_tools.py`

- [ ] **Step 1: 写工具契约失败测试**

Create `agent/tests/unit/test_read_tools.py`:

```python
import json


def test_case_tool_returns_structured_evidence(case_tool) -> None:
    result = json.loads(case_tool.invoke({"case_id": "CASE-1001"}))
    assert result["case_id"] == "CASE-1001"
    assert result["rule_version"] == "RULE-ALIPAY-V1"


def test_payment_tool_does_not_accept_unknown_fields(payment_tool) -> None:
    result = json.loads(
        payment_tool.invoke({"case_id": "CASE-1001", "sql": "DROP TABLE payment"})
    )
    assert result == {
        "error": "INVALID_ARGUMENT",
        "message": "不支持的参数: sql",
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent && uv run pytest tests/unit/test_read_tools.py -q`

Expected: FAIL，工具 fixture 尚不存在。

- [ ] **Step 3: 实现Pydantic白名单工具**

Each tool uses `@tool(args_schema=CaseIdInput)` and serializes a Pydantic result with `model_dump_json()`. Use this strict input model:

```python
class CaseIdInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=r"^CASE-[0-9]{4}$")
```

Expose these names and descriptions exactly:

```python
READ_TOOLS = [
    get_reconciliation_case,
    find_payment_evidence,
    find_ledger_evidence,
    get_audit_timeline,
]
```

Convert repository exceptions into stable JSON errors `CASE_NOT_FOUND`, `INVALID_ARGUMENT`, and `DEPENDENCY_UNAVAILABLE`. Do not expose SQL, connection strings or stack traces.

- [ ] **Step 4: 运行工具测试**

Run: `cd agent && uv run pytest tests/unit/test_read_tools.py -q`

Expected: PASS。

- [ ] **Step 5: 提交只读工具**

```bash
git add agent
git commit -m "feat: 增加对账证据查询工具"
```

### Task 5: 实现版本化 RAG

**Files:**
- Create: `agent/knowledge/rules/alipay-v1.md`
- Create: `agent/knowledge/rules/wechat-v1.md`
- Create: `agent/knowledge/rules/unionpay-v1.md`
- Create: `agent/src/reconciliation_agent/rag/embeddings.py`
- Create: `agent/src/reconciliation_agent/rag/ingest.py`
- Create: `agent/src/reconciliation_agent/rag/retriever.py`
- Create: `agent/src/reconciliation_agent/tools/rules.py`
- Test: `agent/tests/unit/test_rule_chunking.py`
- Test: `agent/tests/integration/test_rule_retrieval.py`

- [ ] **Step 1: 写切分和版本过滤失败测试**

Create `agent/tests/unit/test_rule_chunking.py`:

```python
from reconciliation_agent.rag.ingest import chunk_rule_document


def test_chunks_keep_heading_and_version_metadata() -> None:
    chunks = chunk_rule_document(
        source="alipay-v1.md",
        text="# 支付宝规则\n\n## 金额差异\n\n金额必须完全一致。",
        channel="ALIPAY",
        rule_version="RULE-ALIPAY-V1",
    )
    assert chunks[0].section == "金额差异"
    assert chunks[0].rule_version == "RULE-ALIPAY-V1"
    assert chunks[0].content == "金额必须完全一致。"
```

Create `agent/tests/integration/test_rule_retrieval.py`:

```python
def test_retrieval_never_crosses_rule_version(rule_retriever) -> None:
    results = rule_retriever.search(
        channel="ALIPAY",
        rule_version="RULE-ALIPAY-V1",
        query="金额不一致应该怎么处理",
        limit=3,
    )
    assert results
    assert {item.rule_version for item in results} == {"RULE-ALIPAY-V1"}


def test_missing_version_returns_no_citation(rule_retriever) -> None:
    assert rule_retriever.search(
        channel="ALIPAY",
        rule_version="RULE-MISSING-V9",
        query="金额差异",
        limit=3,
    ) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent && uv run pytest tests/unit/test_rule_chunking.py tests/integration/test_rule_retrieval.py -q`

Expected: FAIL，RAG模块尚不存在。

- [ ] **Step 3: 编写规则文档和确定性切分器**

Each Markdown document begins with YAML front matter containing exact keys `channel`, `rule_version`, `effective_at`, and `source`. `chunk_rule_document` splits at level-two headings, rejects missing metadata, normalizes whitespace, and computes `chunk_id = sha256(source + rule_version + section + content)`.

- [ ] **Step 4: 实现本地嵌入和pgvector检索**

`EmbeddingService` loads one cached model and normalizes vectors:

```python
class EmbeddingService:
    def __init__(self, model_name: str) -> None:
        self._model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return vectors.tolist()
```

`RuleRetriever.search()` first filters `channel` and `rule_version`, then orders by `RuleChunk.embedding.cosine_distance(query_vector)`, limits results to 1-5, and returns `chunk_id`, `source`, `section`, `rule_version`, `content`, and distance.

- [ ] **Step 5: 实现规则工具并运行测试**

`search_reconciliation_rules` requires `channel`, `rule_version`, and `query`; it returns `RULE_VERSION_NOT_FOUND` when the filtered version has no chunks. It never falls back to another version.

Run:

```bash
cd agent
uv run python -m reconciliation_agent.cli ingest-rules
uv run pytest tests/unit/test_rule_chunking.py tests/integration/test_rule_retrieval.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交版本化RAG**

```bash
git add agent
git commit -m "feat: 增加版本化对账规则 RAG"
```

### Task 6: 实现受限 ReAct 和证据分析工作流

**Files:**
- Replace: `agent/src/react_agent/**` with `agent/src/reconciliation_agent/{context,model,prompts,state,workflow}.py`
- Modify: `agent/langgraph.json`
- Test: `agent/tests/unit/test_workflow_routing.py`
- Test: `agent/tests/integration/test_analysis_workflow.py`

- [ ] **Step 1: 写路由和拒答失败测试**

Create `agent/tests/unit/test_workflow_routing.py`:

```python
from reconciliation_agent.state import EvidenceStatus
from reconciliation_agent.workflow import route_after_validation


def test_missing_rule_routes_to_refusal() -> None:
    assert route_after_validation(
        {"evidence_status": EvidenceStatus.MISSING_RULE}
    ) == "refuse_analysis"


def test_complete_evidence_routes_to_draft() -> None:
    assert route_after_validation(
        {"evidence_status": EvidenceStatus.COMPLETE}
    ) == "draft_analysis"
```

Create `agent/tests/integration/test_analysis_workflow.py` with a scripted fake model that requests `get_reconciliation_case`, `find_payment_evidence`, `find_ledger_evidence`, and `search_reconciliation_rules`, then returns a structured analysis. Assert the graph records each tool call, cites `RULE-ALIPAY-V1`, and stops in at most 8 calls. A second scripted run for `CASE-1004` must return `status="INSUFFICIENT_EVIDENCE"` without a resolution recommendation.

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent && uv run pytest tests/unit/test_workflow_routing.py tests/integration/test_analysis_workflow.py -q`

Expected: FAIL，工作流模块尚不存在。

- [ ] **Step 3: 实现模型适配器和重试边界**

`model.py` creates `ChatOpenAI` from settings:

```python
def build_chat_model(settings: Settings) -> BaseChatModel:
    return ChatOpenAI(
        model=settings.model_name,
        base_url=settings.model_base_url,
        api_key=settings.model_api_key,
        timeout=settings.model_timeout_seconds,
        max_tokens=settings.model_max_output_tokens,
        max_retries=0,
    )
```

Wrap model invocation with Tenacity: stop after 3 total attempts, wait 1 second then 2 seconds, and retry only timeout/rate-limit transport errors. Validation and authentication errors are not retried.

- [ ] **Step 4: 实现显式状态和节点**

`AgentState` contains messages, case id, intent, tool call count, collected facts, citations, evidence status, draft analysis, proposed resolution, confirmation request, and final response. Build these nodes:

```python
builder.add_node("parse_request", parse_request)
builder.add_node("collect_case_facts", collect_case_facts)
builder.add_node("tools", ToolNode(READ_TOOLS + [search_reconciliation_rules]))
builder.add_node("validate_evidence", validate_evidence)
builder.add_node("draft_analysis", draft_analysis)
builder.add_node("refuse_analysis", refuse_analysis)
builder.add_node("finalize", finalize)
```

The ReAct loop increments `tool_call_count` and replaces further tool calls with a deterministic `TOOL_BUDGET_EXHAUSTED` result after 8 calls. `validate_evidence` requires the case, the result-type-specific structured facts, and at least one citation matching both channel and locked rule version.

- [ ] **Step 5: 配置LangGraph入口并运行测试**

Create a checkpointer factory for the exported graph. Tests pass an `InMemorySaver`; the server export uses a PostgreSQL connection pool with `autocommit=True` and `dict_row`, calls `PostgresSaver.setup()` once, and compiles the graph with that saver:

```python
def build_postgres_checkpointer(database_url: str) -> PostgresSaver:
    pool = ConnectionPool(
        conninfo=database_url.replace("postgresql+psycopg://", "postgresql://"),
        kwargs={"autocommit": True, "row_factory": dict_row},
        min_size=1,
        max_size=4,
        open=True,
    )
    saver = PostgresSaver(pool)
    saver.setup()
    return saver


def build_graph(checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    return builder.compile(checkpointer=checkpointer, name="Reconciliation Agent")


graph = build_graph(build_postgres_checkpointer(get_settings().database_url))
```

An integration test must invoke a graph to an interrupt, create a fresh graph instance with the same PostgreSQL database, and resume the same `thread_id`. This is the proof that Human-in-the-loop state survives an Agent process restart.

Update `agent/langgraph.json`:

```json
{
  "$schema": "https://langgra.ph/schema.json",
  "dependencies": ["."],
  "graphs": {
    "reconciliation_agent": "./src/reconciliation_agent/workflow.py:graph"
  },
  "env": ".env"
}
```

Run: `cd agent && uv run pytest tests/unit/test_workflow_routing.py tests/integration/test_analysis_workflow.py -q`

Expected: PASS。

- [ ] **Step 6: 提交分析工作流**

```bash
git add agent
git commit -m "feat: 实现证据驱动的对账 Agent"
```

### Task 7: 实现不可绕过的人工确认

**Files:**
- Create: `agent/src/reconciliation_agent/safety/confirmations.py`
- Create: `agent/src/reconciliation_agent/tools/resolution.py`
- Modify: `agent/src/reconciliation_agent/workflow.py`
- Modify: `agent/tests/conftest.py`
- Test: `agent/tests/unit/test_confirmation_service.py`
- Test: `agent/tests/integration/test_resolution_workflow.py`

- [ ] **Step 1: 写令牌绑定和防重放失败测试**

Create `agent/tests/unit/test_confirmation_service.py`:

```python
from datetime import timedelta

import pytest

from reconciliation_agent.domain.schemas import ResolutionProposal
from reconciliation_agent.safety.confirmations import ConfirmationRejected


def test_token_is_bound_to_exact_action(confirmation_service) -> None:
    proposal = ResolutionProposal(
        run_id="RUN-1",
        case_id="CASE-1001",
        resolution_code="CHANNEL_CONFIRMED",
        note="渠道金额为准",
    )
    token = confirmation_service.issue(proposal)
    changed = proposal.model_copy(update={"resolution_code": "INTERNAL_CONFIRMED"})
    with pytest.raises(ConfirmationRejected, match="确认参数已变化"):
        confirmation_service.consume(token, changed)


def test_token_cannot_be_replayed(confirmation_service) -> None:
    proposal = ResolutionProposal(
        run_id="RUN-2",
        case_id="CASE-1002",
        resolution_code="IGNORED_TEST_DATA",
        note="演示数据",
    )
    token = confirmation_service.issue(proposal)
    confirmation_service.consume(token, proposal)
    with pytest.raises(ConfirmationRejected, match="确认已使用"):
        confirmation_service.consume(token, proposal)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent && uv run pytest tests/unit/test_confirmation_service.py -q`

Expected: FAIL，确认服务尚不存在。

- [ ] **Step 3: 实现一次性令牌服务**

Generate 32-byte URL-safe random tokens, store only `sha256(token)` with bound parameters and `expires_at = issued_at + 10 minutes`, and consume in one database transaction with `SELECT FOR UPDATE`. Comparison uses `hmac.compare_digest`. The clear token may exist only in the short-lived LangGraph interrupt state and browser response; never write it to domain tables, `agent_run`, application logs or screenshots.

Use one API throughout production and tests: `issue(proposal: ResolutionProposal) -> str` and `consume(token: str, proposal: ResolutionProposal) -> None`. Add `confirmation_service`, `repository`, and mutable `frozen_clock` fixtures to `tests/conftest.py`; the clock starts at `2026-08-11T00:00:00Z` and exposes `advance(delta)`.

- [ ] **Step 4: 实现LangGraph interrupt/resume**

Before resolution, issue a token for the exact proposal and include it in the action args returned by the interrupt. The custom frontend hides the token but preserves it in the approval decision:

```python
confirmation_token = confirmation_service.issue(proposed)
decision = interrupt(
    {
        "action_requests": [
            {
                "name": "resolve_case",
                "args": {
                    **proposed.model_dump(),
                    "confirmation_token": confirmation_token,
                },
            }
        ],
        "review_configs": [
            {
                "action_name": "resolve_case",
                "allowed_decisions": ["approve", "reject"],
            }
        ],
    }
)
```

Only an `approve` decision consumes the bound token from the approved args, calls `resolve_case`, appends an audit event, and marks the case `RESOLVED`. `reject` routes to `finalize` without writes. The resolution transaction checks the case is still `OPEN` and relies on a unique case resolution constraint to stop races.

- [ ] **Step 5: 运行确认端到端测试**

Create `test_resolution_workflow.py` covering these executable paths:

```python
from datetime import timedelta

import pytest
from langgraph.types import Command

from reconciliation_agent.domain.models import CaseStatus
from reconciliation_agent.domain.repository import CaseAlreadyResolvedError
from reconciliation_agent.domain.schemas import ResolveCaseCommand, ResolutionProposal
from reconciliation_agent.safety.confirmations import ConfirmationRejected


@pytest.mark.anyio
async def test_reject_keeps_case_open(interrupted_graph, repository) -> None:
    config = {"configurable": {"thread_id": "THREAD-REJECT"}}
    await interrupted_graph.ainvoke(
        {"messages": [("user", "解决CASE-1001")]}, config=config
    )
    await interrupted_graph.ainvoke(
        Command(resume={"decisions": [{"type": "reject"}]}), config=config
    )
    assert repository.get_case("CASE-1001").status is CaseStatus.OPEN


@pytest.mark.anyio
async def test_approve_resolves_case_once(interrupted_graph, repository) -> None:
    config = {"configurable": {"thread_id": "THREAD-APPROVE"}}
    await interrupted_graph.ainvoke(
        {"messages": [("user", "解决CASE-1002")]}, config=config
    )
    await interrupted_graph.ainvoke(
        Command(resume={"decisions": [{"type": "approve"}]}), config=config
    )
    assert repository.get_case("CASE-1002").status is CaseStatus.RESOLVED
    with pytest.raises(CaseAlreadyResolvedError):
        repository.resolve_case(
            ResolveCaseCommand(
                case_id="CASE-1002",
                resolution_code="IGNORED_TEST_DATA",
                note="演示数据",
                confirmed_by="demo-admin",
                run_id="THREAD-APPROVE",
            )
        )


def test_expired_confirmation_is_rejected(confirmation_service, frozen_clock) -> None:
    proposal = ResolutionProposal(
        run_id="RUN-EXPIRED",
        case_id="CASE-1003",
        resolution_code="INTERNAL_CONFIRMED",
        note="内部流水为准",
    )
    token = confirmation_service.issue(proposal)
    frozen_clock.advance(timedelta(minutes=11))
    with pytest.raises(ConfirmationRejected, match="确认已过期"):
        confirmation_service.consume(token, proposal)


def test_changed_note_invalidates_confirmation(confirmation_service) -> None:
    original = ResolutionProposal(
        run_id="RUN-CHANGED",
        case_id="CASE-1001",
        resolution_code="INTERNAL_CONFIRMED",
        note="内部流水为准",
    )
    token = confirmation_service.issue(original)
    changed = original.model_copy(update={"note": "渠道流水为准"})
    with pytest.raises(ConfirmationRejected, match="确认参数已变化"):
        confirmation_service.consume(token, changed)
```

Run: `cd agent && uv run pytest tests/unit/test_confirmation_service.py tests/integration/test_resolution_workflow.py -q`

Expected: PASS；数据库断言确认拒绝前无 `case_resolution`，批准后恰好一条。

- [ ] **Step 6: 提交人工确认**

```bash
git add agent
git commit -m "feat: 增加案件解决人工确认"
```

### Task 8: 持久化运行记录并收紧错误输出

**Files:**
- Create: `agent/src/reconciliation_agent/runs.py`
- Modify: `agent/src/reconciliation_agent/workflow.py`
- Modify: `agent/src/reconciliation_agent/domain/repository.py`
- Test: `agent/tests/unit/test_error_sanitization.py`
- Test: `agent/tests/integration/test_run_persistence.py`

- [ ] **Step 1: 写错误脱敏和运行持久化失败测试**

Create `agent/tests/unit/test_error_sanitization.py`:

```python
from reconciliation_agent.runs import public_error


def test_public_error_hides_secrets_and_connection_strings() -> None:
    error = RuntimeError(
        "failed postgresql://agent:db-secret@db/agent with api-key-secret"
    )
    result = public_error(error)
    assert result.code == "INTERNAL_ERROR"
    assert result.message == "Agent服务暂时不可用，请稍后重试。"
    assert "secret" not in result.model_dump_json()
```

Create `agent/tests/integration/test_run_persistence.py`:

```python
async def test_completed_run_keeps_tool_summary_and_citations(
    analysis_graph, run_repository
) -> None:
    result = await analysis_graph.ainvoke(
        {"messages": [("user", "分析案件CASE-1001")]},
        config={"configurable": {"thread_id": "THREAD-RUN-1"}},
    )
    saved = run_repository.get(result["run_id"])
    assert saved.status == "COMPLETED"
    assert saved.tool_names == [
        "get_reconciliation_case",
        "find_payment_evidence",
        "find_ledger_evidence",
        "search_reconciliation_rules",
    ]
    assert saved.citation_ids
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent && uv run pytest tests/unit/test_error_sanitization.py tests/integration/test_run_persistence.py -q`

Expected: FAIL，运行记录服务尚不存在。

- [ ] **Step 3: 实现运行状态和公共错误映射**

Use exact run states `RUNNING`, `WAITING_CONFIRMATION`, `COMPLETED`, `REJECTED`, and `FAILED`. `RunService` creates a run before the first model call, appends only tool names and sanitized result summaries, stores citation ids and final structured result, and updates terminal state in `finalize` or a graph-level failure handler.

`public_error()` maps known errors:

```python
ERROR_MESSAGES = {
    "MODEL_UNAVAILABLE": "模型服务暂时不可用，请稍后重试。",
    "DATABASE_UNAVAILABLE": "数据服务暂时不可用，请稍后重试。",
    "RULE_VERSION_NOT_FOUND": "未找到案件锁定版本的规则，当前无法给出确定结论。",
    "TOOL_BUDGET_EXHAUSTED": "证据查询次数已达到上限，当前无法完成分析。",
}
```

Unknown errors use `INTERNAL_ERROR`; private exception text is logged only through a filter that redacts URL credentials and configured secrets.

- [ ] **Step 4: 运行测试并提交**

Run: `cd agent && uv run pytest tests/unit/test_error_sanitization.py tests/integration/test_run_persistence.py -q`

Expected: PASS。

```bash
git add agent
git commit -m "feat: 增加 Agent 运行记录与错误脱敏"
```

### Task 9: 建立固定评测集

**Files:**
- Create: `agent/evals/cases.json`
- Create: `agent/src/reconciliation_agent/evaluation.py`
- Create: `agent/tests/evaluation/test_evaluation_dataset.py`
- Create: `agent/tests/evaluation/test_scripted_evaluation.py`

- [ ] **Step 1: 写评测数据契约失败测试**

Create `agent/tests/evaluation/test_evaluation_dataset.py`:

```python
from reconciliation_agent.evaluation import load_cases


def test_evaluation_dataset_has_required_coverage() -> None:
    cases = load_cases("evals/cases.json")
    assert 20 <= len(cases) <= 30
    assert {case.category for case in cases} == {
        "tool_selection",
        "tool_arguments",
        "rule_retrieval",
        "citation",
        "refusal",
        "confirmation",
    }
    assert all(case.expected_case_id.startswith("CASE-") for case in cases)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent && uv run pytest tests/evaluation/test_evaluation_dataset.py -q`

Expected: FAIL，评测文件尚不存在。

- [ ] **Step 3: 编写24条固定案例**

Create exactly four cases per category. Each JSON item contains:

```json
{
  "id": "EVAL-001",
  "category": "tool_selection",
  "question": "分析案件CASE-1001的金额差异",
  "expected_case_id": "CASE-1001",
  "expected_tools": [
    "get_reconciliation_case",
    "find_payment_evidence",
    "search_reconciliation_rules"
  ],
  "expected_rule_version": "RULE-ALIPAY-V1",
  "expected_outcome": "ANALYSIS_READY"
}
```

Refusal cases use `CASE-1004` or malformed ids and expect `INSUFFICIENT_EVIDENCE` or `INVALID_REQUEST`. Confirmation cases assert no resolution before approval and one resolution afterward.

- [ ] **Step 4: 实现确定性指标计算**

`evaluation.py` computes exact metrics without LLM-as-judge:

```python
@dataclass(frozen=True)
class EvaluationMetrics:
    tool_selection_accuracy: float
    argument_accuracy: float
    retrieval_recall_at_k: float
    citation_completeness: float
    outcome_consistency: float
    refusal_accuracy: float
```

Provide `python -m reconciliation_agent.evaluation --mode scripted --output build/evaluation.json`. Scripted mode uses fake models and must be part of CI. `--mode live` is opt-in, requires a model key, writes token usage when the provider returns it, and is never run by default CI.

- [ ] **Step 5: 运行评测测试并提交**

Run:

```bash
cd agent
uv run pytest tests/evaluation -q
uv run python -m reconciliation_agent.evaluation --mode scripted --output build/evaluation.json
```

Expected: 24个案例被执行，命令退出码为0，JSON包含六个指标和每个失败案例的明细。

```bash
git add agent
git commit -m "test: 增加固定 Agent 评测体系"
```

### Task 10: 将官方前端改造成中文对账工作台

**Files:**
- Modify: `web/src/app/layout.tsx`
- Modify: `web/src/app/page.tsx`
- Modify: `web/src/app/globals.css`
- Modify: `web/src/providers/Stream.tsx`
- Modify: `web/src/components/thread/index.tsx`
- Modify: `web/src/components/thread/messages/{ai,human,tool-calls,generic-interrupt}.tsx`
- Create: `web/src/components/cases/case-shortcuts.tsx`
- Create: `web/src/components/cases/evidence-panel.tsx`
- Create: `web/src/lib/reconciliation.ts`
- Test: `web/src/lib/reconciliation.test.ts`

- [ ] **Step 1: 写证据解析失败测试**

Create `web/src/lib/reconciliation.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { parseEvidenceArtifact } from "./reconciliation";

describe("parseEvidenceArtifact", () => {
  it("accepts a versioned citation", () => {
    const result = parseEvidenceArtifact({
      caseId: "CASE-1001",
      status: "ANALYSIS_READY",
      citations: [
        {
          chunkId: "rule-alipay-v1-amount",
          source: "alipay-v1.md",
          section: "金额差异",
          ruleVersion: "RULE-ALIPAY-V1",
          content: "金额必须完全一致。",
        },
      ],
    });
    expect(result.citations[0].ruleVersion).toBe("RULE-ALIPAY-V1");
  });
});
```

- [ ] **Step 2: 添加Vitest并确认测试失败**

Run:

```bash
cd web
corepack pnpm add -D vitest jsdom @testing-library/react @testing-library/jest-dom
corepack pnpm exec playwright install chromium
corepack pnpm vitest run src/lib/reconciliation.test.ts
```

Expected: FAIL，业务解析器尚不存在。

- [ ] **Step 3: 实现业务artifact schema**

Use Zod with this exact shape:

```typescript
export const evidenceArtifactSchema = z.object({
  caseId: z.string().regex(/^CASE-[0-9]{4}$/),
  status: z.enum(["ANALYSIS_READY", "INSUFFICIENT_EVIDENCE", "RESOLVED"]),
  summary: z.string().default(""),
  facts: z.array(z.object({ label: z.string(), value: z.string() })).default([]),
  citations: z.array(
    z.object({
      chunkId: z.string(),
      source: z.string(),
      section: z.string(),
      ruleVersion: z.string(),
      content: z.string(),
    }),
  ).default([]),
});
```

- [ ] **Step 4: 完成中文产品界面**

Use a restrained operations layout, not a marketing landing page. The first viewport contains:

- Header title `智能对账分析` and connection status.
- Left column with four fixed case shortcuts and thread history.
- Center chat with Chinese empty state, input placeholder `输入案件编号，例如：分析CASE-1001` and visible tool calls.
- Right evidence panel with facts, rule version, source section, conclusion status and missing evidence.

Keep the existing GitHub attribution link. Replace English setup and error strings with Chinese. Configure `NEXT_PUBLIC_API_URL=http://localhost:2024` and `NEXT_PUBLIC_ASSISTANT_ID=reconciliation_agent` so local users do not see the generic deployment form.

- [ ] **Step 5: 运行前端测试和构建**

Run:

```bash
cd web
corepack pnpm vitest run
corepack pnpm format:check
corepack pnpm build
```

Expected: 测试通过、格式检查通过、Next.js构建成功。

- [ ] **Step 6: 提交中文工作台**

```bash
git add web
git commit -m "feat: 增加中文对账 Agent 工作台"
```

### Task 11: 定制证据展示和确认交互

**Files:**
- Create: `web/src/components/cases/resolution-confirmation.tsx`
- Modify: `web/src/components/thread/agent-inbox/components/inbox-item-input.tsx`
- Modify: `web/src/components/thread/agent-inbox/hooks/use-interrupted-actions.tsx`
- Modify: `web/src/components/thread/messages/generic-interrupt.tsx`
- Create: `web/tests/reconciliation-flow.spec.ts`
- Create: `web/playwright.config.ts`

- [ ] **Step 1: 写Playwright确认流程**

Create `web/tests/reconciliation-flow.spec.ts`:

```typescript
import { expect, test } from "@playwright/test";

test("rejecting a proposed resolution leaves the case unresolved", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "CASE-1001" }).click();
  await page.getByPlaceholder("输入案件编号，例如：分析CASE-1001").fill(
    "分析CASE-1001并建议处理方式",
  );
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("等待人工确认")).toBeVisible();
  await page.getByRole("button", { name: "拒绝执行" }).click();
  await expect(page.getByText("已取消，本次未修改案件")).toBeVisible();
  await expect(page.getByText("案件状态：待处理")).toBeVisible();
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd web && corepack pnpm playwright test tests/reconciliation-flow.spec.ts`

Expected: FAIL，业务确认组件和测试服务尚未接通。

- [ ] **Step 3: 实现专用确认卡片**

Render interrupt action `resolve_case` as a fixed card with case id, resolution code, note and `10分钟内有效`。Only two commands are available: `确认执行` submits `approve`; `拒绝执行` submits `reject`. Do not expose generic argument editing because changing reviewed parameters must create a new proposal and token.

Translate success and error messages. While submitting, disable both buttons and retain stable card dimensions. On success, show the returned audit event id; on rejection, show that no case data changed.

- [ ] **Step 4: 运行组件和浏览器测试**

Run:

```bash
cd web
corepack pnpm vitest run
corepack pnpm playwright test tests/reconciliation-flow.spec.ts
corepack pnpm build
```

Expected: PASS；浏览器测试覆盖拒绝和批准两条路径。

- [ ] **Step 5: 提交确认交互**

```bash
git add web
git commit -m "feat: 增加案件解决确认交互"
```

### Task 12: 容器化三服务并提供健康检查

**Files:**
- Create: `agent/Dockerfile`
- Create: `web/Dockerfile`
- Create: `compose.yaml`
- Create: `.env.example`
- Create: `Makefile`
- Test: `agent/tests/integration/test_health.py`

- [ ] **Step 1: 写健康检查失败测试**

Create `agent/tests/integration/test_health.py`:

```python
import httpx


def test_langgraph_info_exposes_reconciliation_graph(agent_base_url: str) -> None:
    response = httpx.get(f"{agent_base_url}/info", timeout=5)
    response.raise_for_status()
    assert "reconciliation_agent" in response.text
```

- [ ] **Step 2: 创建安全环境变量模板**

Create `.env.example`:

```dotenv
POSTGRES_DB=reconciliation_agent
POSTGRES_USER=agent_app
POSTGRES_PASSWORD=change-me
DATABASE_URL=postgresql+psycopg://agent_app:change-me@postgres:5432/reconciliation_agent
MODEL_BASE_URL=https://api.deepseek.com/v1
MODEL_API_KEY=replace-with-personal-api-key
MODEL_NAME=deepseek-chat
MODEL_TIMEOUT_SECONDS=30
MODEL_MAX_OUTPUT_TOKENS=2048
NEXT_PUBLIC_API_URL=http://localhost:2024
NEXT_PUBLIC_ASSISTANT_ID=reconciliation_agent
```

`.env.example` contains placeholders only. `.env` remains ignored and is never copied into Docker images.

- [ ] **Step 3: 创建Docker镜像**

`agent/Dockerfile` uses `python:3.12-slim`, installs uv, syncs the lock file, downloads `BAAI/bge-small-zh-v1.5` during build, runs migrations and seed through an entrypoint, then starts `uv run langgraph dev --host 0.0.0.0 --port 2024 --no-browser --no-reload`.

`web/Dockerfile` uses a multi-stage Node 22 image, enables corepack, runs `pnpm install --frozen-lockfile` and `pnpm build`, then starts the production Next.js server on port 3000.

- [ ] **Step 4: 创建Compose编排**

`compose.yaml` defines:

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg17
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 20
  agent:
    build: ./agent
    depends_on:
      postgres:
        condition: service_healthy
    ports: ["2024:2024"]
  web:
    build:
      context: ./web
      args:
        NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-http://localhost:2024}
        NEXT_PUBLIC_ASSISTANT_ID: reconciliation_agent
    depends_on:
      agent:
        condition: service_started
    ports: ["3000:3000"]
```

Add a named PostgreSQL volume and health checks for all services. Do not mount source code or `.env` into production containers.

- [ ] **Step 5: 添加Makefile并验证Compose**

Provide `make install`, `make test`, `make evaluate`, `make up`, `make down`, and `make verify`. Commands must delegate to uv, pnpm, and Docker Compose without embedding secrets.

Run:

```bash
cp .env.example .env
docker compose config --quiet
docker compose up --build -d
docker compose ps
curl --fail http://localhost:2024/info
curl --fail http://localhost:3000
```

Expected: 三个服务均healthy，两个HTTP请求成功。

- [ ] **Step 6: 提交容器化运行环境**

```bash
git add .env.example Makefile compose.yaml agent/Dockerfile web/Dockerfile agent/tests/integration/test_health.py
git commit -m "feat: 增加 Agent 三服务一键启动"
```

### Task 13: 建立CI、使用手册和迁移说明

**Files:**
- Create: `.github/workflows/build.yml`
- Create: `README.md`
- Create: `docs/USER_GUIDE.md`
- Create: `docs/MIGRATION.md`
- Create: `docs/INTERVIEW_GUIDE.md`
- Test: `agent/tests/unit/test_documentation.py`

- [ ] **Step 1: 写文档契约失败测试**

Create `agent/tests/unit/test_documentation.py`:

```python
from pathlib import Path


def test_readme_declares_upstream_and_synthetic_data() -> None:
    readme = Path("../README.md").read_text(encoding="utf-8")
    assert "langchain-ai/agent-chat-ui" in readme
    assert "langchain-ai/react-agent" in readme
    assert "合成数据" in readme
    assert "不处理真实资金" in readme


def test_migration_guide_never_requests_company_credentials() -> None:
    guide = Path("../docs/MIGRATION.md").read_text(encoding="utf-8")
    assert "个人模型 API Key" in guide
    assert "不要复制公司" in guide
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd agent && uv run pytest tests/unit/test_documentation.py -q`

Expected: FAIL，用户文档尚不存在。

- [ ] **Step 3: 编写文档**

`README.md` must include architecture, capabilities, explicit non-goals, exact upstream commits, local setup, API key safety, tests, evaluation metrics, screenshots, and a truthful authorship statement. `USER_GUIDE.md` gives the CASE-1001 analysis and approve/reject demo. `MIGRATION.md` starts from a fresh clone and personal API key. `INTERVIEW_GUIDE.md` answers why transactions use tools rather than RAG, why rules are version-filtered, how tool budgets work, how HITL prevents writes, and what came from upstream.

- [ ] **Step 4: 创建GitHub Actions**

Workflow services use `pgvector/pgvector:pg17`. Jobs:

```yaml
jobs:
  backend:
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: cd agent && uv sync --locked --all-groups
      - run: cd agent && uv run alembic upgrade head
      - run: cd agent && uv run pytest -q
      - run: cd agent && uv run ruff check .
  frontend:
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: {node-version: 22, cache: pnpm, cache-dependency-path: web/pnpm-lock.yaml}
      - run: corepack enable
      - run: cd web && pnpm install --frozen-lockfile
      - run: cd web && pnpm vitest run
      - run: cd web && pnpm format:check
      - run: cd web && pnpm build
```

CI uses a non-secret fake model key and scripted fake models; it never calls a paid model.

- [ ] **Step 5: 运行文档和完整验证**

Run:

```bash
cd agent && uv run pytest tests/unit/test_documentation.py -q
make verify
git diff --check
```

Expected: 后端、前端、评测和格式检查全部通过。

- [ ] **Step 6: 提交工程文档和CI**

```bash
git add .github README.md docs agent/tests/unit/test_documentation.py
git commit -m "docs: 完善 Agent 运行迁移与面试手册"
```

### Task 14: 最终安全审查和端到端验收

**Files:**
- Modify only files required by findings from the checks below
- Create: `docs/ACCEPTANCE.md`

- [ ] **Step 1: 扫描凭据和Java仓库耦合**

Run:

```bash
rg -n "gho_|sk-[A-Za-z0-9]|api[_-]?key\s*=\s*[^r$]" . --glob '!**/pnpm-lock.yaml' --glob '!**/uv.lock'
rg -n "ledger-reconciliation-platform|localhost:8080|jdbc:postgresql" agent web compose.yaml
```

Expected: 第一条无真实密钥；第二条不包含Java仓库运行依赖，只允许README或设计文档中的领域背景说明。

- [ ] **Step 2: 执行完整自动化验证**

Run:

```bash
make verify
docker compose --env-file .env.example config --quiet
git diff --check
git status --short --branch
```

Expected: 所有命令退出码为0；工作区只包含预期验收文档改动。

- [ ] **Step 3: 执行真实模型人工验收**

Use a personal model API key in `.env`, then run:

```bash
docker compose up --build -d
curl --fail http://localhost:2024/info
curl --fail http://localhost:3000
```

Verify these exact scenarios in the browser:

1. `CASE-1001` returns amount mismatch evidence and `RULE-ALIPAY-V1` citations.
2. `CASE-1004` refuses a conclusion because `RULE-MISSING-V9` is unavailable.
3. Rejecting a `CASE-1002` proposal leaves it open.
4. Approving the same proposal resolves it once; a second approval is rejected.
5. Tool calls, citations and final status remain visible in the UI.

- [ ] **Step 4: 记录验收证据**

Create `docs/ACCEPTANCE.md` with date, commit hash, commands, pass/fail counts, model provider/name without key, the five manual scenarios, known limitations, and screenshots stored under `docs/images/`. Do not write unsupported throughput or accuracy claims.

- [ ] **Step 5: 最终提交并推送功能分支**

```bash
git add docs/ACCEPTANCE.md docs/images
git commit -m "test: 记录智能对账 Agent 验收结果"
git push -u origin feature/agent-foundation
```

Expected: remote feature branch contains only Agent repository changes; the Java repository remains at its pre-project commit with a clean worktree.
