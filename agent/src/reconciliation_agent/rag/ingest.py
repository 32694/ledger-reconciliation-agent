"""Strict Markdown parsing and idempotent rule ingestion."""

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from reconciliation_agent.domain.models import RuleChunk

_FRONT_MATTER = re.compile(
    r"\A---[ \t]*\n(?P<metadata>.*?)\n---[ \t]*(?:\n|\Z)(?P<body>.*)\Z",
    re.DOTALL,
)
_LEVEL_TWO_HEADING = re.compile(r"^##(?!#)\s+(.+?)\s*$")
_LEGACY_SOURCE = "synthetic://reconciliation-rules"


class Embedder(Protocol):
    """Embedding operation required by ingestion."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector for every text."""


class RuleDocumentMetadata(BaseModel):
    """Required version-locking metadata from Markdown front matter."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    channel: Literal["ALIPAY", "WECHAT", "UNIONPAY"]
    rule_version: str = Field(pattern=r"^RULE-[A-Z]+-V[0-9]+$")
    effective_at: datetime
    source: str = Field(min_length=1, max_length=255)

    @field_validator("effective_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Reject ambiguous timestamps without an explicit timezone."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("effective_at must include a timezone")
        return value


class ParsedRuleDocument(BaseModel):
    """Validated metadata and Markdown body."""

    metadata: RuleDocumentMetadata
    body: str


class RuleChunkDraft(BaseModel):
    """Deterministic rule section before vector persistence."""

    chunk_id: str
    source: str
    channel: str
    rule_version: str
    section: str
    content: str


def parse_rule_document(text: str) -> ParsedRuleDocument:
    """Parse exact YAML front matter and return the remaining Markdown body."""
    match = _FRONT_MATTER.match(text.replace("\r\n", "\n"))
    if match is None:
        raise ValueError("rule document requires YAML front matter")
    raw_metadata = yaml.safe_load(match.group("metadata"))
    if not isinstance(raw_metadata, dict):
        raise ValueError("rule document metadata must be a mapping")
    return ParsedRuleDocument(
        metadata=RuleDocumentMetadata.model_validate(raw_metadata),
        body=match.group("body").strip(),
    )


def _normalized_content(lines: list[str]) -> str:
    return re.sub(r"\s+", " ", "\n".join(lines)).strip()


def _chunk_id(source: str, rule_version: str, section: str, content: str) -> str:
    payload = f"{source}{rule_version}{section}{content}".encode()
    return hashlib.sha256(payload).hexdigest()


def chunk_rule_document(
    source: str,
    text: str,
    channel: str,
    rule_version: str,
) -> list[RuleChunkDraft]:
    """Split Markdown at level-two headings with stable metadata and IDs."""
    chunks: list[RuleChunkDraft] = []
    section: str | None = None
    content_lines: list[str] = []

    def append_section() -> None:
        if section is None:
            return
        content = _normalized_content(content_lines)
        if not content:
            raise ValueError(f"rule section has no content: {section}")
        chunks.append(
            RuleChunkDraft(
                chunk_id=_chunk_id(source, rule_version, section, content),
                source=source,
                channel=channel,
                rule_version=rule_version,
                section=section,
                content=content,
            )
        )

    for line in text.replace("\r\n", "\n").splitlines():
        heading = _LEVEL_TWO_HEADING.match(line)
        if heading is not None:
            append_section()
            section = heading.group(1).strip()
            content_lines = []
        elif section is not None:
            content_lines.append(line)
    append_section()
    if not chunks:
        raise ValueError("rule document requires at least one level-two section")
    return chunks


def _load_documents(
    rules_directory: Path,
) -> list[tuple[RuleDocumentMetadata, list[RuleChunkDraft]]]:
    documents: list[tuple[RuleDocumentMetadata, list[RuleChunkDraft]]] = []
    for path in sorted(rules_directory.glob("*.md")):
        parsed = parse_rule_document(path.read_text(encoding="utf-8"))
        if parsed.metadata.source != path.name:
            raise ValueError(f"rule source must match file name: {path.name}")
        chunks = chunk_rule_document(
            source=parsed.metadata.source,
            text=parsed.body,
            channel=parsed.metadata.channel,
            rule_version=parsed.metadata.rule_version,
        )
        documents.append((parsed.metadata, chunks))
    if not documents:
        raise ValueError("rules directory contains no Markdown documents")
    return documents


def ingest_rule_directory(
    session: Session,
    rules_directory: Path,
    embedding_service: Embedder,
) -> int:
    """Upsert all versioned rule documents without committing the transaction."""
    documents = _load_documents(rules_directory)
    drafts = [chunk for _, chunks in documents for chunk in chunks]
    vectors = embedding_service.embed([draft.content for draft in drafts])
    if len(vectors) != len(drafts) or any(len(vector) != 512 for vector in vectors):
        raise ValueError(
            "embedding service must return one 512-dimensional vector per chunk"
        )

    session.execute(delete(RuleChunk).where(RuleChunk.source == _LEGACY_SOURCE))
    vector_by_id = {
        draft.chunk_id: vector for draft, vector in zip(drafts, vectors, strict=True)
    }

    for metadata, chunks in documents:
        chunk_ids = [chunk.chunk_id for chunk in chunks]
        session.execute(
            delete(RuleChunk).where(
                RuleChunk.source == metadata.source,
                RuleChunk.rule_version == metadata.rule_version,
                RuleChunk.chunk_id.not_in(chunk_ids),
            )
        )
        for draft in chunks:
            existing = session.scalar(
                select(RuleChunk).where(RuleChunk.chunk_id == draft.chunk_id)
            )
            values = {
                "channel": draft.channel,
                "rule_version": draft.rule_version,
                "effective_at": metadata.effective_at,
                "source": draft.source,
                "section": draft.section,
                "content": draft.content,
                "embedding": vector_by_id[draft.chunk_id],
            }
            if existing is None:
                session.add(RuleChunk(chunk_id=draft.chunk_id, **values))
            else:
                if (
                    existing.source != draft.source
                    or existing.rule_version != draft.rule_version
                ):
                    raise ValueError(
                        f"rule chunk ID ownership conflict: {draft.chunk_id}"
                    )
                for field, value in values.items():
                    setattr(existing, field, value)
    session.flush()
    return len(drafts)
