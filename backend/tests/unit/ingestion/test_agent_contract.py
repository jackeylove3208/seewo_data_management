from uuid import uuid4

import pytest

from app.ingestion.agent_contract import AgentContractError, AgentContractMapper
from app.schemas.agent_ingestion import AgentEntityKind, AgentSourceRole


def test_maps_chinese_student_row_to_agent_contract() -> None:
    record = AgentContractMapper().map_row(
        task_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        source_role=AgentSourceRole.AUTHORITATIVE,
        row_number=2,
        row={
            "类别": "学生",
            "姓名": "李四",
            "编号": " S-001 ",
            "班级": "高一（1）班",
            "电话": "138 0013 8000",
            "邮箱": "LI@example.COM ",
        },
    )

    assert record.entity_kind is AgentEntityKind.STUDENT
    assert record.stable_locator == "csv:2"
    assert record.stable_order == 1
    assert (record.number, record.phone, record.email, record.class_name) == (
        "S-001",
        "13800138000",
        "li@example.com",
        "高一(1)班",
    )


def test_non_student_discards_class_and_target_email_only_is_valid() -> None:
    mapper = AgentContractMapper()
    record = mapper.map_row(
        task_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        source_role=AgentSourceRole.TARGET,
        row_number=5,
        row={"category": "teacher", "name": "张三", "class": "ignored", "email": "a@b.cn"},
    )

    assert record.class_name is None
    assert mapper.validation_mark(record) is None


def test_incomplete_authority_is_excluded_without_raw_phone_in_mark() -> None:
    mapper = AgentContractMapper()
    record = mapper.map_row(
        task_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        source_role=AgentSourceRole.AUTHORITATIVE,
        row_number=2,
        row={
            "category": "student",
            "name": "李四",
            "number": "S1",
            "class": "一班",
            "phone": "13800138000",
        },
    )

    mark = mapper.validation_mark(record)

    assert mark is not None
    assert mark.inclusion_state == "excluded"
    assert mark.affected_fields == ("email",)
    assert "13800138000" not in str(mark.safe_evidence)


def test_target_without_identity_is_retained_as_target_extra_candidate() -> None:
    mapper = AgentContractMapper()
    record = mapper.map_row(
        task_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        source_role=AgentSourceRole.TARGET,
        row_number=2,
        row={"category": "department", "name": "教务处"},
    )

    mark = mapper.validation_mark(record)

    assert mark is not None
    assert mark.inclusion_state == "anomaly"
    assert mark.reason_code == "target_identity_absent"


def test_unrecognizable_schema_fails_closed() -> None:
    with pytest.raises(AgentContractError, match="unrecognizable"):
        AgentContractMapper().assert_recognizable_headers(("foo", "bar"))


def test_explicit_v2_mapping_projects_unfamiliar_headers_without_guessing() -> None:
    mapper = AgentContractMapper()
    mapping = {
        "category": "人员类别",
        "name": "显示姓名",
        "number": "学籍号码",
        "class_name": "行政班名称",
        "phone": "联系电话值",
        "email": "电子信箱值",
    }

    record = mapper.map_row(
        task_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        source_role=AgentSourceRole.AUTHORITATIVE,
        row_number=2,
        row={
            "人员类别": "学生",
            "显示姓名": "李四",
            "学籍号码": " S-002 ",
            "行政班名称": "二班",
            "联系电话值": "138 0013 8001",
            "电子信箱值": "LI2@example.test",
        },
        field_mapping=mapping,
    )

    assert record.number == "S-002"
    assert record.phone == "13800138001"
    assert record.email == "li2@example.test"


def test_deterministic_header_mapping_rejects_two_columns_for_one_contract_field() -> None:
    mapper = AgentContractMapper()

    with pytest.raises(AgentContractError, match="ambiguous"):
        mapper.resolve_header_mapping(("category", "类别", "number"))
