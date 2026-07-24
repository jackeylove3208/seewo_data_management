from pathlib import Path
from uuid import uuid4

import polars as pl
import pytest

from app.ingestion.csv_reader import CsvFormatError, inspect_csv, read_csv_frame
from app.ingestion.field_mapping import FieldMappingProfile, default_mapping_registry
from app.ingestion.schema_validation import validate_frame
from app.schemas.canonical_entities import (
    ClassEntity,
    OrganizationUnit,
    SourceRole,
    Student,
    Teacher,
)
from tests.fixtures.legacy_csv import write_legacy_csv_pair


def test_real_mofa_csv_has_supported_schema_and_stable_rows(tmp_path: Path) -> None:
    _, mofa_path = write_legacy_csv_pair(tmp_path)
    inspection = inspect_csv(mofa_path)
    frame = read_csv_frame(mofa_path, inspection)

    assert inspection.encoding == "utf-8"
    assert inspection.headers == (
        "entity_type",
        "id",
        "name",
        "parent_id",
        "grade",
        "class_name",
        "subject",
        "phone",
        "email",
        "extra",
    )
    assert frame.height == 518
    assert frame["_row_number"][0] == 2


@pytest.mark.parametrize(
    ("filename", "profile_version", "role", "expected_count"),
    [
        ("mofa_data.csv", "mofa-v1", SourceRole.TARGET, 518),
        (
            "third_party_data.csv",
            "third-party-v1",
            SourceRole.AUTHORITATIVE,
            515,
        ),
    ],
)
def test_real_csv_maps_all_rows_to_canonical_entities(
    filename: str,
    profile_version: str,
    role: SourceRole,
    expected_count: int,
    tmp_path: Path,
) -> None:
    authoritative, target = write_legacy_csv_pair(tmp_path)
    path = {
        "third_party_data.csv": authoritative,
        "mofa_data.csv": target,
    }[filename]
    inspection = inspect_csv(path)
    frame = read_csv_frame(path, inspection)
    profile = default_mapping_registry().get(profile_version)

    result = validate_frame(
        frame,
        profile=profile,
        tenant_id="school-1",
        snapshot_id=uuid4(),
        source_role=role,
    )

    assert result.fatal_errors == ()
    assert result.summary.accepted == expected_count
    assert result.summary.quarantined == 0
    assert isinstance(result.entities[0], OrganizationUnit)
    assert any(isinstance(entity, ClassEntity) for entity in result.entities)
    assert any(isinstance(entity, Teacher) for entity in result.entities)
    assert any(isinstance(entity, Student) for entity in result.entities)


def test_mapping_interprets_parent_by_entity_type(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        """entity_type,id,name,parent_id,grade,class_name,subject,phone,email,extra
教师,T001, 张三 ,D01,,,语文,+86 138-0000-0000, Teacher@Example.COM ,备注
学生,S0001,李四,C01,高一,高一(1)班,,13800000001,student@example.com,家长
""",
    )
    frame = read_csv_frame(path, inspect_csv(path))

    result = validate_frame(
        frame,
        profile=default_mapping_registry().get("third-party-v1"),
        tenant_id="school-1",
        snapshot_id=uuid4(),
        source_role=SourceRole.AUTHORITATIVE,
        validate_relationships=False,
    )

    teacher = result.entities[0]
    student = result.entities[1]
    assert isinstance(teacher, Teacher)
    assert teacher.department_source_id == "D01"
    assert teacher.name == "张三"
    assert teacher.phone == "13800000000"
    assert teacher.email == "teacher@example.com"
    assert isinstance(student, Student)
    assert student.class_source_id == "C01"
    assert result.summary.normalized_with_warning == 1
    assert teacher.raw_payload["name"] == " 张三 "


def test_extended_csv_columns_reach_entity_resolution_fields(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        """entity_type,id,name,parent_id,grade,class_name,subject,phone,email,extra,code,campus_id,school_year,employee_number,student_number
部门,D01,教务处,,,,,,,,DEPT-01,CAMPUS-1,,,
班级,C01,高一(1)班,D01,高一,高一(1)班,,,,,,,2024,,
教师,T01,张三,D01,,,语文,13800000000,teacher@example.com,,,,,E-001,
学生,S01,李四,C01,高一,高一(1)班,,13800000001,student@example.com,,,,,,STU-001
""",
    )
    frame = read_csv_frame(path, inspect_csv(path))

    result = validate_frame(
        frame,
        profile=default_mapping_registry().get("third-party-v1"),
        tenant_id="school-1",
        snapshot_id=uuid4(),
        source_role=SourceRole.AUTHORITATIVE,
    )

    organization, class_entity, teacher, student = result.entities
    assert isinstance(organization, OrganizationUnit)
    assert organization.code == "DEPT-01"
    assert organization.campus_id == "CAMPUS-1"
    assert isinstance(class_entity, ClassEntity)
    assert class_entity.school_year == "2024"
    assert isinstance(teacher, Teacher)
    assert teacher.employee_number == "E-001"
    assert isinstance(student, Student)
    assert student.student_number == "STU-001"


