from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent_runtime.repository import AgentRuntimeRepository
from app.models.agent_analysis import (
    AgentApprovalGroupRecord,
    AgentClarificationRecord,
    AgentConnectorCapabilityRecord,
    AgentFindingDependencyRecord,
    AgentFindingRecord,
    AgentFindingSolutionRecord,
    AgentGovernanceOperationRecord,
    AgentGovernancePlanRecord,
    AgentIdentityClaimRecord,
    AgentIdentityEvidenceRecord,
    AgentIdentityPostingRecord,
    AgentInputMarkRecord,
    AgentInputRecord,
    AgentModelAttemptRecord,
    AgentModelBatchItemRecord,
    AgentModelBatchRecord,
    AgentWorkItemRecord,
)
from app.models.agent_runtime import AgentRunRecord
from app.models.analyses import AnalysisRecord
from app.models.api_connectors import (
    ApiAuthoritySourceRecord,
    ApiConnectionRecord,
    ApiConnectionSecretRecord,
)
from app.models.differences import DifferenceRecord
from app.models.executions import ExecutionBatchRecord, GovernancePlanRecord, TargetVersionRecord
from app.models.proposals import GovernanceProposalRecord
from app.models.quality import MatchingQualityRecord
from app.models.reconciliation import ReconciliationTask
from app.models.rematching import (
    EntityRematchJobRecord,
)
from app.models.remote_sources import RemoteSourceRecord
from app.models.reporting import AgentReportRecord, AgentRollbackCycleRecord
from app.models.snapshots import Snapshot, SourceFile
from app.models.workflow import WorkflowStageRun
from app.tasks.deletion_service import (
    TaskDeletionBlocked,
    TaskDeletionNotFound,
    TaskDeletionService,
)

TEST_REMOTE_UPLOAD_ROOT = Path("storage/uploads/remote")


def task(tenant_id: str = "school-1") -> ReconciliationTask:
    return ReconciliationTask(
        id=uuid4(),
        tenant_id=tenant_id,
        scope_id="all",
        snapshot_mode="full",
        entity_types=["teacher"],
        status="ready",
        stage="analysis",
        idempotency_key=str(uuid4()),
        request_hash="a" * 64,
    )


def deletion_service(session) -> TaskDeletionService:
    return TaskDeletionService(session, TEST_REMOTE_UPLOAD_ROOT)


def analysis_run(task_id) -> WorkflowStageRun:
    now = datetime.now(UTC)
    return WorkflowStageRun(
        id=uuid4(),
        task_id=task_id,
        stage="analysis",
        attempt=1,
        status="succeeded",
        processed=1,
        total=1,
        succeeded=1,
        manual_review=0,
        failed=0,
        retryable=False,
        started_at=now,
        completed_at=now,
    )


@pytest.mark.asyncio
async def test_deletes_analyzed_task_and_owned_records(session, tmp_path: Path) -> None:
    removable = task()
    survivor = task()
    removable_path = tmp_path / "removable.csv"
    survivor_path = tmp_path / "survivor.csv"
    removable_path.write_text("data", encoding="utf-8")
    survivor_path.write_text("data", encoding="utf-8")
    session.add_all(
        [
            removable,
            survivor,
            analysis_run(removable.id),
            analysis_run(survivor.id),
            SourceFile(
                id=uuid4(),
                task_id=removable.id,
                source_role="authoritative",
                original_name="removable.csv",
                storage_name="removable.csv",
                storage_path=str(removable_path),
                sha256="b" * 64,
                size_bytes=4,
            ),
            SourceFile(
                id=uuid4(),
                task_id=survivor.id,
                source_role="authoritative",
                original_name="survivor.csv",
                storage_name="survivor.csv",
                storage_path=str(survivor_path),
                sha256="c" * 64,
                size_bytes=4,
            ),
        ]
    )
    await session.flush()

    await deletion_service(session).delete(removable.id, "school-1")

    assert await session.get(ReconciliationTask, removable.id) is None
    assert await session.get(ReconciliationTask, survivor.id) is not None
    assert (
        await session.scalar(select(SourceFile).where(SourceFile.task_id == removable.id)) is None
    )
    assert survivor_path.exists()
    assert not removable_path.exists()


@pytest.mark.asyncio
async def test_deleting_task_keeps_referenced_local_source_file(
    session,
    tmp_path: Path,
) -> None:
    removable = task()
    external_path = tmp_path / "authorized-original.csv"
    external_path.write_text("编号,姓名\n001,测试", encoding="utf-8")
    session.add_all(
        [
            removable,
            analysis_run(removable.id),
            SourceFile(
                id=uuid4(),
                task_id=removable.id,
                source_role="target",
                original_name=external_path.name,
                storage_name="external-target-reference",
                storage_path=str(external_path),
                sha256="b" * 64,
                size_bytes=external_path.stat().st_size,
                managed_storage=False,
            ),
        ]
    )
    await session.flush()

    await deletion_service(session).delete(removable.id, "school-1")

    assert await session.get(ReconciliationTask, removable.id) is None
    assert external_path.exists()


