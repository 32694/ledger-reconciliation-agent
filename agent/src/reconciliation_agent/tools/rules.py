"""Version-locked reconciliation rule search tool."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from functools import lru_cache
from typing import Literal, Protocol

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.v1 import ValidationError as ValidationErrorV1
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from reconciliation_agent.rag.embeddings import (
    EmbeddingService,
    EmbeddingUnavailableError,
    get_embedding_settings,
)
from reconciliation_agent.rag.retriever import RuleCitation, RuleRetriever
from reconciliation_agent.tools.common import ErrorResult, get_tool_engine


class RuleSearchInput(BaseModel):
    """Strict metadata and query accepted by rule retrieval."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    channel: Literal["ALIPAY", "WECHAT", "UNIONPAY"]
    rule_version: str = Field(pattern=r"^RULE-[A-Z]+-V[0-9]+$")
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=3, ge=1, le=5, strict=True)


class RuleSearchResult(BaseModel):
    """Ordered version-locked citations."""

    citations: list[RuleCitation]


class RuleSearchRepository(Protocol):
    """Search operation needed by the LangChain tool."""

    def search(
        self, channel: str, rule_version: str, query: str, limit: int
    ) -> list[RuleCitation]:
        """Return exact-version citations."""


RuleRetrieverProvider = Callable[[], AbstractContextManager[RuleSearchRepository]]


@lru_cache
def _get_embedding_service() -> EmbeddingService:
    return EmbeddingService(get_embedding_settings().embedding_model)


@contextmanager
def production_rule_retriever_provider() -> Iterator[RuleSearchRepository]:
    """Provide a closed repository scope without connecting at import time."""
    # EmbeddingService 使用本地模型，规则文本和查询内容不发送到聊天模型供应商。
    with Session(get_tool_engine()) as session:
        yield RuleRetriever(session, _get_embedding_service())


def _validation_error_json(error: ValidationError | ValidationErrorV1) -> str:
    extras = sorted(
        str(detail["loc"][-1])
        for detail in error.errors()
        if detail["type"] == "extra_forbidden" and detail["loc"]
    )
    message = f"不支持的参数: {', '.join(extras)}" if extras else "规则检索参数无效"
    return ErrorResult(error="INVALID_ARGUMENT", message=message).model_dump_json()


def build_rule_search_tool(provider: RuleRetrieverProvider) -> BaseTool:
    """Build the rule search tool with an injected retriever provider."""

    @tool(args_schema=RuleSearchInput)
    def search_reconciliation_rules(
        channel: str,
        rule_version: str,
        query: str,
        limit: int = 3,
    ) -> str:
        """Search read-only reconciliation rules within one exact locked version."""
        # channel 与 rule_version 是强制过滤条件，向量相似度只负责版本内排序。
        try:
            with provider() as retriever:
                citations = retriever.search(channel, rule_version, query, limit)
        except (SQLAlchemyError, EmbeddingUnavailableError):
            return ErrorResult(
                error="DEPENDENCY_UNAVAILABLE",
                message="规则检索暂时不可用，请稍后重试",
            ).model_dump_json()
        if not citations:
            return ErrorResult(
                error="RULE_VERSION_NOT_FOUND",
                message=f"未找到指定版本的对账规则: {rule_version}",
            ).model_dump_json()
        return RuleSearchResult(citations=citations).model_dump_json()

    search_reconciliation_rules.handle_validation_error = _validation_error_json
    return search_reconciliation_rules


search_reconciliation_rules = build_rule_search_tool(production_rule_retriever_provider)
