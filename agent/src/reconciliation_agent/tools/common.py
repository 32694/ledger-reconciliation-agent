"""Shared contracts and repository scope for read-only evidence tools."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from functools import lru_cache
from typing import Protocol

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.v1 import ValidationError as ValidationErrorV1
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from reconciliation_agent.domain.models import (
    AuditEvent,
    LedgerEntry,
    Payment,
    ReconciliationCase,
)
from reconciliation_agent.domain.repository import (
    CaseNotFoundError,
    ReconciliationRepository,
)
from reconciliation_agent.settings import get_database_settings


class CaseIdInput(BaseModel):
    """Strict case identifier accepted by read-only tools."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^CASE-[0-9]{4}$")


class ErrorResult(BaseModel):
    """Stable error returned to the model."""

    error: str
    message: str


class ReadRepository(Protocol):
    """Repository operations required by evidence tools."""

    def get_case(self, case_id: str) -> ReconciliationCase:
        """Return a reconciliation case."""

    def find_payments(self, case: ReconciliationCase) -> list[Payment]:
        """Return payment evidence for a case."""

    def find_ledger_entries(self, case: ReconciliationCase) -> list[LedgerEntry]:
        """Return ledger evidence for a case."""

    def get_audit_timeline(self, case_id: str) -> list[AuditEvent]:
        """Return audit evidence for a case."""


RepositoryProvider = Callable[[], AbstractContextManager[ReadRepository]]
ReadOperation = Callable[[ReadRepository, ReconciliationCase], BaseModel]


@lru_cache
def get_tool_engine() -> Engine:
    """Build the shared lazy SQLAlchemy engine for tool invocations."""
    settings = get_database_settings()
    return create_engine(settings.database_url_for_sqlalchemy)


@contextmanager
def production_repository_provider() -> Iterator[ReadRepository]:
    """Provide one closed, non-committing repository scope per invocation."""
    with Session(get_tool_engine()) as session:
        yield ReconciliationRepository(session)


def invoke_read_tool(
    provider: RepositoryProvider,
    case_id: str,
    operation: ReadOperation,
) -> str:
    """Run a read operation and serialize its result or a stable expected error."""
    try:
        with provider() as repository:
            case = repository.get_case(case_id)
            return operation(repository, case).model_dump_json()
    except CaseNotFoundError:
        return ErrorResult(
            error="CASE_NOT_FOUND", message=f"未找到案件: {case_id}"
        ).model_dump_json()
    except SQLAlchemyError:
        return ErrorResult(
            error="DEPENDENCY_UNAVAILABLE",
            message="证据存储暂时不可用，请稍后重试",
        ).model_dump_json()


def validation_error_json(error: ValidationError | ValidationErrorV1) -> str:
    """Convert tool input validation details to a stable public error."""
    details = error.errors()
    extras = sorted(
        str(detail["loc"][-1])
        for detail in details
        if detail["type"] == "extra_forbidden" and detail["loc"]
    )
    if extras:
        message = f"不支持的参数: {', '.join(extras)}"
    elif any(
        detail["type"] == "missing" and detail["loc"] == ("case_id",)
        for detail in details
    ):
        message = "缺少必填参数: case_id"
    else:
        message = "参数 case_id 必须符合 CASE-四位数字格式"
    return ErrorResult(error="INVALID_ARGUMENT", message=message).model_dump_json()


def configure_validation_errors(tool: BaseTool) -> BaseTool:
    """Make LangChain invocation return stable JSON for invalid input."""
    tool.handle_validation_error = validation_error_json
    return tool
