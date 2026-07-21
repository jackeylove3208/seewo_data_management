from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.analyses import AnalysisRecord
from app.models.differences import DifferenceRecord
from app.models.proposals import GovernanceProposalRecord
from app.models.quality import MatchingQualityRecord
from app.models.reconciliation import ReconciliationTask
from app.models.rematching import (
    EntityRematchJobRecord,
)
from app.models.snapshots import Snapshot, SourceFile
from app.models.workflow import WorkflowStageRun
from app.tasks.deletion_service import (
    TaskDeletionBlocked,
    TaskDeletionNotFound,
    TaskDeletionService,
)


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

    await TaskDeletionService(session).delete(removable.id, "school-1")

    assert await session.get(ReconciliationTask, removable.id) is None
    assert await session.get(ReconciliationTask, survivor.id) is not None
    assert (
        await session.scalar(select(SourceFile).where(SourceFile.task_id == removable.id)) is None
    )
    assert survivor_path.exists()
    assert not removable_path.exists()


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

    await TaskDeletionService(session).delete(removable.id, "school-1")

    assert await session.get(ReconciliationTask, removable.id) is None
    assert await session.get(EntityRematchJobRecord, job.id) is None
    assert await session.scalar(
        select(MatchingQualityRecord).where(MatchingQualityRecord.task_id == removable.id)
    ) is None


@pytest.mark.asyncio
async def test_refuses_task_without_successful_analysis(session) -> None:
    pending = task()
    session.add(pending)
    await session.flush()

    with pytest.raises(TaskDeletionBlocked, match="尚未完成 AI 分析"):
        await TaskDeletionService(session).delete(pending.id, "school-1")

    assert await session.get(ReconciliationTask, pending.id) is not None


@pytest.mark.asyncio
async def test_refuses_task_with_any_governance_proposal(session) -> None:
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
    session.add(
        GovernanceProposalRecord(
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
    )
    await session.flush()

    with pytest.raises(TaskDeletionBlocked, match="已有治理方案"):
        await TaskDeletionService(session).delete(protected.id, "school-1")

    assert await session.get(ReconciliationTask, protected.id) is not None


@pytest.mark.asyncio
async def test_missing_and_cross_tenant_tasks_are_not_found(session) -> None:
    foreign = task("other-school")
    session.add(foreign)
    await session.flush()

    with pytest.raises(TaskDeletionNotFound):
        await TaskDeletionService(session).delete(uuid4(), "school-1")
    with pytest.raises(TaskDeletionNotFound):
        await TaskDeletionService(session).delete(foreign.id, "school-1")
