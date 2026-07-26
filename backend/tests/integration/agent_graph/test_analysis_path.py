from uuid import uuid4

import pytest

from app.agent_graph.analysis_executors import (
    compile_analysis_payloads,
    partition_bounded_resources,
    validate_normalized_output,
)
from app.ai.skills.contracts import (
    AgentFinding,
    AgentFindingBatch,
    GovernanceSolution,
    GovernanceSolutionBatch,
    NormalizedOrganizationBatch,
    NormalizedRecord,
)


def test_normalization_resources_are_partitioned_at_fifty() -> None:
    resources = tuple(f"row:{index}" for index in range(1, 52))

    batches = partition_bounded_resources(resources)

    assert tuple(len(batch) for batch in batches) == (50, 1)
    assert batches[0][0] == "row:1"
    assert batches[1][0] == "row:51"


def test_normalized_output_must_exactly_cover_manifest_rows() -> None:
    output = NormalizedOrganizationBatch(
        schema_version="agent-contract-v1",
        records=(
            NormalizedRecord(
                locator="row:1",
                entity_kind="student",
                category="学生",
                name="测试学生",
                number="S001",
                phone_token="STUDENT_PHONE_ABCDEF123456",
                email="student@example.test",
                class_name="一年级一班",
                invalid=False,
            ),
        ),
    )

    assert validate_normalized_output(("row:1",), output) is output
    with pytest.raises(ValueError, match="exactly cover"):
        validate_normalized_output(("row:2",), output)


def test_analysis_rejects_forged_evidence_and_compiles_one_ai_solution() -> None:
    work_item_id = uuid4()
    finding_id = uuid4()
    findings = AgentFindingBatch(
        schema_version="agent-contract-v1",
        findings=(
            AgentFinding(
                finding_id=finding_id,
                work_item_id=work_item_id,
                disposition="field_difference",
                category_zh="学生班级不一致",
                analysis_zh="权威记录与希沃目标记录的班级字段不一致。",
                proposed_operation="update",
                evidence_refs=("paired-record:1",),
                solution_zh="按第三方权威值更新希沃目标中的班级字段。",
                risk="high",
            ),
        ),
    )
    solutions = GovernanceSolutionBatch(
        schema_version="agent-contract-v1",
        solutions=(
            GovernanceSolution(
                finding_id=finding_id,
                solution_zh="按第三方权威值更新希沃目标中的班级字段。",
                operation="update",
                risk="high",
            ),
        ),
    )

    payloads = compile_analysis_payloads(
        expected_work_item_kinds={work_item_id: "field_difference"},
        allowed_evidence_refs=frozenset({"paired-record:1"}),
        findings=findings,
        solutions=solutions,
    )

    assert len(payloads) == 1
    assert payloads[0].work_item_id == work_item_id
    assert payloads[0].solutions[0].recommended is True
    forged = findings.model_copy(
        update={
            "findings": (
                findings.findings[0].model_copy(
                    update={"evidence_refs": ("paired-record:foreign",)}
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="evidence manifest"):
        compile_analysis_payloads(
            expected_work_item_kinds={work_item_id: "field_difference"},
            allowed_evidence_refs=frozenset({"paired-record:1"}),
            findings=forged,
            solutions=solutions,
        )


def test_correct_rows_are_not_accepted_as_actionable_findings() -> None:
    with pytest.raises(ValueError, match="actionable"):
        compile_analysis_payloads(
            expected_work_item_kinds={uuid4(): "correct"},
            allowed_evidence_refs=frozenset(),
            findings=AgentFindingBatch(
                schema_version="agent-contract-v1",
                findings=(),
            ),
            solutions=GovernanceSolutionBatch(
                schema_version="agent-contract-v1",
                solutions=(),
            ),
        )
