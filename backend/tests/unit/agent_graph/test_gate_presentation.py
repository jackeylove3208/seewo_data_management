from uuid import uuid4

from app.api.routes.agent import _graph_human_gate_view
from app.models.agent_analysis import AgentApprovalGroupRecord
from app.models.agent_graph import AgentGraphRunRecord, AgentHumanGateRecord
from app.models.agent_runtime import AgentRunRecord
from app.schemas.agent_graph_api import (
    AgentGraphApprovalChangeView,
    AgentGraphApprovalItemView,
)


def test_medium_student_field_update_is_not_presented_as_phone_risk() -> None:
    finding_id = uuid4()
    gate = AgentHumanGateRecord(
        id=uuid4(),
        graph_run_id=uuid4(),
        tenant_id="school-1",
        cursor=15,
        gate_kind="high_risk_approval",
        member_ids=[str(finding_id)],
        content_hash="sha256:" + ("a" * 64),
        status="pending",
    )
    graph = AgentGraphRunRecord(
        id=uuid4(),
        run_id=uuid4(),
        tenant_id="school-1",
        graph_version="agent-sync-graph-v1",
        current_node="wait_high_risk_approvals",
        cursor=16,
        status="waiting_human",
        termination_requested=False,
    )
    run = AgentRunRecord(status="waiting_human")
    approval_group = AgentApprovalGroupRecord(
        run_id=uuid4(),
        task_id=uuid4(),
        tenant_id="school-1",
        group_key="field_difference:student:update:agent-risk-v1",
        membership_hash="b" * 64,
        finding_ids=[str(finding_id)],
        issue_kind="field_difference",
        entity_kind="student",
        operation="update",
        policy_version="agent-risk-v1",
        risk="medium",
        status="pending",
    )
    item = AgentGraphApprovalItemView(
        finding_id=finding_id,
        entity_kind="student",
        entity_name="周可欣",
        entity_number="S004",
        class_name="高一2班",
        source_locator="csv:9",
        source_row_number=9,
        operation_zh="修改希沃中的学生记录",
        issue_zh="类别不一致",
        analysis_zh="希沃类别为 student，权威类别为学生。",
        solution_zh="将类别修改为学生。",
        changes=(
            AgentGraphApprovalChangeView(
                field="category",
                field_zh="类别",
                before="student",
                after="学生",
            ),
        ),
    )

    view = _graph_human_gate_view(
        gate,
        graph=graph,
        run=run,
        approval_group=approval_group,
        items=(item,),
    )

    assert view.summary_zh == "修改 1 条学生记录"
    assert view.risk_reason_zh == (
        "该操作属于中风险变更，默认建议同意，但仍可逐项拒绝。"
    )
    assert view.actionable is True
    assert view.unavailable_reason_zh is None


def test_student_class_clear_is_presented_as_opt_in() -> None:
    item = AgentGraphApprovalItemView(
        finding_id=uuid4(),
        entity_kind="student",
        entity_name="李四",
        entity_number="S001",
        class_name="一班",
        source_locator="csv:2",
        operation_zh="修改希沃中的学生记录",
        issue_zh="班级仅存在于希沃",
        analysis_zh="第三方班级为空。",
        solution_zh="可选择将希沃班级设置为空。",
        changes=(
            AgentGraphApprovalChangeView(
                field="class_name",
                field_zh="班级",
                before="一班",
                after=None,
            ),
        ),
    )

    assert item.selection_mode == "opt_in"
