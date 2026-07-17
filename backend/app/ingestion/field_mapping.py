from dataclasses import dataclass

from app.schemas.canonical_entities import EntityType, SourceRole


@dataclass(frozen=True)
class FieldMappingProfile:
    version: str
    name: str
    source_role: SourceRole
    columns: dict[str, str]
    entity_type_values: dict[str, EntityType]

    @property
    def required_source_columns(self) -> dict[str, str]:
        return {
            canonical: self.columns[canonical] for canonical in ("entity_type", "source_id", "name")
        }


class FieldMappingRegistry:
    def __init__(self, profiles: tuple[FieldMappingProfile, ...]) -> None:
        self._profiles = {profile.version: profile for profile in profiles}

    def get(self, version: str) -> FieldMappingProfile:
        try:
            return self._profiles[version]
        except KeyError as error:
            raise LookupError(f"unknown field mapping version: {version}") from error

    def list(self) -> tuple[FieldMappingProfile, ...]:
        return tuple(self._profiles.values())


DEFAULT_COLUMNS = {
    "entity_type": "entity_type",
    "source_id": "id",
    "name": "name",
    "code": "code",
    "campus_id": "campus_id",
    "parent_source_id": "parent_id",
    "grade": "grade",
    "class_name": "class_name",
    "school_year": "school_year",
    "employee_number": "employee_number",
    "student_number": "student_number",
    "subject": "subject",
    "phone": "phone",
    "email": "email",
    "extra": "extra",
    "member_source_id": "member_id",
    "container_source_id": "container_id",
    "role": "role",
}

DEFAULT_ENTITY_TYPES = {
    "部门": EntityType.ORGANIZATION_UNIT,
    "班级": EntityType.CLASS,
    "教师": EntityType.TEACHER,
    "学生": EntityType.STUDENT,
    "关系": EntityType.MEMBERSHIP,
    "organization_unit": EntityType.ORGANIZATION_UNIT,
    "class": EntityType.CLASS,
    "teacher": EntityType.TEACHER,
    "student": EntityType.STUDENT,
    "membership": EntityType.MEMBERSHIP,
}


def default_mapping_registry() -> FieldMappingRegistry:
    return FieldMappingRegistry(
        (
            FieldMappingProfile(
                version="third-party-v1",
                name="第三方混合组织 CSV v1",
                source_role=SourceRole.AUTHORITATIVE,
                columns=DEFAULT_COLUMNS,
                entity_type_values=DEFAULT_ENTITY_TYPES,
            ),
            FieldMappingProfile(
                version="mofa-v1",
                name="希沃魔方混合组织 CSV v1",
                source_role=SourceRole.TARGET,
                columns=DEFAULT_COLUMNS,
                entity_type_values=DEFAULT_ENTITY_TYPES,
            ),
        )
    )
