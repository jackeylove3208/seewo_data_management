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

