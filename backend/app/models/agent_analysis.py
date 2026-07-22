from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


def _json() -> JSON:
    return JSON().with_variant(JSONB(), "postgresql")


class ImmutableAgentAnalysisRecordError(ValueError):
    pass


class AgentConnectorCapabilityRecord(Base, TimestampMixin):
    __tablename__ = "agent_connector_capabilities"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    source_role: Mapped[str] = mapped_column(String(32))
    connector_kind: Mapped[str] = mapped_column(String(32))
    capability_hash: Mapped[str] = mapped_column(String(64))
    capabilities: Mapped[dict[str, Any]] = mapped_column(_json())

    __table_args__ = (
        UniqueConstraint("run_id", "source_role", "capability_hash", name="uq_agent_capability"),
        CheckConstraint(
            "source_role IN ('authoritative', 'target')", name="ck_agent_capability_source_role"
        ),
    )


class AgentInputRecord(Base, TimestampMixin):
    __tablename__ = "agent_input_records"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"), index=True)
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    source_role: Mapped[str] = mapped_column(String(32))
    stable_locator: Mapped[str] = mapped_column(String(512))
    stable_order: Mapped[int] = mapped_column(Integer)
    entity_kind: Mapped[str] = mapped_column(String(32), index=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    class_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    raw_row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("run_id", "source_role", "stable_order", name="uq_agent_input_order"),
        UniqueConstraint("run_id", "source_role", "stable_locator", name="uq_agent_input_locator"),
        CheckConstraint("stable_order >= 1", name="ck_agent_input_stable_order"),
        CheckConstraint(
            "entity_kind IN ('department', 'student', 'teacher')", name="ck_agent_input_kind"
        ),
        CheckConstraint(
            "entity_kind = 'student' OR class_name IS NULL", name="ck_agent_input_class"
        ),
        CheckConstraint(
            "source_role IN ('authoritative', 'target')", name="ck_agent_input_source_role"
        ),
    )


class AgentInputMarkRecord(Base, TimestampMixin):
    __tablename__ = "agent_input_marks"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    input_record_id: Mapped[UUID] = mapped_column(ForeignKey("agent_input_records.id"), index=True)
    reason_code: Mapped[str] = mapped_column(String(128), index=True)
    affected_fields: Mapped[list[str]] = mapped_column(_json(), default=list)
    inclusion_state: Mapped[str] = mapped_column(String(32), index=True)
    report_disposition: Mapped[str] = mapped_column(String(64))
    safe_evidence: Mapped[dict[str, Any]] = mapped_column(_json(), default=dict)

    __table_args__ = (
        UniqueConstraint("input_record_id", "reason_code", name="uq_agent_input_mark"),
        CheckConstraint(
            "inclusion_state IN ('included', 'excluded', 'anomaly')",
            name="ck_agent_mark_inclusion_state",
        ),
    )


class AgentIdentityPostingRecord(Base, TimestampMixin):
    __tablename__ = "agent_identity_postings"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"), index=True)
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    input_record_id: Mapped[UUID] = mapped_column(ForeignKey("agent_input_records.id"), index=True)
    entity_kind: Mapped[str] = mapped_column(String(32), index=True)
    key_kind: Mapped[str] = mapped_column(String(16), index=True)
    normalized_value: Mapped[str] = mapped_column(String(512))

    __table_args__ = (
        UniqueConstraint("run_id", "input_record_id", "key_kind", name="uq_agent_identity_posting"),
        CheckConstraint("key_kind IN ('number', 'phone', 'email')", name="ck_agent_posting_key"),
        Index(
            "ix_agent_identity_lookup",
            "tenant_id",
            "snapshot_id",
            "entity_kind",
            "key_kind",
            "normalized_value",
        ),
    )


class AgentWorkItemRecord(Base, TimestampMixin):
    __tablename__ = "agent_work_items"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    source_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    target_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    subject_input_id: Mapped[UUID] = mapped_column(ForeignKey("agent_input_records.id"), index=True)
    entity_kind: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    state: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    idempotency_hash: Mapped[str] = mapped_column(String(64))
    evidence_hash: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("run_id", "idempotency_hash", name="uq_agent_work_item_replay"),
        CheckConstraint(
            "entity_kind IN ('department', 'student', 'teacher')",
            name="ck_agent_work_item_entity_kind",
        ),
        CheckConstraint(
            "kind IN ('resolved', 'identity_conflict', 'target_extra', 'target_duplicate', "
            "'target_missing', 'field_difference', 'authority_invalid', 'correct')",
            name="ck_agent_work_item_kind",
        ),
        CheckConstraint(
            "state IN ('pending', 'claimed', 'awaiting_clarification', 'analyzed', 'blocked')",
            name="ck_agent_work_item_state",
        ),
    )


class AgentIdentityEvidenceRecord(Base, TimestampMixin):
    __tablename__ = "agent_identity_evidence"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    work_item_id: Mapped[UUID] = mapped_column(ForeignKey("agent_work_items.id"), index=True)
    posting_id: Mapped[UUID] = mapped_column(ForeignKey("agent_identity_postings.id"), index=True)
    key_kind: Mapped[str] = mapped_column(String(16))
    normalized_value: Mapped[str] = mapped_column(String(512))
    evidence_hash: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("work_item_id", "posting_id", name="uq_agent_identity_evidence"),
    )


