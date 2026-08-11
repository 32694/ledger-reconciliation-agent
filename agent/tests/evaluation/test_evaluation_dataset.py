from reconciliation_agent.evaluation import load_cases


def test_evaluation_dataset_has_required_coverage() -> None:
    cases = load_cases("evals/cases.json")

    assert len(cases) == 24
    assert {case.category for case in cases} == {
        "tool_selection",
        "tool_arguments",
        "rule_retrieval",
        "citation",
        "refusal",
        "confirmation",
    }
    assert all(case.expected_case_id.startswith("CASE-") for case in cases)
    assert all(
        sum(item.category == category for item in cases) == 4
        for category in {case.category for case in cases}
    )
