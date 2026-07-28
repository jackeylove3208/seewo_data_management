from sqlalchemy.dialects import postgresql, sqlite

from app.models.mappings import SnapshotEntityEmbedding


def test_embedding_block_uses_jsonb_on_postgresql_and_json_on_sqlite() -> None:
    column_type = SnapshotEntityEmbedding.__table__.c.block_key.type
    postgresql_dialect = postgresql.dialect()
    sqlite_dialect = sqlite.dialect()

    assert (
        column_type.dialect_impl(postgresql_dialect).compile(dialect=postgresql_dialect) == "JSONB"
    )
    assert column_type.dialect_impl(sqlite_dialect).compile(dialect=sqlite_dialect) == "JSON"


def test_embedding_block_has_indexed_scalar_partition_columns() -> None:
    table = SnapshotEntityEmbedding.__table__

    assert {
        "snapshot_id",
        "tenant_id",
        "entity_type",
        "campus_id",
        "grade",
        "parent_mapping_id",
    } <= set(table.columns.keys())
    assert "ix_target_embedding_partition" in {index.name for index in table.indexes}


def test_snapshot_embedding_is_role_aware() -> None:
    table = SnapshotEntityEmbedding.__table__

    assert table.name == "snapshot_entity_embeddings"
    assert "source_role" in table.c
    assert "uq_snapshot_embedding_version" in {constraint.name for constraint in table.constraints}
