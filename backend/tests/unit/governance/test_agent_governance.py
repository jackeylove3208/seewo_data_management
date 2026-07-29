from dataclasses import replace
from uuid import uuid4

import pytest

from app.governance.agent_governance import (
    AgentFindingInput,
    AgentOperation,
    AgentRiskPolicy,
    ClarificationError,
    compile_agent_plan,
    confirm_clarification,
    group_high_risk_findings,
    interpret_clarification,
)
from app.schemas.canonical_entities import EntityType
from app.schemas.executions import (
    GovernanceOperation,
    OperationType,
    ProposalSource,
    ProposalVersionRef,
)
from app.schemas.governance import RiskLevel


def finding(
    *,
    kind: str = "field_difference",
    entity: str = "student",
    operation: str = "update",
    fields: tuple[str, ...] = ("name",),
) -> AgentFindingInput:
    return AgentFindingInput(
        finding_id=uuid4(),
        work_item_id=uuid4(),
        entity_kind=entity,
        kind=kind,
        operation=AgentOperation(operation),
        changed_fields=frozenset(fields),
        before={field: "old" for field in fields},
        after={field: "new" for field in fields},
        target_source_identifier="S-1",
        dependencies=frozenset(),
        analysis_terminal=True,
        target_version="sha256:" + "a" * 64,
    )


def test_student_phone_and_delete_are_server_owned_high_risk() -> None:
    policy = AgentRiskPolicy()

    assert policy.assess(finding(fields=("phone",))).risk == "high"
    assert policy.assess(finding(kind="target_extra", operation="delete", fields=())).risk == "high"


def test_student_create_with_phone_is_not_high_risk() -> None:
    decision = AgentRiskPolicy().assess(
        finding(kind="target_missing", operation="create", fields=("phone",))
    )

    assert decision.risk == "medium"
    assert decision.requires_approval is True


def test_medium_risk_findings_are_grouped_for_operator_review() -> None:
    medium = finding(kind="target_missing", operation="create", fields=("name",))

    groups = group_high_risk_findings((medium,))

    assert len(groups) == 1
    assert groups[0].risk == "medium"


def test_grouping_freezes_compatible_membership() -> None:
    groups = group_high_risk_findings((finding(fields=("phone",)), finding(fields=("phone",))))

    assert len(groups) == 1
    assert groups[0].membership_hash
    assert groups[0].risk == "high"
    assert len(groups[0].finding_ids) == 2


def test_grouping_splits_compatible_findings_into_bounded_approval_groups() -> None:
    findings = tuple(finding(fields=("phone",)) for _index in range(101))

    groups = group_high_risk_findings(findings)

    assert [len(group.finding_ids) for group in groups] == [50, 50, 1]
    assert [group.segment_index for group in groups] == [0, 1, 2]
    assert len({group.id for group in groups}) == 3
    assert {
        finding_id
        for group in groups
        for finding_id in group.finding_ids
    } == {item.finding_id for item in findings}


def test_conflict_interpretation_is_bounded_and_requires_second_confirmation() -> None:
    candidate = uuid4()
    decision = interpret_clarification(
        "使用候选 " + str(candidate),
        candidates=(candidate,),
        allowed_outcomes=("use_candidate", "skip"),
    )
    assert decision.outcome == "use_candidate"
    assert decision.candidate_id == candidate
    assert confirm_clarification(decision, confirmed=True).confirmed is True

    with pytest.raises(ClarificationError):
        interpret_clarification(
            "开始另一个任务", candidates=(candidate,), allowed_outcomes=("skip",)
        )


def test_plan_rejects_unresolved_analysis_or_unapproved_high_risk() -> None:
    high = finding(fields=("phone",))
    with pytest.raises(ValueError, match="approval"):
        compile_agent_plan((high,), approved_group_ids=frozenset(), confirmed_conflicts=frozenset())


def test_compiled_plan_binds_high_risk_approval_and_frozen_target_version() -> None:
    high = finding(kind="target_extra", operation="delete", fields=())
    group = group_high_risk_findings((high,))[0]

    plan = compile_agent_plan(
        (high,), approved_group_ids=frozenset({group.id}), confirmed_conflicts=frozenset()
    )

    assert plan.target_version == high.target_version
    assert len(plan.operations) == 1
    assert plan.operations[0].operation == AgentOperation.DELETE


def test_compiled_plan_supports_mixed_per_finding_decisions() -> None:
    approved = finding(entity="teacher", fields=("name",))
    rejected = finding(entity="teacher", fields=("email",))

    plan = compile_agent_plan(
        (approved, rejected),
        approved_group_ids=frozenset(),
        approved_finding_ids=frozenset({approved.finding_id}),
        rejected_finding_ids=frozenset({rejected.finding_id}),
        confirmed_conflicts=frozenset(),
    )

    assert [item.finding_id for item in plan.operations] == [approved.finding_id]


def test_compiled_plan_excludes_all_creates_with_the_same_target_identifier() -> None:
    duplicate_a = replace(
        finding(kind="target_missing", operation="create"),
        target_source_identifier=None,
        after={"source_id": "S036", "name": "学生甲"},
    )
    duplicate_b = replace(
        finding(kind="target_missing", operation="create"),
        target_source_identifier=None,
        after={"source_id": "S036", "name": "学生乙"},
    )
    independent = replace(
        finding(kind="target_missing", operation="create"),
        target_source_identifier=None,
        after={"source_id": "S037", "name": "学生丙"},
    )
    findings = (duplicate_a, duplicate_b, independent)

    plan = compile_agent_plan(
        findings,
        approved_group_ids=frozenset(),
        approved_finding_ids=frozenset(item.finding_id for item in findings),
        confirmed_conflicts=frozenset(),
    )

    assert [item.finding_id for item in plan.operations] == [
        independent.finding_id
    ]
    assert plan.excluded_finding_ids == frozenset(
        {duplicate_a.finding_id, duplicate_b.finding_id}
    )

    duplicate_only_plan = compile_agent_plan(
        (duplicate_a, duplicate_b),
        approved_group_ids=frozenset(),
        approved_finding_ids=frozenset(
            {duplicate_a.finding_id, duplicate_b.finding_id}
        ),
        confirmed_conflicts=frozenset(),
    )
    assert duplicate_only_plan.operations == ()
    assert duplicate_only_plan.excluded_finding_ids == frozenset(
        {duplicate_a.finding_id, duplicate_b.finding_id}
    )


def test_conflict_remains_non_executable_until_confirmed_work_item_is_present() -> None:
    conflict = finding(kind="identity_conflict", operation="update")
    with pytest.raises(ValueError, match="no executable"):
        compile_agent_plan(
            (conflict,), approved_group_ids=frozenset(), confirmed_conflicts=frozenset()
        )


def test_delete_is_a_typed_csv_operation() -> None:
    operation = GovernanceOperation(
        proposal=ProposalVersionRef(proposal_id=uuid4(), proposal_version=1),
        proposal_source=ProposalSource.AI,
        difference_id=uuid4(),
        difference_version=1,
        analysis_id=uuid4(),
        analysis_version="agent-analysis-v1",
        operation_type=OperationType.DISABLE,
        entity_type=EntityType.STUDENT,
        target_source_identifier="S-1",
        before={"name": "Ada"},
        after={},
        changed_fields=frozenset({"name"}),
        reversible=True,
        risk=RiskLevel.HIGH,
        compensation_for=uuid4(),
        restore_absence=True,
    )
    assert operation.operation_type is OperationType.DISABLE
