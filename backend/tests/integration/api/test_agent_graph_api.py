from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_graph.repository import AgentGraphRepository
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase, AgentRunKind
from app.ai.providers.base import LLMResponse
from app.main import create_app
from app.models.agent_analysis import (
    AgentApprovalGroupRecord,
    AgentClarificationRecord,
    AgentFindingRecord,
    AgentFindingSolutionRecord,
    AgentIdentityClaimRecord,
    AgentInputRecord,
    AgentModelBatchRecord,
    AgentWorkItemRecord,
)
from app.models.agent_graph import AgentGraphRunRecord
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.repositories.agent_governance import AgentGovernanceRepository
from tests.settings import build_test_settings


@pytest.fixture
def graph_agent_client(tmp_path: Path):
    settings = build_test_settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'agent-graph-api.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        export_root=tmp_path / "exports",
        auto_create_schema=True,
        new_agent_enabled=True,
        agent_graph_enabled=True,
        new_agent_analysis_only=True,
        tokenization_secret="test-tokenization-secret",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_graph_progress_and_tenant_safe_gate_decision(
    graph_agent_client: TestClient,
) -> None:
    agent_client = graph_agent_client
    async def seed() -> tuple[str, str, str, str]:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student", "teacher"],
                status="running",
                stage="analysis",
                workflow_version="agent-graph-v1",
                idempotency_key=str(uuid4()),
                request_hash=str(uuid4()),
            )
            session.add(task)
            await session.flush()
            run = await AgentRuntimeRepository(session).create_run(
                task_id=task.id,
                tenant_id=task.tenant_id,
                conversation_id=None,
                kind=AgentRunKind.SYNC,
                workflow_version="agent-graph-v1",
            )
            graph = await AgentGraphRepository(session).create_run_state(
                run_id=run.id,
                graph_version="agent-sync-graph-v1",
                initial_node="aggregate_risk",
            )
            snapshots: dict[str, Snapshot] = {}
            for role in ("authoritative", "target"):
                source = SourceFile(
                    task_id=task.id,
                    source_role=role,
                    original_name=f"{role}.csv",
                    storage_name=f"{uuid4()}.csv",
                    storage_path=f"/synthetic/{role}.csv",
                    sha256=uuid4().hex * 2,
                    size_bytes=10,
                    detected_encoding="utf-8",
                )
                session.add(source)
                await session.flush()
                snapshot = Snapshot(
                    id=uuid4(),
                    task_id=task.id,
                    source_file_id=source.id,
                    source_role=role,
                    schema_version="agent-contract-v1",
                    mapping_version="agent-contract-v1",
                    file_hash=source.sha256,
                    content_hash=uuid4().hex * 2,
                    state="published",
                    summary={},
                )
                session.add(snapshot)
                snapshots[role] = snapshot
            await session.flush()
            student_batch = AgentModelBatchRecord(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                entity_kind="student",
                input_hash=uuid4().hex * 2,
                item_count=1,
                status="completed",
                output_hash=uuid4().hex * 2,
            )
            session.add(student_batch)
            authority_student = AgentInputRecord(
                run_id=run.id,
                task_id=task.id,
                snapshot_id=snapshots["authoritative"].id,
                tenant_id=task.tenant_id,
                source_role="authoritative",
                stable_locator="csv:2",
                stable_order=1,
                entity_kind="student",
                category="学生",
                name="李明",
                number="S-002",
                class_name="三年级一班",
                phone="13900005678",
                email="student@example.test",
                raw_row_number=2,
                input_hash=uuid4().hex * 2,
            )
            target_student = AgentInputRecord(
                run_id=run.id,
                task_id=task.id,
                snapshot_id=snapshots["target"].id,
                tenant_id=task.tenant_id,
                source_role="target",
                stable_locator="csv:12",
                stable_order=1,
                entity_kind="student",
                category="学生",
                name="李明",
                number="S-002",
                class_name="三年级一班",
                phone="13800001234",
                email="student@example.test",
                raw_row_number=12,
                input_hash=uuid4().hex * 2,
            )
            session.add_all((authority_student, target_student))
            await session.flush()
            student_work_item = AgentWorkItemRecord(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                source_snapshot_id=snapshots["authoritative"].id,
                target_snapshot_id=snapshots["target"].id,
                subject_input_id=target_student.id,
                entity_kind="student",
                kind="field_difference",
                state="analyzed",
                idempotency_hash=uuid4().hex * 2,
                evidence_hash=uuid4().hex * 2,
            )
            session.add(student_work_item)
            await session.flush()
            session.add(
                AgentIdentityClaimRecord(
                    run_id=run.id,
                    task_id=task.id,
                    source_snapshot_id=snapshots["authoritative"].id,
                    target_snapshot_id=snapshots["target"].id,
                    authority_input_id=authority_student.id,
                    target_input_id=target_student.id,
                    work_item_id=student_work_item.id,
                )
            )
            student_finding = AgentFindingRecord(
                run_id=run.id,
                task_id=task.id,
                work_item_id=student_work_item.id,
                batch_id=student_batch.id,
                kind="field_difference",
                category_zh="手机号不一致",
                analysis_zh="第三方权威手机号与希沃手机号不一致。",
                evidence_refs=["paired-record:student"],
                content_hash=uuid4().hex * 2,
            )
            session.add(student_finding)
            await session.flush()
            session.add(
                AgentFindingSolutionRecord(
                    finding_id=student_finding.id,
                    ordinal=1,
                    operation="update",
                    risk="high",
                    solution_zh="将希沃手机号修改为第三方权威值。",
                    recommended=True,
                )
            )
            finding_ids = (str(student_finding.id),)
            batch = AgentModelBatchRecord(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                entity_kind="teacher",
                input_hash=uuid4().hex * 2,
                item_count=3,
                status="completed",
                output_hash=uuid4().hex * 2,
            )
            session.add(batch)
            await session.flush()
            delete_finding_ids: list[str] = []
            for row_number, name in enumerate(
                ("王老师", "李老师", "陈老师"),
                start=8,
            ):
                target_input = AgentInputRecord(
                    run_id=run.id,
                    task_id=task.id,
                    snapshot_id=snapshots["target"].id,
                    tenant_id=task.tenant_id,
                    source_role="target",
                    stable_locator=f"csv:{row_number}",
                    stable_order=row_number,
                    entity_kind="teacher",
                    category="老师",
                    name=name,
                    number=f"T-{row_number:03d}",
                    class_name=None,
                    phone=f"1380000{row_number:04d}",
                    email=f"teacher{row_number}@example.test",
                    raw_row_number=row_number,
                    input_hash=uuid4().hex * 2,
                )
                session.add(target_input)
                await session.flush()
                work_item = AgentWorkItemRecord(
                    run_id=run.id,
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    source_snapshot_id=snapshots["authoritative"].id,
                    target_snapshot_id=snapshots["target"].id,
                    subject_input_id=target_input.id,
                    entity_kind="teacher",
                    kind="target_extra",
                    state="analyzed",
                    idempotency_hash=uuid4().hex * 2,
                    evidence_hash=uuid4().hex * 2,
                )
                session.add(work_item)
                await session.flush()
                finding = AgentFindingRecord(
                    run_id=run.id,
                    task_id=task.id,
                    work_item_id=work_item.id,
                    batch_id=batch.id,
                    kind="target_extra",
                    category_zh="希沃多余记录",
                    analysis_zh=(
                        f"{name}仅存在于希沃目标数据中。"
                        + (
                            "联系邮箱 teacher8@example.test。"
                            if row_number == 8
                            else ""
                        )
                    ),
                    evidence_refs=[f"target-row:{row_number}"],
                    content_hash=uuid4().hex * 2,
                )
                session.add(finding)
                await session.flush()
                session.add(
                    AgentFindingSolutionRecord(
                        finding_id=finding.id,
                        ordinal=1,
                        operation="delete",
                        risk="high",
                        solution_zh=f"删除希沃中的{name}记录。",
                        recommended=True,
                    )
                )
                delete_finding_ids.append(str(finding.id))
            session.add(
                AgentApprovalGroupRecord(
                    run_id=run.id,
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    group_key="field_difference:student:update:agent-risk-v1",
                    membership_hash="membership-hash",
                    finding_ids=list(finding_ids),
                    issue_kind="field_difference",
                    entity_kind="student",
                    operation="update",
                    policy_version="agent-risk-v1",
                    risk="high",
                    status="pending",
                )
            )
            session.add(
                AgentApprovalGroupRecord(
                    run_id=run.id,
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    group_key="target_extra:teacher:delete:agent-risk-v1",
                    membership_hash="d" * 64,
                    finding_ids=delete_finding_ids,
                    issue_kind="target_extra",
                    entity_kind="teacher",
                    operation="delete",
                    policy_version="agent-risk-v1",
                    risk="high",
                    status="pending",
                )
            )
            gate = await AgentGraphRepository(session).record_human_gate(
                graph_run_id=graph.id,
                cursor=graph.cursor,
                gate_kind="high_risk_approval",
                member_ids=finding_ids,
                content_hash="sha256:" + ("a" * 64),
                status="pending",
            )
            delete_gate = await AgentGraphRepository(session).record_human_gate(
                graph_run_id=graph.id,
                cursor=graph.cursor,
                gate_kind="high_risk_approval",
                member_ids=tuple(delete_finding_ids),
                content_hash="sha256:" + ("b" * 64),
                status="pending",
            )
            graph.current_node = "wait_high_risk_approvals"
            graph.cursor = 1
            run.status = "waiting_human"
            await session.commit()
            return str(task.id), str(gate.id), str(delete_gate.id)

    task_id, gate_id, delete_gate_id = agent_client.portal.call(seed)
    progress = agent_client.get(f"/api/agent/tasks/{task_id}/graph")

    assert progress.status_code == 200, progress.text
    body = progress.json()
    assert body["business_stage"] == "governance_execution"
    assert body["current_action_zh"] == "正在等待治理操作审核"
    gates = {item["id"]: item for item in body["human_gates"]}
    update_gate = gates[gate_id]
    assert update_gate["item_count"] == 1
    assert update_gate["entity_kind"] == "student"
    assert update_gate["operation"] == "update"
    assert update_gate["issue_kind"] == "field_difference"
    assert update_gate["summary_zh"] == "修改 1 条学生手机号"
    assert update_gate["risk_reason_zh"] == (
        "学生手机号属于高危隐私字段，本次操作会修改希沃目标中的手机号。"
    )
    assert update_gate["actionable"] is True
    assert update_gate["unavailable_reason_zh"] is None
    assert len(update_gate["items"]) == 1
    phone_item = update_gate["items"][0]
    assert phone_item["entity_name"] == "李明"
    assert phone_item["entity_number"] == "S-002"
    assert phone_item["class_name"] == "三年级一班"
    assert phone_item["changes"] == [
        {
            "field": "phone",
            "field_zh": "手机号",
            "before": "138****1234",
            "after": "139****5678",
        }
    ]
    delete_gate = gates[delete_gate_id]
    assert delete_gate["entity_kind"] == "teacher"
    assert delete_gate["operation"] == "delete"
    assert delete_gate["summary_zh"] == "删除 3 条教师记录"
    assert delete_gate["risk_reason_zh"] == (
        "删除会永久移除希沃目标中的记录，治理后只能通过回滚任务恢复。"
    )
    assert delete_gate["actionable"] is True
    assert len(delete_gate["items"]) == 3
    first_delete = delete_gate["items"][0]
    assert first_delete["entity_name"] == "王老师"
    assert first_delete["entity_number"] == "T-008"
    assert first_delete["source_locator"] == "csv:8"
    assert first_delete["source_row_number"] == 8
    assert first_delete["operation_zh"] == "删除希沃中的教师记录"
    assert first_delete["issue_zh"] == "希沃多余记录"
    assert first_delete["analysis_zh"] == (
        "王老师仅存在于希沃目标数据中。联系邮箱 t***@example.test。"
    )
    assert first_delete["solution_zh"] == "删除希沃中的王老师记录。"
    assert first_delete["changes"] == []
    assert "13800000008" not in progress.text
    assert "teacher8@example.test" not in progress.text
    assert "prompt" not in progress.text.casefold()
    assert "content_hash" not in progress.text

    override = agent_client.post(
        f"/api/agent/tasks/{task_id}/graph/gates/{gate_id}/decision",
        json={
            "decision": "approve",
            "tenant_id": "other-school",
        },
    )
    assert override.status_code == 422

    async def set_run_status(value: str) -> None:
        async with agent_client.app.state.database.session_factory() as session:
            run = await session.scalar(
                select(AgentRunRecord).where(
                    AgentRunRecord.task_id == UUID(task_id)
                )
            )
            assert run is not None
            run.status = value
            await session.commit()

    agent_client.portal.call(set_run_status, "running")
    non_actionable = agent_client.post(
        f"/api/agent/tasks/{task_id}/graph/gates/{gate_id}/decision",
        json={"decision": "approve"},
    )
    assert non_actionable.status_code == 409
    assert non_actionable.json()["detail"]["code"] == "stale_graph_gate"
    agent_client.portal.call(set_run_status, "waiting_human")

    delete_finding_ids = [
        item["finding_id"] for item in delete_gate["items"]
    ]
    atomic_failure = agent_client.post(
        f"/api/agent/tasks/{task_id}/graph/gates/decisions",
        json={
            "decisions": [
                {
                    "gate_id": gate_id,
                    "decision": "approve",
                    "reason": "批量审核",
                },
                {
                    "gate_id": delete_gate_id,
                    "decision": "approve",
                    "approved_finding_ids": delete_finding_ids,
                    "rejected_finding_ids": [],
                    "graph_cursor": 1,
                    "membership_hash": "f" * 64,
                    "reason": "批量审核",
                },
            ]
        },
    )
    assert atomic_failure.status_code == 409
    after_failure = agent_client.get(
        f"/api/agent/tasks/{task_id}/graph"
    ).json()
    assert {
        item["id"]: item["status"] for item in after_failure["human_gates"]
    } == {
        gate_id: "pending",
        delete_gate_id: "pending",
    }

    approved = agent_client.post(
        f"/api/agent/tasks/{task_id}/graph/gates/decisions",
        json={
            "decisions": [
                {
                    "gate_id": gate_id,
                    "decision": "approve",
                    "reason": "批量审核",
                },
                {
                    "gate_id": delete_gate_id,
                    "decision": "approve",
                    "approved_finding_ids": delete_finding_ids[:2],
                    "rejected_finding_ids": delete_finding_ids[2:],
                    "graph_cursor": 1,
                    "membership_hash": "d" * 64,
                    "reason": "批量审核",
                },
            ]
        },
    )
    assert approved.status_code == 200, approved.text
    assert [item["status"] for item in approved.json()["decisions"]] == [
        "approved",
        "approved",
    ]

    decided_body = agent_client.get(
        f"/api/agent/tasks/{task_id}/graph"
    ).json()
    assert decided_body["status"] == "running"
    decided_gates = {item["id"]: item for item in decided_body["human_gates"]}
    assert decided_gates[gate_id]["status"] == "approved"
    assert decided_gates[gate_id]["actionable"] is False
    assert decided_gates[gate_id]["unavailable_reason_zh"] == "该审批已经处理完成。"
    assert decided_gates[gate_id]["summary_zh"] == "修改 1 条学生手机号"
    assert decided_gates[delete_gate_id]["status"] == "approved"

    completed_decisions = agent_client.get(
        f"/api/agent/tasks/{task_id}/graph"
    ).json()
    completed_gates = {
        item["id"]: item for item in completed_decisions["human_gates"]
    }
    assert completed_decisions["status"] == "running"
    assert completed_gates[delete_gate_id]["status"] == "approved"
    assert completed_gates[delete_gate_id]["member_decisions"] == {
        **{finding_id: "approved" for finding_id in delete_finding_ids[:2]},
        **{finding_id: "rejected" for finding_id in delete_finding_ids[2:]},
    }


