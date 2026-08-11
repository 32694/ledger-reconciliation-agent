import pytest
from pydantic import ValidationError

from reconciliation_agent.domain.models import ResolutionCode
from reconciliation_agent.domain.schemas import ResolveCaseCommand


def test_resolve_case_command_strips_whitespace() -> None:
    command = ResolveCaseCommand(
        case_id=" CASE-123 ",
        resolution_code=ResolutionCode.OTHER,
        note=" reviewed ",
        confirmed_by=" operator ",
        agent_run_id=" run-123 ",
    )

    assert command.case_id == "CASE-123"
    assert command.note == "reviewed"
    assert command.confirmed_by == "operator"
    assert command.agent_run_id == "run-123"


def test_resolve_case_command_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ResolveCaseCommand(
            case_id="CASE-123",
            resolution_code=ResolutionCode.OTHER,
            note="reviewed",
            confirmed_by="operator",
            unexpected="value",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("case_id", ""),
        ("case_id", "case-123"),
        ("case_id", "CASE-" + "A" * 60),
        ("note", " "),
        ("note", "n" * 2001),
        ("confirmed_by", " "),
        ("confirmed_by", "u" * 129),
        ("agent_run_id", ""),
        ("agent_run_id", "r" * 65),
    ],
)
def test_resolve_case_command_rejects_invalid_fields(field: str, value: str) -> None:
    data = {
        "case_id": "CASE-123",
        "resolution_code": ResolutionCode.OTHER,
        "note": "reviewed",
        "confirmed_by": "operator",
        "agent_run_id": "run-123",
    }
    data[field] = value

    with pytest.raises(ValidationError):
        ResolveCaseCommand.model_validate(data)
