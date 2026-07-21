import json
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import ColumnElement, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.executions import (
    ExecutionAuditEventRecord,
    ExecutionBatchRecord,
    ExecutionOperationRecord,
    GovernancePlanExplanationRecord,
    GovernancePlanRecord,
    OperationAttemptRecord,
    TargetVersionRecord,
)
from app.models.proposals import GovernanceProposalRecord
from app.models.reconciliation import ReconciliationTask
from app.models.reporting import RestoreExecutionLinkRecord, RestoreExecutionResultRecord
from app.models.snapshots import Snapshot
from app.schemas.canonical_entities import EntityType
from app.schemas.executions import (
    GovernanceOperation,
    GovernancePlan,
    OperationStatus,
    OperationType,
    PlanExplanationResponse,
    ProposalSource,
    ProposalVersionRef,
)
from app.schemas.governance import RiskLevel

if TYPE_CHECKING:
    from app.executions.executor import StoredExecutionOperation


class ExecutionPersistenceConflict(ValueError):
    pass


class ExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_plan(
        self,
        plan: GovernancePlan,
        *,
        created_by: str,
    ) -> GovernancePlanRecord:
        if not created_by.strip():
            raise ValueError("plan creator is required")
        existing = await self.session.scalar(
            select(GovernancePlanRecord).where(
                GovernancePlanRecord.task_id == plan.task_id,
                GovernancePlanRecord.content_hash == plan.content_hash,
            )
        )
        if existing is not None:
            return existing
        payload = plan.model_dump(mode="json")
        record = GovernancePlanRecord(
            id=plan.id,
            task_id=plan.task_id,
            version=plan.version,
            source_snapshot_id=plan.source_snapshot_id,
            target_snapshot_id=plan.target_snapshot_id,
            target_version=plan.target_version,
            proposal_versions=cast(list[dict[str, Any]], payload["proposals"]),
            operations=cast(list[dict[str, Any]], payload["operations"]),
            content_hash=plan.content_hash,
            created_by=created_by,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(record)
                await self.session.flush()
        except IntegrityError as error:
            existing = cast(
                GovernancePlanRecord | None,
                await self.session.scalar(
                    select(GovernancePlanRecord).where(
                        GovernancePlanRecord.task_id == plan.task_id,
                        GovernancePlanRecord.content_hash == plan.content_hash,
                    )
                ),
            )
            if existing is not None:
                return existing
            raise ExecutionPersistenceConflict("governance plan could not be stored") from error
        return record

    async def get_plan(self, plan_id: UUID) -> GovernancePlanRecord | None:
        return await self.session.get(GovernancePlanRecord, plan_id)

    async def append_plan_explanation(
        self,
        plan_id: UUID,
        response: PlanExplanationResponse,
    ) -> GovernancePlanExplanationRecord:
        if await self.get_plan(plan_id) is None:
            raise LookupError("governance plan not found")
        if response.request_id is not None:
            existing = await self.session.scalar(
                select(GovernancePlanExplanationRecord).where(
                    GovernancePlanExplanationRecord.request_id == response.request_id
                )
            )
            if existing is not None:
                return existing
        record = GovernancePlanExplanationRecord(
            plan_id=plan_id,
            explanation=response.explanation.model_dump(mode="json"),
            provider=response.provider,
            model=response.model,
            usage={
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
            },
            request_id=response.request_id,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_batch(self, batch_id: UUID) -> ExecutionBatchRecord | None:
        return await self.session.get(ExecutionBatchRecord, batch_id)

    async def get_target_version(self, version_id: UUID) -> TargetVersionRecord | None:
        return await self.session.get(TargetVersionRecord, version_id)

    @staticmethod
    def _accepted_target_version_clause() -> ColumnElement[bool]:
        restore_batch = exists(
            select(RestoreExecutionLinkRecord.id).where(
                RestoreExecutionLinkRecord.compensation_batch_id == TargetVersionRecord.batch_id
            )
        )
        accepted_restore_output = exists(
            select(RestoreExecutionResultRecord.id)
            .join(
                RestoreExecutionLinkRecord,
                RestoreExecutionResultRecord.restore_execution_link_id
                == RestoreExecutionLinkRecord.id,
            )
            .where(RestoreExecutionResultRecord.output_version_id == TargetVersionRecord.id)
        )
        return or_(~restore_batch, accepted_restore_output)

    async def current_target_version(self, task_id: UUID) -> TargetVersionRecord | None:
        return cast(
            TargetVersionRecord | None,
            await self.session.scalar(
                select(TargetVersionRecord)
                .where(
                    TargetVersionRecord.task_id == task_id,
                    self._accepted_target_version_clause(),
                )
                .order_by(TargetVersionRecord.created_at.desc(), TargetVersionRecord.id.desc())
            ),
        )

    async def latest_target_version_for_batch(self, batch_id: UUID) -> TargetVersionRecord | None:
        return cast(
            TargetVersionRecord | None,
            await self.session.scalar(
                select(TargetVersionRecord)
                .where(TargetVersionRecord.batch_id == batch_id)
                .order_by(
                    TargetVersionRecord.created_at.desc(),
                    TargetVersionRecord.id.desc(),
                )
            ),
        )

    async def retry_target_version(self, batch_id: UUID) -> TargetVersionRecord:
        batch = await self.get_batch(batch_id)
        if batch is None:
            raise LookupError("execution batch not found")
        plan = await self.get_plan(batch.plan_id)
        if plan is None:
            raise LookupError("governance plan not found")
        candidate = await self.latest_target_version_for_batch(batch_id)
        if candidate is None:
            candidate = await self.get_target_version(batch.input_target_version_id)
        if candidate is None:
            raise LookupError("execution target version not found")
        current = await self.current_target_version(plan.task_id)
        if current is None or current.id != candidate.id:
            raise ExecutionPersistenceConflict("target version drift blocks retry")
        return candidate

    async def execution_operations(self, batch_id: UUID) -> tuple["StoredExecutionOperation", ...]:
        from app.executions.executor import StoredExecutionOperation

        records = await self.list_operations(batch_id)
        return tuple(
            StoredExecutionOperation(
                record_id=record.id,
                operation=GovernanceOperation(
                    id=record.operation_id,
                    proposal=ProposalVersionRef(
                        proposal_id=record.proposal_id,
                        proposal_version=record.proposal_version,
                    ),
                    proposal_source=ProposalSource(record.proposal_source),
                    difference_id=record.difference_id,
                    difference_version=record.difference_version,
                    analysis_id=record.analysis_id,
                    analysis_version=record.analysis_version,
                    operation_type=OperationType(record.operation_type),
                    entity_type=EntityType(record.entity_type),
                    target_entity_id=record.target_entity_id,
                    target_source_identifier=record.target_source_identifier,
                    before=record.before,
                    after=record.after,
                    changed_fields=frozenset(record.changed_fields),
                    dependencies=frozenset(UUID(item) for item in record.dependencies),
                    reversible=record.reversible,
                    risk=RiskLevel(record.risk),
                    compensation_for=record.compensation_for,
                    restore_absence=record.restore_absence,
                ),
            )
            for record in records
        )

    async def create_batch(
        self,
        *,
        plan_id: UUID,
        plan_version: int,
        input_target_version_id: UUID,
        idempotency_key: str,
        confirmed_by: str,
        high_risk_acknowledged: bool,
        preflight_result: Mapping[str, Any],
        independent_reviewer_id: str | None = None,
    ) -> ExecutionBatchRecord:
        if not idempotency_key.strip():
            raise ValueError("batch idempotency key is required")
        if not confirmed_by.strip():
            raise ValueError("batch confirmer is required")
        if independent_reviewer_id is not None and not independent_reviewer_id.strip():
            raise ValueError("independent reviewer cannot be blank")
        normalized_preflight = _json_value(preflight_result)
        existing = await self.session.scalar(
            select(ExecutionBatchRecord).where(
                ExecutionBatchRecord.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            if (
                existing.plan_id != plan_id
                or existing.plan_version != plan_version
                or existing.input_target_version_id != input_target_version_id
                or existing.confirmed_by != confirmed_by
                or existing.independent_reviewer_id != independent_reviewer_id
                or existing.high_risk_acknowledged != high_risk_acknowledged
                or existing.preflight_result != normalized_preflight
            ):
                raise ExecutionPersistenceConflict(
                    "batch idempotency key was used for another confirmation"
                )
            return existing
        plan = await self.session.get(GovernancePlanRecord, plan_id)
        if plan is None:
            raise LookupError("governance plan not found")
        if plan.version != plan_version:
            raise ExecutionPersistenceConflict("governance plan version is stale")
        input_version = await self.session.get(TargetVersionRecord, input_target_version_id)
        if input_version is None:
            raise LookupError("input target version not found")
        if input_version.task_id != plan.task_id:
            raise ExecutionPersistenceConflict("target version belongs to another task")

        batch = ExecutionBatchRecord(
            plan_id=plan.id,
            plan_version=plan.version,
            input_target_version_id=input_version.id,
            idempotency_key=idempotency_key,
            status="confirmed",
            confirmed_by=confirmed_by,
            independent_reviewer_id=independent_reviewer_id,
            high_risk_acknowledged=high_risk_acknowledged,
            preflight_result=normalized_preflight,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(batch)
                await self.session.flush()
                for payload in plan.operations:
                    operation = GovernanceOperation.model_validate_json(json.dumps(payload))
                    await self._validate_operation_references(operation)
                    self.session.add(self._operation_record(batch.id, operation))
                await self.session.flush()
        except IntegrityError as error:
            raise ExecutionPersistenceConflict(
                "execution batch and operations could not be stored"
            ) from error
        return batch

    async def list_operations(self, batch_id: UUID) -> tuple[ExecutionOperationRecord, ...]:
        records = await self.session.scalars(
            select(ExecutionOperationRecord)
            .where(ExecutionOperationRecord.batch_id == batch_id)
            .order_by(ExecutionOperationRecord.created_at, ExecutionOperationRecord.id)
        )
        return tuple(records)

    async def append_attempt(
        self,
        operation_id: UUID,
        *,
        status: str | OperationStatus,
        error_code: str | None = None,
        error_detail: Mapping[str, Any] | None = None,
        actual_after: Mapping[str, Any] | None = None,
        verification: Mapping[str, Any] | None = None,
        retryable: bool = False,
        target_version_id: UUID | None = None,
    ) -> OperationAttemptRecord:
        if await self.session.get(ExecutionOperationRecord, operation_id) is None:
            raise LookupError("execution operation not found")
        normalized_status = OperationStatus(status).value
        if target_version_id is not None:
            if await self.session.get(TargetVersionRecord, target_version_id) is None:
                raise LookupError("attempt target version not found")
        for _attempt in range(3):
            latest = await self.session.scalar(
                select(func.max(OperationAttemptRecord.attempt_number)).where(
                    OperationAttemptRecord.operation_id == operation_id
                )
            )
            record = OperationAttemptRecord(
                operation_id=operation_id,
                attempt_number=(latest or 0) + 1,
                status=normalized_status,
                error_code=error_code,
                error_detail=_optional_json(error_detail),
                actual_after=_optional_json(actual_after),
                verification=_optional_json(verification),
                retryable=retryable,
                target_version_id=target_version_id,
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(record)
                    await self.session.flush()
            except IntegrityError:
                continue
            return record
        raise ExecutionPersistenceConflict("could not allocate operation attempt number")

    async def list_attempts(self, operation_id: UUID) -> tuple[OperationAttemptRecord, ...]:
        records = await self.session.scalars(
            select(OperationAttemptRecord)
            .where(OperationAttemptRecord.operation_id == operation_id)
            .order_by(OperationAttemptRecord.attempt_number, OperationAttemptRecord.id)
        )
        return tuple(records)

    async def create_target_version(
        self,
        *,
        task_id: UUID,
        tenant_id: str,
        source_snapshot_id: UUID,
        parent_version_id: UUID | None,
        batch_id: UUID | None,
        file_sha256: str,
        content_hash: str,
        storage_path: str | Path,
    ) -> TargetVersionRecord:
        if not tenant_id.strip():
            raise ValueError("target version tenant is required")
        task = await self.session.scalar(
            select(ReconciliationTask).where(ReconciliationTask.id == task_id).with_for_update()
        )
        if task is None:
            raise LookupError("target version task not found")
        if task.tenant_id != tenant_id:
            raise ExecutionPersistenceConflict("target version tenant does not match task")
        source_snapshot = await self.session.get(Snapshot, source_snapshot_id)
        if source_snapshot is None:
            raise LookupError("target version source snapshot not found")
        if source_snapshot.task_id != task_id:
            raise ExecutionPersistenceConflict(
                "target version source snapshot belongs to another task"
            )
        if parent_version_id is not None:
            parent = await self.session.get(TargetVersionRecord, parent_version_id)
            if parent is None:
                raise LookupError("parent target version not found")
            if (
                parent.task_id != task_id
                or parent.tenant_id != tenant_id
                or parent.source_snapshot_id != source_snapshot_id
            ):
                raise ExecutionPersistenceConflict(
                    "parent target version belongs to another task, tenant, or snapshot"
                )
            current = await self.current_target_version(task_id)
            if current is None or current.id != parent_version_id:
                raise ExecutionPersistenceConflict("parent target version is no longer current")
        if batch_id is not None:
            batch = await self.session.get(ExecutionBatchRecord, batch_id)
            if batch is None:
                raise LookupError("execution batch not found")
            plan = await self.session.get(GovernancePlanRecord, batch.plan_id)
            if plan is None or plan.task_id != task_id:
                raise ExecutionPersistenceConflict("execution batch belongs to another task")
        record = TargetVersionRecord(
            parent_version_id=parent_version_id,
            task_id=task_id,
            tenant_id=tenant_id,
            source_snapshot_id=source_snapshot_id,
            batch_id=batch_id,
            file_sha256=file_sha256,
            content_hash=content_hash,
            storage_path=str(storage_path),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(record)
                await self.session.flush()
        except IntegrityError as error:
            raise ExecutionPersistenceConflict("target version could not be stored") from error
        return record

    async def append_audit_event(
        self,
        *,
        batch_id: UUID,
        actor_id: str,
        event_type: str,
        details: Mapping[str, Any],
        operation_id: UUID | None = None,
    ) -> ExecutionAuditEventRecord:
        if not actor_id.strip():
            raise ValueError("audit actor is required")
        if not event_type.strip():
            raise ValueError("audit event type is required")
        if await self.session.get(ExecutionBatchRecord, batch_id) is None:
            raise LookupError("execution batch not found")
        if operation_id is not None:
            operation = await self.session.get(ExecutionOperationRecord, operation_id)
            if operation is None or operation.batch_id != batch_id:
                raise ExecutionPersistenceConflict("audit operation does not belong to the batch")
        record = ExecutionAuditEventRecord(
            batch_id=batch_id,
            operation_id=operation_id,
            actor_id=actor_id,
            event_type=event_type,
            details=_json_value(details),
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def _validate_operation_references(self, operation: GovernanceOperation) -> None:
        proposal = await self.session.get(GovernanceProposalRecord, operation.proposal.proposal_id)
        if proposal is None:
            raise ExecutionPersistenceConflict("operation proposal not found")
        if (
            proposal.proposal_version != operation.proposal.proposal_version
            or proposal.proposal_source != operation.proposal_source.value
            or proposal.difference_id != operation.difference_id
            or proposal.difference_version != operation.difference_version
            or proposal.analysis_id != operation.analysis_id
            or proposal.analysis_version != operation.analysis_version
        ):
            raise ExecutionPersistenceConflict("operation does not match its exact proposal facts")

    @staticmethod
    def _operation_record(
        batch_id: UUID, operation: GovernanceOperation
    ) -> ExecutionOperationRecord:
        payload = operation.model_dump(mode="json")
        return ExecutionOperationRecord(
            batch_id=batch_id,
            operation_id=operation.id,
            proposal_id=operation.proposal.proposal_id,
            proposal_version=operation.proposal.proposal_version,
            proposal_source=operation.proposal_source.value,
            difference_id=operation.difference_id,
            difference_version=operation.difference_version,
            analysis_id=operation.analysis_id,
            analysis_version=operation.analysis_version,
            operation_type=operation.operation_type.value,
            entity_type=operation.entity_type.value,
            target_entity_id=operation.target_entity_id,
            target_source_identifier=operation.target_source_identifier,
            before=cast(dict[str, Any] | None, payload["before"]),
            after=cast(dict[str, Any] | None, payload["after"]),
            changed_fields=sorted(operation.changed_fields),
            dependencies=sorted(str(item) for item in operation.dependencies),
            reversible=operation.reversible,
            risk=operation.risk.value,
            compensation_for=operation.compensation_for,
            restore_absence=operation.restore_absence,
        )


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


def _optional_json(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return cast(dict[str, Any] | None, _json_value(value))
