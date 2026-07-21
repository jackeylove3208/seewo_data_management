from uuid import UUID, uuid5

import pytest
from pydantic import ValidationError

from app.governance.risk_policy import RiskAssessment, assess_operation
from app.schemas.executions import OperationType
from app.schemas.governance import RiskLevel


@pytest.mark.parametrize("operation_type", [OperationType.MOVE, OperationType.DISABLE])
def test_move_and_disable_are_high_risk(operation_type: OperationType) -> None:
    assessment = assess_operation(
        operation_type=operation_type,
        before={"status": "active"},
        after={"status": "disabled"},
        changed_fields=frozenset({"status"}),
        has_dependents=False,
    )

    assert assessment.risk is RiskLevel.HIGH
    assert assessment.requires_explicit_acknowledgement is True
    assert assessment.reversible is True


def test_operation_with_selected_dependents_is_high_risk() -> None:
    assessment = assess_operation(
        operation_type=OperationType.UPDATE,
        before={"name": "Before"},
        after={"name": "After"},
        changed_fields=frozenset({"name"}),
        has_dependents=True,
    )

    assert assessment.risk is RiskLevel.HIGH
    assert assessment.requires_explicit_acknowledgement is True


@pytest.mark.parametrize("operation_type", [OperationType.CREATE, OperationType.UPDATE])
def test_create_and_update_are_medium_risk_without_escalation(
    operation_type: OperationType,
) -> None:
    before = None if operation_type is OperationType.CREATE else {"name": "Before"}

    assessment = assess_operation(
        operation_type=operation_type,
        before=before,
        after={"name": "After"},
        changed_fields=frozenset({"name"}),
        has_dependents=False,
    )

    assert assessment.risk is RiskLevel.MEDIUM
    assert assessment.requires_explicit_acknowledgement is False


def test_skip_is_low_risk_non_reversible_without_escalation() -> None:
    assessment = assess_operation(
        operation_type=OperationType.SKIP,
        before={"name": "Unchanged"},
        after={"name": "Unchanged"},
        changed_fields=frozenset(),
        has_dependents=False,
    )

    assert assessment == RiskAssessment(
        risk=RiskLevel.LOW,
        requires_explicit_acknowledgement=False,
        reversible=False,
    )


@pytest.mark.parametrize(
    "operation_type",
    [OperationType.UPDATE, OperationType.MOVE, OperationType.DISABLE],
)
def test_target_mutation_is_not_reversible_without_before_fact_for_every_change(
    operation_type: OperationType,
) -> None:
    assessment = assess_operation(
        operation_type=operation_type,
        before={},
        after={"status": "disabled"},
        changed_fields=frozenset({"status"}),
        has_dependents=False,
    )

    assert assessment.reversible is False


def test_removing_a_field_is_reversible_when_its_before_value_is_stored() -> None:
    assessment = assess_operation(
        operation_type=OperationType.UPDATE,
        before={"email": "before@example.test"},
        after={},
        changed_fields=frozenset({"email"}),
        has_dependents=False,
    )

    assert assessment.reversible is True


def test_assessment_rejects_changed_fields_that_omit_an_actual_change() -> None:
    with pytest.raises(ValueError, match="changed_fields"):
        assess_operation(
            operation_type=OperationType.UPDATE,
            before={"name": "Before"},
            after={"name": "After", "email": "new@example.test"},
            changed_fields=frozenset({"name"}),
            has_dependents=False,
        )


def test_create_is_reversible_when_created_state_is_stored() -> None:
    assessment = assess_operation(
        operation_type=OperationType.CREATE,
        before=None,
        after={"name": "Created"},
        changed_fields=frozenset({"name"}),
        has_dependents=False,
    )

    assert assessment.reversible is True


def test_skip_with_a_selected_dependent_is_escalated_to_high_risk() -> None:
    assessment = assess_operation(
        operation_type=OperationType.SKIP,
        before={"name": "Unchanged"},
        after={"name": "Unchanged"},
        changed_fields=frozenset(),
        has_dependents=True,
    )

    assert assessment.risk is RiskLevel.HIGH
    assert assessment.requires_explicit_acknowledgement is True
    assert assessment.reversible is False


def test_risk_assessment_is_strict_frozen_and_consistent() -> None:
    assessment = RiskAssessment(
        risk=RiskLevel.HIGH,
        requires_explicit_acknowledgement=True,
        reversible=True,
    )

    with pytest.raises(ValidationError):
        RiskAssessment(
            risk="high",
            requires_explicit_acknowledgement=True,
            reversible=True,
        )
    with pytest.raises(ValidationError):
        RiskAssessment(
            risk=RiskLevel.HIGH,
            requires_explicit_acknowledgement=False,
            reversible=True,
        )
    with pytest.raises(ValidationError):
        RiskAssessment(
            risk=RiskLevel.HIGH,
            requires_explicit_acknowledgement=True,
            reversible=True,
            operation_id=uuid5(UUID(int=0), "unexpected"),
        )
    with pytest.raises(ValidationError):
        assessment.reversible = False
