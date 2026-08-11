import json
from pathlib import Path

from reconciliation_agent.evaluation import (
    EvaluationObservation,
    evaluate,
    load_cases,
    main,
    run_scripted,
)


def test_scripted_evaluation_executes_all_cases_with_exact_metrics() -> None:
    cases = load_cases("evals/cases.json")

    report = run_scripted(cases)

    assert report.case_count == 24
    assert report.failures == []
    assert report.metrics.model_dump() == {
        "tool_selection_accuracy": 1.0,
        "argument_accuracy": 1.0,
        "retrieval_recall_at_k": 1.0,
        "citation_completeness": 1.0,
        "outcome_consistency": 1.0,
        "refusal_accuracy": 1.0,
    }


def test_metric_failure_keeps_case_level_details() -> None:
    case = load_cases("evals/cases.json")[0]
    wrong = EvaluationObservation(
        case_id=case.id,
        selected_case_id="CASE-9999",
        tool_names=[],
        retrieved_rule_versions=[],
        citation_rule_versions=[],
        outcome="INSUFFICIENT_EVIDENCE",
        resolution_count_before=0,
        resolution_count_after=0,
    )

    report = evaluate([case], [wrong])

    assert report.metrics.outcome_consistency == 0.0
    assert report.failures[0].case_id == case.id
    assert "outcome_consistency" in report.failures[0].failed_metrics


def test_confirmation_metric_requires_no_write_before_approval() -> None:
    case = next(
        item
        for item in load_cases("evals/cases.json")
        if item.expected_outcome == "CONFIRMATION_REQUIRED"
    )
    unsafe = EvaluationObservation(
        case_id=case.id,
        selected_case_id=case.expected_case_id,
        tool_names=case.expected_tools,
        retrieved_rule_versions=[case.expected_rule_version]
        if case.expected_rule_version
        else [],
        citation_rule_versions=[case.expected_rule_version]
        if case.expected_rule_version
        else [],
        outcome="CONFIRMATION_REQUIRED",
        resolution_count_before=1,
        resolution_count_after=1,
    )

    report = evaluate([case], [unsafe])

    assert report.metrics.outcome_consistency == 0.0
    assert report.failures[0].failed_metrics == ["outcome_consistency"]


def test_scripted_cli_writes_machine_readable_report(tmp_path: Path) -> None:
    output = tmp_path / "evaluation.json"

    exit_code = main(
        [
            "--mode",
            "scripted",
            "--dataset",
            "evals/cases.json",
            "--output",
            str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["mode"] == "scripted"
    assert payload["case_count"] == 24
    assert payload["failures"] == []
