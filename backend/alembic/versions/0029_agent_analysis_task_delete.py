"""Allow task deletion to remove append-only Agent analysis records.

Revision ID: 0029_agent_analysis_task_delete
Revises: 0028_agent_conversation_messages
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0029_agent_analysis_task_delete"
down_revision: str | Sequence[str] | None = "0028_agent_conversation_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABLE_TABLES = (
    "agent_connector_capabilities",
    "agent_input_records",
    "agent_input_marks",
    "agent_identity_postings",
    "agent_work_items",
    "agent_identity_evidence",
    "agent_identity_claims",
    "agent_model_batch_items",
    "agent_model_attempts",
    "agent_findings",
    "agent_finding_solutions",
    "agent_finding_dependencies",
)

_SQLITE_DELETE_OWNERSHIP = {
    "agent_connector_capabilities": (
        "OLD.task_id = agent_task_deletion_guard.task_id"
    ),
    "agent_input_records": "OLD.task_id = agent_task_deletion_guard.task_id",
    "agent_input_marks": (
        "EXISTS (SELECT 1 FROM agent_input_records parent "
        "WHERE parent.id = OLD.input_record_id "
        "AND parent.task_id = agent_task_deletion_guard.task_id)"
    ),
    "agent_identity_postings": "OLD.task_id = agent_task_deletion_guard.task_id",
    "agent_work_items": "OLD.task_id = agent_task_deletion_guard.task_id",
    "agent_identity_evidence": (
        "EXISTS (SELECT 1 FROM agent_work_items parent "
        "WHERE parent.id = OLD.work_item_id "
        "AND parent.task_id = agent_task_deletion_guard.task_id)"
    ),
    "agent_identity_claims": "OLD.task_id = agent_task_deletion_guard.task_id",
    "agent_model_batch_items": (
        "EXISTS (SELECT 1 FROM agent_model_batches parent "
        "WHERE parent.id = OLD.batch_id "
        "AND parent.task_id = agent_task_deletion_guard.task_id)"
    ),
    "agent_model_attempts": (
        "EXISTS (SELECT 1 FROM agent_model_batches parent "
        "WHERE parent.id = OLD.batch_id "
        "AND parent.task_id = agent_task_deletion_guard.task_id)"
    ),
    "agent_findings": "OLD.task_id = agent_task_deletion_guard.task_id",
    "agent_finding_solutions": (
        "EXISTS (SELECT 1 FROM agent_findings parent "
        "WHERE parent.id = OLD.finding_id "
        "AND parent.task_id = agent_task_deletion_guard.task_id)"
    ),
    "agent_finding_dependencies": (
        "EXISTS (SELECT 1 FROM agent_findings parent "
        "WHERE parent.id = OLD.finding_id "
        "AND parent.task_id = agent_task_deletion_guard.task_id)"
    ),
}


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION reject_agent_analysis_mutation()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE'
                   AND current_setting('app.task_deletion', true) = 'on' THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'new-Agent analysis records are append-only';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_task_deletion_guard (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                task_id TEXT NULL
            )
            """
        )
        op.execute(
            """
            INSERT OR IGNORE INTO agent_task_deletion_guard (id, task_id)
            VALUES (1, NULL)
            """
        )
        for table in _IMMUTABLE_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS reject_{table}_delete")
            ownership = _SQLITE_DELETE_OWNERSHIP[table]
            op.execute(
                f"""
                CREATE TRIGGER reject_{table}_delete
                BEFORE DELETE ON {table}
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM agent_task_deletion_guard
                    WHERE id = 1
                      AND task_id IS NOT NULL
                      AND ({ownership})
                )
                BEGIN SELECT RAISE(
                    ABORT,
                    'new-Agent analysis records are append-only'
                ); END
                """
            )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION reject_agent_analysis_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'new-Agent analysis records are append-only';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    elif dialect == "sqlite":
        for table in _IMMUTABLE_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS reject_{table}_delete")
            op.execute(
                f"""
                CREATE TRIGGER reject_{table}_delete
                BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(
                    ABORT,
                    'new-Agent analysis records are append-only'
                ); END
                """
            )
        op.execute("DROP TABLE IF EXISTS agent_task_deletion_guard")
