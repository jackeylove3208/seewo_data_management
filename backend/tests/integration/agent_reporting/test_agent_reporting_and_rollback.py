import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.agent_reporting.service import AgentReportingService
from app.agent_runtime.csv_governance_handlers import build_agent_report_facts
from app.agent_runtime.csv_rollback_handlers import _rollback_operations
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.service import AgentSupervisorService
from app.agent_runtime.state_machine import AgentRunKind
from app.core.security import OperatorContext
from app.models.agent_analysis import (
    AgentFindingRecord,
    AgentGovernanceOperationRecord,
    AgentGovernancePlanRecord,
    AgentInputMarkRecord,
    AgentInputRecord,
    AgentModelBatchRecord,
    AgentWorkItemRecord,
)
from app.models.reconciliation import ReconciliationTask
from app.models.reporting import AgentReportRecord
from app.models.snapshots import Snapshot, SourceFile


def _task(
    tenant_id: str = "school-1",
    *,
    kind: str = "sync",
    target: dict[str, str] | None = None,
) -> ReconciliationTask:
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
        agent_intent={"target": target} if target is not None else None,
        idempotency_key=str(uuid4()),
        request_hash="a" * 64,
    )


def _verified_mutation(operation_id: str = "op-1") -> dict[str, object]:
    return {
        "id": operation_id,
        "status": "succeeded",
        "verification": {"valid": True},
    }


