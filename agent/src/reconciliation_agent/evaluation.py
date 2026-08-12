"""Deterministic evaluation dataset, metrics and CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

Category = Literal[
    "tool_selection",
    "tool_arguments",
    "rule_retrieval",
    "citation",
    "refusal",
    "confirmation",
]

Outcome = Literal[
    "ANALYSIS_READY",
    "INSUFFICIENT_EVIDENCE",
    "INVALID_REQUEST",
    "CONFIRMATION_REQUIRED",
    "RESOLVED",
    "FAILED",
]

_CASE_PATTERN = re.compile(r"CASE-[A-Z0-9-]+", re.IGNORECASE)
_VALID_CASE_PATTERN = re.compile(r"^CASE-[0-9]{4}$")
_SCENARIOS = {
    "CASE-1001": (
        [
            "get_reconciliation_case",
            "find_payment_evidence",
            "find_ledger_evidence",
            "search_reconciliation_rules",
        ],
        "RULE-ALIPAY-V1",
    ),
    "CASE-1002": (
        [
            "get_reconciliation_case",
            "find_payment_evidence",
            "search_reconciliation_rules",
        ],
        "RULE-WECHAT-V1",
    ),
    "CASE-1003": (
        [
            "get_reconciliation_case",
            "find_payment_evidence",
            "find_ledger_evidence",
            "search_reconciliation_rules",
        ],
        "RULE-UNIONPAY-V1",
    ),
    "CASE-1004": (
        [
            "get_reconciliation_case",
            "find_payment_evidence",
            "find_ledger_evidence",
            "search_reconciliation_rules",
        ],
        "RULE-MISSING-V9",
    ),
}


class EvaluationCase(BaseModel):
    """One immutable expected behavior in the fixed evaluation set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^EVAL-[0-9]{3}$")
    category: Category
    question: str = Field(min_length=1)
    expected_case_id: str = Field(min_length=1)
    expected_tools: list[str]
    expected_rule_version: str | None
    expected_outcome: Outcome


class EvaluationObservation(BaseModel):
    """Deterministic facts observed from one runner execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    selected_case_id: str | None
    tool_names: list[str]
    retrieved_rule_versions: list[str]
    citation_rule_versions: list[str]
    outcome: Outcome
    resolution_count_before: int = Field(ge=0)
    resolution_count_after: int = Field(ge=0)


class EvaluationMetrics(BaseModel):
    """Exact metrics computed without an LLM judge."""

    model_config = ConfigDict(frozen=True)

    tool_selection_accuracy: float
    argument_accuracy: float
    retrieval_recall_at_k: float
    citation_completeness: float
    outcome_consistency: float
    refusal_accuracy: float


class EvaluationFailure(BaseModel):
    """Metric-level failure details for one evaluation case."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    failed_metrics: list[str]
    expected_outcome: Outcome
    actual_outcome: Outcome


class EvaluationReport(BaseModel):
    """Machine-readable evaluation summary."""

    mode: Literal["scripted", "live"]
    case_count: int
    metrics: EvaluationMetrics
    failures: list[EvaluationFailure]
    token_usage: dict[str, int] | None = None


