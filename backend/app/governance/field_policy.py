from app.schemas.canonical_entities import EntityType
from app.schemas.proposals import (
    EditorFieldType,
    EntityEditorField,
    EntityEditorSchema,
)

EDITABLE_FIELDS: dict[EntityType, frozenset[str]] = {
    EntityType.ORGANIZATION_UNIT: frozenset({"name", "code", "parent_source_id", "status"}),
    EntityType.CLASS: frozenset(
        {"name", "grade", "class_name", "school_year", "parent_source_id", "status"}
    ),
    EntityType.TEACHER: frozenset(
        {"name", "employee_number", "department_source_id", "subject", "phone", "email", "status"}
    ),
    EntityType.STUDENT: frozenset(
        {
            "name",
            "student_number",
            "class_source_id",
            "grade",
            "class_name",
            "phone",
            "email",
            "status",
        }
    ),
    EntityType.MEMBERSHIP: frozenset({"member_source_id", "container_source_id", "role", "status"}),
}


def editable_fields(entity_type: EntityType) -> frozenset[str]:
    return EDITABLE_FIELDS[entity_type]


FIELD_TYPES: dict[str, EditorFieldType] = {
    "email": EditorFieldType.EMAIL,
    "phone": EditorFieldType.PHONE,
    "status": EditorFieldType.STATUS,
    "parent_source_id": EditorFieldType.RELATION,
    "department_source_id": EditorFieldType.RELATION,
    "class_source_id": EditorFieldType.RELATION,
    "member_source_id": EditorFieldType.RELATION,
    "container_source_id": EditorFieldType.RELATION,
}


def editor_schema(entity_type: EntityType) -> EntityEditorSchema:
    fields = tuple(
        EntityEditorField(
            name=name,
            label=name.replace("_", " ").title(),
            field_type=FIELD_TYPES.get(name, EditorFieldType.TEXT),
            required=name == "name",
        )
        for name in sorted(editable_fields(entity_type))
    )
    return EntityEditorSchema(entity_type=entity_type, fields=fields)