def test_blocked_graph_progress_preserves_the_original_business_stage(
    graph_agent_client: TestClient,
) -> None:
    agent_client = graph_agent_client

    async def seed() -> str:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="failed",
                stage="ingestion",
                workflow_version="agent-graph-v1",
                idempotency_key=str(uuid4()),
                request_hash=str(uuid4()),
            )
            session.add(task)
            await session.flush()
            run = await AgentRuntimeRepository(session).create_run(
                task_id=task.id,
                tenant_id=task.tenant_id,
                conversation_id=None,
                kind=AgentRunKind.SYNC,
                workflow_version="agent-graph-v1",
            )
            run.phase = AgentPhase.INGEST_AND_NORMALIZE.value
            run.status = "blocked_model_error"
            graph = await AgentGraphRepository(session).create_run_state(
                run_id=run.id,
                graph_version="agent-sync-graph-v1",
                initial_node="inspect_sources",
            )
            graph.current_node = "blocked_model_error"
            graph.status = "blocked_model_error"
            await session.commit()
            return str(task.id)

    task_id = agent_client.portal.call(seed)

    progress = agent_client.get(f"/api/agent/tasks/{task_id}/graph")

    assert progress.status_code == 200, progress.text
    assert progress.json()["business_stage"] == "data_ingestion"
    assert progress.json()["current_action_zh"] == (
        "Agent 处理已安全暂停，等待终止任务"
    )


