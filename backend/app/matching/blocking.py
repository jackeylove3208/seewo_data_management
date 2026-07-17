from app.schemas.canonical_entities import EntityType
from app.schemas.matching import BlockKey, NormalizedRecord


def block_key(record: NormalizedRecord) -> BlockKey:
    return BlockKey(
        tenant_id=record.tenant_id,
        entity_type=record.entity_type,
        campus_id=record.values.get("campus_id"),
        grade=record.values.get("grade") if record.entity_type is EntityType.CLASS else None,
        parent_mapping_id=record.parent_mapping_id,
    )
