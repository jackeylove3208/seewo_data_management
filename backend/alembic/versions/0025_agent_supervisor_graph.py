"""Add controlled Agent graph audit persistence.

Revision ID: 0025_agent_supervisor_graph
Revises: 0024_agent_task_api
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision: str = "0025_agent_supervisor_graph"
down_revision: str | Sequence[str] | None = "0024_agent_task_api"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    tables = (
        set()
        if context.is_offline_mode()
        else set(inspect(op.get_bind()).get_table_names())
    )
    if "agent_graph_runs" not in tables:
        op.create_table(
            "agent_graph_runs",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("run_id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("graph_version", sa.String(length=128), nullable=False),
            sa.Column("current_node", sa.String(length=128), nullable=False),
            sa.Column("cursor", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("retry_count", sa.Integer(), nullable=False),
            sa.Column("replan_count", sa.Integer(), nullable=False),
            sa.Column("termination_requested", sa.Boolean(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id"),
        )
        for column in ("run_id", "tenant_id", "current_node", "status"):
            op.create_index(f"ix_agent_graph_runs_{column}", "agent_graph_runs", [column])

    if "agent_graph_candidate_sets" not in tables:
        op.create_table(
            "agent_graph_candidate_sets",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("graph_run_id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("cursor", sa.Integer(), nullable=False),
            sa.Column("action_set_hash", sa.String(length=71), nullable=False),
            sa.Column("candidate_evaluations", _json_type(), nullable=False),
            sa.Column("allowed_actions", _json_type(), nullable=False),
            sa.Column("single_action_reason_code", sa.String(length=64), nullable=True),
            sa.Column("excluded_action_summaries", _json_type(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["graph_run_id"], ["agent_graph_runs.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "graph_run_id",
                "cursor",
                name="uq_agent_graph_candidate_set_cursor",
            ),
        )
        for column in ("graph_run_id", "tenant_id"):
            op.create_index(
                f"ix_agent_graph_candidate_sets_{column}",
                "agent_graph_candidate_sets",
                [column],
            )

    if "agent_supervisor_decisions" not in tables:
        op.create_table(
            "agent_supervisor_decisions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("candidate_set_id", sa.Uuid(), nullable=False),
            sa.Column("graph_run_id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("cursor", sa.Integer(), nullable=False),
            sa.Column("selected_action_id", sa.String(length=128), nullable=False),
            sa.Column("decision", _json_type(), nullable=False),
            sa.Column("model_provenance", _json_type(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["candidate_set_id"],
                ["agent_graph_candidate_sets.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["graph_run_id"], ["agent_graph_runs.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("candidate_set_id"),
        )
        for column in (
            "candidate_set_id",
            "graph_run_id",
            "tenant_id",
            "selected_action_id",
        ):
            op.create_index(
                f"ix_agent_supervisor_decisions_{column}",
                "agent_supervisor_decisions",
                [column],
            )

    if "agent_graph_transitions" not in tables:
        op.create_table(
            "agent_graph_transitions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("graph_run_id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("cursor", sa.Integer(), nullable=False),
            sa.Column("from_node", sa.String(length=128), nullable=False),
            sa.Column("to_node", sa.String(length=128), nullable=False),
            sa.Column("action_id", sa.String(length=128), nullable=False),
            sa.Column("guard_results", _json_type(), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["graph_run_id"], ["agent_graph_runs.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "graph_run_id",
                "cursor",
                name="uq_agent_graph_transition_cursor",
            ),
        )
        for column in (
            "graph_run_id",
            "tenant_id",
            "from_node",
            "to_node",
            "action_id",
        ):
            op.create_index(
                f"ix_agent_graph_transitions_{column}",
                "agent_graph_transitions",
                [column],
            )

    if "agent_evidence_manifests" not in tables:
        op.create_table(
            "agent_evidence_manifests",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("graph_run_id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("cursor", sa.Integer(), nullable=False),
            sa.Column("graph_node", sa.String(length=128), nullable=False),
            sa.Column("action_id", sa.String(length=128), nullable=False),
            sa.Column("manifest", _json_type(), nullable=False),
            sa.Column("content_hash", sa.String(length=71), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["graph_run_id"], ["agent_graph_runs.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "graph_run_id",
                "cursor",
                "action_id",
                "content_hash",
                name="uq_agent_evidence_manifest_content",
            ),
        )
        for column in (
            "graph_run_id",
            "tenant_id",
            "graph_node",
            "action_id",
            "content_hash",
        ):
            op.create_index(
                f"ix_agent_evidence_manifests_{column}",
                "agent_evidence_manifests",
                [column],
            )

    if "agent_subagent_invocations" not in tables:
        op.create_table(
            "agent_subagent_invocations",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("graph_run_id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("cursor", sa.Integer(), nullable=False),
            sa.Column("action_id", sa.String(length=128), nullable=False),
            sa.Column("evidence_manifest_id", sa.Uuid(), nullable=False),
            sa.Column("execution_mode", sa.String(length=32), nullable=False),
            sa.Column("skill_name", sa.String(length=128), nullable=False),
            sa.Column("skill_version", sa.String(length=64), nullable=False),
            sa.Column("schema_version", sa.String(length=64), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("input_hash", sa.String(length=71), nullable=False),
            sa.Column("output_hash", sa.String(length=71), nullable=False),
            sa.Column("model_provenance", _json_type(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["evidence_manifest_id"],
                ["agent_evidence_manifests.id"],
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["graph_run_id"], ["agent_graph_runs.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "graph_run_id",
                "cursor",
                "action_id",
                "attempt",
                name="uq_agent_subagent_invocation_attempt",
            ),
        )
        for column in (
            "graph_run_id",
            "tenant_id",
            "action_id",
            "evidence_manifest_id",
            "execution_mode",
            "status",
        ):
            op.create_index(
                f"ix_agent_subagent_invocations_{column}",
                "agent_subagent_invocations",
                [column],
            )

    if "agent_tool_calls" not in tables:
        op.create_table(
            "agent_tool_calls",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("invocation_id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("tool_name", sa.String(length=128), nullable=False),
            sa.Column("arguments_hash", sa.String(length=71), nullable=False),
            sa.Column("result_hash", sa.String(length=71), nullable=False),
            sa.Column("authorized", sa.Boolean(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("trace_id", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["invocation_id"],
                ["agent_subagent_invocations.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "invocation_id",
                "sequence",
                name="uq_agent_tool_call_sequence",
            ),
        )
        for column in (
            "invocation_id",
            "tenant_id",
            "tool_name",
            "status",
            "trace_id",
        ):
            op.create_index(
                f"ix_agent_tool_calls_{column}",
                "agent_tool_calls",
                [column],
            )

    if "agent_human_gates" not in tables:
        op.create_table(
            "agent_human_gates",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("graph_run_id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("cursor", sa.Integer(), nullable=False),
            sa.Column("gate_kind", sa.String(length=64), nullable=False),
            sa.Column("member_ids", _json_type(), nullable=False),
            sa.Column("content_hash", sa.String(length=71), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("decision", _json_type(), nullable=True),
            sa.Column("decided_by", sa.String(length=255), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["graph_run_id"], ["agent_graph_runs.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "graph_run_id",
                "cursor",
                "gate_kind",
                "content_hash",
                name="uq_agent_human_gate_content",
            ),
        )
        for column in ("graph_run_id", "tenant_id", "gate_kind", "status"):
            op.create_index(
                f"ix_agent_human_gates_{column}",
                "agent_human_gates",
                [column],
            )


def downgrade() -> None:
    for table in (
        "agent_human_gates",
        "agent_tool_calls",
        "agent_subagent_invocations",
        "agent_evidence_manifests",
        "agent_graph_transitions",
        "agent_supervisor_decisions",
        "agent_graph_candidate_sets",
        "agent_graph_runs",
    ):
        op.drop_table(table)
