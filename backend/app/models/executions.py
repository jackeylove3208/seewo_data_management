from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

json_document = JSON().with_variant(JSONB(), "postgresql")


class ImmutableExecutionRecordError(ValueError):
    pass


class GovernancePlanRecord(Base, TimestampMixin):
    __tablename__ = "governance_plans"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("reconciliation_tasks.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    source_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    target_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    target_version: Mapped[str] = mapped_column(String(128))
    proposal_versions: Mapped[list[dict[str, Any]]] = mapped_column(json_document)
    operations: Mapped[list[dict[str, Any]]] = mapped_column(json_document)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(128), index=True)

    __table_args__ = (
        UniqueConstraint("task_id", "content_hash", name="uq_governance_plan_content"),
        CheckConstraint("version >= 1", name="ck_governance_plan_version"),
        CheckConstraint("length(content_hash) = 64", name="ck_governance_plan_hash"),
    )


class ExecutionBatchRecord(Base, TimestampMixin):
    __tablename__ = "execution_batches"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("governance_plans.id"), index=True)
    plan_version: Mapped[int] = mapped_column(Integer)
    input_target_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="confirmed", index=True)
    confirmed_by: Mapped[str] = mapped_column(String(128), index=True)
    independent_reviewer_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    high_risk_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    preflight_result: Mapped[dict[str, Any]] = mapped_column(json_document)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        CheckConstraint("plan_version >= 1", name="ck_execution_batch_plan_version"),
        CheckConstraint("status = 'confirmed'", name="ck_execution_batch_status"),
    )


class ExecutionOperationRecord(Base, TimestampMixin):
    __tablename__ = "execution_operations"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("execution_batches.id"), index=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey("governance_proposals.id"), index=True
    )
    proposal_version: Mapped[int] = mapped_column(Integer)
    proposal_source: Mapped[str] = mapped_column(String(32))
    difference_id: Mapped[UUID] = mapped_column(ForeignKey("difference_items.id"), index=True)
    difference_version: Mapped[int] = mapped_column(Integer)
    analysis_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_results.id"), index=True)
    analysis_version: Mapped[str] = mapped_column(String(64))
    operation_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    target_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("canonical_entities.id"), nullable=True
    )
    target_source_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(json_document, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(json_document, nullable=True)
    changed_fields: Mapped[list[str]] = mapped_column(json_document)
    dependencies: Mapped[list[str]] = mapped_column(json_document)
    reversible: Mapped[bool] = mapped_column(Boolean)
    risk: Mapped[str] = mapped_column(String(32))
    compensation_for: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    restore_absence: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("batch_id", "operation_id", name="uq_execution_batch_operation"),
        CheckConstraint("proposal_version >= 1", name="ck_execution_operation_proposal_version"),
        CheckConstraint(
            "difference_version >= 1", name="ck_execution_operation_difference_version"
        ),
        CheckConstraint(
            "proposal_source IN ('ai', 'operator')",
            name="ck_execution_operation_proposal_source",
        ),
        CheckConstraint(
            "operation_type IN ('create', 'update', 'move', 'disable', 'skip')",
            name="ck_execution_operation_type",
        ),
        CheckConstraint(
            "risk IN ('low', 'medium', 'high')", name="ck_execution_operation_risk"
        ),
    )


class TargetVersionRecord(Base, TimestampMixin):
    __tablename__ = "target_versions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    parent_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("target_versions.id"), nullable=True, index=True
    )
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("reconciliation_tasks.id"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    source_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    batch_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("execution_batches.id"), nullable=True, unique=True, index=True
    )
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    storage_path: Mapped[str] = mapped_column(String(1024), unique=True)

    __table_args__ = (
        CheckConstraint("length(file_sha256) = 64", name="ck_target_version_file_hash"),
        CheckConstraint("length(content_hash) = 64", name="ck_target_version_content_hash"),
    )


class OperationAttemptRecord(Base, TimestampMixin):
    __tablename__ = "operation_attempts"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_operations.id"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    error_detail: Mapped[dict[str, Any] | None] = mapped_column(json_document, nullable=True)
    actual_after: Mapped[dict[str, Any] | None] = mapped_column(json_document, nullable=True)
    verification: Mapped[dict[str, Any] | None] = mapped_column(json_document, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    target_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("target_versions.id"), nullable=True, index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "operation_id", "attempt_number", name="uq_operation_attempt_number"
        ),
        CheckConstraint("attempt_number >= 1", name="ck_operation_attempt_number"),
        CheckConstraint(
            "status IN ('pending', 'blocked', 'running', 'succeeded', 'failed', "
            "'verification_failed')",
            name="ck_operation_attempt_status",
        ),
    )


class ExecutionAuditEventRecord(Base, TimestampMixin):
    __tablename__ = "execution_audit_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("execution_batches.id"), index=True)
    operation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("execution_operations.id"), nullable=True, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(json_document)


def _reject_execution_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ImmutableExecutionRecordError(f"{type(target).__name__} is immutable")


for immutable_model in (
    GovernancePlanRecord,
    ExecutionBatchRecord,
    ExecutionOperationRecord,
    OperationAttemptRecord,
    TargetVersionRecord,
    ExecutionAuditEventRecord,
):
    event.listen(immutable_model, "before_update", _reject_execution_mutation)
    event.listen(immutable_model, "before_delete", _reject_execution_mutation)


# Short aliases keep the domain names from the execution contract available while
# the Record suffix remains consistent with the repository's existing model style.
GovernancePlan = GovernancePlanRecord
ExecutionBatch = ExecutionBatchRecord
ExecutionOperation = ExecutionOperationRecord
OperationAttempt = OperationAttemptRecord
TargetVersion = TargetVersionRecord
ExecutionAuditEvent = ExecutionAuditEventRecord