@pytest.mark.asyncio
async def test_deletes_materialized_api_source_before_its_task(
    session,
    tmp_path: Path,
) -> None:
    removable = task()
    removable.workflow_version = "agent-graph-v1"
    secret = ApiConnectionSecretRecord(
        tenant_id=removable.tenant_id,
        ciphertext=b"encrypted",
        key_version="fernet-v1",
    )
    connection = ApiConnectionRecord(
        tenant_id=removable.tenant_id,
        provider_id="dingtalk",
        display_name="待删除任务的连接",
        public_configuration={},
        secret_ref="pending",
        manifest_version="v1",
        adapter_version="v1",
        capabilities={},
        visibility_summary={},
        state="active",
        created_by="operator-1",
        updated_by="operator-1",
    )
    session.add_all([removable, secret])
    await session.flush()
    connection.secret_ref = str(secret.id)
    session.add(connection)
    await session.flush()
    artifact = tmp_path / "api-authority.jsonl"
    artifact.write_text("{}\n", encoding="utf-8")
    source = SourceFile(
        task_id=removable.id,
        source_role="authoritative",
        original_name=artifact.name,
        storage_name=artifact.name,
        storage_path=str(artifact),
        sha256="b" * 64,
        size_bytes=artifact.stat().st_size,
    )
    session.add(source)
    await session.flush()
    snapshot = Snapshot(
        id=uuid4(),
        task_id=removable.id,
        source_file_id=source.id,
        source_role="authoritative",
        schema_version="agent-contract-v1",
        mapping_version="api-projection-v1",
        file_hash=source.sha256,
        content_hash="c" * 64,
        state="published",
        summary={},
    )
    session.add(snapshot)
    await session.flush()
    api_source = ApiAuthoritySourceRecord(
        tenant_id=removable.tenant_id,
        task_id=removable.id,
        connection_id=connection.id,
        frozen_public_configuration={},
        frozen_secret_ref=connection.secret_ref,
        selected_entities=["teacher"],
        selection_hash="d" * 64,
        state="ready",
        source_file_id=source.id,
        snapshot_id=snapshot.id,
        content_sha256=source.sha256,
        record_count=1,
        page_count=1,
        manifest_version="v1",
        adapter_version="v1",
        projection_version="api-projection-v1",
    )
    session.add(api_source)
    await session.flush()

    await deletion_service(session).delete(removable.id, removable.tenant_id)

    assert await session.get(ApiAuthoritySourceRecord, api_source.id) is None
    assert await session.get(ReconciliationTask, removable.id) is None
    assert await session.get(ApiConnectionRecord, connection.id) is not None
    assert not artifact.exists()


@pytest.mark.asyncio
async def test_deletes_rematching_and_quality_records_with_task(session) -> None:
    removable = task()
    session.add(removable)
    await session.flush()
    now = datetime.now(UTC)
    session.add(analysis_run(removable.id))
    await session.flush()
    source_file = SourceFile(
        id=uuid4(),
        task_id=removable.id,
        source_role="authoritative",
        original_name="source.csv",
        storage_name="source.csv",
        storage_path="/tmp/source.csv",
        sha256="a" * 64,
        size_bytes=1,
    )
    target_file = SourceFile(
        id=uuid4(),
        task_id=removable.id,
        source_role="target",
        original_name="target.csv",
        storage_name="target.csv",
        storage_path="/tmp/target.csv",
        sha256="b" * 64,
        size_bytes=1,
    )
    session.add_all([source_file, target_file])
    await session.flush()
    source_snapshot = Snapshot(
        id=uuid4(),
        task_id=removable.id,
        source_file_id=source_file.id,
        source_role="authoritative",
        schema_version="canonical-v1",
        mapping_version="source-v1",
        file_hash="c" * 64,
        content_hash="d" * 64,
        summary={},
    )
    target_snapshot = Snapshot(
        id=uuid4(),
        task_id=removable.id,
        source_file_id=target_file.id,
        source_role="target",
        schema_version="canonical-v1",
        mapping_version="target-v1",
        file_hash="e" * 64,
        content_hash="f" * 64,
        summary={},
    )
    session.add_all([source_snapshot, target_snapshot])
    await session.flush()
    job = EntityRematchJobRecord(
        task_id=removable.id,
        tenant_id="school-1",
        requested_by="operator-1",
        source_snapshot_id=source_snapshot.id,
        target_snapshot_id=target_snapshot.id,
        policy_version="rematching-v1",
        idempotency_key=str(uuid4()),
        status="completed",
        total=0,
        indexed=0,
        processed=0,
        completed_at=now,
    )
    session.add(job)
    await session.flush()
    quality = MatchingQualityRecord(
        task_id=removable.id,
        tenant_id="school-1",
        policy_version="matching-quality-v1",
        mapping_versions=["mapping-v1"],
        result={"passed": True},
        evaluated_at=now,
    )
    session.add(quality)
    await session.flush()

    await deletion_service(session).delete(removable.id, "school-1")

    assert await session.get(ReconciliationTask, removable.id) is None
    assert await session.get(EntityRematchJobRecord, job.id) is None
    assert await session.scalar(
        select(MatchingQualityRecord).where(MatchingQualityRecord.task_id == removable.id)
    ) is None


