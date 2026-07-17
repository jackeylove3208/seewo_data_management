from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid5

import pytest
from pydantic import ValidationError

from app.governance.operation_policy import (
    PlanPolicyError,
    allowed_operations,
    validate_editable_fields,
)
from app.governance.plan_builder import (
    GovernancePlanBuilder,
    PlanCompilationError,
    PlanConflictError,
)
from app.schemas.canonical_entities import EntityType
from app.schemas.differences import DifferenceType
from app.schemas.executions import (
    GovernancePlan,
    OperationType,
    ProposalSource,
    ProposalStatus,
    ProposalVersionRef,
    ReviewedProposalSnapshot,
)
from app.schemas.governance import RiskLevel

TASK_ID = uuid5(UUID(int=0), "task")
SOURCE_SNAPSHOT_ID = uuid5(UUID(int=0), "source-snapshot")
TARGET_SNAPSHOT_ID = uuid5(UUID(int=0), "target-snapshot")
TARGET_VERSION = "target-v3"


def reviewed_proposal(
    name: str = "proposal-a",
    **updates: object,
) -> ReviewedProposalSnapshot:
    payload: dict[str, object] = {
        "proposal": ProposalVersionRef(
            proposal_id=uuid5(UUID(int=0), name),
            proposal_version=3,
        ),
        "current_proposal_version": 3,
        "status": ProposalStatus.PENDING_EXECUTION,
        "task_id": TASK_ID,
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "target_snapshot_id": TARGET_SNAPSHOT_ID,
        "target_version": TARGET_VERSION,
        "proposal_source": ProposalSource.AI,
        "difference_id": uuid5(UUID(int=0), f"difference-{name}"),
        "difference_version": 2,
        "current_difference_version": 2,
        "analysis_version": "analysis-v1",
        "current_analysis_version": "analysis-v1",
        "difference_type": DifferenceType.ATTRIBUTE_CONFLICT,
        "operation_type": OperationType.UPDATE,
        "entity_type": EntityType.TEACHER,
        "target_entity_id": uuid5(UUID(int=0), f"target-{name}"),
        "target_source_identifier": f"teacher-{name}",
        "before": {"name": "Existing teacher"},
        "after": {"name": "Corrected teacher"},
        "changed_fields": frozenset({"name"}),
        "dependencies": frozenset(),
        "reversible": True,
        "risk": RiskLevel.MEDIUM,
    }
    payload.update(updates)
    return ReviewedProposalSnapshot(**payload)


def build(
    *proposals: ReviewedProposalSnapshot,
    **context_updates: object,
) -> GovernancePlan:
    context: dict[str, object] = {
        "task_id": TASK_ID,
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "target_snapshot_id": TARGET_SNAPSHOT_ID,
        "target_version": TARGET_VERSION,
    }
    context.update(context_updates)
    return GovernancePlanBuilder().build(proposals=proposals, **context)


def test_reviewed_proposal_snapshot_is_strict_frozen_and_deeply_immutable() -> None:
    proposal = reviewed_proposal()

    with pytest.raises(ValidationError):
        reviewed_proposal(task_id=str(TASK_ID))
    with pytest.raises(ValidationError):
        ReviewedProposalSnapshot.model_validate(
            {**proposal.model_dump(), "unexpected": True}
        )
    with pytest.raises(ValidationError):
        proposal.current_proposal_version = 4
    assert proposal.before is not None
    with pytest.raises(TypeError):
        proposal.before["name"] = "Mutated"


def test_governance_plan_is_strict_frozen_and_preserves_exact_refs() -> None:
    proposal = reviewed_proposal()

    plan = build(proposal)

    assert plan.task_id == TASK_ID
    assert plan.source_snapshot_id == SOURCE_SNAPSHOT_ID
    assert plan.target_snapshot_id == TARGET_SNAPSHOT_ID
    assert plan.target_version == TARGET_VERSION
    assert plan.proposals == (proposal.proposal,)
    assert len(plan.content_hash) == 64
    with pytest.raises(ValidationError):
        plan.version = 2
    with pytest.raises(ValidationError):
        GovernancePlan.model_validate({**plan.model_dump(), "unexpected": True})


