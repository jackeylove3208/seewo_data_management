from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.type_api import TypeEngine

from app.models.base import Base, TimestampMixin


def _json_type() -> TypeEngine[Any]:
    return JSON().with_variant(JSONB(), "postgresql")


class AgentGraphRunRecord(Base, TimestampMixin):
    __tablename__ = "agent_graph_runs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), unique=True, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    graph_version: Mapped[str] = mapped_column(String(128))
    current_node: Mapped[str] = mapped_column(String(128), index=True)
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    replan_count: Mapped[int] = mapped_column(Integer, default=0)
    termination_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class AgentGraphCandidateSetRecord(Base, TimestampMixin):
    __tablename__ = "agent_graph_candidate_sets"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    graph_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_graph_runs.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    cursor: Mapped[int] = mapped_column(Integer)
    action_set_hash: Mapped[str] = mapped_column(String(71))
    candidate_evaluations: Mapped[list[dict[str, Any]]] = mapped_column(_json_type())
    allowed_actions: Mapped[list[dict[str, Any]]] = mapped_column(_json_type())
    single_action_reason_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    excluded_action_summaries: Mapped[list[dict[str, Any]]] = mapped_column(
        _json_type()
    )

    __table_args__ = (
        UniqueConstraint(
            "graph_run_id",
            "cursor",
            name="uq_agent_graph_candidate_set_cursor",
        ),
    )


class AgentSupervisorDecisionRecord(Base, TimestampMixin):
    __tablename__ = "agent_supervisor_decisions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    candidate_set_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_graph_candidate_sets.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    graph_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_graph_runs.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    cursor: Mapped[int] = mapped_column(Integer)
    selected_action_id: Mapped[str] = mapped_column(String(128), index=True)
    decision: Mapped[dict[str, Any]] = mapped_column(_json_type())
    model_provenance: Mapped[dict[str, Any]] = mapped_column(_json_type())


class AgentGraphTransitionRecord(Base, TimestampMixin):
    __tablename__ = "agent_graph_transitions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    graph_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_graph_runs.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    cursor: Mapped[int] = mapped_column(Integer)
    from_node: Mapped[str] = mapped_column(String(128), index=True)
    to_node: Mapped[str] = mapped_column(String(128), index=True)
    action_id: Mapped[str] = mapped_column(String(128), index=True)
    guard_results: Mapped[dict[str, Any]] = mapped_column(_json_type())
    fencing_token: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint(
            "graph_run_id",
            "cursor",
            name="uq_agent_graph_transition_cursor",
        ),
    )


class AgentEvidenceManifestRecord(Base, TimestampMixin):
    __tablename__ = "agent_evidence_manifests"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    graph_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_graph_runs.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    cursor: Mapped[int] = mapped_column(Integer)
    graph_node: Mapped[str] = mapped_column(String(128), index=True)
    action_id: Mapped[str] = mapped_column(String(128), index=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(_json_type())
    content_hash: Mapped[str] = mapped_column(String(71), index=True)

    __table_args__ = (
        UniqueConstraint(
            "graph_run_id",
            "cursor",
            "action_id",
            "content_hash",
            name="uq_agent_evidence_manifest_content",
        ),
    )


class AgentSubAgentInvocationRecord(Base, TimestampMixin):
    __tablename__ = "agent_subagent_invocations"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    graph_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_graph_runs.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    cursor: Mapped[int] = mapped_column(Integer)
    action_id: Mapped[str] = mapped_column(String(128), index=True)
    evidence_manifest_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_evidence_manifests.id", ondelete="RESTRICT"), index=True
    )
    execution_mode: Mapped[str] = mapped_column(String(32), index=True)
    skill_name: Mapped[str] = mapped_column(String(128))
    skill_version: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    input_hash: Mapped[str] = mapped_column(String(71))
    output_hash: Mapped[str] = mapped_column(String(71))
    model_provenance: Mapped[dict[str, Any]] = mapped_column(_json_type())

    __table_args__ = (
        UniqueConstraint(
            "graph_run_id",
            "cursor",
            "action_id",
            "attempt",
            name="uq_agent_subagent_invocation_attempt",
        ),
    )


class AgentToolCallRecord(Base, TimestampMixin):
    __tablename__ = "agent_tool_calls"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    invocation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_subagent_invocations.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(128), index=True)
    arguments_hash: Mapped[str] = mapped_column(String(71))
    result_hash: Mapped[str] = mapped_column(String(71))
    authorized: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(32), index=True)
    trace_id: Mapped[str] = mapped_column(String(128), index=True)

    __table_args__ = (
        UniqueConstraint(
            "invocation_id",
            "sequence",
            name="uq_agent_tool_call_sequence",
        ),
    )


class AgentHumanGateRecord(Base, TimestampMixin):
    __tablename__ = "agent_human_gates"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    graph_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_graph_runs.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    cursor: Mapped[int] = mapped_column(Integer)
    gate_kind: Mapped[str] = mapped_column(String(64), index=True)
    member_ids: Mapped[list[str]] = mapped_column(_json_type())
    content_hash: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(String(32), index=True)
    decision: Mapped[dict[str, Any] | None] = mapped_column(_json_type(), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "graph_run_id",
            "cursor",
            "gate_kind",
            "content_hash",
            name="uq_agent_human_gate_content",
        ),
    )