def test_incomplete_frozen_approval_details_are_not_actionable(
    graph_agent_client: TestClient,
) -> None:
    agent_client = graph_agent_client

    async def seed() -> tuple[str, str]:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["teacher"],
                status="running",
                stage="governance",
                workflow_version="agent-graph-v1",
                idempotency_key=str(uuid4()),
                request_hash=str(uuid4()),
            )
            session.add(task)
            await session.flush()
            run = await AgentRuntimeRepository(session).create_run(
                task_id=task.id,
                tenant_id=task.tenant_id,
                conversation_id=None,
                kind=AgentRunKind.SYNC,
                workflow_version="agent-graph-v1",
            )
            run.status = "waiting_human"
            graph = await AgentGraphRepository(session).create_run_state(
                run_id=run.id,
                graph_version="agent-sync-graph-v1",
                initial_node="aggregate_risk",
            )
            finding_id = str(uuid4())
            session.add(
                AgentApprovalGroupRecord(
                    run_id=run.id,
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    group_key="target_extra:teacher:delete:agent-risk-v1",
                    membership_hash="incomplete-membership",
                    finding_ids=[finding_id],
                    issue_kind="target_extra",
                    entity_kind="teacher",
                    operation="delete",
                    policy_version="agent-risk-v1",
                    risk="high",
                    status="pending",
                )
            )
            gate = await AgentGraphRepository(session).record_human_gate(
                graph_run_id=graph.id,
                cursor=graph.cursor,
                gate_kind="high_risk_approval",
                member_ids=(finding_id,),
                content_hash="sha256:" + ("c" * 64),
                status="pending",
            )
            graph.current_node = "wait_high_risk_approvals"
            graph.cursor = 1
            await session.commit()
            return str(task.id), str(gate.id)

    task_id, gate_id = agent_client.portal.call(seed)

    progress = agent_client.get(f"/api/agent/tasks/{task_id}/graph")
    gate = progress.json()["human_gates"][0]
    assert gate["item_count"] == 1
    assert gate["items"] == []
    assert gate["actionable"] is False
    assert gate["unavailable_reason_zh"] == (
        "审批明细不完整，任务不能继续治理，请终止任务后重新发起。"
    )

    decision = agent_client.post(
        f"/api/agent/tasks/{task_id}/graph/gates/{gate_id}/decision",
        json={"decision": "approve"},
    )
    assert decision.status_code == 409
    assert decision.json()["detail"]["code"] == "approval_fact_missing"


