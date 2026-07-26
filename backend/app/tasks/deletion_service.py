import logging
from uuid import UUID

from anyio import Path
from sqlalchemy import delete, exists, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_reporting.service import AgentReportingService
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
from app.models.agent_runtime import (
    AgentCheckpointRecord,
    AgentFailureRecord,
    AgentRunRecord,
    AgentTaskEventRecord,
    SchoolTaskLockRecord,
)
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
from app.models.reporting import AgentReportRecord
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
        run_ids: list[UUID] = []
        if task.workflow_version != "legacy-v1":
            runs = list(
                await self.session.scalars(
                    select(AgentRunRecord)
                    .where(AgentRunRecord.task_id == task_id)
                    .with_for_update()
                )
            )
            run_ids = [run.id for run in runs]
            if any(
                run.phase in {"execute_and_verify", "execute_restore"}
                and run.status in {"pending", "running", "waiting_human", "terminating"}
                for run in runs
            ):
                raise TaskDeletionBlocked("该 Agent 任务正在治理执行中，请先终止任务")
            mutation_exists = await self.session.scalar(
                select(
                    exists().where(
                        AgentGovernanceOperationRecord.task_id == task_id,
                        or_(
                            AgentGovernanceOperationRecord.status.in_(
                                ("running", "succeeded", "verification_failed")
                            ),
                            AgentGovernanceOperationRecord.actual_after.is_not(None),
                        ),
                    )
                )
            )
            if mutation_exists:
                raise TaskDeletionBlocked("该 Agent 任务已有目标变更，不能删除")
            if not await AgentReportingService(self.session).deletion_eligible(
                task_id=task_id, tenant_id=tenant_id
            ):
                raise TaskDeletionBlocked("该 Agent 任务已有已验证的目标变更，不能删除")

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

        if task.workflow_version != "legacy-v1":
            await self.session.execute(
                delete(AgentReportRecord).where(AgentReportRecord.task_id == task_id)
            )
            sqlite_guard_enabled = await self._enable_agent_analysis_deletion(task_id)
            try:
                await self._delete_agent_analysis_records(run_ids)
            finally:
                if sqlite_guard_enabled and self.session.is_active:
                    await self._disable_agent_analysis_deletion()
            await self.session.execute(
                delete(AgentTaskEventRecord).where(AgentTaskEventRecord.run_id.in_(run_ids))
            )
            await self.session.execute(
                delete(AgentCheckpointRecord).where(AgentCheckpointRecord.run_id.in_(run_ids))
            )
            await self.session.execute(
                delete(AgentFailureRecord).where(AgentFailureRecord.run_id.in_(run_ids))
            )
            await self.session.execute(
                delete(SchoolTaskLockRecord).where(
                    SchoolTaskLockRecord.owner_task_id == task_id
                )
            )
            await self.session.execute(
                delete(AgentRunRecord).where(AgentRunRecord.task_id == task_id)
            )

        snapshot_rows = (
            await self.session.execute(
                select(Snapshot.id, Snapshot.quarantine_path).where(Snapshot.task_id == task_id)
            )
        ).all()
        snapshot_ids = [row.id for row in snapshot_rows]
        file_rows = (
            await self.session.execute(
                select(
                    SourceFile.id,
                    SourceFile.storage_path,
                    SourceFile.managed_storage,
                ).where(SourceFile.task_id == task_id)
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

        stored_paths = [
            row.storage_path for row in file_rows if row.managed_storage
        ]
        quarantine_paths = [row.quarantine_path for row in snapshot_rows if row.quarantine_path]
        for path in [*stored_paths, *quarantine_paths]:
            try:
                await Path(path).unlink(missing_ok=True)
            except OSError:
                logger.exception("failed to remove stored task file", extra={"path": path})

    async def _enable_agent_analysis_deletion(self, task_id: UUID) -> bool:
        if self.session.bind is None:
            return False
        dialect = self.session.bind.dialect.name
        if dialect == "postgresql":
            await self.session.execute(text("SELECT set_config('app.task_deletion', 'on', true)"))
            return False
        if dialect != "sqlite":
            return False
        guard_exists = await self.session.scalar(
            text(
                "SELECT EXISTS("
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'agent_task_deletion_guard'"
                ")"
            )
        )
        if not guard_exists:
            return False
        await self.session.execute(
            text(
                "UPDATE agent_task_deletion_guard "
                "SET task_id = :task_id WHERE id = 1"
            ),
            {"task_id": task_id.hex},
        )
        return True

    async def _disable_agent_analysis_deletion(self) -> None:
        if self.session.bind is None:
            return
        if self.session.bind.dialect.name == "postgresql":
            await self.session.execute(
                text("SELECT set_config('app.task_deletion', 'off', true)")
            )
        elif self.session.bind.dialect.name == "sqlite":
            await self.session.execute(
                text("UPDATE agent_task_deletion_guard SET task_id = NULL WHERE id = 1")
            )

    async def _delete_agent_analysis_records(self, run_ids: list[UUID]) -> None:
        if not run_ids:
            return
        input_ids = list(
            await self.session.scalars(
                select(AgentInputRecord.id).where(AgentInputRecord.run_id.in_(run_ids))
            )
        )
        posting_ids = list(
            await self.session.scalars(
                select(AgentIdentityPostingRecord.id).where(
                    AgentIdentityPostingRecord.run_id.in_(run_ids)
                )
            )
        )
        work_item_ids = list(
            await self.session.scalars(
                select(AgentWorkItemRecord.id).where(AgentWorkItemRecord.run_id.in_(run_ids))
            )
        )
        batch_ids = list(
            await self.session.scalars(
                select(AgentModelBatchRecord.id).where(AgentModelBatchRecord.run_id.in_(run_ids))
            )
        )
        finding_ids = list(
            await self.session.scalars(
                select(AgentFindingRecord.id).where(AgentFindingRecord.run_id.in_(run_ids))
            )
        )
        plan_ids = list(
            await self.session.scalars(
                select(AgentGovernancePlanRecord.id).where(
                    AgentGovernancePlanRecord.run_id.in_(run_ids)
                )
            )
        )

        await self.session.execute(
            delete(AgentGovernanceOperationRecord).where(
                AgentGovernanceOperationRecord.run_id.in_(run_ids)
            )
        )
        await self.session.execute(
            delete(AgentApprovalGroupRecord).where(
                AgentApprovalGroupRecord.run_id.in_(run_ids)
            )
        )
        await self.session.execute(
            delete(AgentClarificationRecord).where(
                AgentClarificationRecord.run_id.in_(run_ids)
            )
        )
        await self.session.execute(
            delete(AgentFindingDependencyRecord).where(
                or_(
                    AgentFindingDependencyRecord.finding_id.in_(finding_ids),
                    AgentFindingDependencyRecord.depends_on_finding_id.in_(finding_ids),
                )
            )
        )
        await self.session.execute(
            delete(AgentFindingSolutionRecord).where(
                AgentFindingSolutionRecord.finding_id.in_(finding_ids)
            )
        )
        await self.session.execute(
            delete(AgentFindingRecord).where(AgentFindingRecord.id.in_(finding_ids))
        )
        await self.session.execute(
            delete(AgentModelAttemptRecord).where(
                AgentModelAttemptRecord.batch_id.in_(batch_ids)
            )
        )
        await self.session.execute(
            delete(AgentModelBatchItemRecord).where(
                or_(
                    AgentModelBatchItemRecord.batch_id.in_(batch_ids),
                    AgentModelBatchItemRecord.work_item_id.in_(work_item_ids),
                )
            )
        )
        await self.session.execute(
            delete(AgentIdentityClaimRecord).where(
                AgentIdentityClaimRecord.run_id.in_(run_ids)
            )
        )
        await self.session.execute(
            delete(AgentIdentityEvidenceRecord).where(
                or_(
                    AgentIdentityEvidenceRecord.work_item_id.in_(work_item_ids),
                    AgentIdentityEvidenceRecord.posting_id.in_(posting_ids),
                )
            )
        )
        await self.session.execute(
            delete(AgentInputMarkRecord).where(
                AgentInputMarkRecord.input_record_id.in_(input_ids)
            )
        )
        await self.session.execute(
            delete(AgentGovernancePlanRecord).where(
                AgentGovernancePlanRecord.id.in_(plan_ids)
            )
        )
        await self.session.execute(
            delete(AgentModelBatchRecord).where(AgentModelBatchRecord.id.in_(batch_ids))
        )
        await self.session.execute(
            delete(AgentWorkItemRecord).where(AgentWorkItemRecord.id.in_(work_item_ids))
        )
        await self.session.execute(
            delete(AgentIdentityPostingRecord).where(
                AgentIdentityPostingRecord.id.in_(posting_ids)
            )
        )
        await self.session.execute(
            delete(AgentInputRecord).where(AgentInputRecord.id.in_(input_ids))
        )
        await self.session.execute(
            delete(AgentConnectorCapabilityRecord).where(
                AgentConnectorCapabilityRecord.run_id.in_(run_ids)
            )
        )