def test_missing_required_mapping_blocks_ingestion(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "entity_type,id,parent_id\n部门,D01,\n")
    frame = read_csv_frame(path, inspect_csv(path))

    result = validate_frame(
        frame,
        profile=default_mapping_registry().get("mofa-v1"),
        tenant_id="school-1",
        snapshot_id=uuid4(),
        source_role=SourceRole.TARGET,
    )

    assert result.summary.accepted == 0
    assert result.fatal_errors[0].code == "missing_required_column"
    assert result.fatal_errors[0].field == "name"


def test_missing_required_profile_mapping_is_fatal() -> None:
    base = default_mapping_registry().get("mofa-v1")
    columns = dict(base.columns)
    columns.pop("name")
    profile = FieldMappingProfile(
        version="broken-v1",
        name="broken",
        source_role=SourceRole.TARGET,
        columns=columns,
        entity_type_values=base.entity_type_values,
    )
    frame = pl.DataFrame({"entity_type": ["教师"], "id": ["T1"], "name": ["A"]})

    result = validate_frame(
        frame,
        profile=profile,
        tenant_id="school-1",
        snapshot_id=uuid4(),
        source_role=SourceRole.TARGET,
    )

    assert result.summary.accepted == 0
    assert result.fatal_errors[0].code == "missing_required_mapping"
    assert result.fatal_errors[0].field == "name"


def test_unknown_type_duplicate_id_and_orphan_are_quarantined(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        """entity_type,id,name,parent_id,grade,class_name,subject,phone,email,extra
部门,D01,教务处,,,,,,,
教师,T001,张三,D99,,,语文,13800000000,a@example.com,
教师,T002,李四,D01,,,数学,13800000001,b@example.com,
教师,T002,李四重复,D01,,,数学,13800000001,b@example.com,
未知,X01,未知实体,,,,,,,
""",
    )
    frame = read_csv_frame(path, inspect_csv(path))

    result = validate_frame(
        frame,
        profile=default_mapping_registry().get("mofa-v1"),
        tenant_id="school-1",
        snapshot_id=uuid4(),
        source_role=SourceRole.TARGET,
    )

    assert result.summary.accepted == 1
    assert result.summary.quarantined == 4
    assert {issue.code for issue in result.quarantined} == {
        "duplicate_source_id",
        "orphan_reference",
        "unknown_entity_type",
    }


def test_cross_type_membership_reference_requires_unambiguous_role(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        """entity_type,id,name,parent_id,grade,class_name,subject,phone,email,extra,member_id,container_id,role
班级,C01,高一(1)班,,高一,高一(1)班,,,,,,,
教师,X01,张老师,,,,,13800000000,teacher@example.com,,,,
学生,X01,张同学,C01,高一,高一(1)班,,13800000001,student@example.com,,,,
关系,R01,未知成员关系,,,,,,,,X01,C01,member
""",
    )
    frame = read_csv_frame(path, inspect_csv(path))

    result = validate_frame(
        frame,
        profile=default_mapping_registry().get("third-party-v1"),
        tenant_id="school-1",
        snapshot_id=uuid4(),
        source_role=SourceRole.AUTHORITATIVE,
    )

    assert result.summary.accepted == 3
    assert result.summary.quarantined == 1
    assert result.quarantined[0].code == "ambiguous_reference"


def test_source_ids_cannot_collide_after_matching_normalization(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        """entity_type,id,name,parent_id,grade,class_name,subject,phone,email,extra
部门,dept-a,教务处,,,,,,,
部门,ＤＥＰＴ－Ａ,重复教务处,,,,,,,
""",
    )
    frame = read_csv_frame(path, inspect_csv(path))

    result = validate_frame(
        frame,
        profile=default_mapping_registry().get("third-party-v1"),
        tenant_id="school-1",
        snapshot_id=uuid4(),
        source_role=SourceRole.AUTHORITATIVE,
    )

    assert result.summary.accepted == 0
    assert result.summary.quarantined == 2
    assert {issue.code for issue in result.quarantined} == {"duplicate_source_id"}


def test_inspection_rejects_duplicate_headers_and_unsupported_encoding(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text("id,id\n1,2\n", encoding="utf-8")
    unsupported = tmp_path / "unsupported.csv"
    unsupported.write_bytes("名称\n张三\n".encode("utf-16"))

    with pytest.raises(CsvFormatError, match="duplicate header"):
        inspect_csv(duplicate)
    with pytest.raises(CsvFormatError, match="unsupported encoding"):
        inspect_csv(unsupported)


def test_inspection_rejects_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"")

    with pytest.raises(CsvFormatError, match="empty"):
        inspect_csv(empty)


def test_reader_rejects_ragged_rows(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "id,name\n1,A,unexpected\n")

    with pytest.raises(CsvFormatError, match="row shape"):
        read_csv_frame(path, inspect_csv(path))


def write_csv(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "input.csv"
    path.write_text(content, encoding="utf-8", newline="")
    return path