@pytest.mark.asyncio
async def test_terminal_report_includes_safe_input_diagnostics(session) -> None:
    task = _task()
    session.add(task)
    await session.flush()
    run = await AgentRuntimeRepository(session).create_run(
        task_id=task.id,
        tenant_id=task.tenant_id,
        conversation_id=None,
        kind=AgentRunKind.SYNC,
    )
    inputs: list[AgentInputRecord] = []
    for order, role in enumerate(("authoritative", "target"), start=1):
        source_file = SourceFile(
            task_id=task.id,
            source_role=role,
            original_name=f"{role}.jsonl",
            storage_name=f"{uuid4()}.jsonl",
            storage_path=f"/tmp/{role}.jsonl",
            sha256=str(order) * 64,
            size_bytes=1,
        )
        session.add(source_file)
        await session.flush()
        snapshot = Snapshot(
            id=uuid4(),
            task_id=task.id,
            source_file_id=source_file.id,
            source_role=role,
            schema_version="v1",
            mapping_version="v1",
            file_hash=source_file.sha256,
            content_hash=str(order + 2) * 64,
            summary={},
        )
        session.add(snapshot)
        await session.flush()
        inputs.append(
            AgentInputRecord(
                run_id=run.id,
                task_id=task.id,
                snapshot_id=snapshot.id,
                tenant_id=task.tenant_id,
                source_role=role,
                stable_locator=f"{role}:person-1",
                stable_order=1,
                entity_kind="teacher",
                name="合成教师",
                phone="13800000000",
                input_hash=str(order + 4) * 64,
            )
        )
    session.add_all(inputs)
    await session.flush()
    session.add_all(
        [
            AgentInputMarkRecord(
                input_record_id=inputs[0].id,
                reason_code="authority_field_unavailable",
                affected_fields=["phone", "email"],
                inclusion_state="included",
                report_disposition="warning",
                safe_evidence={"visibility": "hidden"},
            ),
            AgentInputMarkRecord(
                input_record_id=inputs[0].id,
                reason_code="authority_identity_absent",
                affected_fields=["number", "phone", "email"],
                inclusion_state="anomaly",
                report_disposition="mandatory",
                safe_evidence={"identity_keys": "absent"},
            ),
            AgentInputMarkRecord(
                input_record_id=inputs[1].id,
                reason_code="target_row_invalid",
                affected_fields=["name"],
                inclusion_state="excluded",
                report_disposition="warning",
                safe_evidence={"validation": "failed"},
            ),
        ]
    )
    await session.flush()

    facts = await build_agent_report_facts(session, run_id=run.id)

    assert facts["input_diagnostics"] == {
        "marked_input_counts": {"authoritative": 1, "target": 1},
        "reason_counts": {
            "authority_field_unavailable": 1,
            "authority_identity_absent": 1,
            "target_row_invalid": 1,
        },
        "unavailable_field_counts": {"email": 1, "phone": 1},
        "identity_absent_count": 1,
    }
    assert facts["excluded_findings"][0] == {
        "source_role": "authoritative",
        "reason": "authority_field_unavailable",
        "affected_fields": ["email", "phone"],
        "inclusion_state": "included",
        "disposition": "warning",
        "safe_evidence": {"visibility": "hidden"},
    }
    assert "13800000000" not in json.dumps(facts, ensure_ascii=False)


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
    assert report.facts["rollback_evidence"]["eligible"] is False
    assert report.rollback_eligible is False
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
async def test_completed_rollback_locks_only_the_same_target_data_source(session) -> None:
    csv_target = {"kind": "csv", "upload_id": str(uuid4())}
    mysql_target = {"kind": "database", "configuration_id": "mysql-school-1"}
    older_csv = _task(target=csv_target)
    latest_csv = _task(target=csv_target)
    mysql_sync = _task(target=mysql_target)
    session.add_all([older_csv, latest_csv, mysql_sync])
    await session.flush()
    service = AgentReportingService(session)
    for task in (older_csv, latest_csv, mysql_sync):
        await service.generate(
            task_id=task.id,
            tenant_id=task.tenant_id,
            kind="sync",
            terminal_state="completed",
            facts={"mutations": [_verified_mutation(str(task.id))]},
        )

    rollback = await service.create_rollback_task(
        source_task_id=latest_csv.id,
        tenant_id=latest_csv.tenant_id,
        requested_by="operator-1",
        target_version_id=uuid4(),
    )
    await service.generate(
        task_id=rollback.task_id,
        tenant_id=latest_csv.tenant_id,
        kind="rollback",
        terminal_state="completed",
        facts={"mutations": [_verified_mutation("rollback-op")]},
    )

    with pytest.raises(ValueError, match="already rolled back"):
        await service.create_rollback_task(
            source_task_id=older_csv.id,
            tenant_id=older_csv.tenant_id,
            requested_by="operator-1",
            target_version_id=uuid4(),
        )

    mysql_preview = await service.create_rollback_task(
        source_task_id=mysql_sync.id,
        tenant_id=mysql_sync.tenant_id,
        requested_by="operator-1",
        target_version_id=uuid4(),
    )
    assert mysql_preview.state == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_only_a_fully_successful_sync_reopens_the_rollback_cycle(session) -> None:
    csv_target = {"kind": "csv", "upload_id": str(uuid4())}
    first_sync = _task(target=csv_target)
    session.add(first_sync)
    await session.flush()
    service = AgentReportingService(session)
    await service.generate(
        task_id=first_sync.id,
        tenant_id=first_sync.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={"mutations": [_verified_mutation()]},
    )
    rollback = await service.create_rollback_task(
        source_task_id=first_sync.id,
        tenant_id=first_sync.tenant_id,
        requested_by="operator-1",
        target_version_id=uuid4(),
    )
    await service.generate(
        task_id=rollback.task_id,
        tenant_id=first_sync.tenant_id,
        kind="rollback",
        terminal_state="completed",
        facts={"mutations": [_verified_mutation("rollback-op")]},
    )

    partial_sync = _task(target=csv_target)
    session.add(partial_sync)
    await session.flush()
    await service.generate(
        task_id=partial_sync.id,
        tenant_id=partial_sync.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={
            "mutations": [
                _verified_mutation("partial-ok"),
                {
                    "id": "partial-failed",
                    "status": "failed",
                    "verification": {"valid": False},
                },
            ]
        },
    )
    with pytest.raises(ValueError, match="already rolled back"):
        await service.create_rollback_task(
            source_task_id=partial_sync.id,
            tenant_id=partial_sync.tenant_id,
            requested_by="operator-1",
            target_version_id=uuid4(),
        )

    successful_sync = _task(target=csv_target)
    session.add(successful_sync)
    await session.flush()
    successful_report = await service.generate(
        task_id=successful_sync.id,
        tenant_id=successful_sync.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={"mutations": [_verified_mutation("next-cycle")]},
    )
    assert successful_report.rollback_eligible is True
    next_preview = await service.create_rollback_task(
        source_task_id=successful_sync.id,
        tenant_id=successful_sync.tenant_id,
        requested_by="operator-1",
        target_version_id=uuid4(),
    )
    assert next_preview.state == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_stale_rollback_preview_cannot_be_confirmed_after_the_cycle_was_consumed(
    session,
) -> None:
    csv_target = {"kind": "csv", "upload_id": str(uuid4())}
    stale_source = _task(target=csv_target)
    winning_source = _task(target=csv_target)
    session.add_all([stale_source, winning_source])
    await session.flush()
    service = AgentReportingService(session)
    for task in (stale_source, winning_source):
        await service.generate(
            task_id=task.id,
            tenant_id=task.tenant_id,
            kind="sync",
            terminal_state="completed",
            facts={"mutations": [_verified_mutation(str(task.id))]},
        )
    stale_preview = await service.create_rollback_task(
        source_task_id=stale_source.id,
        tenant_id=stale_source.tenant_id,
        requested_by="operator-1",
        target_version_id=uuid4(),
    )
    winning_preview = await service.create_rollback_task(
        source_task_id=winning_source.id,
        tenant_id=winning_source.tenant_id,
        requested_by="operator-1",
        target_version_id=uuid4(),
    )
    await service.generate(
        task_id=winning_preview.task_id,
        tenant_id=winning_source.tenant_id,
        kind="rollback",
        terminal_state="completed",
        facts={"mutations": [_verified_mutation("winning-rollback")]},
    )

    with pytest.raises(ValueError, match="already rolled back"):
        await AgentSupervisorService(
            session,
            operator=OperatorContext(
                operator_id="operator-1",
                tenant_id=stale_source.tenant_id,
            ),
        ).confirm_rollback(task_id=stale_preview.task_id)


