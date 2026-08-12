"""Strict version-filtered pgvector retrieval."""

from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from reconciliation_agent.domain.models import RuleChunk


class QueryEmbedder(Protocol):
    """Embedding operation required by retrieval."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one query vector for every text."""


class RuleCitation(BaseModel):
    """A version-locked rule citation returned to the workflow."""

    chunk_id: str
    source: str
    section: str
    rule_version: str
    content: str
    distance: float


class RuleRetriever:
    """Retrieve rule chunks only inside an exact channel and version."""

    def __init__(self, session: Session, embedding_service: QueryEmbedder) -> None:
        """Bind retrieval to one database session and embedding service."""
        self._session = session
        self._embedding_service = embedding_service

    def search(
        self,
        channel: str,
        rule_version: str,
        query: str,
        limit: int = 3,
    ) -> list[RuleCitation]:
        """Return nearest citations after mandatory metadata filtering."""
        # 先验证版本是否存在，再计算向量距离；这样“相似但错误版本”的规则永远不会被返回。
        if isinstance(limit, bool) or not 1 <= limit <= 5:
            raise ValueError("limit must be between 1 and 5")
        if not query.strip():
            raise ValueError("query must not be empty")
        version_exists = self._session.scalar(
            select(RuleChunk.id)
            .where(
                RuleChunk.channel == channel,
                RuleChunk.rule_version == rule_version,
            )
            .limit(1)
        )
        if version_exists is None:
            return []
        vectors = self._embedding_service.embed([query])
        if len(vectors) != 1 or len(vectors[0]) != 512:
            raise ValueError("query embedding must have 512 dimensions")

        # 规则向量已归一化，余弦距离越小表示语义越接近查询。
        distance = RuleChunk.embedding.cosine_distance(vectors[0]).label("distance")
        statement = (
            select(RuleChunk, distance)
            .where(
                RuleChunk.channel == channel,
                RuleChunk.rule_version == rule_version,
            )
            .order_by(distance, RuleChunk.chunk_id)
            .limit(limit)
        )
        return [
            RuleCitation(
                chunk_id=chunk.chunk_id,
                source=chunk.source,
                section=chunk.section,
                rule_version=chunk.rule_version,
                content=chunk.content,
                distance=float(row_distance),
            )
            for chunk, row_distance in self._session.execute(statement)
        ]
