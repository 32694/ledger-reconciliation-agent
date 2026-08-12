"""Evidence-constrained LangGraph workflow for reconciliation analysis."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Literal, cast
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from reconciliation_agent.context import AgentContext
from reconciliation_agent.domain.models import ResolutionCode, ResultType
from reconciliation_agent.domain.schemas import ResolutionProposal
from reconciliation_agent.model import build_chat_model, invoke_model_with_retry
from reconciliation_agent.prompts import SYSTEM_PROMPT
from reconciliation_agent.runs import (
    ERROR_MESSAGES,
    PublicError,
    RunServiceProvider,
    RunStatus,
    production_run_service_provider,
    public_error,
    summarize_tool_messages,
)
from reconciliation_agent.settings import get_settings
from reconciliation_agent.state import (
    AgentState,
    EvidenceStatus,
    EvidenceStatusValue,
    InputState,
)
from reconciliation_agent.tools import READ_TOOLS
from reconciliation_agent.tools.resolution import (
    ResolutionActionProvider,
    execute_confirmed_resolution,
    production_resolution_action_provider,
)
from reconciliation_agent.tools.rules import search_reconciliation_rules

_CASE_ID_PATTERN = re.compile(r"(?<![A-Z0-9_])CASE-[0-9]{4}(?![A-Z0-9_])")


class ResolutionRecommendation(BaseModel):
    """Model-proposed action that still requires later human confirmation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resolution_code: ResolutionCode
    note: str = Field(min_length=1, max_length=2000)


