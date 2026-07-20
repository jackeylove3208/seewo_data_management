from collections import Counter
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import OperatorContext
from app.executions.preflight import ExecutionPreflight, plan_from_record_payload
from app.governance.dependency_binding import bind_selected_dependencies
from app.governance.plan_builder import GovernancePlanBuilder
from app.models.analyses import AnalysisRecord
from app.models.differences import DifferenceRecord
from app.models.executions import GovernancePlanRecord, TargetVersionRecord
from app.models.proposals import GovernanceProposalRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import CanonicalEntityRecord, Snapshot, SourceFile
from app.repositories.executions import ExecutionPersistenceConflict, ExecutionRepository
from app.schemas.canonical_entities import EntityType
from app.schemas.differences import DifferenceType
from app.schemas.executions import (
    ConfirmExecutionBatchRequest,
    ExecutionBatchConfirmation,
    ExecutionBatchStatus,
    ExecutionPreview,
    ExecutionPreviewRequest,
    GovernancePlan,
    OperationType,
    PreflightResult,
    ProposalSource,
    ProposalStatus,
    ProposalVersionRef,
    ReviewedProposalSnapshot,
)
from app.schemas.governance import RiskLevel


class ExecutionPlanningConflict(ValueError):
    pass


class ExecutionPlanningService:
    def __init__(self, session: AsyncSession, *, operator: OperatorContext) -> None:
        self.session = session
        self.operator = operator
        self.repository = ExecutionRepository(session)
        self.preflight = ExecutionPreflight(session)
        self.builder = GovernancePlanBuilder()

    async def preview(self, request: ExecutionPreviewRequest) -> ExecutionPreview:
        task = await self._require_task(request.task_id)
        source_snapshot, target_snapshot = await self._task_snapshots(task.id)
        target_version = await self._ensure_target_version(task, target_snapshot)
        proposals = bind_selected_dependencies(
            tuple(
                [
                    await self._reviewed_proposal(
                        task=task,
                        reference_id=reference.proposal_id,
                        reference_version=reference.proposal_version,
                        source_snapshot=source_snapshot,
                        target_snapshot=target_snapshot,
                        target_version=target_version,
                    )
                    for reference in request.proposals
                ]
            )
        )
        plan = self.builder.build(
            task_id=task.id,
            source_snapshot_id=source_snapshot.id,
            target_snapshot_id=target_snapshot.id,
            target_version=f"sha256:{target_version.file_sha256}",
            proposals=proposals,
        )
        await self.repository.save_plan(plan, created_by=self.operator.operator_id)
        counts = Counter(operation.operation_type for operation in plan.operations)
        sources = Counter(operation.proposal_source for operation in plan.operations)
        return ExecutionPreview(
            plan_id=plan.id,
            plan_version=plan.version,
            input_target_version_id=target_version.id,
            target_version=plan.target_version,
            counts={operation_type: counts[operation_type] for operation_type in OperationType},
            proposal_sources={source: sources[source] for source in ProposalSource},
            operations=plan.operations,
            high_risk=any(operation.risk is RiskLevel.HIGH for operation in plan.operations),
        )

    async def confirm(
        self,
        request: ConfirmExecutionBatchRequest,
        *,
        idempotency_key: str,
    ) -> ExecutionBatchConfirmation:
        record = await self.repository.get_plan(request.plan_id)
        if record is None:
            raise LookupError("governance plan not found")
        await self._require_task(record.task_id)
        if record.version != request.plan_version:
            raise ExecutionPlanningConflict("governance plan version is stale")
        plan = self._plan(record)
        preflight = await self.preflight.check(plan)
        if not preflight.valid:
            raise ExecutionPlanningConflict(preflight.model_dump_json())
        high_risk = any(operation.risk is RiskLevel.HIGH for operation in plan.operations)
        if high_risk and not request.high_risk_acknowledged:
            raise ExecutionPlanningConflict("high-risk acknowledgement is required")
        try:
            batch = await self.repository.create_batch(
                plan_id=plan.id,
                plan_version=plan.version,
                input_target_version_id=preflight.target_version_id,
                idempotency_key=idempotency_key,
                confirmed_by=self.operator.operator_id,
                high_risk_acknowledged=request.high_risk_acknowledged,
                preflight_result=preflight.model_dump(mode="json"),
            )
        except ExecutionPersistenceConflict as error:
            raise ExecutionPlanningConflict(str(error)) from error
        await self.repository.append_audit_event(
            batch_id=batch.id,
            actor_id=self.operator.operator_id,
            event_type="batch_confirmed",
            details={"plan_id": plan.id, "plan_version": plan.version},
        )
        return ExecutionBatchConfirmation(
            id=batch.id,
            plan_id=batch.plan_id,
            plan_version=batch.plan_version,
            input_target_version_id=batch.input_target_version_id,
            status=ExecutionBatchStatus.CONFIRMED,
            confirmed_by=batch.confirmed_by,
            independent_reviewer_id=batch.independent_reviewer_id,
            high_risk_acknowledged=batch.high_risk_acknowledged,
            preflight=preflight,
            confirmed_at=batch.confirmed_at,
        )

    async def get_plan(self, plan_id: UUID) -> GovernancePlan:
        record = await self.repository.get_plan(plan_id)
        if record is None:
            raise LookupError("governance plan not found")
        await self._require_task(record.task_id)
        return self._plan(record)

    async def revalidate(self, plan_id: UUID) -> PreflightResult:
        plan = await self.get_plan(plan_id)
        result = await self.preflight.check(plan)
        if not result.valid:
            raise ExecutionPlanningConflict(result.model_dump_json())
        return result

    async def _require_task(self, task_id: UUID) -> ReconciliationTask:
        task = await self.session.get(ReconciliationTask, task_id)
        if task is None or task.tenant_id != self.operator.tenant_id:
            raise LookupError("reconciliation task not found")
        return task

    async def _task_snapshots(self, task_id: UUID) -> tuple[Snapshot, Snapshot]:
        records = tuple(
            await self.session.scalars(select(Snapshot).where(Snapshot.task_id == task_id))
        )
        by_role = {record.source_role: record for record in records}
        try:
            return by_role["authoritative"], by_role["target"]
        except KeyError as error:
            raise ExecutionPlanningConflict("task snapshots are incomplete") from error

    async def _ensure_target_version(
        self, task: ReconciliationTask, target_snapshot: Snapshot
    ) -> TargetVersionRecord:
        current = await self.preflight.current_target_version(task.id)
        if current is not None:
            return current
        source_file = await self.session.get(SourceFile, target_snapshot.source_file_id)
        if source_file is None:
            raise ExecutionPlanningConflict("target source file is missing")
        return await self.repository.create_target_version(
            task_id=task.id,
            tenant_id=task.tenant_id,
            source_snapshot_id=target_snapshot.id,
            parent_version_id=None,
            batch_id=None,
            file_sha256=target_snapshot.file_hash,
            content_hash=target_snapshot.content_hash,
            storage_path=Path(source_file.storage_path),
        )

    async def _reviewed_proposal(
        self,
        *,
        task: ReconciliationTask,
        reference_id: UUID,
        reference_version: int,
        source_snapshot: Snapshot,
        target_snapshot: Snapshot,
        target_version: TargetVersionRecord,
    ) -> ReviewedProposalSnapshot:
        proposal = await self.session.get(GovernanceProposalRecord, reference_id)
        if proposal is None or proposal.task_id != task.id or proposal.tenant_id != task.tenant_id:
            raise LookupError("governance proposal not found")
        current_version = await self.session.scalar(
            select(func.max(GovernanceProposalRecord.proposal_version)).where(
                GovernanceProposalRecord.difference_id == proposal.difference_id,
                GovernanceProposalRecord.difference_version == proposal.difference_version,
            )
        )
        difference = await self.session.get(DifferenceRecord, proposal.difference_id)
        analysis = await self.session.get(AnalysisRecord, proposal.analysis_id)
        if difference is None or analysis is None:
            raise ExecutionPlanningConflict("proposal context is incomplete")
        before = {item["field"]: item.get("before") for item in proposal.changes}
        after = {item["field"]: item.get("after") for item in proposal.changes}
        operation_type = OperationType(proposal.operation_type)
        if operation_type is OperationType.CREATE:
            if difference.source_entity_id is None:
                raise ExecutionPlanningConflict(
                    "create proposal is missing its authoritative source entity"
                )
            source = await self.session.get(
                CanonicalEntityRecord,
                difference.source_entity_id,
            )
            if source is None or source.snapshot_id != source_snapshot.id:
                raise ExecutionPlanningConflict("create proposal source entity is stale or missing")
            after["source_id"] = source.source_id
        target_source_identifier: str | None = None
        if proposal.target_entity_id is not None:
            target = await self.session.get(CanonicalEntityRecord, proposal.target_entity_id)
            target_source_identifier = target.source_id if target is not None else None
        return ReviewedProposalSnapshot(
            proposal=ProposalVersionRef(
                proposal_id=proposal.id,
                proposal_version=reference_version,
            ),
            current_proposal_version=current_version or 0,
            status=ProposalStatus(proposal.status),
            task_id=proposal.task_id,
            source_snapshot_id=source_snapshot.id,
            target_snapshot_id=target_snapshot.id,
            target_version=f"sha256:{target_version.file_sha256}",
            proposal_source=ProposalSource(proposal.proposal_source),
            difference_id=difference.id,
            difference_version=proposal.difference_version,
            current_difference_version=difference.version,
            analysis_id=analysis.id,
            analysis_version=proposal.analysis_version,
            current_analysis_version=analysis.analysis_version,
            difference_type=DifferenceType(difference.difference_type),
            operation_type=operation_type,
            entity_type=EntityType(difference.entity_type),
            target_entity_id=proposal.target_entity_id,
            target_source_identifier=target_source_identifier,
            before=None if operation_type is OperationType.CREATE else before,
            after=after,
            changed_fields=frozenset(after),
            dependencies=frozenset(),
            reversible=True,
            risk=RiskLevel(proposal.risk),
        )

    @staticmethod
    def _plan(record: GovernancePlanRecord) -> GovernancePlan:
        return plan_from_record_payload(
            plan_id=record.id,
            version=record.version,
            task_id=record.task_id,
            source_snapshot_id=record.source_snapshot_id,
            target_snapshot_id=record.target_snapshot_id,
            target_version=record.target_version,
            proposal_versions=record.proposal_versions,
            operations=record.operations,
            content_hash=record.content_hash,
        )
