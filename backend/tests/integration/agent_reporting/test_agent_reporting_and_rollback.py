from uuid import uuid4

import pytest

from app.agent_reporting.service import AgentReportingService
from app.agent_runtime.csv_governance_handlers import build_agent_report_facts
from app.agent_runtime.csv_rollback_handlers import _rollback_operations
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.models.agent_analysis import (
    AgentFindingRecord,
    AgentGovernanceOperationRecord,
    AgentGovernancePlanRecord,
    AgentInputRecord,
    AgentModelBatchRecord,
    AgentWorkItemRecord,
)
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile


def _task(tenant_id: str = "school-1", *, kind: str = "sync") -> ReconciliationTask:
    return ReconciliationTask(
        id=uuid4(),
        tenant_id=tenant_id,
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        status="completed",
        stage="terminal",
        workflow_version="new-agent-v1",
        task_kind=kind,
        idempotency_key=str(uuid4()),
        request_hash="a" * 64,
    )


@pytest.mark.asyncio
async def test_agent_report_persists_terminal_facts_and_keeps_narrative_out_of_rollback_evidence(
    session,
) -> None:
    task = _task()
    session.add(task)
    await session.flush()

    report = await AgentReportingService(session).generate(
        task_id=task.id,
        tenant_id=task.tenant_id,
        kind="sync",
        terminal_state="partial",
        facts={
            "invalid_rows": [{"source": "authority", "row": 3}],
            "excluded_findings": [{"id": "finding-1", "reason": "rejected"}],
            "mutations": [
                {"id": "op-1", "status": "succeeded", "verification": {"valid": True}},
                {"id": "op-2", "status": "failed", "verification": {"valid": False}},
            ],
        },
        narrative={"summary": "建议回滚 op-1"},
    )

    assert report.facts["mutation_summary"] == {
        "succeeded": 1,
        "failed": 1,
        "blocked": 0,
        "rejected": 0,
        "skipped": 0,
    }
    assert report.facts["rollback_evidence"]["successful_mutation_ids"] == ["op-1"]
    assert report.facts["rollback_evidence"]["eligible"] is True
    assert report.content["narrative"]["summary"] == "建议回滚 op-1"
    assert report.updated_at is not None


@pytest.mark.asyncio
async def test_agent_report_marks_abnormal_input_ineligible_for_rollback(session) -> None:
    task = _task()
    session.add(task)
    await session.flush()

    report = await AgentReportingService(session).generate(
        task_id=task.id,
        tenant_id=task.tenant_id,
        kind="sync",
        terminal_state="abnormal_input",
        facts={
            "mutations": [
                {"id": "op-1", "status": "succeeded", "verification": {"valid": True}}
            ]
        },
    )

    assert report.facts["rollback_evidence"]["eligible"] is False
    assert report.facts["rollback_evidence"]["reason"] == "abnormal_input"


@pytest.mark.asyncio
async def test_agent_history_is_tenant_scoped_and_deletion_uses_verified_mutations(session) -> None:
    first = _task()
    second = _task("school-2")
    session.add_all([first, second])
    await session.flush()
    service = AgentReportingService(session)
    await service.generate(
        task_id=first.id,
        tenant_id=first.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={"mutations": []},
    )
    await service.generate(
        task_id=second.id,
        tenant_id=second.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={
            "mutations": [
                {"id": "op-1", "status": "succeeded", "verification": {"valid": True}}
            ]
        },
    )

    history = await service.history(tenant_id="school-1", limit=10)
    assert [item.task_id for item in history.items] == [first.id]
    assert history.items[0].deletion_eligible is True


@pytest.mark.asyncio
async def test_rollback_is_a_new_task_and_is_blocked_by_another_active_school_lock(session) -> None:
    original = _task()
    session.add(original)
    await session.flush()
    service = AgentReportingService(session)
    await service.generate(
        task_id=original.id,
        tenant_id=original.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={
            "mutations": [
                {"id": "op-1", "status": "succeeded", "verification": {"valid": True}}
            ]
        },
    )

    preview = await service.create_rollback_task(
        source_task_id=original.id,
        tenant_id=original.tenant_id,
        requested_by="operator-1",
        target_version_id=uuid4(),
    )

    assert preview.task_id != original.id
    assert preview.task_kind == "rollback"
    assert preview.report_id is None
    assert preview.state == "awaiting_confirmation"
    assert preview.requires_confirmation is True
    assert preview.message_zh == "请确认是否创建独立回滚任务。"
    assert [operation["compensation_for"] for operation in preview.operations] == ["op-1"]

    replay = await service.create_rollback_task(
        source_task_id=original.id,
        tenant_id=original.tenant_id,
        requested_by="operator-1",
        target_version_id=preview.target_version_id,
    )
    assert replay.task_id == preview.task_id
    assert replay.state == "awaiting_confirmation"
    assert replay.requires_confirmation is True

    rollback_run = await AgentRuntimeRepository(session).get_run_for_task(
        preview.task_id
    )
    assert rollback_run is not None
    rollback_run.phase = "terminal"
    rollback_run.status = "completed"
    await session.flush()

    completed = await service.create_rollback_task(
        source_task_id=original.id,
        tenant_id=original.tenant_id,
        requested_by="operator-1",
        target_version_id=preview.target_version_id,
    )

    assert completed.task_id == preview.task_id
    assert completed.state == "completed"
    assert completed.requires_confirmation is False
    assert completed.message_zh == "该任务已完成回滚。"