@pytest.mark.asyncio
async def test_deletes_task_without_successful_analysis(session) -> None:
    pending = task()
    session.add(pending)
    await session.flush()

    await deletion_service(session).delete(pending.id, "school-1")

    assert await session.get(ReconciliationTask, pending.id) is None


@pytest.mark.asyncio
async def test_agent_deletion_service_allows_report_without_verified_mutation(session) -> None:
    pending = task()
    pending.workflow_version = "new-agent-v1"
    session.add(pending)
    await session.flush()

    await deletion_service(session).delete(pending.id, "school-1")
    assert await session.get(ReconciliationTask, pending.id) is None


@pytest.mark.asyncio
async def test_deleting_no_write_rollback_releases_rollback_cycle_reference(session) -> None:
    source = task()
    source.workflow_version = "agent-graph-v1"
    rollback = task()
    rollback.workflow_version = "agent-graph-v1"
    rollback.task_kind = "rollback"
    rollback.parent_task_id = source.id
    session.add_all([source, rollback])
    await session.flush()
    cycle = AgentRollbackCycleRecord(
        tenant_id="school-1",
        data_source_key="target:csv:test",
        target_kind="csv",
        generation=1,
        latest_successful_sync_task_id=source.id,
        completed_rollback_task_id=rollback.id,
        completed_rollback_at=datetime.now(UTC),
    )
    session.add(cycle)
    await session.flush()

    await deletion_service(session).delete(rollback.id, "school-1")

    assert await session.get(ReconciliationTask, rollback.id) is None
    await session.refresh(cycle)
    assert cycle.completed_rollback_task_id is None
    assert cycle.completed_rollback_at is None