def test_student_phone_approval_without_paired_values_is_not_actionable(
    graph_agent_client: TestClient,
) -> None:
    agent_client = graph_agent_client

    async def seed() -> tuple[str, str]:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="running",
                stage="governance",
                workflow_version="agent-graph-v1",
                idempotency_key=str(uuid4()),
                request_hash=str(uuid4()),
            )
            session.add(task)
            await session.flush()
            run = await AgentRuntimeRepository(session).create_run(
                task_id=task.id,
                tenant_id=task.tenant_id,
                conversation_id=None,
                kind=AgentRunKind.SYNC,
                workflow_version="agent-graph-v1",
            )
            run.status = "waiting_human"
            graph = await AgentGraphRepository(session).create_run_state(
                run_id=run.id,
                graph_version="agent-sync-graph-v1",
                initial_node="aggregate_risk",
            )
            snapshots: dict[str, Snapshot] = {}
            for role in ("authoritative", "target"):
                source = SourceFile(
                    task_id=task.id,
                    source_role=role,
                    original_name=f"{role}.csv",
                    storage_name=f"{uuid4()}.csv",
                    storage_path=f"/synthetic/{uuid4()}.csv",
                    sha256=uuid4().hex * 2,
                    size_bytes=1,
                )
                session.add(source)
                await session.flush()
                snapshot = Snapshot(
                    id=uuid4(),
                    task_id=task.id,
                    source_file_id=source.id,
                    source_role=role,
                    schema_version="agent-contract-v1",
                    mapping_version="agent-contract-v1",
                    file_hash=source.sha256,
                    content_hash=uuid4().hex * 2,
                    state="published",
                    summary={},
                )
                session.add(snapshot)
                snapshots[role] = snapshot
            await session.flush()
            batch = AgentModelBatchRecord(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                entity_kind="student",
                input_hash=uuid4().hex * 2,
                item_count=1,
                status="completed",
                output_hash=uuid4().hex * 2,
            )
            target_student = AgentInputRecord(
                run_id=run.id,
                task_id=task.id,
                snapshot_id=snapshots["target"].id,
                tenant_id=task.tenant_id,
                source_role="target",
                stable_locator="csv:12",
                stable_order=1,
                entity_kind="student",
                category="学生",
                name="李明",
                number="S-002",
                class_name="三年级一班",
                phone="13800001234",
                email="student@example.test",
                raw_row_number=12,
                input_hash=uuid4().hex * 2,
            )
            session.add_all((batch, target_student))
            await session.flush()
            work_item = AgentWorkItemRecord(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                source_snapshot_id=snapshots["authoritative"].id,
                target_snapshot_id=snapshots["target"].id,
                subject_input_id=target_student.id,
                entity_kind="student",
                kind="field_difference",
                state="analyzed",
                idempotency_hash=uuid4().hex * 2,
                evidence_hash=uuid4().hex * 2,
            )
            session.add(work_item)
            await session.flush()
            finding = AgentFindingRecord(
                run_id=run.id,
                task_id=task.id,
                work_item_id=work_item.id,
                batch_id=batch.id,
                kind="field_difference",
                category_zh="手机号不一致",
                analysis_zh="手机号需要以第三方权威记录为准。",
                evidence_refs=["paired-record:missing-claim"],
                content_hash=uuid4().hex * 2,
            )
            session.add(finding)
            await session.flush()
            session.add(
                AgentFindingSolutionRecord(
                    finding_id=finding.id,
                    ordinal=1,
                    operation="update",
                    risk="high",
                    solution_zh="修改学生手机号。",
                    recommended=True,
                )
            )
            finding_ids = [str(finding.id)]
            session.add(
                AgentApprovalGroupRecord(
                    run_id=run.id,
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    group_key="field_difference:student:update:missing-pair",
                    membership_hash="missing-paired-values",
                    finding_ids=finding_ids,
                    issue_kind="field_difference",
                    entity_kind="student",
                    operation="update",
                    policy_version="agent-risk-v1",
                    risk="high",
                    status="pending",
                )
            )
            gate = await AgentGraphRepository(session).record_human_gate(
                graph_run_id=graph.id,
                cursor=graph.cursor,
                gate_kind="high_risk_approval",
                member_ids=tuple(finding_ids),
                content_hash="sha256:" + ("d" * 64),
                status="pending",
            )
            graph.current_node = "wait_high_risk_approvals"
            graph.cursor = 1
            await session.commit()
            return str(task.id), str(gate.id)

    task_id, gate_id = agent_client.portal.call(seed)

    progress = agent_client.get(f"/api/agent/tasks/{task_id}/graph")
    gate = progress.json()["human_gates"][0]
    assert len(gate["items"]) == 1
    assert gate["items"][0]["changes"] == []
    assert gate["actionable"] is False
    assert gate["unavailable_reason_zh"] == (
        "审批明细不完整，任务不能继续治理，请终止任务后重新发起。"
    )

    decision = agent_client.post(
        f"/api/agent/tasks/{task_id}/graph/gates/{gate_id}/decision",
        json={"decision": "approve"},
    )
    assert decision.status_code == 409
    assert decision.json()["detail"]["code"] == "approval_fact_missing"


