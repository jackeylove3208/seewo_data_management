from app.schemas.canonical_entities import EntityType
from app.schemas.differences import DifferenceType
from app.schemas.executions import OperationType


class PlanPolicyError(ValueError):
    pass


OPERATION_POLICY: dict[DifferenceType, frozenset[OperationType]] = {
    DifferenceType.SEEWO_MISSING: frozenset(
        {OperationType.CREATE, OperationType.SKIP}
    ),
    DifferenceType.SEEWO_REDUNDANT: frozenset(
        {OperationType.DISABLE, OperationType.SKIP}
    ),
    DifferenceType.ATTRIBUTE_CONFLICT: frozenset(
        {OperationType.UPDATE, OperationType.SKIP}
    ),
    DifferenceType.STRUCTURE_CONFLICT: frozenset(
        {OperationType.MOVE, OperationType.SKIP}
    ),
    DifferenceType.DUPLICATE_CONFLICT: frozenset(
        {OperationType.DISABLE, OperationType.SKIP}
    ),
}


EDITABLE_FIELDS: dict[EntityType, frozenset[str]] = {
    EntityType.ORGANIZATION_UNIT: frozenset(
        {"name", "code", "campus_id", "parent_source_id", "status"}
    ),
    EntityType.CLASS: frozenset(
        {"name", "grade", "class_name", "school_year", "parent_source_id", "status"}
    ),
    EntityType.TEACHER: frozenset(
        {
            "name",
            "employee_number",
            "department_source_id",
            "subject",
            "phone",
            "email",
            "extra",
            "status",
        }
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
            "extra",
            "status",
        }
    ),
    EntityType.MEMBERSHIP: frozenset(
        {"member_source_id", "container_source_id", "role", "status"}
    ),
}


def allowed_operations(difference_type: DifferenceType) -> frozenset[OperationType]:
    return OPERATION_POLICY[difference_type]


def validate_operation(
    difference_type: DifferenceType,
    operation_type: OperationType,
) -> None:
    if operation_type not in allowed_operations(difference_type):
        raise PlanPolicyError(
            f"{operation_type.value} is not allowed for {difference_type.value}"
        )


def validate_editable_fields(
    entity_type: EntityType,
    fields: frozenset[str],
) -> None:
    disallowed = fields - EDITABLE_FIELDS[entity_type]
    if disallowed:
        names = ", ".join(sorted(disallowed))
        raise PlanPolicyError(
            f"fields are protected or unknown for {entity_type.value}: {names}"
        )
