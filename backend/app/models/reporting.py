from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

json_document = JSON().with_variant(JSONB(), "postgresql")


class ImmutableReportingRecordError(ValueError):
    pass


class AgentReportRecord(Base, TimestampMixin):
    __tablename__ = "agent_reports"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("reconciliation_tasks.id", ondelete="CASCADE"), unique=True, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    terminal_state: Mapped[str] = mapped_column(String(64), index=True)
    facts: Mapped[dict[str, Any]] = mapped_column(json_document)
    facts_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict[str, Any]] = mapped_column(json_document)
    rollback_eligible: Mapped[bool] = mapped_column(index=True)
    deletion_eligible: Mapped[bool] = mapped_column(index=True)
    generated_by: Mapped[str] = mapped_column(String(128), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


def _reject_report_job_fact_mutation(_mapper: object, _connection: object, target: object) -> None:
    state = inspect(target)
    if state is None:
        raise ImmutableReportingRecordError("report job inspection failed")
    if any(state.attrs[name].history.has_changes() for name in (
        "execution_id",
        "tenant_id",
        "version",
        "idempotency_key",
        "facts",
        "facts_hash",
        "requested_by",
    )):
        raise ImmutableReportingRecordError("report job facts are immutable")


class ReportJobRecord(Base, TimestampMixin):
    __tablename__ = "report_jobs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    execution_id: Mapped[UUID] = mapped_column(ForeignKey("execution_batches.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    facts: Mapped[dict[str, Any]] = mapped_column(json_document)
    facts_hash: Mapped[str] = mapped_column(String(64))
    requested_by: Mapped[str] = mapped_column(String(128), index=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        UniqueConstraint("execution_id", "version", name="uq_report_job_version"),
        UniqueConstraint(
            "tenant_id", "execution_id", "idempotency_key", name="uq_report_job_idempotency"
        ),
    )


class GovernanceReportRecord(Base):
    __tablename__ = "governance_reports"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("report_jobs.id"), unique=True, index=True)
    execution_id: Mapped[UUID] = mapped_column(ForeignKey("execution_batches.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(json_document)
    facts: Mapped[dict[str, Any]] = mapped_column(json_document)
    facts_hash: Mapped[str] = mapped_column(String(64))
    html_content: Mapped[str] = mapped_column(Text)
    html_hash: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[dict[str, Any]] = mapped_column(json_document)
    generated_by: Mapped[str] = mapped_column(String(128), index=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint("execution_id", "version", name="uq_governance_report_version"),
    )


class RestoreRequestRecord(Base, TimestampMixin):
    __tablename__ = "restore_requests"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    source_version_id: Mapped[UUID] = mapped_column(ForeignKey("target_versions.id"), index=True)
    semantic_source_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("target_versions.id"), index=True
    )
    target_version_id: Mapped[UUID] = mapped_column(ForeignKey("target_versions.id"), index=True)
    preview_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    deterministic_plan: Mapped[dict[str, Any]] = mapped_column(json_document)
    covered_execution_ids: Mapped[list[str]] = mapped_column(json_document)
    ai_candidate: Mapped[dict[str, Any] | None] = mapped_column(json_document, nullable=True)
    ai_provenance: Mapped[dict[str, Any] | None] = mapped_column(json_document, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(128), index=True)


class RestoreExecutionLinkRecord(Base, TimestampMixin):
    __tablename__ = "restore_execution_links"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    restore_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("restore_requests.id"), unique=True, index=True
    )
    compensation_plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("governance_plans.id"), index=True
    )
    compensation_batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_batches.id"), unique=True, index=True
    )
    output_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("target_versions.id"), nullable=True, unique=True, index=True
    )


class RestoreExecutionResultRecord(Base, TimestampMixin):
    __tablename__ = "restore_execution_results"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    restore_execution_link_id: Mapped[UUID] = mapped_column(
        ForeignKey("restore_execution_links.id"), unique=True, index=True
    )
    output_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("target_versions.id"), unique=True, index=True
    )
    verified_content_hash: Mapped[str] = mapped_column(String(64))


def _reject_mutation(_mapper: object, _connection: object, target: object) -> None:
    raise ImmutableReportingRecordError(f"{type(target).__name__} is immutable")


for immutable_model in (
    GovernanceReportRecord,
    RestoreRequestRecord,
    RestoreExecutionLinkRecord,
    RestoreExecutionResultRecord,
):
    event.listen(immutable_model, "before_update", _reject_mutation)
    event.listen(immutable_model, "before_delete", _reject_mutation)

event.listen(ReportJobRecord, "before_update", _reject_report_job_fact_mutation)
event.listen(ReportJobRecord, "before_delete", _reject_mutation)
event.listen(AgentReportRecord, "before_update", _reject_mutation)