def test_rejected_rollback_gate_requests_safe_termination(
    graph_agent_client: TestClient,
) -> None:
    agent_client = graph_agent_client

    async def seed() -> tuple[str, str]:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="running",
                stage="rollback",
                workflow_version="agent-graph-v1",
                task_kind="rollback",
                idempotency_key=str(uuid4()),
                request_hash=str(uuid4()),
            )
            session.add(task)
            await session.flush()
            run = await AgentRuntimeRepository(session).create_run(
                task_id=task.id,
                tenant_id=task.tenant_id,
                conversation_id=None,
                kind=AgentRunKind.ROLLBACK,
                workflow_version="agent-graph-v1",
            )
            graph = await AgentGraphRepository(session).create_run_state(
                run_id=run.id,
                graph_version="agent-rollback-graph-v1",
                initial_node="wait_restore_conflicts",
            )
            gate = await AgentGraphRepository(session).record_human_gate(
                graph_run_id=graph.id,
                cursor=graph.cursor,
                gate_kind="rollback_conflict",
                member_ids=(str(uuid4()),),
                content_hash="sha256:" + ("b" * 64),
                status="pending",
            )
            graph.current_node = "wait_restore_conflicts"
            graph.cursor = 1
            run.status = "waiting_human"
            await session.commit()
            return str(task.id), str(gate.id)

    task_id, gate_id = agent_client.portal.call(seed)
    rejected = agent_client.post(
        f"/api/agent/tasks/{task_id}/graph/gates/{gate_id}/decision",
        json={"decision": "reject", "reason": "不允许继续回滚"},
    )

    assert rejected.status_code == 200, rejected.text

    async def inspect() -> tuple[bool, str]:
        async with agent_client.app.state.database.session_factory() as session:
            run = await session.scalar(
                select(AgentRunRecord).where(
                    AgentRunRecord.task_id == UUID(task_id)
                )
            )
            assert run is not None
            graph = await session.scalar(
                select(AgentGraphRunRecord).where(
                    AgentGraphRunRecord.run_id == run.id
                )
            )
            assert graph is not None
            return graph.termination_requested, run.status

    termination_requested, run_status = agent_client.portal.call(inspect)
    assert termination_requested is True
    assert run_status == "running"


