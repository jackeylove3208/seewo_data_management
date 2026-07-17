import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.normalization.identifiers import normalize_email, normalize_identifier, normalize_phone
from app.normalization.organization import (
    DEFAULT_CLASS_NUMBER_PATTERNS,
    DEFAULT_GRADE_ALIASES,
    DEFAULT_SCHOOL_YEAR_PATTERN,
    normalize_class_number,
    normalize_grade,
    normalize_organization_path,
    normalize_school_year,
    normalize_teacher_display_name,
)
from app.normalization.text import normalize_null, normalize_status
from app.schemas.canonical_entities import (
    CanonicalEntity,
    ClassEntity,
    Membership,
    OrganizationUnit,
    Student,
    Teacher,
)

DEFAULT_TEACHER_SUBJECTS = frozenset(
    {
        "语文",
        "数学",
        "英语",
        "物理",
        "化学",
        "生物",
        "政治",
        "历史",
        "地理",
        "体育",
        "音乐",
        "美术",
    }
)
DEFAULT_RULES_PATH = Path(__file__).with_name("rules.v1.json")


class NormalizationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(default="normalization-v1", min_length=1, max_length=64)
    path_separators: tuple[str, ...] = ("/", ">", "\\")
    grade_aliases: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_GRADE_ALIASES))
    school_year_pattern: str = DEFAULT_SCHOOL_YEAR_PATTERN
    class_number_patterns: tuple[str, ...] = DEFAULT_CLASS_NUMBER_PATTERNS
    teacher_subjects: frozenset[str] = DEFAULT_TEACHER_SUBJECTS

    @classmethod
    def from_file(cls, path: Path = DEFAULT_RULES_PATH) -> "NormalizationConfig":
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    @model_validator(mode="after")
    def require_new_version_for_custom_rules(self) -> "NormalizationConfig":
        defaults_match = (
            self.path_separators == ("/", ">", "\\")
            and self.grade_aliases == DEFAULT_GRADE_ALIASES
            and self.school_year_pattern == DEFAULT_SCHOOL_YEAR_PATTERN
            and self.class_number_patterns == DEFAULT_CLASS_NUMBER_PATTERNS
            and self.teacher_subjects == DEFAULT_TEACHER_SUBJECTS
        )
        if self.version == "normalization-v1" and not defaults_match:
            raise ValueError("custom normalization rules require a new version")
        return self


class NormalizedEntity(BaseModel):
    model_config = ConfigDict(frozen=True)

    entity: CanonicalEntity
    normalized: dict[str, str | None]
    warnings: tuple[str, ...] = ()
    rule_version: str


class NormalizationPipeline:
    def __init__(self, config: NormalizationConfig | None = None) -> None:
        self.config = config or NormalizationConfig.from_file()

    def normalize(self, entity: CanonicalEntity) -> NormalizedEntity:
        values = self._normalize_entity(entity)
        return NormalizedEntity(
            entity=entity,
            normalized=values,
            rule_version=self.config.version,
        )

    def _normalize_entity(self, entity: CanonicalEntity) -> dict[str, str | None]:
        values: dict[str, str | None] = {
            "source_id": normalize_identifier(entity.source_id),
        }
        if isinstance(entity, OrganizationUnit):
            values.update(self._organization_unit(entity))
        elif isinstance(entity, ClassEntity):
            values.update(self._class(entity))
        elif isinstance(entity, Teacher):
            values.update(self._teacher(entity))
        elif isinstance(entity, Student):
            values.update(self._student(entity))
        elif isinstance(entity, Membership):
            values.update(self._membership(entity))
        return values

    def _organization_unit(self, entity: OrganizationUnit) -> dict[str, str | None]:
        raw_path = _raw_string(entity.raw_payload, "organization_path") or entity.name
        return {
            "name": normalize_null(entity.name),
            "display_name": normalize_null(entity.name),
            "code": normalize_identifier(entity.code),
            "parent_source_id": normalize_identifier(entity.parent_source_id),
            "campus_id": normalize_identifier(entity.campus_id),
            "organization_path": normalize_organization_path(
                raw_path,
                self.config.path_separators,
            ),
        }

    def _class(self, entity: ClassEntity) -> dict[str, str | None]:
        class_name = normalize_null(entity.class_name) or normalize_null(entity.name)
        return {
            "name": normalize_null(entity.name),
            "display_name": class_name,
            "grade": normalize_grade(entity.grade, self.config.grade_aliases),
            "class_name": class_name,
            "school_year": normalize_school_year(
                entity.school_year,
                self.config.school_year_pattern,
            )
            or normalize_school_year(class_name, self.config.school_year_pattern),
            "class_number": normalize_class_number(
                class_name,
                self.config.class_number_patterns,
            ),
            "parent_source_id": normalize_identifier(entity.parent_source_id),
            "organization_path": normalize_organization_path(
                _raw_string(entity.raw_payload, "organization_path"),
                self.config.path_separators,
            ),
        }

    def _teacher(self, entity: Teacher) -> dict[str, str | None]:
        display_name, suffix_subject = normalize_teacher_display_name(
            entity.name,
            self.config.teacher_subjects,
        )
        return {
            "name": normalize_null(entity.name),
            "display_name": display_name,
            "employee_number": normalize_identifier(entity.employee_number),
            "parent_source_id": normalize_identifier(entity.department_source_id),
            "subject": normalize_null(entity.subject),
            "subject_hint": suffix_subject or normalize_null(entity.subject),
            "phone": normalize_phone(entity.phone),
            "email": normalize_email(entity.email),
            "status": normalize_status(_raw_string(entity.raw_payload, "status")),
            "organization_path": normalize_organization_path(
                _raw_string(entity.raw_payload, "organization_path"),
                self.config.path_separators,
            ),
        }

    def _student(self, entity: Student) -> dict[str, str | None]:
        return {
            "name": normalize_null(entity.name),
            "display_name": normalize_null(entity.name),
            "student_number": normalize_identifier(entity.student_number),
            "parent_source_id": normalize_identifier(entity.class_source_id),
            "grade": normalize_grade(entity.grade, self.config.grade_aliases),
            "class_name": normalize_null(entity.class_name),
            "phone": normalize_phone(entity.phone),
            "email": normalize_email(entity.email),
            "status": normalize_status(_raw_string(entity.raw_payload, "status")),
        }

    def _membership(self, entity: Membership) -> dict[str, str | None]:
        return {
            "member_source_id": normalize_identifier(entity.member_source_id),
            "container_source_id": normalize_identifier(entity.container_source_id),
            "role": normalize_null(entity.role),
        }


def _raw_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None
