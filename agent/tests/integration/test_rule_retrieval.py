from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select

from reconciliation_agent.domain.models import RuleChunk
from reconciliation_agent.domain.seed import seed_demo_data
from reconciliation_agent.rag.ingest import ingest_rule_directory
from reconciliation_agent.rag.retriever import RuleRetriever


class FixedEmbeddingService:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 511 for _ in texts]


class UnexpectedEmbeddingService:
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("missing versions must not be embedded")


@pytest.fixture
def rule_retriever(session) -> RuleRetriever:
    rows = [
        RuleChunk(
            chunk_id="ALIPAY-V1-AMOUNT",
            channel="ALIPAY",
            rule_version="RULE-ALIPAY-V1",
            effective_at=datetime(2026, 1, 1, tzinfo=UTC),
            source="alipay-v1.md",
            section="金额差异",
            content="金额必须完全一致。",
            embedding=[1.0] + [0.0] * 511,
        ),
        RuleChunk(
            chunk_id="ALIPAY-V2-AMOUNT",
            channel="ALIPAY",
            rule_version="RULE-ALIPAY-V2",
            effective_at=datetime(2026, 2, 1, tzinfo=UTC),
            source="alipay-v2.md",
            section="金额差异",
            content="新版金额规则。",
            embedding=[0.9, 0.1] + [0.0] * 510,
        ),
        RuleChunk(
            chunk_id="WECHAT-V1-AMOUNT",
            channel="WECHAT",
            rule_version="RULE-WECHAT-V1",
            effective_at=datetime(2026, 1, 1, tzinfo=UTC),
            source="wechat-v1.md",
            section="金额差异",
            content="微信金额规则。",
            embedding=[0.8, 0.2] + [0.0] * 510,
        ),
    ]
    session.add_all(rows)
    session.flush()
    return RuleRetriever(session, FixedEmbeddingService())


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
    assert (
        rule_retriever.search(
            channel="ALIPAY",
            rule_version="RULE-MISSING-V9",
            query="金额差异",
            limit=3,
        )
        == []
    )


def test_missing_version_is_filtered_before_embedding(session) -> None:
    retriever = RuleRetriever(session, UnexpectedEmbeddingService())
    assert retriever.search("ALIPAY", "RULE-MISSING-V9", "金额", 3) == []


@pytest.mark.parametrize("limit", [0, 6, True])
def test_retrieval_rejects_out_of_range_limits(rule_retriever, limit) -> None:
    with pytest.raises(ValueError, match="between 1 and 5"):
        rule_retriever.search("ALIPAY", "RULE-ALIPAY-V1", "金额", limit)


def test_rule_ingestion_is_repeatable_and_removes_owned_legacy_rows(
    session, tmp_path
) -> None:
    session.execute(delete(RuleChunk))
    session.add(
        RuleChunk(
            chunk_id="LEGACY-PLACEHOLDER",
            channel="ALIPAY",
            rule_version="RULE-ALIPAY-V1",
            effective_at=datetime(2025, 1, 1, tzinfo=UTC),
            source="synthetic://reconciliation-rules",
            section="legacy",
            content="placeholder",
            embedding=[0.0] * 512,
        )
    )
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "alipay-v1.md").write_text(
        """---
channel: ALIPAY
rule_version: RULE-ALIPAY-V1
effective_at: 2026-01-01T00:00:00Z
source: alipay-v1.md
---
# 支付宝规则

## 金额差异

金额必须完全一致。
""",
        encoding="utf-8",
    )

    ingest_rule_directory(session, rules_dir, FixedEmbeddingService())
    ingest_rule_directory(session, rules_dir, FixedEmbeddingService())

    chunks = list(session.scalars(select(RuleChunk).order_by(RuleChunk.chunk_id)))
    assert len(chunks) == 1
    assert chunks[0].source == "alipay-v1.md"
    assert chunks[0].section == "金额差异"


def test_demo_seed_does_not_create_placeholder_rule_chunks(session) -> None:
    session.execute(delete(RuleChunk))
    seed_demo_data(session)
    assert session.scalar(select(func.count()).select_from(RuleChunk)) == 0
