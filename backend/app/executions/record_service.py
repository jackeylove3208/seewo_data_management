from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import OperatorContext
from app.models.executions import (
    ExecutionAuditEventRecord,
    ExecutionBatchRecord,
    GovernancePlanRecord,
    TargetVersionRecord,
)
from app.models.proposals import GovernanceProposalRecord
from app.models.reconciliation import ReconciliationTask
from app.repositories.executions import ExecutionRepository
from app.schemas.canonical_entities import EntityType
from app.schemas.executions import (
    ExecutionAttemptView,
    ExecutionAuditEventView,
    ExecutionBatchStatus,
    ExecutionOperationView,
    ExecutionRecordDetail,
    ExecutionRecordPage,
    ExecutionRecordSummary,
    OperationStatus,
    OperationType,
    ProposalSource,
)
from app.schemas.governance import RiskLevel


class ExecutionRecordService:
    def __init__(self, session: AsyncSession, *, operator: OperatorContext) -> None:
        self.session = session
        self.operator = operator
        self.repository = ExecutionRepository(session)

    async def get_detail(self, batch_id: UUID) -> ExecutionRecordDetail:
        batch, plan = await self._require_context(batch_id)
        operation_records = await self.repository.list_operations(batch.id)
        operations: list[ExecutionOperationView] = []
        latest_statuses: list[OperationStatus] = []
        retryable_count = 0
        for record in operation_records:
            attempts = await self.repository.list_attempts(record.id)
            if attempts:
                latest = attempts[-1]
                latest_statuses.append(OperationStatus(latest.status))
                retryable_count += int(
                    latest.status == OperationStatus.FAILED.value and latest.retryable
                )
            else:
                latest_statuses.append(OperationStatus.PENDING)
            proposal = await self.session.get(GovernanceProposalRecord, record.proposal_id)
            if proposal is None:
                raise LookupError("execution proposal not found")
            operations.append(
                ExecutionOperationView(
                    record_id=record.id,
                    operation_id=record.operation_id,
                    proposal_id=record.proposal_id,
                    proposal_version=record.proposal_version,
                    proposal_source=ProposalSource(record.proposal_source),
                    proposal_created_by=proposal.created_by,
                    difference_id=record.difference_id,
                    difference_version=record.difference_version,
                    operation_type=OperationType(record.operation_type),
                    entity_type=EntityType(record.entity_type),
                    target_source_identifier=record.target_source_identifier,
                    before=record.before,
                    after=record.after,
                    risk=RiskLevel(record.risk),
                    attempts=tuple(
                        ExecutionAttemptView(
                            attempt_number=attempt.attempt_number,
                            status=OperationStatus(attempt.status),
                            error_code=attempt.error_code,
                            error_detail=attempt.error_detail,
                            actual_after=attempt.actual_after,
                            verification=attempt.verification,
                            retryable=attempt.retryable,
                            target_version_id=attempt.target_version_id,
                            created_at=attempt.created_at,
                        )
                        for attempt in attempts
                    ),
                )
            )
        versions = tuple(
            await self.session.scalars(
                select(TargetVersionRecord)
                .where(TargetVersionRecord.batch_id == batch.id)
                .order_by(TargetVersionRecord.created_at, TargetVersionRecord.id)
            )
        )
        events = tuple(
            await self.session.scalars(
                select(ExecutionAuditEventRecord)
                .where(ExecutionAuditEventRecord.batch_id == batch.id)
                .order_by(
                    ExecutionAuditEventRecord.created_at,
                    ExecutionAuditEventRecord.id,
                )
            )
        )
        permitted: list[str] = []
        if retryable_count:
            permitted.append("retry")
        if versions:
            permitted.append("download")
        return ExecutionRecordDetail(
            id=batch.id,
            task_id=plan.task_id,
            source_snapshot_id=plan.source_snapshot_id,
            target_snapshot_id=plan.target_snapshot_id,
            plan_id=plan.id,
            plan_version=plan.version,
            plan_created_by=plan.created_by,
            status=_status(latest_statuses),
            confirmed_by=batch.confirmed_by,
            independent_reviewer_id=batch.independent_reviewer_id,
            high_risk_acknowledged=batch.high_risk_acknowledged,
            input_target_version_id=batch.input_target_version_id,
            output_target_version_ids=tuple(version.id for version in versions),
            confirmed_at=batch.confirmed_at,
            operations=tuple(operations),
            audit_events=tuple(
                ExecutionAuditEventView(
                    id=event.id,
                    operation_id=event.operation_id,
                    actor_id=event.actor_id,
                    event_type=event.event_type,
                    details=event.details,
                    created_at=event.created_at,
                )
                for event in events
            ),
            permitted_actions=tuple(permitted),
        )

    async def list_records(
        self,
        *,
        task_id: UUID | None = None,
        confirmed_by: str | None = None,
        status: ExecutionBatchStatus | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> ExecutionRecordPage:
        query = (
            select(ExecutionBatchRecord)
            .join(GovernancePlanRecord, GovernancePlanRecord.id == ExecutionBatchRecord.plan_id)
            .join(ReconciliationTask, ReconciliationTask.id == GovernancePlanRecord.task_id)
            .where(ReconciliationTask.tenant_id == self.operator.tenant_id)
            .order_by(ExecutionBatchRecord.created_at.desc(), ExecutionBatchRecord.id.desc())
        )
        if task_id is not None:
            query = query.where(GovernancePlanRecord.task_id == task_id)
        if confirmed_by is not None:
            query = query.where(ExecutionBatchRecord.confirmed_by == confirmed_by)
        if created_from is not None:
            query = query.where(ExecutionBatchRecord.created_at >= created_from)
        if created_to is not None:
            query = query.where(ExecutionBatchRecord.created_at <= created_to)
        if cursor is not None:
            cursor_batch, _cursor_plan = await self._require_context(cursor)
            query = query.where(
                or_(
                    ExecutionBatchRecord.created_at < cursor_batch.created_at,
                    and_(
                        ExecutionBatchRecord.created_at == cursor_batch.created_at,
                        ExecutionBatchRecord.id < cursor_batch.id,
                    ),
                )
            )
        records = tuple(await self.session.scalars(query.limit(limit + 1)))
        summaries: list[ExecutionRecordSummary] = []
        for batch in records[:limit]:
            detail = await self.get_detail(batch.id)
            if status is not None and detail.status is not status:
                continue
            summaries.append(
                ExecutionRecordSummary(
                    id=detail.id,
                    task_id=detail.task_id,
                    plan_id=detail.plan_id,
                    plan_version=detail.plan_version,
                    status=detail.status,
                    confirmed_by=detail.confirmed_by,
                    confirmed_at=detail.confirmed_at,
                    operation_count=len(detail.operations),
                    retryable_count=sum(
                        bool(operation.attempts and operation.attempts[-1].retryable)
                        for operation in detail.operations
                    ),
                    output_target_version_id=(
                        detail.output_target_version_ids[-1]
                        if detail.output_target_version_ids
                        else None
                    ),
                )
            )
        return ExecutionRecordPage(
            items=tuple(summaries),
            next_cursor=(
                str(records[limit - 1].id) if len(records) > limit and summaries else None
            ),
        )

    async def latest_output_version(self, batch_id: UUID) -> TargetVersionRecord:
        await self._require_context(batch_id)
        version = await self.session.scalar(
            select(TargetVersionRecord)
            .where(TargetVersionRecord.batch_id == batch_id)
            .order_by(TargetVersionRecord.created_at.desc(), TargetVersionRecord.id.desc())
        )
        if version is None:
            raise LookupError("execution target version not found")
        return version

    async def _require_context(
        self, batch_id: UUID
    ) -> tuple[ExecutionBatchRecord, GovernancePlanRecord]:
        batch = await self.repository.get_batch(batch_id)
        if batch is None:
            raise LookupError("execution record not found")
        plan = await self.repository.get_plan(batch.plan_id)
        if plan is None:
            raise LookupError("execution plan not found")
        task = await self.session.get(ReconciliationTask, plan.task_id)
        if task is None or task.tenant_id != self.operator.tenant_id:
            raise LookupError("execution record not found")
        return batch, plan


def _status(statuses: list[OperationStatus]) -> ExecutionBatchStatus:
    if not statuses or all(item is OperationStatus.PENDING for item in statuses):
        return ExecutionBatchStatus.CONFIRMED
    succeeded = sum(item is OperationStatus.SUCCEEDED for item in statuses)
    if succeeded == len(statuses):
        return ExecutionBatchStatus.SUCCEEDED
    if succeeded:
        return ExecutionBatchStatus.PARTIAL_FAILURE
    return ExecutionBatchStatus.FAILED