@pytest.mark.asyncio
async def test_agent_deletion_removes_persisted_analysis_records_before_run(session) -> None:
    pending = task()
    pending.workflow_version = "new-agent-v1"
    session.add(pending)
    await session.flush()
    run = AgentRunRecord(
        task_id=pending.id,
        tenant_id=pending.tenant_id,
        kind="sync",
        workflow_version=pending.workflow_version,
        phase="analyze_batches",
        status="blocked_model_error",
    )
    session.add(run)
    await session.flush()
    authority_file = SourceFile(
        task_id=pending.id,
        source_role="authoritative",
        original_name="authority.csv",
        storage_name="authority.csv",
        storage_path="/tmp/authority.csv",
        sha256="1" * 64,
        size_bytes=1,
    )
    target_file = SourceFile(
        task_id=pending.id,
        source_role="target",
        original_name="target.csv",
        storage_name="target.csv",
        storage_path="/tmp/target.csv",
        sha256="2" * 64,
        size_bytes=1,
    )
    session.add_all([authority_file, target_file])
    await session.flush()
    authority_snapshot = Snapshot(
        id=uuid4(),
        task_id=pending.id,
        source_file_id=authority_file.id,
        source_role="authoritative",
        schema_version="canonical-v1",
        mapping_version="authority-v1",
        file_hash="3" * 64,
        content_hash="4" * 64,
        summary={},
    )
    target_snapshot = Snapshot(
        id=uuid4(),
        task_id=pending.id,
        source_file_id=target_file.id,
        source_role="target",
        schema_version="canonical-v1",
        mapping_version="target-v1",
        file_hash="5" * 64,
        content_hash="6" * 64,
        summary={},
    )
    session.add_all([authority_snapshot, target_snapshot])
    await session.flush()
    capability = AgentConnectorCapabilityRecord(
        run_id=run.id,
        task_id=pending.id,
        tenant_id=pending.tenant_id,
        source_role="authoritative",
        connector_kind="csv",
        capability_hash="c" * 64,
        capabilities={"read": True},
    )
    authority_input = AgentInputRecord(
        run_id=run.id,
        task_id=pending.id,
        snapshot_id=authority_snapshot.id,
        tenant_id=pending.tenant_id,
        source_role="authoritative",
        stable_locator="authority:1",
        stable_order=1,
        entity_kind="teacher",
        name="测试教师",
        number="T-001",
        input_hash="7" * 64,
    )
    target_input = AgentInputRecord(
        run_id=run.id,
        task_id=pending.id,
        snapshot_id=target_snapshot.id,
        tenant_id=pending.tenant_id,
        source_role="target",
        stable_locator="target:1",
        stable_order=1,
        entity_kind="teacher",
        name="旧姓名",
        number="T-001",
        input_hash="8" * 64,
    )
    session.add_all([capability, authority_input, target_input])
    await session.flush()
    mark = AgentInputMarkRecord(
        input_record_id=target_input.id,
        reason_code="field_difference",
        affected_fields=["name"],
        inclusion_state="included",
        report_disposition="actionable",
        safe_evidence={},
    )
    posting = AgentIdentityPostingRecord(
        run_id=run.id,
        task_id=pending.id,
        snapshot_id=target_snapshot.id,
        tenant_id=pending.tenant_id,
        input_record_id=target_input.id,
        entity_kind="teacher",
        key_kind="number",
        normalized_value="T-001",
    )
    session.add_all([mark, posting])
    await session.flush()
    first_work_item = AgentWorkItemRecord(
        run_id=run.id,
        task_id=pending.id,
        tenant_id=pending.tenant_id,
        source_snapshot_id=authority_snapshot.id,
        target_snapshot_id=target_snapshot.id,
        subject_input_id=target_input.id,
        entity_kind="teacher",
        kind="field_difference",
        state="analyzed",
        idempotency_hash="9" * 64,
        evidence_hash="a" * 64,
    )
    second_work_item = AgentWorkItemRecord(
        run_id=run.id,
        task_id=pending.id,
        tenant_id=pending.tenant_id,
        source_snapshot_id=authority_snapshot.id,
        target_snapshot_id=target_snapshot.id,
        subject_input_id=authority_input.id,
        entity_kind="teacher",
        kind="target_missing",
        state="analyzed",
        idempotency_hash="b" * 64,
        evidence_hash="c" * 64,
    )
    batch = AgentModelBatchRecord(
        run_id=run.id,
        task_id=pending.id,
        tenant_id=pending.tenant_id,
        entity_kind="teacher",
        input_hash="d" * 64,
        item_count=2,
        status="completed",
    )
    session.add_all([first_work_item, second_work_item, batch])
    await session.flush()
    evidence = AgentIdentityEvidenceRecord(
        work_item_id=first_work_item.id,
        posting_id=posting.id,
        key_kind="number",
        normalized_value="T-001",
        evidence_hash="e" * 64,
    )
    claim = AgentIdentityClaimRecord(
        run_id=run.id,
        task_id=pending.id,
        source_snapshot_id=authority_snapshot.id,
        target_snapshot_id=target_snapshot.id,
        authority_input_id=authority_input.id,
        target_input_id=target_input.id,
        work_item_id=first_work_item.id,
    )
    batch_items = [
        AgentModelBatchItemRecord(
            batch_id=batch.id,
            work_item_id=first_work_item.id,
            ordinal=1,
        ),
        AgentModelBatchItemRecord(
            batch_id=batch.id,
            work_item_id=second_work_item.id,
            ordinal=2,
        ),
    ]
    attempt = AgentModelAttemptRecord(
        batch_id=batch.id,
        attempt_number=1,
        status="succeeded",
        provider="test",
        model="test-model",
        usage={},
    )
    session.add_all([evidence, claim, *batch_items, attempt])
    await session.flush()
    first_finding = AgentFindingRecord(
        run_id=run.id,
        task_id=pending.id,
        work_item_id=first_work_item.id,
        batch_id=batch.id,
        kind="field_difference",
        category_zh="字段差异",
        analysis_zh="姓名不一致。",
        evidence_refs=[],
        content_hash="f" * 64,
    )
    second_finding = AgentFindingRecord(
        run_id=run.id,
        task_id=pending.id,
        work_item_id=second_work_item.id,
        batch_id=batch.id,
        kind="target_missing",
        category_zh="目标缺失",
        analysis_zh="目标中没有该教师。",
        evidence_refs=[],
        content_hash="0" * 64,
    )
    session.add_all([first_finding, second_finding])
    await session.flush()
    solution = AgentFindingSolutionRecord(
        finding_id=first_finding.id,
        ordinal=1,
        operation="update",
        risk="medium",
        solution_zh="更新姓名。",
        recommended=True,
    )
    dependency = AgentFindingDependencyRecord(
        finding_id=second_finding.id,
        depends_on_finding_id=first_finding.id,
    )
    approval = AgentApprovalGroupRecord(
        run_id=run.id,
        task_id=pending.id,
        tenant_id=pending.tenant_id,
        group_key="delete:teacher",
        membership_hash="1" * 64,
        finding_ids=[str(second_finding.id)],
        issue_kind="target_missing",
        entity_kind="teacher",
        operation="delete",
        policy_version="test-v1",
        risk="high",
        status="pending",
    )
    clarification = AgentClarificationRecord(
        run_id=run.id,
        task_id=pending.id,
        tenant_id=pending.tenant_id,
        work_item_id=second_work_item.id,
        batch_id=batch.id,
        masked_candidates=[],
        allowed_outcomes=["create", "skip"],
        status="pending",
    )
    session.add_all([solution, dependency, approval, clarification])
    await session.flush()
    plan = AgentGovernancePlanRecord(
        run_id=run.id,
        task_id=pending.id,
        tenant_id=pending.tenant_id,
        source_snapshot_id=authority_snapshot.id,
        target_snapshot_id=target_snapshot.id,
        target_version="target-v1",
        finding_ids=[str(first_finding.id), str(second_finding.id)],
        operations=[],
        content_hash="2" * 64,
        status="compiled",
        compiled_by="test",
    )
    session.add(plan)
    await session.flush()
    operation = AgentGovernanceOperationRecord(
        plan_id=plan.id,
        run_id=run.id,
        task_id=pending.id,
        finding_id=first_finding.id,
        operation_type="update",
        entity_kind="teacher",
        target_source_identifier="T-001",
        before={"name": "旧姓名"},
        after={"name": "测试教师"},
        dependencies=[],
        risk="medium",
        status="pending",
    )
    session.add(operation)
    await session.flush()

    await deletion_service(session).delete(pending.id, "school-1")

    assert await session.get(ReconciliationTask, pending.id) is None
    assert await session.get(AgentRunRecord, run.id) is None
    assert await session.get(AgentConnectorCapabilityRecord, capability.id) is None
    assert await session.get(AgentInputRecord, authority_input.id) is None
    assert await session.get(AgentInputMarkRecord, mark.id) is None
    assert await session.get(AgentIdentityPostingRecord, posting.id) is None
    assert await session.get(AgentWorkItemRecord, first_work_item.id) is None
    assert await session.get(AgentIdentityEvidenceRecord, evidence.id) is None
    assert await session.get(AgentIdentityClaimRecord, claim.id) is None
    assert await session.get(AgentModelBatchRecord, batch.id) is None
    assert await session.get(AgentModelAttemptRecord, attempt.id) is None
    assert await session.get(AgentFindingRecord, first_finding.id) is None
    assert await session.get(AgentFindingSolutionRecord, solution.id) is None
    assert await session.get(AgentApprovalGroupRecord, approval.id) is None
    assert await session.get(AgentClarificationRecord, clarification.id) is None
    assert await session.get(AgentGovernancePlanRecord, plan.id) is None
    assert await session.get(AgentGovernanceOperationRecord, operation.id) is None