def test_termination_requires_preview_and_explicit_confirmation(
    graph_agent_client: TestClient,
) -> None:
    agent_client = graph_agent_client

    async def seed() -> str:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="running",
                stage="ingestion",
                workflow_version="agent-graph-v1",
                idempotency_key=str(uuid4()),
                request_hash=str(uuid4()),
            )
            session.add(task)
            await session.flush()
            run = await AgentRuntimeRepository(session).create_run(
                task_id=task.id,
                tenant_id=task.tenant_id,
                conversation_id=None,
                kind=AgentRunKind.SYNC,
                workflow_version="agent-graph-v1",
            )
            await AgentGraphRepository(session).create_run_state(
                run_id=run.id,
                graph_version="agent-sync-graph-v1",
                initial_node="inspect_sources",
            )
            await session.commit()
            return str(task.id)

    task_id = agent_client.portal.call(seed)
    preview = agent_client.post(
        f"/api/agent/tasks/{task_id}/termination-preview"
    )

    assert preview.status_code == 200, preview.text
    gate = preview.json()
    assert gate["kind"] == "termination_confirmation"
    assert gate["status"] == "pending"

    async def termination_requested() -> bool:
        async with agent_client.app.state.database.session_factory() as session:
            run = await session.scalar(
                select(AgentRunRecord).where(
                    AgentRunRecord.task_id == UUID(task_id)
                )
            )
            assert run is not None
            graph = await AgentGraphRepository(
                session
            ).get_run_state_for_agent_run(run.id)
            assert graph is not None
            return graph.termination_requested

    assert agent_client.portal.call(termination_requested) is False
    confirmed = agent_client.post(
        f"/api/agent/tasks/{task_id}/graph/gates/{gate['id']}/decision",
        json={"decision": "approve", "reason": "确认终止任务"},
    )

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "approved"
    assert agent_client.portal.call(termination_requested) is True


