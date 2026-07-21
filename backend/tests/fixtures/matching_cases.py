from dataclasses import dataclass
from uuid import uuid4

from app.schemas.canonical_entities import EntityType
from app.schemas.matching import NormalizedRecord


@dataclass(frozen=True)
class StudentCascadeCase:
    source_class_name: str
    target_class_name: str
    sources: tuple[NormalizedRecord, ...]
    targets: tuple[NormalizedRecord, ...]


def obvious_student_cascade_case(student_count: int = 473) -> StudentCascadeCase:
    source_snapshot_id = uuid4()
    target_snapshot_id = uuid4()
    target_class_id = uuid4()
    sources: list[NormalizedRecord] = []
    targets: list[NormalizedRecord] = []
    for index in range(student_count):
        phone = f"138{index:08d}"
        name = f"测试学生{index:03d}"
        values = {
            "name": name,
            "display_name": name,
            "student_number": None,
            "phone": phone,
            "email": f"student{index}@example.test",
        }
        sources.append(
            NormalizedRecord(
                entity_id=uuid4(),
                snapshot_id=source_snapshot_id,
                tenant_id="synthetic-school",
                entity_type=EntityType.STUDENT,
                source_id=f"THIRD-{index:03d}",
                values=values,
                parent_mapping_id=None,
                rule_version="normalization-v1",
            )
        )
        targets.append(
            NormalizedRecord(
                entity_id=uuid4(),
                snapshot_id=target_snapshot_id,
                tenant_id="synthetic-school",
                entity_type=EntityType.STUDENT,
                source_id=f"SEEW0-{index:03d}",
                values=values,
                parent_mapping_id=target_class_id,
                rule_version="normalization-v1",
            )
        )
    return StudentCascadeCase(
        source_class_name="高一(1)班",
        target_class_name="2026级1班",
        sources=tuple(sources),
        targets=tuple(targets),
    )