@pytest.mark.asyncio
async def test_agent_deletion_service_protects_verified_mutation(session) -> None:
    protected = task()
    protected.workflow_version = "new-agent-v1"
    session.add(protected)
    await session.flush()
    session.add(
        AgentReportRecord(
            task_id=protected.id,
            tenant_id=protected.tenant_id,
            kind="sync",
            terminal_state="partial",
            facts={
                "rollback_evidence": {"eligible": True, "successful_mutation_ids": ["op-1"]}
            },
            facts_hash="a" * 64,
            content={},
            rollback_eligible=True,
            deletion_eligible=False,
            generated_by="test",
        )
    )
    await session.flush()

    with pytest.raises(TaskDeletionBlocked, match="已验证"):
        await deletion_service(session).delete(protected.id, "school-1")

    assert await session.get(ReconciliationTask, protected.id) is not None


@pytest.mark.asyncio
async def test_agent_deletion_blocks_reportless_actual_target_mutation(session) -> None:
    protected = task()
    protected.workflow_version = "new-agent-v1"
    session.add(protected)
    await session.flush()
    source_file = SourceFile(
        task_id=protected.id,
        source_role="authoritative",
        original_name="authority.csv",
        storage_name="authority.csv",
        storage_path="/tmp/authority.csv",
        sha256="1" * 64,
        size_bytes=1,
    )
    target_file = SourceFile(
        task_id=protected.id,
        source_role="target",
        original_name="target.csv",
        storage_name="target.csv",
        storage_path="/tmp/target.csv",
        sha256="2" * 64,
        size_bytes=1,
    )
    session.add_all([source_file, target_file])
    await session.flush()
    source_snapshot = Snapshot(
        id=uuid4(),
        task_id=protected.id,
        source_file_id=source_file.id,
        source_role="authoritative",
        schema_version="canonical-v1",
        mapping_version="authority-v1",
        file_hash="3" * 64,
        content_hash="4" * 64,
        summary={},
    )
    target_snapshot = Snapshot(
        id=uuid4(),
        task_id=protected.id,
        source_file_id=target_file.id,
        source_role="target",
        schema_version="canonical-v1",
        mapping_version="target-v1",
        file_hash="5" * 64,
        content_hash="6" * 64,
        summary={},
    )
    session.add_all([source_snapshot, target_snapshot])
    await session.flush()
    run = AgentRunRecord(
        task_id=protected.id,
        tenant_id=protected.tenant_id,
        kind="sync",
        workflow_version=protected.workflow_version,
        phase="generate_report",
        status="running",
    )
    session.add(run)
    await session.flush()
    target_input = AgentInputRecord(
        run_id=run.id,
        task_id=protected.id,
        snapshot_id=target_snapshot.id,
        tenant_id=protected.tenant_id,
        source_role="target",
        stable_locator="target:1",
        stable_order=1,
        entity_kind="teacher",
        name="测试教师",
        number="T-001",
        input_hash="7" * 64,
    )
    session.add(target_input)
    await session.flush()
    work_item = AgentWorkItemRecord(
        run_id=run.id,
        task_id=protected.id,
        tenant_id=protected.tenant_id,
        source_snapshot_id=source_snapshot.id,
        target_snapshot_id=target_snapshot.id,
        subject_input_id=target_input.id,
        entity_kind="teacher",
        kind="field_difference",
        state="analyzed",
        idempotency_hash="8" * 64,
        evidence_hash="9" * 64,
    )
    batch = AgentModelBatchRecord(
        run_id=run.id,
        task_id=protected.id,
        tenant_id=protected.tenant_id,
        entity_kind="teacher",
        input_hash="a" * 64,
        item_count=1,
        status="completed",
    )
    session.add_all([work_item, batch])
    await session.flush()
    finding = AgentFindingRecord(
        run_id=run.id,
        task_id=protected.id,
        work_item_id=work_item.id,
        batch_id=batch.id,
        kind="field_difference",
        category_zh="字段差异",
        analysis_zh="目标数据需要修改。",
        evidence_refs=[],
        content_hash="b" * 64,
    )
    session.add(finding)
    await session.flush()
    plan = AgentGovernancePlanRecord(
        run_id=run.id,
        task_id=protected.id,
        tenant_id=protected.tenant_id,
        source_snapshot_id=source_snapshot.id,
        target_snapshot_id=target_snapshot.id,
        target_version="target-v1",
        finding_ids=[str(finding.id)],
        operations=[],
        content_hash="c" * 64,
        status="succeeded",
        compiled_by="test",
    )
    session.add(plan)
    await session.flush()
    operation = AgentGovernanceOperationRecord(
        plan_id=plan.id,
        run_id=run.id,
        task_id=protected.id,
        finding_id=finding.id,
        operation_type="update",
        entity_kind="teacher",
        target_source_identifier="T-001",
        before={"name": "旧姓名"},
        after={"name": "新姓名"},
        dependencies=[],
        risk="medium",
        status="succeeded",
        attempt_count=1,
        actual_after={"name": "新姓名"},
        verification={"matched": True},
    )
    session.add(operation)
    await session.flush()

    with pytest.raises(TaskDeletionBlocked, match="目标变更"):
        await deletion_service(session).delete(protected.id, "school-1")

    assert await session.get(ReconciliationTask, protected.id) is not None
    assert await session.get(AgentGovernanceOperationRecord, operation.id) is not None