@pytest.mark.parametrize(
    ("difference_type", "operation_type", "updates"),
    [
        (
            DifferenceType.SEEWO_MISSING,
            OperationType.CREATE,
            {
                "target_entity_id": None,
                "target_source_identifier": None,
                "before": None,
                "after": {"name": "New teacher"},
                "changed_fields": frozenset({"name"}),
            },
        ),
        (
            DifferenceType.SEEWO_REDUNDANT,
            OperationType.DISABLE,
            {
                "before": {"status": "active"},
                "after": {"status": "disabled"},
                "changed_fields": frozenset({"status"}),
            },
        ),
        (DifferenceType.ATTRIBUTE_CONFLICT, OperationType.UPDATE, {}),
        (
            DifferenceType.STRUCTURE_CONFLICT,
            OperationType.MOVE,
            {
                "before": {"department_source_id": "department-a"},
                "after": {"department_source_id": "department-b"},
                "changed_fields": frozenset({"department_source_id"}),
            },
        ),
        (
            DifferenceType.DUPLICATE_CONFLICT,
            OperationType.DISABLE,
            {
                "before": {"status": "active"},
                "after": {"status": "disabled"},
                "changed_fields": frozenset({"status"}),
            },
        ),
    ],
)
def test_compiles_each_allowed_difference_operation(
    difference_type: DifferenceType,
    operation_type: OperationType,
    updates: Mapping[str, Any],
) -> None:
    proposal = reviewed_proposal(
        difference_type=difference_type,
        operation_type=operation_type,
        **updates,
    )

    operation = build(proposal).operations[0]

    assert operation.operation_type is operation_type
    assert operation.difference_id == proposal.difference_id
    assert operation.before == proposal.before
    assert operation.after == proposal.after


@pytest.mark.parametrize("source", [ProposalSource.AI, ProposalSource.OPERATOR])
def test_ai_and_operator_proposals_follow_the_same_policy_path(
    source: ProposalSource,
) -> None:
    proposal = reviewed_proposal(proposal_source=source)

    operation = build(proposal).operations[0]

    assert operation.proposal_source is source


def test_skip_is_allowed_for_every_difference_type() -> None:
    for difference_type in DifferenceType:
        proposal = reviewed_proposal(
            name=f"skip-{difference_type.value}",
            difference_type=difference_type,
            operation_type=OperationType.SKIP,
            after={"name": "Existing teacher"},
            changed_fields=frozenset(),
            reversible=False,
            risk=RiskLevel.LOW,
        )

        assert build(proposal).operations[0].operation_type is OperationType.SKIP


def test_operation_policy_matches_the_governance_contract() -> None:
    assert allowed_operations(DifferenceType.SEEWO_MISSING) == frozenset(
        {OperationType.CREATE, OperationType.SKIP}
    )
    assert allowed_operations(DifferenceType.SEEWO_REDUNDANT) == frozenset(
        {OperationType.DISABLE, OperationType.SKIP}
    )
    assert allowed_operations(DifferenceType.ATTRIBUTE_CONFLICT) == frozenset(
        {OperationType.UPDATE, OperationType.SKIP}
    )
    assert allowed_operations(DifferenceType.STRUCTURE_CONFLICT) == frozenset(
        {OperationType.MOVE, OperationType.SKIP}
    )
    assert allowed_operations(DifferenceType.DUPLICATE_CONFLICT) == frozenset(
        {OperationType.DISABLE, OperationType.SKIP}
    )


@pytest.mark.parametrize("field", ["tenant_id", "raw_payload", "internal_admin"])
def test_protected_and_unknown_fields_are_rejected(field: str) -> None:
    proposal = reviewed_proposal(
        after={field: "changed"},
        changed_fields=frozenset({field}),
    )

    with pytest.raises(PlanPolicyError, match=field):
        build(proposal)