def load_cases(path: str | Path) -> list[EvaluationCase]:
    """Load and validate a fixed JSON evaluation dataset."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return TypeAdapter(list[EvaluationCase]).validate_python(payload)


def _score(values: Sequence[bool]) -> float:
    return sum(values) / len(values) if values else 1.0


def _retrieval_matches(
    case: EvaluationCase, observation: EvaluationObservation
) -> bool:
    if case.expected_outcome == "INSUFFICIENT_EVIDENCE":
        return observation.retrieved_rule_versions == []
    return (
        case.expected_rule_version is not None
        and case.expected_rule_version in observation.retrieved_rule_versions
    )


def _citation_matches(
    case: EvaluationCase, observation: EvaluationObservation
) -> bool:
    if case.expected_outcome == "INSUFFICIENT_EVIDENCE":
        return observation.citation_rule_versions == []
    return (
        case.expected_rule_version is not None
        and case.expected_rule_version in observation.citation_rule_versions
    )


def _outcome_matches(
    case: EvaluationCase, observation: EvaluationObservation
) -> bool:
    if observation.outcome != case.expected_outcome:
        return False
    if case.category != "confirmation":
        return True
    expected_after = 1 if case.expected_outcome == "RESOLVED" else 0
    return (
        observation.resolution_count_before == 0
        and observation.resolution_count_after == expected_after
    )


def evaluate(
    cases: Sequence[EvaluationCase],
    observations: Sequence[EvaluationObservation],
    *,
    mode: Literal["scripted", "live"] = "scripted",
    token_usage: dict[str, int] | None = None,
) -> EvaluationReport:
    """Compute exact category metrics and retain case-level failures."""
    # 评估指标全部由固定期望和结构化观察计算，不使用另一个 LLM 充当裁判。
    if len(cases) != len(observations):
        raise ValueError("cases and observations must have equal length")
    by_id = {item.case_id: item for item in observations}
    if len(by_id) != len(observations):
        raise ValueError("observation case ids must be unique")

    results: dict[str, list[bool]] = {
        "tool_selection_accuracy": [],
        "argument_accuracy": [],
        "retrieval_recall_at_k": [],
        "citation_completeness": [],
        "outcome_consistency": [],
        "refusal_accuracy": [],
    }
    failures: list[EvaluationFailure] = []
    for case in cases:
        observation = by_id.get(case.id)
        if observation is None:
            raise ValueError(f"missing observation for {case.id}")
        checks: dict[str, bool] = {
            "outcome_consistency": _outcome_matches(case, observation)
        }
        if case.category == "tool_selection":
            checks["tool_selection_accuracy"] = (
                observation.tool_names == case.expected_tools
            )
        elif case.category == "tool_arguments":
            checks["argument_accuracy"] = (
                observation.selected_case_id == case.expected_case_id
            )
        elif case.category == "rule_retrieval":
            checks["retrieval_recall_at_k"] = _retrieval_matches(case, observation)
        elif case.category == "citation":
            checks["citation_completeness"] = _citation_matches(case, observation)
        elif case.category == "refusal":
            checks["refusal_accuracy"] = observation.outcome == case.expected_outcome
        for name, passed in checks.items():
            results[name].append(passed)
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            failures.append(
                EvaluationFailure(
                    case_id=case.id,
                    failed_metrics=failed,
                    expected_outcome=case.expected_outcome,
                    actual_outcome=observation.outcome,
                )
            )

    return EvaluationReport(
        mode=mode,
        case_count=len(cases),
        metrics=EvaluationMetrics(
            **{name: _score(values) for name, values in results.items()}
        ),
        failures=failures,
        token_usage=token_usage,
    )


def _scripted_observation(case: EvaluationCase) -> EvaluationObservation:
    # scripted 模式验证策略和数据契约，不需要模型、PostgreSQL 或外部 API，适合 CI 快速门禁。
    match = _CASE_PATTERN.search(case.question)
    selected_case_id = match.group(0).upper() if match else None
    if selected_case_id is None or not _VALID_CASE_PATTERN.fullmatch(selected_case_id):
        tools: list[str] = []
        retrieved: list[str] = []
        outcome: Outcome = "INVALID_REQUEST"
    elif selected_case_id not in _SCENARIOS:
        tools = ["get_reconciliation_case"]
        retrieved = []
        outcome = "INSUFFICIENT_EVIDENCE"
    else:
        tools, rule_version = _SCENARIOS[selected_case_id]
        retrieved = [] if rule_version == "RULE-MISSING-V9" else [rule_version]
        if rule_version == "RULE-MISSING-V9":
            outcome = "INSUFFICIENT_EVIDENCE"
        elif "批准后" in case.question:
            outcome = "RESOLVED"
        elif any(word in case.question for word in ("解决", "处理")):
            outcome = "CONFIRMATION_REQUIRED"
        else:
            outcome = "ANALYSIS_READY"
    citations = list(retrieved)
    resolved = 1 if outcome == "RESOLVED" else 0
    return EvaluationObservation(
        case_id=case.id,
        selected_case_id=selected_case_id,
        tool_names=list(tools),
        retrieved_rule_versions=retrieved,
        citation_rule_versions=citations,
        outcome=outcome,
        resolution_count_before=0,
        resolution_count_after=resolved,
    )


def run_scripted(cases: Sequence[EvaluationCase]) -> EvaluationReport:
    """Run deterministic policy observations without model or database access."""
    return evaluate(cases, [_scripted_observation(case) for case in cases])


def _live_outcome(result: dict[str, Any]) -> Outcome:
    if result.get("__interrupt__"):
        return "CONFIRMATION_REQUIRED"
    status = (result.get("final_response") or {}).get("status")
    if not isinstance(status, str):
        return "INVALID_REQUEST"
    outcomes: dict[str, Outcome] = {
        "READY": "ANALYSIS_READY",
        "INSUFFICIENT_EVIDENCE": "INSUFFICIENT_EVIDENCE",
        "INVALID_REQUEST": "INVALID_REQUEST",
        "RESOLVED": "RESOLVED",
        "FAILED": "FAILED",
    }
    return outcomes.get(status, "INVALID_REQUEST")


async def _run_live(cases: Sequence[EvaluationCase]) -> EvaluationReport:
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    from reconciliation_agent.model import build_chat_model
    from reconciliation_agent.settings import get_settings
    from reconciliation_agent.workflow import build_graph

    model = build_chat_model(get_settings())
    observations: list[EvaluationObservation] = []
    input_tokens = 0
    output_tokens = 0
    for case in cases:
        graph = build_graph(model=model, checkpointer=InMemorySaver())
        config: RunnableConfig = {
            "configurable": {"thread_id": f"{case.id}-{uuid4()}"}
        }
        result = await graph.ainvoke(
            {"messages": [("user", case.question)]}, config=config
        )
        initial_outcome = _live_outcome(result)
        if initial_outcome == "CONFIRMATION_REQUIRED":
            decision = "approve" if case.expected_outcome == "RESOLVED" else "reject"
            result = await graph.ainvoke(
                Command[Any](resume={"decisions": [{"type": decision}]}),
                config=config,
            )
        outcome = (
            _live_outcome(result)
            if case.expected_outcome == "RESOLVED"
            else initial_outcome
        )
        for message in result.get("messages", []):
            if isinstance(message, AIMessage) and message.usage_metadata:
                input_tokens += message.usage_metadata.get("input_tokens", 0)
                output_tokens += message.usage_metadata.get("output_tokens", 0)
        citations = result.get("citations", [])
        observations.append(
            EvaluationObservation(
                case_id=case.id,
                selected_case_id=result.get("case_id"),
                tool_names=result.get("tool_call_history", []),
                retrieved_rule_versions=[
                    item["rule_version"] for item in citations if "rule_version" in item
                ],
                citation_rule_versions=[
                    item["rule_version"] for item in citations if "rule_version" in item
                ],
                outcome=outcome,
                resolution_count_before=0,
                resolution_count_after=1 if outcome == "RESOLVED" else 0,
            )
        )
    return evaluate(
        cases,
        observations,
        mode="live",
        token_usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate the reconciliation Agent")
    parser.add_argument("--mode", choices=("scripted", "live"), default="scripted")
    parser.add_argument("--dataset", default="evals/cases.json")
    parser.add_argument("--output", default="build/evaluation.json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run an evaluation mode and write its machine-readable report."""
    args = _parser().parse_args(argv)
    cases = load_cases(args.dataset)
    report = (
        run_scripted(cases)
        if args.mode == "scripted"
        else asyncio.run(_run_live(cases))
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return 0 if not report.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