@pytest.mark.asyncio
async def test_agent_deletion_blocks_active_execution_phase(session) -> None:
    protected = task()
    protected.workflow_version = "new-agent-v1"
    session.add(protected)
    await session.flush()
    run = AgentRunRecord(
        task_id=protected.id,
        tenant_id=protected.tenant_id,
        kind="sync",
        workflow_version=protected.workflow_version,
        phase="execute_and_verify",
        status="running",
    )
    session.add(run)
    await session.flush()

    with pytest.raises(TaskDeletionBlocked, match="治理执行中"):
        await deletion_service(session).delete(protected.id, "school-1")

    assert await session.get(ReconciliationTask, protected.id) is not None
    assert await session.get(AgentRunRecord, run.id) is not None


@pytest.mark.asyncio
async def test_deletes_task_with_unexecuted_governance_proposal(session) -> None:
    protected = task()
    source_file_id = uuid4()
    snapshot_id = uuid4()
    difference_id = uuid4()
    analysis_id = uuid4()
    now = datetime.now(UTC)
    session.add(protected)
    await session.flush()
    session.add_all(
        [
            analysis_run(protected.id),
            SourceFile(
                id=source_file_id,
                task_id=protected.id,
                source_role="authoritative",
                original_name="source.csv",
                storage_name="source-protected.csv",
                storage_path="/tmp/source-protected.csv",
                sha256="d" * 64,
                size_bytes=4,
            ),
        ]
    )
    await session.flush()
    session.add(
        Snapshot(
            id=snapshot_id,
            task_id=protected.id,
            source_file_id=source_file_id,
            source_role="authoritative",
            schema_version="canonical-v1",
            mapping_version="third-party-v1",
            file_hash="e" * 64,
            content_hash="f" * 64,
            summary={},
        )
    )
    await session.flush()
    session.add(
        DifferenceRecord(
            id=difference_id,
            task_id=protected.id,
            tenant_id="school-1",
            source_snapshot_id=snapshot_id,
            target_snapshot_id=snapshot_id,
            entity_type="teacher",
            difference_type="attribute",
            proposed_action="update",
            evidence={},
            comparison_rule_version="comparison-v1",
            evidence_hash="1" * 64,
        )
    )
    await session.flush()
    session.add(
        AnalysisRecord(
            id=analysis_id,
            difference_id=difference_id,
            difference_version=1,
            analysis_version="analysis-v1",
            status="succeeded",
            output={},
            provider="test",
            model="test",
            skill_name="test",
            skill_version="1",
            prompt_version="1",
            tool_trace_ids=[],
            gateway_request_ids=[],
            usage={},
            generated_at=now,
        )
    )
    await session.flush()
    proposal = GovernanceProposalRecord(
        id=uuid4(),
        task_id=protected.id,
        tenant_id="school-1",
        difference_id=difference_id,
        difference_version=1,
        analysis_id=analysis_id,
        analysis_version="analysis-v1",
        proposal_version=1,
        proposal_source="ai",
        operation_type="update",
        target_entity_id=None,
        changes=[],
        rationale="test",
        evidence_refs=[],
        risk="low",
        created_by="operator-1",
        status="pending_execution",
        supersedes_id=None,
    )
    session.add(proposal)
    await session.flush()
    plan = GovernancePlanRecord(
        task_id=protected.id,
        version=1,
        source_snapshot_id=snapshot_id,
        target_snapshot_id=snapshot_id,
        target_version="target-v1",
        proposal_versions=[],
        operations=[],
        content_hash="2" * 64,
        created_by="operator-1",
    )
    session.add(plan)
    session.add(
        TargetVersionRecord(
            task_id=protected.id,
            tenant_id=protected.tenant_id,
            source_snapshot_id=snapshot_id,
            file_sha256="3" * 64,
            content_hash="4" * 64,
            storage_path="/tmp/target-version.csv",
        )
    )
    await session.flush()

    await deletion_service(session).delete(protected.id, "school-1")

    assert await session.get(ReconciliationTask, protected.id) is None
    assert await session.get(GovernanceProposalRecord, proposal.id) is None
    assert await session.get(GovernancePlanRecord, plan.id) is None
    assert await session.scalar(
        select(TargetVersionRecord).where(TargetVersionRecord.task_id == protected.id)
    ) is None


