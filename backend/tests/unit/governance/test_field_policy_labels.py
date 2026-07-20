from app.governance.field_policy import editor_schema
from app.schemas.canonical_entities import EntityType


def test_editor_schema_uses_chinese_business_labels() -> None:
    schema = editor_schema(EntityType.TEACHER)
    labels = {field.name: field.label for field in schema.fields}

    assert labels["phone"] == "手机号"
    assert labels["email"] == "邮箱"
    assert labels["department_source_id"] == "所属部门"
