from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent_graph.actions import InvalidSupervisorDecision, validate_supervisor_decision
from app.agent_graph.contracts import (
    AllowedActionSetV1,
    AllowedActionV1,
    ExcludedActionSummaryV1,
    SingleActionReasonCode,
    SupervisorContextV1,
    SupervisorDecisionV1,
    UnselectedActionReasonV1,
)
from app.agent_graph.supervisor import build_supervisor_context
from app.agent_graph.tools import GRAPH_NODE_TOOL_NAMES


def _action(action_id: str, *, evidence: str, successor: str) -> AllowedActionV1:
    return AllowedActionV1(
        action_id=action_id,
        kind="dispatch_sub_agent",
        sub_agent="reconciliation-analysis",
        resource_ids=(f"resource:{action_id}",),
        required_evidence=(evidence,),
        risk="low",
        requires_human=False,
        successor_node=successor,
    )


def _context() -> SupervisorContextV1:
    action_set = AllowedActionSetV1(
        allowed_actions=(
            _action(
                "analyze_students",
                evidence="student-finding-batch-v1",
                successor="student_analysis_complete",
            ),
            _action(
                "analyze_teachers",
                evidence="teacher-finding-batch-v1",
                successor="teacher_analysis_complete",
            ),
        ),
        action_set_hash="sha256:" + ("a" * 64),
        excluded_action_summaries=(
            ExcludedActionSummaryV1(
                action_id="execute_changes",
                rejected_guard_codes=("approval_missing",),
            ),
        ),
    )
    return SupervisorContextV1(
        tenant_ref="tenant-ref:demo",
        task_id="task:1",
        run_id="run:1",
        run_kind="sync",
        workflow_version="agent-graph-v1",
        graph_version="agent-sync-graph-v1",
        current_node="analyze_actionable_batches",
        graph_cursor=4,
        status="running",
        action_set=action_set,
        active_blockers=("batch_priority_unknown",),
    )


def test_supervisor_decision_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SupervisorDecisionV1.model_validate(
            {
                "action_id": "analyze_students",
                "reason_zh": "先处理学生批次。",
                "expected_result": "student-finding-batch-v1",
                "unknown": "not allowed",
            }
        )


def test_all_governance_execution_nodes_authorize_the_same_phase_tools() -> None:
    expected = GRAPH_NODE_TOOL_NAMES["execute_ready_operations"]

    assert GRAPH_NODE_TOOL_NAMES["execute_remaining_independent"] == expected
    assert "request_execution_batch" in expected


def test_valid_decision_covers_every_unselected_action() -> None:
    context = _context()
    decision = SupervisorDecisionV1(
        action_id="analyze_students",
        reason_zh="学生批次已经具备完整双边证据。",
        expected_result="student-finding-batch-v1",
        observed_blockers=("batch_priority_unknown",),
        risk_notes_zh=("当前动作不触发目标写入。",),
        why_not_other_actions_zh=(
            UnselectedActionReasonV1(
                action_id="analyze_teachers",
                reason_zh="教师批次可以在学生批次后继续处理。",
            ),
        ),
        operator_message_zh="正在分析学生异常。",
    )

    assert validate_supervisor_decision(context, decision) == decision


def test_operator_message_is_sanitized_before_audit_persistence() -> None:
    context = _context()
    decision = SupervisorDecisionV1(
        action_id="analyze_students",
        reason_zh="学生批次已经具备完整双边证据。",
        expected_result="student-finding-batch-v1",
        why_not_other_actions_zh=(
            UnselectedActionReasonV1(
                action_id="analyze_teachers",
                reason_zh="教师批次稍后处理。",
            ),
        ),
        operator_message_zh=(
            "正在检查手机号 13800138000，"
            "内部记录 operation:00000000-0000-0000-0000-000000000001。"
        ),
    )

    validated = validate_supervisor_decision(context, decision)

    assert validated.operator_message_zh is not None
    assert "13800138000" not in validated.operator_message_zh
    assert "00000000-0000-0000-0000-000000000001" not in validated.operator_message_zh
    assert "***8000" in validated.operator_message_zh
    assert "[内部引用]" in validated.operator_message_zh


def test_production_supervisor_context_summarizes_server_facts() -> None:
    base = _context()
    state = SimpleNamespace(
        id="graph-run-1",
        graph_version=base.graph_version,
        current_node=base.current_node,
        cursor=4,
        status="running",
        retry_count=1,
        replan_count=1,
        termination_requested=False,
    )
    run = SimpleNamespace(
        id="run-1",
        task_id="task-1",
        kind="sync",
    )

    context = build_supervisor_context(  # type: ignore[arg-type]
        state,
        run,
        base.action_set,
    )

    assert context.active_blockers == ("guard:approval_missing",)
    assert context.completed_action_summary == ("completed_action_count:4",)
    assert set(context.pending_work_summary) == {
        "reconciliation-analysis:analyze_students:1",
        "reconciliation-analysis:analyze_teachers:1",
    }
    assert set(context.evidence_manifest_refs) == {
        "student-finding-batch-v1",
        "teacher-finding-batch-v1",
    }
    assert context.retry_and_replan_budget == 2


def test_decision_rejects_missing_why_not_reason() -> None:
    context = _context()
    decision = SupervisorDecisionV1(
        action_id="analyze_students",
        reason_zh="先处理学生批次。",
        expected_result="student-finding-batch-v1",
    )

    with pytest.raises(InvalidSupervisorDecision, match="unselected action coverage"):
        validate_supervisor_decision(context, decision)


def test_decision_rejects_non_member_action_blocker_and_evidence() -> None:
    context = _context()
    reasons = (
        UnselectedActionReasonV1(
            action_id="analyze_teachers",
            reason_zh="本轮不选择教师批次。",
        ),
    )

    with pytest.raises(InvalidSupervisorDecision, match="not allowed"):
        validate_supervisor_decision(
            context,
            SupervisorDecisionV1(
                action_id="execute_changes",
                reason_zh="尝试越权执行。",
                expected_result="execution-result-v1",
                why_not_other_actions_zh=reasons,
            ),
        )
    with pytest.raises(InvalidSupervisorDecision, match="expected result"):
        validate_supervisor_decision(
            context,
            SupervisorDecisionV1(
                action_id="analyze_students",
                reason_zh="要求无权产生的证据。",
                expected_result="execution-result-v1",
                why_not_other_actions_zh=reasons,
            ),
        )
    with pytest.raises(InvalidSupervisorDecision, match="unknown blocker"):
        validate_supervisor_decision(
            context,
            SupervisorDecisionV1(
                action_id="analyze_students",
                reason_zh="引用不存在的阻断。",
                expected_result="student-finding-batch-v1",
                observed_blockers=("invented_blocker",),
                why_not_other_actions_zh=reasons,
            ),
        )


def test_single_action_decision_requires_no_why_not_entries() -> None:
    action = _action(
        "wait_for_approval",
        evidence="approval-gate-v1",
        successor="wait_high_risk_approvals",
    )
    context = _context().model_copy(
        update={
            "action_set": AllowedActionSetV1(
                allowed_actions=(action,),
                action_set_hash="sha256:" + ("b" * 64),
                single_action_reason_code=SingleActionReasonCode.HUMAN_GATE_REQUIRED,
            )
        }
    )
    decision = SupervisorDecisionV1(
        action_id=action.action_id,
        reason_zh="当前必须等待人工审批。",
        expected_result="approval-gate-v1",
    )

    assert validate_supervisor_decision(context, decision) == decision
