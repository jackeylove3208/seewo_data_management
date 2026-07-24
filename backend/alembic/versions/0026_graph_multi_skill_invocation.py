"""Allow one graph action to invoke multiple pinned Skills.

Revision ID: 0026_graph_multi_skill
Revises: 0025_agent_supervisor_graph
"""

from collections.abc import Sequence

from sqlalchemy import inspect

from alembic import context, op

revision: str = "0026_graph_multi_skill"
down_revision: str | Sequence[str] | None = "0025_agent_supervisor_graph"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "agent_subagent_invocations"
_CONSTRAINT = "uq_agent_subagent_invocation_attempt"


def upgrade() -> None:
    if context.is_offline_mode():
        _replace_constraint(include_skill=True, drop_existing=True)
        return
    inspector = inspect(op.get_bind())
    if _TABLE not in inspector.get_table_names():
        return
    unique_names = {
        item["name"]
        for item in inspector.get_unique_constraints(_TABLE)
        if item.get("name")
    }
    if _CONSTRAINT in unique_names:
        _replace_constraint(include_skill=True, drop_existing=True)
    else:
        _replace_constraint(include_skill=True, drop_existing=False)


def downgrade() -> None:
    if context.is_offline_mode():
        _replace_constraint(include_skill=False, drop_existing=True)
        return
    inspector = inspect(op.get_bind())
    if _TABLE not in inspector.get_table_names():
        return
    unique_names = {
        item["name"]
        for item in inspector.get_unique_constraints(_TABLE)
        if item.get("name")
    }
    if _CONSTRAINT in unique_names:
        _replace_constraint(include_skill=False, drop_existing=True)
    else:
        _replace_constraint(include_skill=False, drop_existing=False)


def _replace_constraint(*, include_skill: bool, drop_existing: bool) -> None:
    columns = ["graph_run_id", "cursor", "action_id"]
    if include_skill:
        columns.append("skill_name")
    columns.append("attempt")
    with op.batch_alter_table(_TABLE) as batch:
        if drop_existing:
            batch.drop_constraint(_CONSTRAINT, type_="unique")
        batch.create_unique_constraint(_CONSTRAINT, columns)
