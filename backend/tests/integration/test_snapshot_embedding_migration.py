from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command


def test_target_embeddings_are_compatibly_migrated_to_target_role(tmp_path: Path) -> None:
    database_path = tmp_path / "role-aware-embeddings.db"
    url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0011_analysis_item_created_at")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO target_entity_embeddings (
                    id, entity_id, snapshot_id, tenant_id, entity_type, campus_id,
                    grade, source_id, normalized_values, parent_mapping_id, block_key,
                    provider, model, dimensions, representation_version,
                    representation, embedding, created_at
                ) VALUES (
                    :id, :entity_id, :snapshot_id, 'school-1', 'student', NULL,
                    '高一', 'sw-s1', '{}', NULL, '{}', 'enterprise',
                    'embedding-v1', 3, 'entity-representation-v1', 'student',
                    '[0.1, 0.2, 0.3]', CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": "11111111111111111111111111111111",
                "entity_id": "22222222222222222222222222222222",
                "snapshot_id": "33333333333333333333333333333333",
            },
        )

    command.upgrade(config, "head")

    inspector = inspect(engine)
    assert "snapshot_entity_embeddings" in inspector.get_table_names()
    assert "target_entity_embeddings" not in inspector.get_table_names()
    with engine.connect() as connection:
        migrated = connection.execute(
            text(
                "SELECT source_role, provider, model, representation_version "
                "FROM snapshot_entity_embeddings"
            )
        ).one()
    assert migrated == ("target", "enterprise", "embedding-v1", "entity-representation-v1")

    command.downgrade(config, "0011_analysis_item_created_at")
    inspector = inspect(engine)
    assert "target_entity_embeddings" in inspector.get_table_names()
    assert "snapshot_entity_embeddings" not in inspector.get_table_names()