class DraftAnalysis(BaseModel):
    """Strict draft emitted by the model after it stops using tools."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["READY"]
    summary: str = Field(min_length=1, max_length=4000)
    resolution_recommendation: ResolutionRecommendation


def _message_text(message: AnyMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        item if isinstance(item, str) else str(item.get("text", ""))
        for item in content
    )


def _parse_draft_analysis(text: str) -> DraftAnalysis:
    """Parse a strict draft while tolerating markdown/prose around its JSON."""
    # 某些兼容模型会在 JSON 外包裹 Markdown 或解释文字；这里抽取对象后仍交给 Pydantic 严格校验。
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            candidate = "\n".join(lines[1:-1]).strip()

    try:
        return DraftAnalysis.model_validate_json(candidate)
    except (ValidationError, ValueError) as direct_error:
        decoder = json.JSONDecoder()
        for index, character in enumerate(candidate):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            try:
                return DraftAnalysis.model_validate(value)
            except ValidationError:
                continue
        raise direct_error


def parse_request(state: AgentState) -> dict[str, Any]:
    """Extract the fixed-format case identifier and requested intent."""
    # 案件编号和意图用确定性规则解析，不让模型自行选择任意案件或静默执行写操作。
    question = _message_text(state["messages"][-1]) if state["messages"] else ""
    match = _CASE_ID_PATTERN.search(question.upper())
    case_id = match.group(0) if match else None
    intent: Literal["ANALYZE", "RESOLVE"] = (
        "RESOLVE" if any(word in question for word in ("解决", "处理", "结案")) else "ANALYZE"
    )
    return {
        "case_id": case_id,
        "intent": intent,
        "tool_call_count": 0,
        "tool_call_history": [],
        "collected_facts": {},
        "citations": [],
        "evidence_status": (
            EvidenceStatus.UNKNOWN.value if case_id else EvidenceStatus.MISSING_CASE.value
        ),
        "budget_exhausted": False,
        "run_persisted": False,
        "public_error": None,
    }


def _route_after_parse(
    state: AgentState,
) -> Literal["collect_case_facts", "refuse_analysis", "finalize"]:
    if state.get("public_error"):
        return "finalize"
    return "collect_case_facts" if state.get("case_id") else "refuse_analysis"


def _route_after_model(
    state: AgentState,
) -> Literal["tools", "validate_evidence", "finalize"]:
    if state.get("public_error"):
        return "finalize"
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"
    return "validate_evidence"


def _route_after_tools(
    state: AgentState,
) -> Literal["collect_case_facts", "validate_evidence", "finalize"]:
    if state.get("public_error"):
        return "finalize"
    if state.get("budget_exhausted"):
        return "validate_evidence"
    return "collect_case_facts"


def _current_turn_messages(messages: Sequence[AnyMessage]) -> Sequence[AnyMessage]:
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return messages[index:]
    return messages


def _tool_calls_by_id(messages: Sequence[AnyMessage]) -> dict[str, dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                calls[str(call["id"])] = cast(dict[str, Any], call)
    return calls


def _decode_tool_message(message: ToolMessage) -> dict[str, Any] | None:
    try:
        value = json.loads(_message_text(message))
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _collect_tool_evidence(
    messages: Sequence[AnyMessage],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    facts: dict[str, Any] = {}
    citations: list[dict[str, Any]] = []
    errors: list[str] = []
    # ToolMessage 通过 tool_call_id 回连原调用，才能验证规则检索时实际使用的 channel/version 参数。
    calls = _tool_calls_by_id(messages)
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        value = _decode_tool_message(message)
        if value is None:
            continue
        if isinstance(value.get("error"), str):
            errors.append(value["error"])
            continue
        if message.name == "get_reconciliation_case":
            facts["case"] = value
        elif message.name == "find_payment_evidence":
            facts["payments"] = value
        elif message.name == "find_ledger_evidence":
            facts["ledger"] = value
        elif message.name == "get_audit_timeline":
            facts["audit"] = value
        elif message.name == "search_reconciliation_rules":
            call = calls.get(message.tool_call_id, {})
            args = call.get("args", {})
            facts["rule_search"] = {
                "channel": args.get("channel"),
                "rule_version": args.get("rule_version"),
            }
            raw_citations = value.get("citations", [])
            if isinstance(raw_citations, list):
                citations = [item for item in raw_citations if isinstance(item, dict)]
    return facts, citations, errors


def _has_required_structured_facts(
    case: dict[str, Any], facts: dict[str, Any]
) -> bool:
    # CHANNEL_ONLY 合法地没有内部支付；其他差异必须同时具备支付和账本证据。
    result_type = case.get("result_type")
    payments = facts.get("payments")
    ledger = facts.get("ledger")
    if not isinstance(payments, dict) or not isinstance(payments.get("payments"), list):
        return False
    if result_type == ResultType.CHANNEL_ONLY.value:
        return True
    if not payments["payments"]:
        return False
    return (
        isinstance(ledger, dict)
        and isinstance(ledger.get("entries"), list)
        and bool(ledger["entries"])
    )


def _structured_facts_are_consistent(
    case: dict[str, Any], facts: dict[str, Any]
) -> bool:
    """Check semantic relationships across trusted payment and ledger facts."""
    # 这层不依赖模型结论，而是验证 case_id、交易号、状态、金额和借贷平衡等跨工具关系。
    payments_value = facts.get("payments")
    if not isinstance(payments_value, dict):
        return False
    if payments_value.get("case_id") != case.get("case_id"):
        return False
    payments = payments_value.get("payments")
    if not isinstance(payments, list):
        return False

    if case.get("result_type") == ResultType.CHANNEL_ONLY.value:
        return not payments and not (
            isinstance(facts.get("ledger"), dict)
            and facts["ledger"].get("entries")
        )

    ledger_value = facts.get("ledger")
    if not isinstance(ledger_value, dict):
        return False
    if ledger_value.get("case_id") != case.get("case_id"):
        return False
    entries = ledger_value.get("entries")
    if not isinstance(entries, list) or len(payments) != 1 or not entries:
        return False

    payment = payments[0]
    if not isinstance(payment, dict):
        return False
    if payment.get("status") != "SUCCESS":
        return False
    if payment.get("channel_transaction_id") != case.get("channel_transaction_id"):
        return False
    payment_amount = payment.get("amount_cents")
    if not isinstance(payment_amount, int) or payment_amount <= 0:
        return False

    # 每条分录必须引用同一 payment_id，且借方合计 = 贷方合计 = 支付金额。
    debit_total = 0
    credit_total = 0
    for entry in entries:
        if not isinstance(entry, dict):
            return False
        if entry.get("business_reference") != payment.get("payment_id"):
            return False
        amount = entry.get("amount_cents")
        if not isinstance(amount, int) or amount <= 0:
            return False
        direction = entry.get("direction")
        if direction == "DEBIT":
            debit_total += amount
        elif direction == "CREDIT":
            credit_total += amount
        else:
            return False

    return debit_total == credit_total == payment_amount


def validate_evidence(state: AgentState) -> dict[str, Any]:
    """Accept only matching structured facts and exact-version rule citations."""
    # 安全顺序：先检查依赖和结构化证据，再检查语义一致性，最后检查规则版本锁定。
    facts, citations, errors = _collect_tool_evidence(
        _current_turn_messages(state["messages"])
    )
    case = facts.get("case")
    if "DEPENDENCY_UNAVAILABLE" in errors:
        return {
            "collected_facts": facts,
            "citations": [],
            "evidence_status": EvidenceStatus.UNKNOWN.value,
            "public_error": {
                "code": "DATABASE_UNAVAILABLE",
                "message": ERROR_MESSAGES["DATABASE_UNAVAILABLE"],
            },
        }
    if state.get("budget_exhausted"):
        status: EvidenceStatusValue = EvidenceStatus.TOOL_BUDGET_EXHAUSTED.value
    elif not isinstance(case, dict):
        status = EvidenceStatus.MISSING_CASE.value
    elif not _has_required_structured_facts(case, facts):
        status = EvidenceStatus.MISSING_STRUCTURED_FACTS.value
    elif not _structured_facts_are_consistent(case, facts):
        status = EvidenceStatus.INCONSISTENT_STRUCTURED_FACTS.value
    else:
        rule_search = facts.get("rule_search")
        # RAG 命中“相似规则”仍不够，调用参数和每条 citation 都必须等于案件锁定版本。
        matching_rule = (
            isinstance(rule_search, dict)
            and rule_search.get("channel") == case.get("channel")
            and rule_search.get("rule_version") == case.get("rule_version")
            and bool(citations)
            and all(
                citation.get("rule_version") == case.get("rule_version")
                for citation in citations
            )
        )
        status = (
            EvidenceStatus.COMPLETE.value
            if matching_rule
            else EvidenceStatus.MISSING_RULE.value
        )
    return {
        "collected_facts": facts,
        "citations": citations if status == EvidenceStatus.COMPLETE else [],
        "evidence_status": status,
    }


def route_after_validation(
    state: dict[str, Any],
) -> Literal["draft_analysis", "refuse_analysis", "finalize"]:
    """Route complete evidence to drafting and every gap to deterministic refusal."""
    if state.get("public_error"):
        return "finalize"
    if state.get("evidence_status") == EvidenceStatus.COMPLETE:
        return "draft_analysis"
    return "refuse_analysis"


def draft_analysis(state: AgentState) -> dict[str, Any]:
    """Validate the model draft without accepting model-supplied citations."""
    # 模型只负责自然语言总结和建议；引用始终来自工具消息，模型无法伪造证据来源。
    last_message = state["messages"][-1]
    try:
        draft = _parse_draft_analysis(_message_text(last_message))
    except (ValidationError, ValueError):
        return {
            "draft_analysis": None,
            "proposed_resolution": None,
            "evidence_status": EvidenceStatus.INVALID_MODEL_OUTPUT.value,
        }
    return {
        "draft_analysis": draft.model_dump(mode="json"),
        "proposed_resolution": draft.resolution_recommendation.model_dump(mode="json"),
    }


def refuse_analysis(state: AgentState) -> dict[str, Any]:
    """Clear any proposed write when evidence is incomplete."""
    del state
    return {"draft_analysis": None, "proposed_resolution": None}


def _route_after_draft(
    state: AgentState,
) -> Literal["prepare_confirmation", "finalize"]:
    if (
        state.get("evidence_status") == EvidenceStatus.COMPLETE
        and state.get("intent") == "RESOLVE"
        and state.get("proposed_resolution")
    ):
        return "prepare_confirmation"
    return "finalize"


def _confirmation_decision(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("confirmation decision must be an object")
    decisions = value.get("decisions")
    if not isinstance(decisions, list) or len(decisions) != 1:
        raise ValueError("exactly one confirmation decision is required")
    decision = decisions[0]
    if not isinstance(decision, dict) or decision.get("type") not in {
        "approve",
        "reject",
    }:
        raise ValueError("confirmation decision must approve or reject")
    return decision


def _route_after_confirmation(
    state: AgentState,
) -> Literal["execute_resolution", "finalize"]:
    if state.get("public_error"):
        return "finalize"
    decision = state.get("confirmation_decision") or {}
    return "execute_resolution" if decision.get("type") == "approve" else "finalize"


def _route_after_confirmation_preparation(
    state: AgentState,
) -> Literal["await_confirmation", "finalize"]:
    return "finalize" if state.get("public_error") else "await_confirmation"


def finalize(state: AgentState) -> dict[str, Any]:
    """Build the stable public response from validated state only."""
    # 最终响应由状态机拼装，确保缺证据时即使模型声称 READY 也只能返回拒答。
    draft = state.get("draft_analysis")
    resolution_result = state.get("resolution_result")
    decision = state.get("confirmation_decision") or {}
    failure = state.get("public_error")
    response: dict[str, Any]
    if isinstance(failure, dict):
        response = {
            "status": "FAILED",
            "case_id": state.get("case_id"),
            "error": failure,
        }
    elif isinstance(resolution_result, dict):
        response = {
            "status": "RESOLVED",
            "case_id": state.get("case_id"),
            "summary": "案件已在人工确认后完成处理。",
            "resolution": resolution_result,
            "citations": state.get("citations", []),
        }
    elif decision.get("type") == "reject":
        response = {
            "status": "REJECTED",
            "case_id": state.get("case_id"),
            "summary": "用户已拒绝案件处理建议，案件保持未处理状态。",
            "citations": state.get("citations", []),
            "resolution_recommendation": state.get("proposed_resolution"),
        }
    elif state.get("evidence_status") == EvidenceStatus.COMPLETE and isinstance(draft, dict):
        response = {
            "status": "READY",
            "case_id": state.get("case_id"),
            "summary": draft["summary"],
            "citations": state.get("citations", []),
            "resolution_recommendation": state.get("proposed_resolution"),
        }
    elif state.get("case_id") is None:
        response = {
            "status": "INVALID_REQUEST",
            "case_id": None,
            "summary": "请求中缺少有效案件编号，格式应为 CASE-四位数字。",
            "citations": [],
            "resolution_recommendation": None,
        }
    else:
        evidence_status = state.get("evidence_status", EvidenceStatus.UNKNOWN.value)
        error_code = {
            EvidenceStatus.MISSING_RULE.value: "RULE_VERSION_NOT_FOUND",
            EvidenceStatus.INCONSISTENT_STRUCTURED_FACTS.value: "STRUCTURED_FACTS_INCONSISTENT",
            EvidenceStatus.TOOL_BUDGET_EXHAUSTED.value: "TOOL_BUDGET_EXHAUSTED",
        }.get(evidence_status)
        response = {
            "status": "INSUFFICIENT_EVIDENCE",
            "case_id": state.get("case_id"),
            "summary": (
                ERROR_MESSAGES[error_code]
                if error_code
                else "证据不完整，无法生成可靠的对账结论。"
            ),
            "evidence_status": evidence_status,
            "citations": [],
            "resolution_recommendation": None,
        }
    return {"final_response": response, "confirmation_request": None}


def build_graph(
    *,
    model: BaseChatModel | None = None,
    tools: Sequence[BaseTool] | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    max_tool_calls: int = 8,
    resolution_action_provider: ResolutionActionProvider | None = None,
    run_service_provider: RunServiceProvider | None = None,
    confirmed_by: str = "demo-admin",
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile a graph with injectable model, tools and test checkpointer."""
    # 所有外部依赖均可注入，使单元测试能够替换模型、工具、checkpoint 和写操作。
    tool_list = list(tools or [*READ_TOOLS, search_reconciliation_rules])
    tool_node = ToolNode(tool_list)
    resolved_model = model
    model_max_retries = 2
    action_provider = (
        resolution_action_provider or production_resolution_action_provider
    )
    runs_provider = run_service_provider or production_run_service_provider

    def fail_run(state: AgentState, error: BaseException) -> dict[str, Any]:
        # 原始异常可能含连接串或供应商细节，只持久化脱敏后的公共错误。
        safe_error = public_error(error)
        run_id = state.get("run_id")
        if state.get("run_persisted") and isinstance(run_id, str):
            failed_response = {
                "status": "FAILED",
                "case_id": state.get("case_id"),
                "error": safe_error.model_dump(),
            }
            try:
                with runs_provider() as service:
                    service.finish(
                        run_id,
                        status=RunStatus.FAILED,
                        citations=[],
                        final_response=failed_response,
                        error=safe_error,
                    )
            except Exception:
                pass
        return {"public_error": safe_error.model_dump()}

    def start_run(state: AgentState) -> dict[str, Any]:
        # 在第一次模型调用前落运行记录，模型或数据库中途失败也能留下可审计轨迹。
        current_messages = _current_turn_messages(state["messages"])
        question = _message_text(current_messages[0]) if current_messages else ""
        try:
            with runs_provider() as service:
                run_id = service.start(question)
        except Exception as error:
            return {
                "run_id": f"UNPERSISTED-{uuid4()}",
                "run_persisted": False,
                "public_error": public_error(error).model_dump(),
            }
        return {"run_id": run_id, "run_persisted": True}

    async def collect_case_facts(state: AgentState) -> dict[str, Any]:
        nonlocal model_max_retries, resolved_model
        try:
            if resolved_model is None:
                settings = get_settings()
                resolved_model = build_chat_model(settings)
                model_max_retries = settings.model_max_retries
            # bind_tools 让模型只能从显式白名单选择工具，不能直接访问数据库或任意代码。
            bound_model = resolved_model.bind_tools(tool_list)
            prompt = SYSTEM_PROMPT.format(
                case_id=state.get("case_id") or "UNKNOWN",
                max_tool_calls=max_tool_calls,
            )
            response = await invoke_model_with_retry(
                bound_model,
                [
                    SystemMessage(content=prompt),
                    *_current_turn_messages(state["messages"]),
                ],
                max_retries=model_max_retries,
            )
        except Exception as error:
            return fail_run(state, error)
        return {"messages": [response]}

    async def execute_tools(
        state: AgentState, config: RunnableConfig
    ) -> dict[str, Any]:
        try:
            last_message = state["messages"][-1]
            if not isinstance(last_message, AIMessage):
                return {}
            # 服务端硬截断工具调用，不能只依赖 prompt 中“最多调用 N 次”的软约束。
            remaining = max(0, max_tool_calls - state.get("tool_call_count", 0))
            allowed = last_message.tool_calls[:remaining]
            overflow = last_message.tool_calls[remaining:]
            messages: list[ToolMessage] = []
            if allowed:
                bounded_request = AIMessage(content="", tool_calls=allowed)
                tool_result = await tool_node.ainvoke(
                    {"messages": [bounded_request]}, config=config
                )
                messages.extend(cast(list[ToolMessage], tool_result["messages"]))
            run_id = state.get("run_id")
            if allowed and isinstance(run_id, str) and state.get("run_persisted"):
                with runs_provider() as service:
                    service.record_tools(
                        run_id,
                        summarize_tool_messages(messages[: len(allowed)]),
                    )
        except Exception as error:
            return fail_run(state, error)
        messages.extend(
            ToolMessage(
                content=json.dumps(
                    {
                        "error": "TOOL_BUDGET_EXHAUSTED",
                        "message": f"工具调用次数不能超过 {max_tool_calls} 次",
                    },
                    ensure_ascii=False,
                ),
                name=str(call["name"]),
                tool_call_id=str(call["id"]),
                status="error",
            )
            for call in overflow
        )
        return {
            "messages": messages,
            "tool_call_count": state.get("tool_call_count", 0) + len(allowed),
            "tool_call_history": [
                *state.get("tool_call_history", []),
                *(str(call["name"]) for call in allowed),
            ],
            "budget_exhausted": bool(overflow),
        }

    def prepare_confirmation(state: AgentState) -> dict[str, Any]:
        proposed = state.get("proposed_resolution")
        case_id = state.get("case_id")
        run_id = state.get("run_id")
        try:
            if (
                not isinstance(proposed, dict)
                or not isinstance(case_id, str)
                or not isinstance(run_id, str)
            ):
                raise ValueError("resolution proposal is required for confirmation")
            # 令牌与完整 proposal 绑定，确认后修改案件、处理码或备注都会使令牌失效。
            proposal = ResolutionProposal(
                run_id=run_id,
                case_id=case_id,
                **proposed,
            )
            with action_provider() as (confirmation_service, _repository):
                confirmation_token = confirmation_service.issue(proposal)
            if state.get("run_persisted"):
                with runs_provider() as service:
                    service.mark_waiting(run_id)
        except Exception as error:
            return fail_run(state, error)
        action_args = {
            **proposal.model_dump(mode="json"),
            "confirmation_token": confirmation_token,
        }
        return {
            "confirmation_request": {
                "action_requests": [
                    {"name": "resolve_case", "args": action_args}
                ],
                "review_configs": [
                    {
                        "action_name": "resolve_case",
                        "allowed_decisions": ["approve", "reject"],
                    }
                ],
            }
        }

    def await_confirmation(state: AgentState) -> dict[str, Any]:
        request = state.get("confirmation_request")
        if not isinstance(request, dict):
            return fail_run(state, ValueError("confirmation request is missing"))
        # interrupt 会把图暂停并写入 checkpoint，等待前端 approve/reject 后从原位置恢复。
        decision_value = interrupt(request)
        try:
            decision = _confirmation_decision(decision_value)
        except Exception as error:
            return fail_run(state, error)
        return {"confirmation_decision": decision}

    def execute_resolution(state: AgentState) -> dict[str, Any]:
        try:
            request = state.get("confirmation_request")
            decision = state.get("confirmation_decision") or {}
            if not isinstance(request, dict) or decision.get("type") != "approve":
                raise ValueError("approved confirmation is required")
            action_requests = request.get("action_requests")
            if not isinstance(action_requests, list) or len(action_requests) != 1:
                raise ValueError("one resolution action is required")
            original_args = action_requests[0].get("args")
            if not isinstance(original_args, dict):
                raise ValueError("resolution arguments are missing")
            approved_args = dict(original_args)
            edited_args = decision.get("args")
            if isinstance(edited_args, dict):
                approved_args.update(edited_args)
            token = approved_args.pop("confirmation_token", None)
            if not isinstance(token, str):
                raise ValueError("confirmation token is missing")
            proposal = ResolutionProposal.model_validate(approved_args)
            # 真正写库前再次校验一次性令牌；UI 按钮本身不是权限边界。
            result = execute_confirmed_resolution(
                action_provider,
                confirmation_token=token,
                proposal=proposal,
                confirmed_by=confirmed_by,
            )
        except Exception as error:
            return {**fail_run(state, error), "confirmation_request": None}
        return {
            "resolution_result": result.model_dump(mode="json"),
            "confirmation_request": None,
        }

    def persist_finalize(state: AgentState) -> dict[str, Any]:
        updates = finalize(state)
        response = cast(dict[str, Any], updates["final_response"])
        run_id = state.get("run_id")
        if not state.get("run_persisted") or not isinstance(run_id, str):
            return updates
        if response["status"] in {"READY", "RESOLVED"}:
            status = RunStatus.COMPLETED
        elif response["status"] == "FAILED":
            status = RunStatus.FAILED
        else:
            status = RunStatus.REJECTED
        safe_error = None
        if status is RunStatus.FAILED and isinstance(state.get("public_error"), dict):
            safe_error = PublicError.model_validate(state["public_error"])
        try:
            with runs_provider() as service:
                service.finish(
                    run_id,
                    status=status,
                    citations=state.get("citations", []),
                    final_response=response,
                    error=safe_error,
                )
        except Exception as error:
            failed_state = cast(AgentState, dict(state))
            failure_update = fail_run(state, error)
            failed_state["public_error"] = cast(
                dict[str, str], failure_update["public_error"]
            )
            return finalize(failed_state)
        return updates

    # 图的主干：解析 -> 调模型/工具循环 -> 确定性校验 -> 可选人工确认 -> 最终响应。
    builder = StateGraph(
        AgentState,
        input_schema=InputState,
        context_schema=AgentContext,
    )
    builder.add_node("parse_request", parse_request)
    builder.add_node("start_run", start_run)
    builder.add_node("collect_case_facts", collect_case_facts)
    builder.add_node("tools", execute_tools)
    builder.add_node("validate_evidence", validate_evidence)
    builder.add_node("draft_analysis", draft_analysis)
    builder.add_node("refuse_analysis", refuse_analysis)
    builder.add_node("prepare_confirmation", prepare_confirmation)
    builder.add_node("await_confirmation", await_confirmation)
    builder.add_node("execute_resolution", execute_resolution)
    builder.add_node("finalize", persist_finalize)
    builder.add_edge(START, "parse_request")
    builder.add_edge("parse_request", "start_run")
    builder.add_conditional_edges("start_run", _route_after_parse)
    builder.add_conditional_edges("collect_case_facts", _route_after_model)
    builder.add_conditional_edges("tools", _route_after_tools)
    builder.add_conditional_edges("validate_evidence", route_after_validation)
    builder.add_conditional_edges("draft_analysis", _route_after_draft)
    builder.add_edge("refuse_analysis", "finalize")
    builder.add_conditional_edges(
        "prepare_confirmation", _route_after_confirmation_preparation
    )
    builder.add_conditional_edges("await_confirmation", _route_after_confirmation)
    builder.add_edge("execute_resolution", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer, name="Reconciliation Agent")


# LangGraph API injects its own persistent checkpointer into this exported graph.
graph = build_graph()