@pytest.mark.parametrize(
    ("decision", "expected_run_status", "expected_termination_requested"),
    (
        ("approve", "running", True),
        ("reject", "blocked_model_error", False),
    ),
)
def test_blocked_model_error_allows_termination_confirmation_decision(
    graph_agent_client: TestClient,
    decision: str,
    expected_run_status: str,
    expected_termination_requested: bool,
) -> None:
    agent_client = graph_agent_client

    async def seed() -> str:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="running",
                stage="governance",
                workflow_version="agent-graph-v1",
                idempotency_key=str(uuid4()),
                request_hash=str(uuid4()),
            )
            session.add(task)
            await session.flush()
            run = await AgentRuntimeRepository(session).create_run(
                task_id=task.id,
                tenant_id=task.tenant_id,
                conversation_id=None,
                kind=AgentRunKind.SYNC,
                workflow_version="agent-graph-v1",
            )
            run.status = "blocked_model_error"
            graph = await AgentGraphRepository(session).create_run_state(
                run_id=run.id,
                graph_version="agent-sync-graph-v1",
                initial_node="inspect_sources",
            )
            graph.current_node = "blocked_model_error"
            graph.status = "blocked_model_error"
            await session.commit()
            return str(task.id)

    task_id = agent_client.portal.call(seed)
    preview = agent_client.post(
        f"/api/agent/tasks/{task_id}/termination-preview"
    )
    assert preview.status_code == 200, preview.text

    response = agent_client.post(
        (
            f"/api/agent/tasks/{task_id}/graph/gates/"
            f"{preview.json()['id']}/decision"
        ),
        json={"decision": decision},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == (
        "approved" if decision == "approve" else "rejected"
    )

    async def inspect() -> tuple[str, bool]:
        async with agent_client.app.state.database.session_factory() as session:
            run = await session.scalar(
                select(AgentRunRecord).where(
                    AgentRunRecord.task_id == UUID(task_id)
                )
            )
            assert run is not None
            graph = await AgentGraphRepository(
                session
            ).get_run_state_for_agent_run(run.id)
            assert graph is not None
            return run.status, graph.termination_requested

    assert agent_client.portal.call(inspect) == (
        expected_run_status,
        expected_termination_requested,
    )
    progress = agent_client.get(f"/api/agent/tasks/{task_id}/graph")
    assert progress.status_code == 200, progress.text
    assert (
        progress.json()["termination_requested"]
        is expected_termination_requested
    )


def test_identity_conflict_uses_skill_model_and_requires_second_confirmation(
    graph_agent_client: TestClient,
) -> None:
    agent_client = graph_agent_client

    async def seed() -> tuple[str, str, str, str]:
        async with agent_client.app.state.database.session_factory() as session:
            task = ReconciliationTask(
                tenant_id="school-1",
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="running",
                stage="analysis",
                workflow_version="agent-graph-v1",
                idempotency_key=str(uuid4()),
                request_hash=str(uuid4()),
            )
            session.add(task)
            await session.flush()
            snapshots: dict[str, Snapshot] = {}
            for role in ("authoritative", "target"):
                source = SourceFile(
                    task_id=task.id,
                    source_role=role,
                    original_name=f"{role}.csv",
                    storage_name=f"{uuid4()}.csv",
                    storage_path=f"/synthetic/{role}.csv",
                    sha256=role[0] * 64,
                    size_bytes=10,
                    detected_encoding="utf-8",
                )
                session.add(source)
                await session.flush()
                snapshot = Snapshot(
                    id=uuid4(),
                    task_id=task.id,
                    source_file_id=source.id,
                    source_role=role,
                    schema_version="agent-contract-v1",
                    mapping_version="agent-contract-v1",
                    file_hash=source.sha256,
                    content_hash=role[-1] * 64,
                    state="published",
                    summary={},
                )
                session.add(snapshot)
                snapshots[role] = snapshot
            run = await AgentRuntimeRepository(session).create_run(
                task_id=task.id,
                tenant_id=task.tenant_id,
                conversation_id=None,
                kind=AgentRunKind.SYNC,
                workflow_version="agent-graph-v1",
            )
            run.phase = AgentPhase.CLARIFY_IDENTITY_CONFLICTS.value
            run.status = "waiting_human"
            graph = await AgentGraphRepository(session).create_run_state(
                run_id=run.id,
                graph_version="agent-sync-graph-v1",
                initial_node="resolve_identity_conflicts",
            )
            target_input = AgentInputRecord(
                run_id=run.id,
                task_id=task.id,
                snapshot_id=snapshots["target"].id,
                tenant_id=task.tenant_id,
                source_role="target",
                stable_locator="csv:1",
                stable_order=1,
                entity_kind="student",
                category="student",
                name="测试学生",
                number="S-009",
                class_name="一年级一班",
                phone="13800000009",
                email="student@example.test",
                raw_row_number=1,
                input_hash="e" * 64,
            )
            session.add(target_input)
            await session.flush()
            work_item = AgentWorkItemRecord(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                source_snapshot_id=snapshots["authoritative"].id,
                target_snapshot_id=snapshots["target"].id,
                subject_input_id=target_input.id,
                entity_kind="student",
                kind="identity_conflict",
                state="awaiting_clarification",
                idempotency_hash="f" * 64,
                evidence_hash="1" * 64,
            )
            session.add(work_item)
            await session.flush()
            candidate_id = uuid4()
            second_candidate_id = uuid4()
            clarification = await AgentGovernanceRepository(
                session
            ).create_clarification(
                run=run,
                task=task,
                work_item_id=work_item.id,
                candidates=(
                    {
                        "id": str(candidate_id),
                        "entity_kind": "student",
                        "category": "student",
                        "name": "测试学生",
                        "number": "S-001",
                        "class_name": "一年级一班",
                        "phone": "138****0001",
                        "email": "student@example.test",
                    },
                    {
                        "id": str(second_candidate_id),
                        "entity_kind": "student",
                        "category": "student",
                        "name": "测试学生二号",
                        "number": "S-002",
                        "class_name": "一年级二班",
                        "phone": "13812345678*",
                        "email": "secret.person@example.test",
                    },
                ),
                allowed_outcomes=("use_candidate", "target_extra"),
            )
            await AgentGraphRepository(session).record_human_gate(
                graph_run_id=graph.id,
                cursor=graph.cursor,
                gate_kind="identity_conflict",
                member_ids=(str(clarification.id),),
                content_hash="sha256:" + ("9" * 64),
                status="pending",
            )
            graph.cursor = 1
            await session.commit()
            return (
                str(task.id),
                str(clarification.id),
                str(candidate_id),
                str(second_candidate_id),
            )

    task_id, clarification_id, candidate_id, second_candidate_id = (
        agent_client.portal.call(seed)
    )
    progress = agent_client.get(f"/api/agent/tasks/{task_id}/graph")
    assert progress.status_code == 200, progress.text
    identity_gate = next(
        gate
        for gate in progress.json()["human_gates"]
        if gate["kind"] == "identity_conflict"
    )
    assert identity_gate["conflicts"] == [
        {
            "clarification_id": clarification_id,
            "status": "pending",
            "summary_zh": "唯一身份字段命中了多个第三方权威候选，Agent 无法安全选择。",
            "subject": {
                "entity_kind": "student",
                "category": "student",
                "name": "测试学生",
                "number": "S-009",
                "class_name": "一年级一班",
                "phone_masked": "***0009",
                "email_masked": "s***@example.test",
            },
            "candidates": [
                {
                    "entity_kind": "student",
                    "category": "student",
                    "name": "测试学生",
                    "number": "S-001",
                    "class_name": "一年级一班",
                    "phone_masked": "***0001",
                    "email_masked": "s***@example.test",
                },
                {
                    "entity_kind": "student",
                    "category": "student",
                    "name": "测试学生二号",
                    "number": "S-002",
                    "class_name": "一年级二班",
                    "phone_masked": "***5678",
                    "email_masked": "s***@example.test",
                },
            ],
            "allowed_outcomes": ["use_candidate", "target_extra"],
            "interpretation_zh": None,
        }
    ]

    async def replace_candidates(candidates: list[dict[str, str]]) -> None:
        async with agent_client.app.state.database.session_factory() as session:
            record = await session.get(
                AgentClarificationRecord,
                UUID(clarification_id),
            )
            assert record is not None
            record.masked_candidates = candidates
            await session.commit()

    original_candidates = [
        {
            "id": candidate_id,
            "entity_kind": "student",
            "category": "student",
            "name": "测试学生",
            "number": "S-001",
            "class_name": "一年级一班",
            "phone": "138****0001",
            "email": "student@example.test",
        },
        {
            "id": second_candidate_id,
            "entity_kind": "student",
            "category": "student",
            "name": "测试学生二号",
            "number": "S-002",
            "class_name": "一年级二班",
            "phone": "13812345678*",
            "email": "secret.person@example.test",
        },
    ]
    agent_client.portal.call(replace_candidates, [])
    incomplete_progress = agent_client.get(f"/api/agent/tasks/{task_id}/graph")
    incomplete_gate = next(
        gate
        for gate in incomplete_progress.json()["human_gates"]
        if gate["kind"] == "identity_conflict"
    )
    assert incomplete_gate["actionable"] is False
    incomplete = agent_client.post(
        f"/api/agent/tasks/{task_id}/clarification",
        json={"message": "请按希沃多余处理。"},
    )
    assert incomplete.status_code == 409, incomplete.text
    assert incomplete.json()["detail"]["code"] == "incomplete_conflict_evidence"
    agent_client.portal.call(
        replace_candidates,
        [
            {"id": candidate_id, "entity_kind": "student"},
            {"id": second_candidate_id, "entity_kind": "student"},
        ],
    )
    id_only_progress = agent_client.get(f"/api/agent/tasks/{task_id}/graph")
    id_only_gate = next(
        gate
        for gate in id_only_progress.json()["human_gates"]
        if gate["kind"] == "identity_conflict"
    )
    assert id_only_gate["actionable"] is False
    agent_client.portal.call(replace_candidates, original_candidates)
    resource_id = f"identity-conflict:{clarification_id}"
    draft = {
        "schema_version": "agent-contract-v1",
        "conflict_id": clarification_id,
        "decision": "select_candidate",
        "selected_candidate_id": candidate_id,
        "interpretation_zh": "我理解为选择编号 S-001 的候选，确认后继续。",
        "requires_second_confirmation": True,
    }

    class ConflictProvider:
        def __init__(self) -> None:
            self.requests: list[str] = []
            self.outputs = [
                {
                    "result": {
                        "tool_call": {
                            "name": "read_frozen_conflict",
                            "arguments": {"resource_id": resource_id},
                        }
                    }
                },
                {
                    "result": {
                        "tool_call": {
                            "name": "submit_conflict_interpretation",
                            "arguments": {"resource_id": resource_id, **draft},
                        }
                    }
                },
                {"result": draft},
            ]

        async def complete_json_once(self, _request) -> LLMResponse:
            self.requests.append(str(_request))
            return LLMResponse(
                output=self.outputs.pop(0),
                provider="scripted",
                model="conflict-model",
                request_id=str(uuid4()),
            )

    conflict_provider = ConflictProvider()
    agent_client.app.state.graph_skill_provider = conflict_provider
    interpreted = agent_client.post(
        f"/api/agent/tasks/{task_id}/clarification",
        json={"message": "请选择编号 S-001 的候选。"},
    )

    assert interpreted.status_code == 200, interpreted.text
    assert interpreted.json() == {
        "decision_id": clarification_id,
        "status": "interpreted",
        "task_id": task_id,
        "decision": "select_candidate",
        "selected_candidate_id": candidate_id,
        "interpretation_zh": "我理解为选择编号 S-001 的候选，确认后继续。",
        "requires_second_confirmation": True,
    }
    model_visible_payload = "\n".join(conflict_provider.requests)
    assert "secret.person@example.test" not in model_visible_payload
    assert "13812345678" not in model_visible_payload
    assert "s***@example.test" in model_visible_payload
    assert "***5678" in model_visible_payload
    interpreted_progress = agent_client.get(f"/api/agent/tasks/{task_id}/graph")
    interpreted_conflict = next(
        gate
        for gate in interpreted_progress.json()["human_gates"]
        if gate["kind"] == "identity_conflict"
    )["conflicts"][0]
    assert interpreted_conflict["status"] == "interpreted"
    assert (
        interpreted_conflict["interpretation_zh"]
        == "我理解为选择编号 S-001 的候选，确认后继续。"
    )

    agent_client.app.state.graph_skill_provider = ConflictProvider()
    reinterpreted = agent_client.post(
        f"/api/agent/tasks/{task_id}/clarification",
        json={"message": "重新确认：请选择第三方候选 A。"},
    )
    assert reinterpreted.status_code == 200, reinterpreted.text
    assert reinterpreted.json()["decision_id"] == clarification_id
    assert reinterpreted.json()["status"] == "interpreted"

    confirmed = agent_client.post(
        f"/api/agent/tasks/{task_id}/clarification/{clarification_id}/confirm",
        json={},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
