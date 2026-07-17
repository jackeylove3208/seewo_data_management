from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.snapshots import CanonicalEntityRecord
from app.schemas.canonical_entities import EntityType, SourceRole
from app.schemas.differences import DifferenceEntityReference, DifferenceItem

SEARCHABLE_FIELDS = (
    "name",
    "code",
    "class_name",
    "employee_number",
    "student_number",
    "phone",
    "email",
    "member_source_id",
    "container_source_id",
)


async def search_candidates(
    session: AsyncSession,
    difference: DifferenceItem,
    query: str,
    top_k: int,
) -> dict[str, Any]:
    if not 1 <= top_k <= 10:
        raise ValueError("top_k must be between 1 and 10")
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must not be empty")

    pattern = f"%{_escape_like(normalized_query)}%"
    filters = (
        CanonicalEntityRecord.snapshot_id == difference.evidence.target_snapshot_id,
        CanonicalEntityRecord.entity_type == difference.entity_type.value,
        or_(
            CanonicalEntityRecord.source_id.ilike(pattern, escape="\\"),
            *(
                CanonicalEntityRecord.canonical_payload[field]
                .as_string()
                .ilike(pattern, escape="\\")
                for field in SEARCHABLE_FIELDS
            ),
        ),
    )
    total = await session.scalar(select(func.count(CanonicalEntityRecord.id)).where(*filters))
    records = tuple(
        await session.scalars(
            select(CanonicalEntityRecord)
            .where(*filters)
            .order_by(
                CanonicalEntityRecord.raw_row_number,
                CanonicalEntityRecord.id,
            )
            .limit(top_k)
        )
    )
    items = tuple(
        DifferenceEntityReference(
            entity_id=record.id,
            entity_type=EntityType(record.entity_type),
            source_role=SourceRole.TARGET,
            source_id=record.source_id,
            raw_row_number=record.raw_row_number,
            payload=record.canonical_payload,
            raw_payload=record.raw_payload,
        ).model_dump(mode="json")
        for record in records
    )
    return {"items": items, "total": int(total or 0)}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