def test_entity_editable_fields_are_checked_by_entity_type() -> None:
    validate_editable_fields(EntityType.TEACHER, frozenset({"department_source_id"}))

    with pytest.raises(PlanPolicyError, match="department_source_id"):
        validate_editable_fields(
            EntityType.ORGANIZATION_UNIT,
            frozenset({"department_source_id"}),
        )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"current_proposal_version": 4}, "proposal version"),
        ({"status": ProposalStatus.SUPERSEDED}, "pending_execution"),
        ({"status": ProposalStatus.EXECUTED}, "pending_execution"),
        ({"current_difference_version": 3}, "difference version"),
        ({"current_analysis_version": "analysis-v2"}, "analysis version"),
        ({"task_id": uuid5(UUID(int=0), "other-task")}, "task"),
        ({"source_snapshot_id": uuid5(UUID(int=0), "other-source")}, "source snapshot"),
        ({"target_snapshot_id": uuid5(UUID(int=0), "other-target")}, "target snapshot"),
        ({"target_version": "target-v4"}, "target version"),
    ],
)
def test_stale_or_cross_context_proposals_are_rejected(
    updates: Mapping[str, object],
    message: str,
) -> None:
    proposal = reviewed_proposal(**updates)

    with pytest.raises(PlanCompilationError, match=message):
        build(proposal)


def test_duplicate_proposal_selection_is_rejected() -> None:
    proposal = reviewed_proposal()

    with pytest.raises(PlanCompilationError, match="duplicate proposal"):
        build(proposal, proposal)


def test_empty_selection_is_rejected() -> None:
    with pytest.raises(PlanCompilationError, match="at least one"):
        build()


def test_disallowed_operation_is_rejected() -> None:
    proposal = reviewed_proposal(
        difference_type=DifferenceType.SEEWO_MISSING,
        operation_type=OperationType.UPDATE,
    )

    with pytest.raises(PlanPolicyError, match="update"):
        build(proposal)


def test_conflicting_operations_on_the_same_target_field_are_rejected() -> None:
    target_id = uuid5(UUID(int=0), "shared-target")
    first = reviewed_proposal(name="first", target_entity_id=target_id)
    second = reviewed_proposal(name="second", target_entity_id=target_id)

    with pytest.raises(PlanConflictError, match="name"):
        build(first, second)


def test_conflict_detection_uses_all_available_target_identifiers() -> None:
    shared_source_identifier = "teacher-shared"
    first = reviewed_proposal(
        name="first",
        target_source_identifier=shared_source_identifier,
    )
    second = reviewed_proposal(
        name="second",
        target_entity_id=None,
        target_source_identifier=shared_source_identifier,
    )

    with pytest.raises(PlanConflictError, match="name"):
        build(first, second)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ({"email": None}, {}),
        ({}, {"email": None}),
    ],
)
def test_missing_and_explicit_null_are_different_fact_values(
    before: dict[str, object],
    after: dict[str, object],
) -> None:
    proposal = reviewed_proposal(
        before=before,
        after=after,
        changed_fields=frozenset(),
    )

    with pytest.raises(PlanPolicyError, match="changed_fields"):
        build(proposal)


@pytest.mark.parametrize("invalid_number", [float("nan"), float("inf"), float("-inf")])
def test_reviewed_proposal_rejects_non_finite_json_numbers(
    invalid_number: float,
) -> None:
    with pytest.raises(ValidationError):
        reviewed_proposal(after={"name": invalid_number})


def test_equivalent_selection_order_produces_identical_plan_and_hash() -> None:
    first = reviewed_proposal(name="first")
    second = reviewed_proposal(
        name="second",
        entity_type=EntityType.STUDENT,
        target_source_identifier="student-second",
    )

    forward = build(first, second)
    reverse = build(second, first)

    assert forward == reverse
    assert forward.content_hash == reverse.content_hash
    assert forward.proposals == tuple(
        sorted(
            (first.proposal, second.proposal),
            key=lambda ref: (str(ref.proposal_id), ref.proposal_version),
        )
    )
    assert tuple(operation.proposal for operation in forward.operations) == forward.proposals


def test_operation_identity_includes_canonical_operation_content() -> None:
    original = reviewed_proposal()
    revised = reviewed_proposal(after={"name": "Another corrected teacher"})

    original_plan = build(original)
    repeated_plan = build(original)
    revised_plan = build(revised)

    assert original_plan.operations[0].id == repeated_plan.operations[0].id
    assert original_plan.operations[0].id != revised_plan.operations[0].id
    assert original_plan.content_hash != revised_plan.content_hash