@pytest.mark.asyncio
async def test_report_dependencies_reach_rollback_task_and_reverse_the_execution_dag(
    session,
) -> None:
    original = _task()
    session.add(original)
    await session.flush()
    run = await AgentRuntimeRepository(session).create_run(
        task_id=original.id,
        tenant_id=original.tenant_id,
        conversation_id=None,
        kind=AgentRunKind.SYNC,
    )
    source_file = SourceFile(
        task_id=original.id,
        source_role="target",
        original_name="target.csv",
        storage_name=f"{uuid4()}.csv",
        storage_path="/tmp/synthetic-target.csv",
        sha256="1" * 64,
        size_bytes=1,
        detected_encoding="utf-8",
    )
    session.add(source_file)
    await session.flush()
    snapshot = Snapshot(
        id=uuid4(),
        task_id=original.id,
        source_file_id=source_file.id,
        source_role="target",
        schema_version="v1",
        mapping_version="v1",
        file_hash="1" * 64,
        content_hash="2" * 64,
        summary={},
    )
    session.add(snapshot)
    await session.flush()
    subject = AgentInputRecord(
        run_id=run.id,
        task_id=original.id,
        snapshot_id=snapshot.id,
        tenant_id=original.tenant_id,
        source_role="target",
        stable_locator="csv:2",
        stable_order=1,
        entity_kind="student",
        category="student",
        name="张三",
        number="S001",
        class_name="一班",
        phone="A",
        email="student@example.test",
        raw_row_number=2,
        input_hash="3" * 64,
    )
    session.add(subject)
    await session.flush()
    work_items = [
        AgentWorkItemRecord(
            run_id=run.id,
            task_id=original.id,
            tenant_id=original.tenant_id,
            source_snapshot_id=snapshot.id,
            target_snapshot_id=snapshot.id,
            subject_input_id=subject.id,
            entity_kind="student",
            kind="field_difference",
            state="analyzed",
            idempotency_hash=str(index) * 64,
            evidence_hash=str(index + 2) * 64,
        )
        for index in (4, 5)
    ]
    batch = AgentModelBatchRecord(
        run_id=run.id,
        task_id=original.id,
        tenant_id=original.tenant_id,
        entity_kind="student",
        input_hash="6" * 64,
        item_count=2,
        status="completed",
    )
    session.add_all([*work_items, batch])
    await session.flush()
    findings = [
        AgentFindingRecord(
            run_id=run.id,
            task_id=original.id,
            work_item_id=work.id,
            batch_id=batch.id,
            kind="field_difference",
            category_zh="字段不一致",
            analysis_zh="synthetic dependency fact",
            evidence_refs=["synthetic"],
            content_hash=str(index) * 64,
        )
        for index, work in zip((7, 8), work_items, strict=True)
    ]
    session.add_all(findings)
    await session.flush()
    parent_id = uuid4()
    child_id = uuid4()
    plan = AgentGovernancePlanRecord(
        run_id=run.id,
        task_id=original.id,
        tenant_id=original.tenant_id,
        source_snapshot_id=snapshot.id,
        target_snapshot_id=snapshot.id,
        target_version="target-v1",
        finding_ids=[str(item.id) for item in findings],
        operations=[],
        content_hash="9" * 64,
        status="succeeded",
        compiled_by="test",
    )
    session.add(plan)
    await session.flush()
    operations = [
        AgentGovernanceOperationRecord(
            id=parent_id,
            plan_id=plan.id,
            run_id=run.id,
            task_id=original.id,
            finding_id=findings[0].id,
            operation_type="update",
            entity_kind="student",
            target_source_identifier="csv:2",
            before={"phone": "A"},
            after={"phone": "B"},
            dependencies=[],
            risk="medium",
            status="succeeded",
            attempt_count=1,
            actual_after={"phone": "B"},
            verification={"valid": True},
        ),
        AgentGovernanceOperationRecord(
            id=child_id,
            plan_id=plan.id,
            run_id=run.id,
            task_id=original.id,
            finding_id=findings[1].id,
            operation_type="update",
            entity_kind="student",
            target_source_identifier="csv:2",
            before={"email": "old@example.test"},
            after={"email": "new@example.test"},
            dependencies=[str(parent_id)],
            risk="medium",
            status="succeeded",
            attempt_count=1,
            actual_after={"email": "new@example.test"},
            verification={"valid": True},
        ),
    ]
    session.add_all(operations)
    await session.flush()

    facts = await build_agent_report_facts(session, run_id=run.id)
    report = await AgentReportingService(session).generate(
        task_id=original.id,
        tenant_id=original.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts=facts,
    )
    preview = await AgentReportingService(session).create_rollback_task(
        source_task_id=original.id,
        tenant_id=original.tenant_id,
        requested_by="operator-1",
        target_version_id=uuid4(),
    )
    rollback_operations = _rollback_operations(
        tuple(dict(item) for item in preview.operations),
        target_version="sha256:test",
    )

    reported = {
        str(item["id"]): item["dependencies"]
        for item in report.facts["rollback_evidence"]["successful_mutations"]
    }
    assert reported == {
        str(parent_id): [],
        str(child_id): [str(parent_id)],
    }
    assert [item.finding_id for item in rollback_operations] == [
        child_id,
        parent_id,
    ]
    assert rollback_operations[1].dependencies == frozenset(
        {rollback_operations[0].id}
    )
