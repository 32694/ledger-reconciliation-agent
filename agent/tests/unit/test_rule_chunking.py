import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from reconciliation_agent.rag.ingest import (
    chunk_rule_document,
    parse_rule_document,
)


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


def test_chunking_normalizes_whitespace_and_is_deterministic() -> None:
    text = "# 规则\n\n## 金额差异\n\n金额  必须\n完全一致。\n\n## 单边流水\n\n不得自动核销。"
    first = chunk_rule_document("rules.md", text, "ALIPAY", "RULE-ALIPAY-V1")
    second = chunk_rule_document("rules.md", text, "ALIPAY", "RULE-ALIPAY-V1")

    assert [chunk.section for chunk in first] == ["金额差异", "单边流水"]
    assert first[0].content == "金额 必须 完全一致。"
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert (
        first[0].chunk_id
        == hashlib.sha256(
            "rules.mdRULE-ALIPAY-V1金额差异金额 必须 完全一致。".encode()
        ).hexdigest()
    )


def test_document_front_matter_is_parsed_strictly() -> None:
    document = parse_rule_document(
        """---
channel: ALIPAY
rule_version: RULE-ALIPAY-V1
effective_at: 2026-01-01T00:00:00Z
source: alipay-v1.md
---
# 支付宝规则

## 金额差异

金额必须完全一致。
"""
    )

    assert document.metadata.channel == "ALIPAY"
    assert document.metadata.effective_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert document.body.startswith("# 支付宝规则")


@pytest.mark.parametrize(
    "front_matter",
    [
        "channel: ALIPAY\nrule_version: RULE-ALIPAY-V1\nsource: a.md",
        (
            "channel: ALIPAY\nrule_version: RULE-ALIPAY-V1\n"
            "effective_at: 2026-01-01T00:00:00Z\nsource: a.md\nextra: no"
        ),
    ],
)
def test_document_rejects_missing_or_extra_metadata(front_matter: str) -> None:
    with pytest.raises(ValidationError):
        parse_rule_document(f"---\n{front_matter}\n---\n## 规则\n正文")


def test_document_rejects_missing_front_matter() -> None:
    with pytest.raises(ValueError, match="YAML front matter"):
        parse_rule_document("## 规则\n正文")


def test_chunking_rejects_documents_without_level_two_sections() -> None:
    with pytest.raises(ValueError, match="level-two"):
        chunk_rule_document("a.md", "# 只有标题", "ALIPAY", "RULE-ALIPAY-V1")
