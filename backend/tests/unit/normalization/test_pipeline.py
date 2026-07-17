from uuid import uuid4

import pytest

from app.normalization.pipeline import NormalizationConfig, NormalizationPipeline
from app.schemas.canonical_entities import ClassEntity, SourceRole, Teacher


def test_class_variants_share_comparable_fields() -> None:
    pipeline = NormalizationPipeline(NormalizationConfig())
    left = pipeline.normalize(
        ClassEntity(
            tenant_id="school-1",
            snapshot_id=uuid4(),
            source_role=SourceRole.AUTHORITATIVE,
            source_id="class-a",
            raw_row_number=1,
            raw_payload={},
            name="高一(1)班",
            grade="高一",
            school_year="2024",
        )
    )
    right = pipeline.normalize(
        ClassEntity(
            tenant_id="school-1",
            snapshot_id=uuid4(),
            source_role=SourceRole.TARGET,
            source_id="class-b",
            raw_row_number=1,
            raw_payload={},
            name="2024级1班",
            grade="高一",
            school_year="2024学年",
        )
    )

    assert left.normalized["class_number"] == right.normalized["class_number"] == "1"
    assert left.normalized["school_year"] == right.normalized["school_year"] == "2024"
    assert left.rule_version == "normalization-v1"


def test_teacher_subject_suffix_is_evidence_not_data_loss() -> None:
    entity = Teacher(
        tenant_id="school-1",
        snapshot_id=uuid4(),
        source_role=SourceRole.AUTHORITATIVE,
        source_id="teacher-1",
        raw_row_number=1,
        raw_payload={"name": "张三（语文）"},
        name="张三（语文）",
    )
    result = NormalizationPipeline(NormalizationConfig()).normalize(entity)

    assert result.normalized["display_name"] == "张三"
    assert result.normalized["subject_hint"] == "语文"
    assert result.entity.name == "张三（语文）"


def test_unknown_parenthetical_text_is_preserved_in_teacher_name() -> None:
    entity = Teacher(
        tenant_id="school-1",
        snapshot_id=uuid4(),
        source_role=SourceRole.AUTHORITATIVE,
        source_id="teacher-2",
        raw_row_number=2,
        raw_payload={},
        name="李四（新入职）",
    )
    result = NormalizationPipeline(NormalizationConfig()).normalize(entity)

    assert result.normalized["display_name"] == "李四(新入职)"
    assert result.normalized["subject_hint"] is None


def test_custom_normalization_rules_require_and_record_new_version() -> None:
    with pytest.raises(ValueError, match="version"):
        NormalizationConfig(grade_aliases={"一年级": "小学一"})

    config = NormalizationConfig(
        version="normalization-school-v2",
        grade_aliases={"一年级": "小学一"},
    )
    entity = ClassEntity(
        tenant_id="school-1",
        snapshot_id=uuid4(),
        source_role=SourceRole.AUTHORITATIVE,
        source_id="class-1",
        raw_row_number=1,
        raw_payload={},
        name="一年级1班",
        grade="一年级",
    )

    result = NormalizationPipeline(config).normalize(entity)

    assert result.normalized["grade"] == "小学一"
    assert result.rule_version == "normalization-school-v2"
