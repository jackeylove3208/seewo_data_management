from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SourceRole(StrEnum):
    AUTHORITATIVE = "authoritative"
    TARGET = "target"


class EntityType(StrEnum):
    ORGANIZATION_UNIT = "organization_unit"
    CLASS = "class"
    TEACHER = "teacher"
    STUDENT = "student"
    MEMBERSHIP = "membership"


def member_entity_types_for_role(role: str | None) -> tuple[EntityType, ...]:
    normalized = role.casefold() if role else ""
    if normalized in {"student", "学生", "pupil", "learner"}:
        return (EntityType.STUDENT,)
    if normalized in {"teacher", "教师", "staff"}:
        return (EntityType.TEACHER,)
    return (EntityType.TEACHER, EntityType.STUDENT)


class ProvenancedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    snapshot_id: UUID
    source_role: SourceRole
    source_id: str = Field(min_length=1, max_length=255)
    raw_row_number: int = Field(ge=1)
    raw_payload: dict[str, Any]


class OrganizationUnit(ProvenancedEntity):
    entity_type: Literal[EntityType.ORGANIZATION_UNIT] = EntityType.ORGANIZATION_UNIT
    name: str = Field(min_length=1)
    code: str | None = None
    parent_source_id: str | None = None
    campus_id: str | None = None


class ClassEntity(ProvenancedEntity):
    entity_type: Literal[EntityType.CLASS] = EntityType.CLASS
    name: str = Field(min_length=1)
    grade: str | None = None
    class_name: str | None = None
    school_year: str | None = None
    parent_source_id: str | None = None


class Teacher(ProvenancedEntity):
    entity_type: Literal[EntityType.TEACHER] = EntityType.TEACHER
    name: str = Field(min_length=1)
    employee_number: str | None = None
    department_source_id: str | None = None
    subject: str | None = None
    phone: str | None = None
    email: str | None = None
    extra: str | None = None


class Student(ProvenancedEntity):
    entity_type: Literal[EntityType.STUDENT] = EntityType.STUDENT
    name: str = Field(min_length=1)
    student_number: str | None = None
    class_source_id: str | None = None
    grade: str | None = None
    class_name: str | None = None
    phone: str | None = None
    email: str | None = None
    extra: str | None = None


class Membership(ProvenancedEntity):
    entity_type: Literal[EntityType.MEMBERSHIP] = EntityType.MEMBERSHIP
    member_source_id: str = Field(min_length=1)
    container_source_id: str = Field(min_length=1)
    role: str = Field(min_length=1)


CanonicalEntity = Annotated[
    OrganizationUnit | ClassEntity | Teacher | Student | Membership,
    Field(discriminator="entity_type"),
]