class AgentIdentityClaimRecord(Base, TimestampMixin):
    __tablename__ = "agent_identity_claims"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"), index=True)
    source_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    target_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("snapshots.id"), index=True)
    authority_input_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_input_records.id"), index=True
    )
    target_input_id: Mapped[UUID] = mapped_column(ForeignKey("agent_input_records.id"), index=True)
    work_item_id: Mapped[UUID] = mapped_column(ForeignKey("agent_work_items.id"), index=True)

    __table_args__ = (
        UniqueConstraint("run_id", "authority_input_id", name="uq_agent_claim_authority"),
        UniqueConstraint("run_id", "target_input_id", name="uq_agent_claim_target"),
        UniqueConstraint("work_item_id", name="uq_agent_claim_work_item"),
    )


class AgentModelBatchRecord(Base, TimestampMixin):
    __tablename__ = "agent_model_batches"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    entity_kind: Mapped[str] = mapped_column(String(32), index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    item_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "input_hash", name="uq_agent_model_batch_input"),
        CheckConstraint("item_count >= 1 AND item_count <= 50", name="ck_agent_batch_item_count"),
        CheckConstraint(
            "entity_kind IN ('department', 'student', 'teacher')",
            name="ck_agent_batch_entity_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'completed', 'blocked')",
            name="ck_agent_batch_status",
        ),
    )


class AgentModelBatchItemRecord(Base):
    __tablename__ = "agent_model_batch_items"

    batch_id: Mapped[UUID] = mapped_column(ForeignKey("agent_model_batches.id"), primary_key=True)
    work_item_id: Mapped[UUID] = mapped_column(ForeignKey("agent_work_items.id"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer)

    __table_args__ = (UniqueConstraint("batch_id", "ordinal", name="uq_agent_batch_item_order"),)


class AgentModelAttemptRecord(Base, TimestampMixin):
    __tablename__ = "agent_model_attempts"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("agent_model_batches.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    skill_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    skill_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gateway_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(_json(), default=dict)
    safe_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        UniqueConstraint("batch_id", "attempt_number", name="uq_agent_model_attempt"),
        CheckConstraint(
            "attempt_number >= 1 AND attempt_number <= 4", name="ck_agent_attempt_number"
        ),
        CheckConstraint("status IN ('succeeded', 'failed')", name="ck_agent_attempt_status"),
    )


class AgentFindingRecord(Base, TimestampMixin):
    __tablename__ = "agent_findings"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"), index=True)
    work_item_id: Mapped[UUID] = mapped_column(ForeignKey("agent_work_items.id"), index=True)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("agent_model_batches.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    category_zh: Mapped[str] = mapped_column(String(255))
    analysis_zh: Mapped[str] = mapped_column(String(8000))
    evidence_refs: Mapped[list[str]] = mapped_column(_json())
    content_hash: Mapped[str] = mapped_column(String(64))

    __table_args__ = (UniqueConstraint("work_item_id", name="uq_agent_finding_work_item"),)


class AgentFindingSolutionRecord(Base, TimestampMixin):
    __tablename__ = "agent_finding_solutions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    finding_id: Mapped[UUID] = mapped_column(ForeignKey("agent_findings.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(32))
    risk: Mapped[str] = mapped_column(String(32))
    solution_zh: Mapped[str] = mapped_column(String(4000))
    recommended: Mapped[bool] = mapped_column(Boolean)

    __table_args__ = (
        UniqueConstraint("finding_id", "ordinal", name="uq_agent_finding_solution"),
        CheckConstraint("ordinal >= 1 AND ordinal <= 3", name="ck_agent_solution_ordinal"),
        CheckConstraint(
            "operation IN ('create', 'update', 'delete', 'retain', 'skip')",
            name="ck_agent_solution_operation",
        ),
        CheckConstraint("risk IN ('low', 'medium', 'high')", name="ck_agent_solution_risk"),
    )


class AgentFindingDependencyRecord(Base):
    __tablename__ = "agent_finding_dependencies"

    finding_id: Mapped[UUID] = mapped_column(ForeignKey("agent_findings.id"), primary_key=True)
    depends_on_finding_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_findings.id"), primary_key=True
    )


def _reject_mutation(_mapper: object, _connection: object, target: object) -> None:
    raise ImmutableAgentAnalysisRecordError(f"{type(target).__name__} is immutable")


for _record in (
    AgentConnectorCapabilityRecord,
    AgentInputRecord,
    AgentInputMarkRecord,
    AgentIdentityPostingRecord,
    AgentWorkItemRecord,
    AgentIdentityEvidenceRecord,
    AgentIdentityClaimRecord,
    AgentModelAttemptRecord,
    AgentModelBatchItemRecord,
    AgentFindingRecord,
    AgentFindingSolutionRecord,
    AgentFindingDependencyRecord,
):
    event.listen(_record, "before_update", _reject_mutation)
    event.listen(_record, "before_delete", _reject_mutation)