@pytest.mark.asyncio
async def test_refuses_task_with_governance_execution_batch(session) -> None:
    protected = task()
    source_file_id = uuid4()
    snapshot_id = uuid4()
    session.add(protected)
    await session.flush()
    session.add(
        SourceFile(
            id=source_file_id,
            task_id=protected.id,
            source_role="authoritative",
            original_name="source.csv",
            storage_name="source-executed.csv",
            storage_path="/tmp/source-executed.csv",
            sha256="f" * 64,
            size_bytes=4,
        )
    )
    await session.flush()
    session.add(
        Snapshot(
            id=snapshot_id,
            task_id=protected.id,
            source_file_id=source_file_id,
            source_role="authoritative",
            schema_version="canonical-v1",
            mapping_version="third-party-v1",
            file_hash="a" * 64,
            content_hash="b" * 64,
            summary={},
        )
    )
    await session.flush()
    plan = GovernancePlanRecord(
        task_id=protected.id,
        version=1,
        source_snapshot_id=snapshot_id,
        target_snapshot_id=snapshot_id,
        target_version="target-v1",
        proposal_versions=[],
        operations=[],
        content_hash="c" * 64,
        created_by="operator-1",
    )
    session.add(plan)
    await session.flush()
    session.add(
        ExecutionBatchRecord(
            plan_id=plan.id,
            plan_version=plan.version,
            input_target_version_id=uuid4(),
            idempotency_key=str(uuid4()),
            confirmed_by="operator-1",
            preflight_result={},
        )
    )
    await session.flush()

    with pytest.raises(TaskDeletionBlocked, match="治理执行记录"):
        await deletion_service(session).delete(protected.id, "school-1")

    assert await session.get(ReconciliationTask, protected.id) is not None


