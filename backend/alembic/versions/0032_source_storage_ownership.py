"""Track source-file storage ownership and allow repeated external references.

Revision ID: 0032_source_storage_ownership
Revises: 0031_agent_reviewable_risk
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0032_source_storage_ownership"
down_revision: str | Sequence[str] | None = "0031_agent_reviewable_risk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAMING_CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _backfill_external_local_references() -> None:
    tasks = sa.table(
        "reconciliation_tasks",
        sa.column("id", sa.Uuid()),
        sa.column("agent_intent", sa.JSON()),
    )
    files = sa.table(
        "source_files",
        sa.column("task_id", sa.Uuid()),
        sa.column("source_role", sa.String()),
        sa.column("managed_storage", sa.Boolean()),
    )
    local_authority_tasks = sa.select(tasks.c.id).where(
        tasks.c.agent_intent["source"]["kind"].as_string() == "local"
    )
    local_target_tasks = sa.select(tasks.c.id).where(
        tasks.c.agent_intent["target"]["kind"].as_string() == "local"
    )
    op.execute(
        files.update()
        .where(
            sa.or_(
                sa.and_(
                    files.c.source_role == "authoritative",
                    files.c.task_id.in_(local_authority_tasks),
                ),
                sa.and_(
                    files.c.source_role == "target",
                    files.c.task_id.in_(local_target_tasks),
                ),
            )
        )
        .values(managed_storage=False)
    )


def upgrade() -> None:
    if context.is_offline_mode():
        op.add_column(
            "source_files",
            sa.Column(
                "managed_storage",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            ),
        )
        op.drop_constraint(
            "source_files_storage_path_key",
            "source_files",
            type_="unique",
        )
        _backfill_external_local_references()
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    column_names = {
        column["name"] for column in inspector.get_columns("source_files")
    }
    storage_path_unique = next(
        (
            constraint
            for constraint in inspector.get_unique_constraints("source_files")
            if constraint.get("column_names") == ["storage_path"]
        ),
        None,
    )
    if "managed_storage" not in column_names or storage_path_unique is not None:
        with op.batch_alter_table(
            "source_files",
            naming_convention=_NAMING_CONVENTION,
        ) as batch_op:
            if "managed_storage" not in column_names:
                batch_op.add_column(
                    sa.Column(
                        "managed_storage",
                        sa.Boolean(),
                        server_default=sa.true(),
                        nullable=False,
                    )
                )
            if storage_path_unique is not None:
                batch_op.drop_constraint(
                    storage_path_unique.get("name")
                    or "uq_source_files_storage_path",
                    type_="unique",
                )
    _backfill_external_local_references()


def downgrade() -> None:
    if not context.is_offline_mode():
        files = sa.table(
            "source_files",
            sa.column("storage_path", sa.String()),
        )
        duplicate_path = op.get_bind().execute(
            sa.select(files.c.storage_path)
            .group_by(files.c.storage_path)
            .having(sa.func.count() > 1)
            .limit(1)
        ).first()
        if duplicate_path is not None:
            raise RuntimeError(
                "cannot downgrade source storage ownership with repeated external "
                "source references"
            )
    with op.batch_alter_table(
        "source_files",
        naming_convention=_NAMING_CONVENTION,
    ) as batch_op:
        batch_op.create_unique_constraint(
            "uq_source_files_storage_path",
            ["storage_path"],
        )
        batch_op.drop_column("managed_storage")
