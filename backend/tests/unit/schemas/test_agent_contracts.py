from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.agent_ingestion import (
    AgentContractRecord,
    AgentEntityKind,
    AgentInputMark,
    AgentSourceRole,
)
from app.schemas.agent_reconciliation import (
    AgentFindingPayload,
    AgentSolutionPayload,
    IdentityKeyKind,
    WorkItemKind,
    WorkItemState,
)


def _record(**changes: object) -> AgentContractRecord:
    values: dict[str, object] = {
        "task_id": uuid4(),
        "run_id": uuid4(),
        "snapshot_id": uuid4(),
        "tenant_id": "school-1",
        "source_role": AgentSourceRole.AUTHORITATIVE,
        "stable_locator": "csv:authoritative:2",
        "stable_order": 2,
        "entity_kind": AgentEntityKind.STUDENT,
        "category": "学生",
        "name": "测试学生",
        "number": "S-001",
        "class_name": "一年级一班",
        "phone": "13800000000",
        "email": "student@example.test",
    }
    values.update(changes)
    return AgentContractRecord.model_validate(values)


def test_agent_contract_accepts_only_three_entity_kinds_and_freezes_values() -> None:
    record = _record()

    assert set(AgentEntityKind) == {
        AgentEntityKind.DEPARTMENT,
        AgentEntityKind.STUDENT,
        AgentEntityKind.TEACHER,
    }
    with pytest.raises(ValidationError):
        _record(entity_kind="class")
    with pytest.raises(ValidationError):
        record.name = "changed"  # type: ignore[misc]


def test_class_is_applicable_only_to_students() -> None:
    assert _record(entity_kind=AgentEntityKind.TEACHER, class_name=None).class_name is None

    with pytest.raises(ValidationError, match="class_name"):
        _record(entity_kind=AgentEntityKind.TEACHER, class_name="不适用")


def test_reconciliation_payloads_are_strict_and_bound_solution_cardinality() -> None:
    solution = AgentSolutionPayload(
        operation="update",
        risk="low",
        solution_zh="补齐权威字段",
        recommended=True,
    )
    finding = AgentFindingPayload(
        work_item_id=uuid4(),
        kind="field_difference",
        category_zh="字段缺失",
        analysis_zh="目标记录缺少班级。",
        evidence_refs=("evidence:1",),
        solutions=(solution,),
    )

    assert finding.solutions == (solution,)
    assert IdentityKeyKind.NUMBER.value == "number"
    assert WorkItemKind.TARGET_EXTRA.value == "target_extra"
    assert WorkItemKind.AUTHORITY_INVALID.value == "authority_invalid"
    assert WorkItemState.PENDING.value == "pending"
    with pytest.raises(ValidationError, match="at most 3"):
        AgentFindingPayload(
            work_item_id=finding.work_item_id,
            kind="field_difference",
            category_zh="字段缺失",
            analysis_zh="目标记录缺少班级。",
            evidence_refs=("evidence:1",),
            solutions=(solution, solution, solution, solution),
        )
    with pytest.raises(ValidationError):
        AgentSolutionPayload(
            operation="update",
            risk="low",
            solution_zh="x",
            recommended=True,
            unexpected="value",
        )

    with pytest.raises(ValidationError, match="exactly one"):
        AgentFindingPayload(
            work_item_id=uuid4(),
            kind="field_difference",
            category_zh="字段缺失",
            analysis_zh="目标记录缺少班级。",
            evidence_refs=("evidence:1",),
            solutions=(
                AgentSolutionPayload(
                    operation="update",
                    risk="low",
                    solution_zh="方案一",
                    recommended=False,
                ),
                AgentSolutionPayload(
                    operation="skip",
                    risk="low",
                    solution_zh="方案二",
                    recommended=False,
                ),
            ),
        )


def test_input_marks_reject_sensitive_evidence_fields() -> None:
    with pytest.raises(ValidationError, match="sensitive"):
        AgentInputMark(
            input_record_id=uuid4(),
            reason_code="missing_identity",
            affected_fields=("phone",),
            inclusion_state="anomaly",
            report_disposition="report",
            safe_evidence={"phone": "13800000000"},
        )
    with pytest.raises(ValidationError, match="unsupported"):
        AgentInputMark(
            input_record_id=uuid4(),
            reason_code="missing_identity",
            affected_fields=("phone",),
            inclusion_state="anomaly",
            report_disposition="report",
            safe_evidence={"message": "call +1-415-555-0199"},
        )