@pytest.mark.asyncio
async def test_missing_and_cross_tenant_tasks_are_not_found(session) -> None:
    foreign = task("other-school")
    session.add(foreign)
    await session.flush()

    with pytest.raises(TaskDeletionNotFound):
        await deletion_service(session).delete(uuid4(), "school-1")
    with pytest.raises(TaskDeletionNotFound):
        await deletion_service(session).delete(foreign.id, "school-1")


@pytest.mark.asyncio
async def test_deletes_failed_remote_source_task_and_its_managed_artifacts(
    session,
    tmp_path: Path,
) -> None:
    failed = task()
    conversation = await AgentRuntimeRepository(session).create_conversation(
        tenant_id=failed.tenant_id,
        created_by="operator-1",
    )
    session.add(failed)
    await session.flush()
    remote = RemoteSourceRecord(
        tenant_id=failed.tenant_id,
        created_by="operator-1",
        conversation_id=conversation.id,
        task_id=failed.id,
        original_url="https://data.example.test/failed.csv",
        display_origin="data.example.test",
        state="failed",
        safe_problem_code="remote_source_timeout",
    )
    session.add(remote)
    await session.flush()

    remote_root = tmp_path / "uploads" / "remote"
    remote_root.mkdir(parents=True)
    completed = remote_root / f"{remote.id.hex}-stale.csv"
    partial = remote_root / f".{remote.id.hex}-interrupted.part"
    unrelated = remote_root / f"{remote.id.hex}0-stale.csv"
    completed.write_text("id,name\n1,Student\n", encoding="utf-8")
    partial.write_text("partial", encoding="utf-8")
    unrelated.write_text("keep", encoding="utf-8")

    await TaskDeletionService(session, remote_root).delete(failed.id, failed.tenant_id)

    assert await session.get(ReconciliationTask, failed.id) is None
    assert await session.get(RemoteSourceRecord, remote.id) is None
    assert not completed.exists()
    assert not partial.exists()
    assert unrelated.exists()


@pytest.mark.asyncio
async def test_deletes_materialized_remote_source_before_its_source_file(
    session,
    tmp_path: Path,
) -> None:
    materialized = task()
    conversation = await AgentRuntimeRepository(session).create_conversation(
        tenant_id=materialized.tenant_id,
        created_by="operator-1",
    )
    session.add(materialized)
    await session.flush()

    remote_id = uuid4()
    remote_root = tmp_path / "uploads" / "remote"
    remote_root.mkdir(parents=True)
    completed = remote_root / f"{remote_id.hex}-digest.csv"
    partial = remote_root / f".{remote_id.hex}-interrupted.part"
    completed.write_text("id,name\n1,Student\n", encoding="utf-8")
    partial.write_text("partial", encoding="utf-8")
    source = SourceFile(
        id=uuid4(),
        task_id=materialized.id,
        source_role="authoritative",
        original_name="remote.csv",
        storage_name=completed.name,
        storage_path=str(completed),
        sha256="a" * 64,
        size_bytes=completed.stat().st_size,
        managed_storage=True,
    )
    session.add(source)
    await session.flush()
    session.add(
        Snapshot(
            id=uuid4(),
            task_id=materialized.id,
            source_file_id=source.id,
            source_role="authoritative",
            schema_version="canonical-v1",
            mapping_version="remote-v1",
            file_hash="b" * 64,
            content_hash="c" * 64,
            summary={},
        )
    )
    session.add(
        RemoteSourceRecord(
            id=remote_id,
            tenant_id=materialized.tenant_id,
            created_by="operator-1",
            conversation_id=conversation.id,
            task_id=materialized.id,
            source_file_id=source.id,
            original_url="https://data.example.test/materialized.csv",
            display_origin="data.example.test",
            state="ready",
            content_sha256="a" * 64,
            size_bytes=completed.stat().st_size,
            media_type="text/csv",
        )
    )
    await session.flush()

    await TaskDeletionService(session, remote_root).delete(
        materialized.id,
        materialized.tenant_id,
    )

    assert await session.get(ReconciliationTask, materialized.id) is None
    assert await session.get(SourceFile, source.id) is None
    assert (
        await session.scalar(
            select(RemoteSourceRecord).where(RemoteSourceRecord.task_id == materialized.id)
        )
        is None
    )
    assert not completed.exists()
    assert not partial.exists()
