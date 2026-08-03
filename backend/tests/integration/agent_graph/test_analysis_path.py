from uuid import uuid4

import pytest

from app.agent_graph import analysis_executors
from app.agent_graph.analysis_executors import (
    compile_analysis_payloads,
    validate_normalized_output,
)
from app.agent_graph.evidence import IdentityKeyHitV1, PairedRecordEvidenceV1
from app.ai.skills.contracts import (
    AgentFinding,
    AgentFindingBatch,
    AnalysisTemplateOutput,
    GovernanceSolution,
    GovernanceSolutionBatch,
    IdentityWorkItem,
    NormalizedOrganizationBatch,
    NormalizedRecord,
)


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


def test_graph_analysis_rejects_retention_for_target_extra() -> None:
    work_item_id = uuid4()
    finding_id = uuid4()
    findings = AgentFindingBatch(
        schema_version="agent-contract-v1",
        findings=(
            AgentFinding(
                finding_id=finding_id,
                work_item_id=work_item_id,
                disposition="target_extra",
                category_zh="希沃多余",
                analysis_zh="第三方权威数据没有对应记录。",
                proposed_operation="retain",
                evidence_refs=("paired-record:extra",),
                solution_zh="保留该记录。",
                risk="low",
            ),
        ),
    )

    with pytest.raises(ValueError, match="operation"):
        compile_analysis_payloads(
            expected_work_item_kinds={work_item_id: "target_extra"},
            allowed_evidence_refs=frozenset({"paired-record:extra"}),
            findings=findings,
            solutions=GovernanceSolutionBatch(
                schema_version="agent-contract-v1",
                solutions=(
                    GovernanceSolution(
                        finding_id=finding_id,
                        solution_zh="保留该记录。",
                        operation="retain",
                        risk="low",
                    ),
                ),
            ),
        )


def _target_extra_work_item(*, name: str, number: str) -> IdentityWorkItem:
    work_item_id = uuid4()
    evidence_ref = f"paired-record:{work_item_id}"
    return IdentityWorkItem(
        work_item_id=work_item_id,
        entity_kind="student",
        target_locator=f"csv:{number}",
        candidate_evidence_refs=(evidence_ref,),
        paired_evidence=PairedRecordEvidenceV1(
            evidence_ref=evidence_ref,
            work_item_id=str(work_item_id),
            persisted_kind="target_extra",
            entity_kind="student",
            target_record={
                "input_ref": f"input:{uuid4()}",
                "locator": f"csv:{number}",
                "entity_kind": "student",
                "category": "学生",
                "name": name,
                "number": number,
                "class_name": "一班",
                "phone_token": None,
                "email": None,
            },
            authority_record=None,
            allowed_operations=("delete",),
            evidence_refs=(evidence_ref,),
        ),
    )


def test_template_profile_is_connector_neutral_and_excludes_person_values() -> None:
    first = _target_extra_work_item(name="测试学生甲", number="S001")
    second = _target_extra_work_item(name="测试学生乙", number="S999")

    first_context = analysis_executors.build_analysis_template_context((first,))
    second_context = analysis_executors.build_analysis_template_context((second,))

    assert first_context is not None
    assert second_context is not None
    assert first_context.profile_hash == second_context.profile_hash
    assert "S001" not in first_context.profile_hash
    assert "测试学生甲" not in first_context.profile.model_dump_json()


def test_template_instantiation_keeps_each_work_item_and_evidence_reference() -> None:
    items = (
        _target_extra_work_item(name="测试学生甲", number="S001"),
        _target_extra_work_item(name="测试学生乙", number="S002"),
    )
    context = analysis_executors.build_analysis_template_context(items)
    assert context is not None
    template = AnalysisTemplateOutput(
        schema_version="agent-contract-v1",
        profile_hash=context.profile_hash,
        category_zh="目标端多余学生",
        analysis_zh="目标端存在记录，但第三方权威端不存在对应记录。",
        proposed_operation="delete",
        solution_zh="按高风险审批流程删除目标端多余记录。",
        risk="high",
    )

    payloads = analysis_executors.instantiate_analysis_template(
        context,
        template,
        allowed_evidence_refs=frozenset(
            item.paired_evidence.evidence_ref for item in items
        ),
    )

    assert [payload.work_item_id for payload in payloads] == [
        item.work_item_id for item in items
    ]
    assert [payload.evidence_refs for payload in payloads] == [
        (item.paired_evidence.evidence_ref,) for item in items
    ]
    assert all(payload.solutions[0].risk == "high" for payload in payloads)


def test_target_missing_template_uses_server_owned_medium_create_risk() -> None:
    work_item_id = uuid4()
    authority_ref = f"input:{uuid4()}"
    evidence_ref = f"paired-record:{work_item_id}"
    item = IdentityWorkItem(
        work_item_id=work_item_id,
        entity_kind="teacher",
        target_locator="csv:2",
        candidate_evidence_refs=(evidence_ref,),
        paired_evidence=PairedRecordEvidenceV1(
            evidence_ref=evidence_ref,
            work_item_id=str(work_item_id),
            persisted_kind="target_missing",
            entity_kind="teacher",
            target_record=None,
            authority_record={
                "input_ref": authority_ref,
                "locator": "csv:2",
                "entity_kind": "teacher",
                "category": "教师",
                "name": "测试教师",
                "number": "T001",
                "class_name": None,
                "phone_token": None,
                "email": None,
            },
            identity_key_hits=(
                IdentityKeyHitV1(key_kind="number", authority_ref=authority_ref),
            ),
            allowed_candidates=(authority_ref,),
            allowed_operations=("create", "retain"),
            evidence_refs=(evidence_ref,),
        ),
    )
    context = analysis_executors.build_analysis_template_context((item,))
    assert context is not None

    payload = analysis_executors.instantiate_analysis_template(
        context,
        AnalysisTemplateOutput(
            schema_version="agent-contract-v1",
            profile_hash=context.profile_hash,
            category_zh="目标端缺少教师",
            analysis_zh="第三方权威端存在记录，但目标端没有对应记录。",
            proposed_operation="create",
            solution_zh="在目标端创建对应教师记录。",
            risk="medium",
        ),
        allowed_evidence_refs=frozenset({evidence_ref}),
    )[0]

    assert payload.kind == "target_missing"
    assert payload.solutions[0].operation == "create"
    assert payload.solutions[0].risk == "medium"


def test_template_rejects_representative_person_value_in_narrative() -> None:
    item = _target_extra_work_item(name="测试学生甲", number="S001")
    context = analysis_executors.build_analysis_template_context((item,))
    assert context is not None

    with pytest.raises(
        analysis_executors.AnalysisTemplateValidationError,
        match="analysis_template_contains_representative_value",
    ):
        analysis_executors.instantiate_analysis_template(
            context,
            AnalysisTemplateOutput(
                schema_version="agent-contract-v1",
                profile_hash=context.profile_hash,
                category_zh="目标端多余学生",
                analysis_zh="测试学生甲在目标端存在但权威端不存在。",
                proposed_operation="delete",
                solution_zh="按审批流程删除目标端多余记录。",
                risk="high",
            ),
            allowed_evidence_refs=frozenset(
                {item.paired_evidence.evidence_ref}
            ),
        )
