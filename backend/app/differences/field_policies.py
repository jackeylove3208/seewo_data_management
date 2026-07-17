import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.normalization.identifiers import normalize_email, normalize_identifier, normalize_phone
from app.normalization.text import normalize_null, normalize_status
from app.schemas.canonical_entities import EntityType
from app.schemas.differences import FieldDifference

DEFAULT_POLICY_PATH = Path(__file__).with_name("policies.v1.json")
UNRESOLVED_RELATION = "__unresolved_relation__"


class CompareKind(StrEnum):
    NORMALIZED_SCALAR = "normalized_scalar"
    IDENTIFIER = "identifier"
    PHONE = "phone"
    EMAIL = "email"
    UNORDERED_SET = "unordered_set"
    STRUCTURE_ID = "structure_id"


class FieldRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(min_length=1, max_length=128)
    kind: CompareKind


class ComparisonPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1, max_length=64)
    entities: dict[EntityType, tuple[FieldRule, ...]]

    @classmethod
    def from_file(cls, path: Path = DEFAULT_POLICY_PATH) -> "ComparisonPolicyConfig":
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))


class FieldComparisonPolicy:
    def __init__(self, config: ComparisonPolicyConfig | None = None) -> None:
        self.config = config or ComparisonPolicyConfig.from_file()
        missing = set(EntityType) - set(self.config.entities)
        if missing:
            raise ValueError(f"comparison policy is missing entity types: {sorted(missing)}")

    @property
    def version(self) -> str:
        return self.config.version

    def compare(
        self,
        entity_type: EntityType,
        source: dict[str, Any],
        target: dict[str, Any],
        *,
        source_raw: dict[str, Any] | None = None,
        target_raw: dict[str, Any] | None = None,
    ) -> tuple[FieldDifference, ...]:
        differences: list[FieldDifference] = []
        for rule in self.config.entities[entity_type]:
            source_field = _field_value(source, rule.field)
            target_field = _field_value(target, rule.field)
            if rule.kind is CompareKind.STRUCTURE_ID and UNRESOLVED_RELATION in {
                source_field,
                target_field,
            }:
                continue
            normalized_source = _normalized(rule, source_field)
            normalized_target = _normalized(rule, target_field)
            if normalized_source == normalized_target:
                continue
            differences.append(
                FieldDifference(
                    field=rule.field,
                    source_value=_raw_field_value(
                        entity_type,
                        source_raw or source,
                        rule.field,
                    ),
                    target_value=_raw_field_value(
                        entity_type,
                        target_raw or target,
                        rule.field,
                    ),
                    normalized_source=normalized_source,
                    normalized_target=normalized_target,
                    comparison=(
                        "structure" if rule.kind is CompareKind.STRUCTURE_ID else "attribute"
                    ),
                )
            )
        return tuple(differences)


def _field_value(values: dict[str, Any], field: str) -> Any:
    if field == "name" and "display_name" in values:
        return values.get("display_name")
    return values.get(field)


def _raw_field_value(
    entity_type: EntityType,
    values: dict[str, Any],
    field: str,
) -> Any:
    raw_field = {
        (EntityType.ORGANIZATION_UNIT, "parent_mapping_id"): "parent_source_id",
        (EntityType.CLASS, "parent_mapping_id"): "parent_source_id",
        (EntityType.TEACHER, "parent_mapping_id"): "department_source_id",
        (EntityType.STUDENT, "parent_mapping_id"): "class_source_id",
        (EntityType.MEMBERSHIP, "member_mapping_id"): "member_source_id",
        (EntityType.MEMBERSHIP, "container_mapping_id"): "container_source_id",
    }.get((entity_type, field), field)
    return values.get(raw_field)


def _normalized(rule: FieldRule, value: Any) -> Any:
    if rule.kind is CompareKind.UNORDERED_SET:
        values = value if isinstance(value, (list, tuple, set, frozenset)) else ()
        return tuple(sorted(normalize_null(str(item)) for item in values if item is not None))
    text = str(value) if value is not None else None
    if rule.kind in {CompareKind.IDENTIFIER, CompareKind.STRUCTURE_ID}:
        return normalize_identifier(text)
    if rule.kind is CompareKind.PHONE:
        return normalize_phone(text)
    if rule.kind is CompareKind.EMAIL:
        return normalize_email(text)
    if rule.field == "status":
        return normalize_status(text)
    return normalize_null(text)
