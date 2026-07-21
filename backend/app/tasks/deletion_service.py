import logging
from uuid import UUID

from anyio import Path
from sqlalchemy import delete, exists, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analyses import AnalysisRecord
from app.models.analysis_jobs import AnalysisJobRecord, AnalysisWorkItemRecord
from app.models.differences import DifferenceRecord
from app.models.executions import (
    ExecutionBatchRecord,
    GovernancePlanExplanationRecord,
    GovernancePlanRecord,
    TargetVersionRecord,
)
from app.models.mappings import EntityMapping, TargetEntityEmbedding
from app.models.proposal_batches import ProposalBatchRecord
from app.models.proposals import GovernanceProposalRecord
from app.models.quality import MatchingQualityRecord
from app.models.reconciliation import ReconciliationTask
from app.models.rematching import (
    EntityRematchCandidateEdgeRecord,
    EntityRematchJobRecord,
    EntityRematchWorkItemRecord,
)
from app.models.snapshots import (
    CanonicalEntityRecord,
    IngestionIssueRecord,
    RawSnapshotRow,
    Snapshot,
    SourceFile,
)
from app.models.workflow import WorkflowStageRun

logger = logging.getLogger(__name__)


class TaskDeletionNotFound(LookupError):
    pass


class TaskDeletionBlocked(ValueError):
    pass


class TaskDeletionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def delete(self, task_id: UUID, tenant_id: str) -> None:
        task = await self.session.scalar(
            select(ReconciliationTask)
            .where(
                ReconciliationTask.id == task_id,
                ReconciliationTask.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if task is None:
            raise TaskDeletionNotFound(f"reconciliation task not found: {task_id}")

        execution_exists = await self.session.scalar(
            select(
                exists().where(
                    ExecutionBatchRecord.plan_id == GovernancePlanRecord.id,
                    GovernancePlanRecord.task_id == task_id,
                )
            )
        )
        if execution_exists:
            raise TaskDeletionBlocked("该任务已有治理执行记录，不能删除")

        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            await self.session.execute(text("SELECT set_config('app.task_deletion', 'on', true)"))

        snapshot_rows = (
            await self.session.execute(
                select(Snapshot.id, Snapshot.quarantine_path).where(Snapshot.task_id == task_id)
            )
        ).all()
        snapshot_ids = [row.id for row in snapshot_rows]
        file_rows = (
            await self.session.execute(
                select(SourceFile.id, SourceFile.storage_path).where(SourceFile.task_id == task_id)
            )
        ).all()
        source_file_ids = [row.id for row in file_rows]
        entity_ids = list(
            (
                await self.session.scalars(
                    select(CanonicalEntityRecord.id).where(
                        CanonicalEntityRecord.snapshot_id.in_(snapshot_ids)
                    )
                )
            ).all()
        )
        difference_ids = list(
            (
                await self.session.scalars(
                    select(DifferenceRecord.id).where(DifferenceRecord.task_id == task_id)
                )
            ).all()
        )
        analysis_ids = list(
            (
                await self.session.scalars(
                    select(AnalysisRecord.id).where(
                        AnalysisRecord.difference_id.in_(difference_ids)
                    )
                )
            ).all()
        )
        job_ids = list(
            (
                await self.session.scalars(
                    select(AnalysisJobRecord.id).where(AnalysisJobRecord.task_id == task_id)
                )
            ).all()
        )
        governance_plan_ids = list(
            (
                await self.session.scalars(
                    select(GovernancePlanRecord.id).where(GovernancePlanRecord.task_id == task_id)
                )
            ).all()
        )

        rematch_job_ids = list(
            (
                await self.session.scalars(
                    select(EntityRematchJobRecord.id).where(
                        EntityRematchJobRecord.task_id == task_id
                    )
                )
            ).all()
        )
        rematch_item_ids = list(
            (
                await self.session.scalars(
                    select(EntityRematchWorkItemRecord.id).where(
                        EntityRematchWorkItemRecord.job_id.in_(rematch_job_ids)
                    )
                )
            ).all()
        )

        await self.session.execute(
            delete(MatchingQualityRecord).where(MatchingQualityRecord.task_id == task_id)
        )
        await self.session.execute(
            delete(GovernancePlanExplanationRecord).where(
                GovernancePlanExplanationRecord.plan_id.in_(governance_plan_ids)
            )
        )
        await self.session.execute(
            delete(GovernancePlanRecord).where(GovernancePlanRecord.id.in_(governance_plan_ids))
        )
        await self.session.execute(
            delete(TargetVersionRecord).where(TargetVersionRecord.task_id == task_id)
        )
        await self.session.execute(
            delete(EntityRematchCandidateEdgeRecord).where(
                EntityRematchCandidateEdgeRecord.work_item_id.in_(rematch_item_ids)
            )
        )
        await self.session.execute(
            delete(EntityRematchWorkItemRecord).where(
                EntityRematchWorkItemRecord.id.in_(rematch_item_ids)
            )
        )
        await self.session.execute(
            delete(EntityRematchJobRecord).where(EntityRematchJobRecord.id.in_(rematch_job_ids))
        )

        await self.session.execute(
            delete(AnalysisWorkItemRecord).where(
                or_(
                    AnalysisWorkItemRecord.job_id.in_(job_ids),
                    AnalysisWorkItemRecord.difference_id.in_(difference_ids),
                    AnalysisWorkItemRecord.result_id.in_(analysis_ids),
                )
            )
        )
        await self.session.execute(
            delete(ProposalBatchRecord).where(ProposalBatchRecord.task_id == task_id)
        )
        await self.session.execute(
            delete(AnalysisJobRecord).where(AnalysisJobRecord.id.in_(job_ids))
        )
        await self.session.execute(
            delete(GovernanceProposalRecord).where(GovernanceProposalRecord.task_id == task_id)
        )
        await self.session.execute(
            delete(AnalysisRecord).where(AnalysisRecord.id.in_(analysis_ids))
        )
        await self.session.execute(
            delete(DifferenceRecord).where(DifferenceRecord.id.in_(difference_ids))
        )
        await self.session.execute(
            delete(TargetEntityEmbedding).where(
                or_(
                    TargetEntityEmbedding.snapshot_id.in_(snapshot_ids),
                    TargetEntityEmbedding.entity_id.in_(entity_ids),
                )
            )
        )
        await self.session.execute(delete(EntityMapping).where(EntityMapping.task_id == task_id))
        await self.session.execute(
            delete(CanonicalEntityRecord).where(CanonicalEntityRecord.id.in_(entity_ids))
        )
        await self.session.execute(
            delete(RawSnapshotRow).where(RawSnapshotRow.snapshot_id.in_(snapshot_ids))
        )
        await self.session.execute(
            delete(IngestionIssueRecord).where(IngestionIssueRecord.snapshot_id.in_(snapshot_ids))
        )
        await self.session.execute(delete(Snapshot).where(Snapshot.id.in_(snapshot_ids)))
        await self.session.execute(
            delete(WorkflowStageRun).where(WorkflowStageRun.task_id == task_id)
        )
        await self.session.execute(delete(SourceFile).where(SourceFile.id.in_(source_file_ids)))
        await self.session.execute(
            delete(ReconciliationTask).where(ReconciliationTask.id == task_id)
        )
        await self.session.commit()

        stored_paths = [row.storage_path for row in file_rows]
        quarantine_paths = [row.quarantine_path for row in snapshot_rows if row.quarantine_path]
        for path in [*stored_paths, *quarantine_paths]:
            try:
                await Path(path).unlink(missing_ok=True)
            except OSError:
                logger.exception("failed to remove stored task file", extra={"path": path})