@pytest.mark.asyncio
async def test_completed_rollback_before_cycle_tracking_is_still_locked(session) -> None:
    csv_target = {"kind": "csv", "upload_id": str(uuid4())}
    source = _task(target=csv_target)
    rollback = _task(kind="rollback", target=csv_target)
    rollback.parent_task_id = source.id
    rollback.agent_intent = {
        "source_task_id": str(source.id),
        "target": csv_target,
    }
    now = datetime.now(UTC)
    source_report = AgentReportRecord(
        task_id=source.id,
        tenant_id=source.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={
            "output_target_version_id": str(uuid4()),
            "mutations": [_verified_mutation("historic-sync")],
            "rollback_evidence": {
                "eligible": True,
                "successful_mutation_ids": ["historic-sync"],
                "successful_mutations": [_verified_mutation("historic-sync")],
            },
        },
        facts_hash="b" * 64,
        content={},
        rollback_eligible=True,
        deletion_eligible=False,
        generated_by="historic-test",
        created_at=now,
    )
    rollback_report = AgentReportRecord(
        task_id=rollback.id,
        tenant_id=rollback.tenant_id,
        kind="rollback",
        terminal_state="completed",
        facts={"mutations": [_verified_mutation("historic-rollback")]},
        facts_hash="c" * 64,
        content={},
        rollback_eligible=False,
        deletion_eligible=False,
        generated_by="historic-test",
        created_at=now + timedelta(seconds=1),
    )
    session.add_all([source, rollback])
    await session.flush()
    session.add_all([source_report, rollback_report])
    await session.flush()

    with pytest.raises(ValueError, match="already rolled back"):
        await AgentReportingService(session).create_rollback_task(
            source_task_id=source.id,
            tenant_id=source.tenant_id,
            requested_by="operator-1",
            target_version_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_legacy_partial_report_is_revalidated_before_rollback(session) -> None:
    source = _task(
        target={"kind": "csv", "upload_id": str(uuid4())},
    )
    session.add(source)
    await session.flush()
    session.add(
        AgentReportRecord(
            task_id=source.id,
            tenant_id=source.tenant_id,
            kind="sync",
            terminal_state="partial",
            facts={
                "output_target_version_id": str(uuid4()),
                "mutations": [
                    _verified_mutation("legacy-partial-ok"),
                    {
                        "id": "legacy-partial-failed",
                        "status": "failed",
                        "verification": {"valid": False},
                    },
                ],
            },
            facts_hash="d" * 64,
            content={},
            rollback_eligible=True,
            deletion_eligible=False,
            generated_by="legacy-test",
        )
    )
    await session.flush()

    with pytest.raises(ValueError, match="not eligible"):
        await AgentReportingService(session).create_rollback_task(
            source_task_id=source.id,
            tenant_id=source.tenant_id,
            requested_by="operator-1",
            target_version_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_rollback_preview_cannot_cross_a_new_successful_sync_generation(
    session,
) -> None:
    csv_target = {"kind": "csv", "upload_id": str(uuid4())}
    first_sync = _task(target=csv_target)
    session.add(first_sync)
    await session.flush()
    reporting = AgentReportingService(session)
    await reporting.generate(
        task_id=first_sync.id,
        tenant_id=first_sync.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={"mutations": [_verified_mutation("generation-1")]},
    )
    old_preview = await reporting.create_rollback_task(
        source_task_id=first_sync.id,
        tenant_id=first_sync.tenant_id,
        requested_by="operator-1",
        target_version_id=uuid4(),
    )

    second_sync = _task(target=csv_target)
    session.add(second_sync)
    await session.flush()
    await reporting.generate(
        task_id=second_sync.id,
        tenant_id=second_sync.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={"mutations": [_verified_mutation("generation-2")]},
    )

    with pytest.raises(ValueError, match="sync cycle changed"):
        await AgentSupervisorService(
            session,
            operator=OperatorContext(
                operator_id="operator-1",
                tenant_id=first_sync.tenant_id,
            ),
        ).confirm_rollback(task_id=old_preview.task_id)


@pytest.mark.asyncio
async def test_api_target_uses_the_same_once_per_cycle_rollback_rule(session) -> None:
    api_target = {"kind": "api", "configuration_id": "sis-api-school-1"}
    source = _task(target=api_target)
    session.add(source)
    await session.flush()
    reporting = AgentReportingService(session)
    await reporting.generate(
        task_id=source.id,
        tenant_id=source.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={"mutations": [_verified_mutation("api-sync")]},
    )
    rollback = await reporting.create_rollback_task(
        source_task_id=source.id,
        tenant_id=source.tenant_id,
        requested_by="operator-1",
        target_version_id=uuid4(),
    )
    await reporting.generate(
        task_id=rollback.task_id,
        tenant_id=source.tenant_id,
        kind="rollback",
        terminal_state="completed",
        facts={"mutations": [_verified_mutation("api-rollback")]},
    )

    with pytest.raises(ValueError, match="already rolled back"):
        await reporting.create_rollback_task(
            source_task_id=source.id,
            tenant_id=source.tenant_id,
            requested_by="operator-1",
            target_version_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_legacy_preview_without_generation_fails_closed_after_new_sync(
    session,
) -> None:
    csv_target = {"kind": "csv", "upload_id": str(uuid4())}
    first_sync = _task(target=csv_target)
    session.add(first_sync)
    await session.flush()
    reporting = AgentReportingService(session)
    await reporting.generate(
        task_id=first_sync.id,
        tenant_id=first_sync.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={"mutations": [_verified_mutation("legacy-preview-source")]},
    )
    preview = await reporting.create_rollback_task(
        source_task_id=first_sync.id,
        tenant_id=first_sync.tenant_id,
        requested_by="operator-1",
        target_version_id=uuid4(),
    )
    rollback_task = await session.get(ReconciliationTask, preview.task_id)
    assert rollback_task is not None
    rollback_task.agent_intent = {
        key: value
        for key, value in (rollback_task.agent_intent or {}).items()
        if key != "rollback_cycle_generation"
    }

    next_sync = _task(target=csv_target)
    session.add(next_sync)
    await session.flush()
    await reporting.generate(
        task_id=next_sync.id,
        tenant_id=next_sync.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={"mutations": [_verified_mutation("next-sync")]},
    )

    with pytest.raises(ValueError, match="missing sync cycle"):
        await reporting.create_rollback_task(
            source_task_id=first_sync.id,
            tenant_id=first_sync.tenant_id,
            requested_by="operator-1",
            target_version_id=preview.target_version_id,
        )
    with pytest.raises(ValueError, match="missing sync cycle"):
        await AgentSupervisorService(
            session,
            operator=OperatorContext(
                operator_id="operator-1",
                tenant_id=first_sync.tenant_id,
            ),
        ).confirm_rollback(task_id=preview.task_id)


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
