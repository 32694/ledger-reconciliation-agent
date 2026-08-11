"""Agent run lifecycle, summaries and public error boundaries."""

import json
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from enum import Enum
from typing import Any
from uuid import uuid4

import httpx
import openai
from langchain_core.messages import ToolMessage
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from reconciliation_agent.domain.repository import AgentRunRepository
from reconciliation_agent.tools.common import get_tool_engine

ERROR_MESSAGES = {
    "MODEL_UNAVAILABLE": "模型服务暂时不可用，请稍后重试。",
    "DATABASE_UNAVAILABLE": "数据服务暂时不可用，请稍后重试。",
    "RULE_VERSION_NOT_FOUND": "未找到案件锁定版本的规则，当前无法给出确定结论。",
    "TOOL_BUDGET_EXHAUSTED": "证据查询次数已达到上限，当前无法完成分析。",
    "INTERNAL_ERROR": "Agent服务暂时不可用，请稍后重试。",
}

_URL_CREDENTIALS = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<credentials>[^/@\s]+)@"
)


class RunStatus(str, Enum):
    """Persisted lifecycle state of one Agent investigation."""

    RUNNING = "RUNNING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class PublicError(BaseModel):
    """Stable error safe to return to clients and persist."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


def public_error(error: BaseException) -> PublicError:
    """Map a private exception to a stable public error without its text."""
    if isinstance(
        error,
        TimeoutError
        | httpx.TransportError
        | openai.APIConnectionError
        | openai.APITimeoutError
        | openai.RateLimitError,
    ):
        code = "MODEL_UNAVAILABLE"
    elif isinstance(error, SQLAlchemyError):
        code = "DATABASE_UNAVAILABLE"
    else:
        code = "INTERNAL_ERROR"
    return PublicError(code=code, message=ERROR_MESSAGES[code])


def redact_private_error(text: str, *, secrets: Sequence[str] = ()) -> str:
    """Redact URL credentials and explicitly configured secrets for private logs."""
    redacted = _URL_CREDENTIALS.sub(r"\g<scheme>***@", text)
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        redacted = redacted.replace(secret, "***")
    return redacted


def summarize_tool_messages(messages: Sequence[ToolMessage]) -> list[dict[str, Any]]:
    """Reduce tool outputs to names, status codes and non-sensitive counts."""
    summaries: list[dict[str, Any]] = []
    for message in messages:
        summary: dict[str, Any] = {"name": message.name or "unknown"}
        try:
            value = json.loads(str(message.content))
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict) and isinstance(value.get("error"), str):
            summary["result"] = value["error"]
        elif isinstance(value, dict) and isinstance(value.get("citations"), list):
            summary["result"] = "OK"
            summary["record_count"] = len(value["citations"])
        elif isinstance(value, dict):
            records = next(
                (
                    value[key]
                    for key in ("payments", "entries", "events")
                    if isinstance(value.get(key), list)
                ),
                None,
            )
            summary["result"] = "OK"
            summary["record_count"] = len(records) if records is not None else 1
        else:
            summary["result"] = "INVALID_RESULT"
        summaries.append(summary)
    return summaries


class RunService:
    """Apply validated state transitions to persisted Agent runs."""

    def __init__(self, session: Session) -> None:
        """Bind lifecycle operations to one database transaction."""
        self._repository = AgentRunRepository(session)

    def start(self, question: str) -> str:
        """Create a uniquely identified running investigation."""
        run_id = f"RUN-{uuid4()}"
        self._repository.create(
            run_id=run_id,
            question=question,
            status=RunStatus.RUNNING.value,
        )
        return run_id

    def record_tools(self, run_id: str, summaries: list[dict[str, Any]]) -> None:
        """Append sanitized tool summaries in execution order."""
        run = self._repository.get(run_id, for_update=True)
        run.tool_calls = [*run.tool_calls, *summaries]

    def mark_waiting(self, run_id: str) -> None:
        """Mark a run paused for human confirmation."""
        run = self._repository.get(run_id, for_update=True)
        run.status = RunStatus.WAITING_CONFIRMATION.value

    def finish(
        self,
        run_id: str,
        *,
        status: RunStatus,
        citations: Sequence[dict[str, Any]],
        final_response: dict[str, Any],
        error: PublicError | None = None,
    ) -> None:
        """Persist a terminal result using only public error text."""
        run = self._repository.get(run_id, for_update=True)
        run.status = status.value
        run.citations = [
            {"chunk_id": str(item["chunk_id"])}
            for item in citations
            if isinstance(item.get("chunk_id"), str)
        ]
        run.conclusion = json.dumps(final_response, ensure_ascii=False, sort_keys=True)
        run.error_message = error.message if error else None


RunServiceProvider = Callable[[], AbstractContextManager[RunService]]


@contextmanager
def production_run_service_provider() -> Iterator[RunService]:
    """Commit one run lifecycle operation per workflow node."""
    with Session(get_tool_engine()) as session, session.begin():
        yield RunService(session)
