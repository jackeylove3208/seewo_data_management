import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import polars as pl
from pydantic import ValidationError

from app.ingestion.field_mapping import FieldMappingProfile
from app.normalization.identifiers import normalize_identifier
from app.schemas.canonical_entities import (
    CanonicalEntity,
    ClassEntity,
    EntityType,
    Membership,
    OrganizationUnit,
    SourceRole,
    Student,
    Teacher,
    member_entity_types_for_role,
)
from app.schemas.ingestion import IngestionIssue, IngestionSummary


@dataclass(frozen=True)
class ValidationResult:
    entities: tuple[CanonicalEntity, ...]
    raw_rows: tuple[dict[str, Any], ...]
    warnings: tuple[IngestionIssue, ...]
    quarantined: tuple[IngestionIssue, ...]
    fatal_errors: tuple[IngestionIssue, ...]
    summary: IngestionSummary


def validate_frame(
    frame: pl.DataFrame,
    *,
    profile: FieldMappingProfile,
    tenant_id: str,
    snapshot_id: UUID,
    source_role: SourceRole,
    validate_relationships: bool = True,
) -> ValidationResult:
    missing_mappings = profile.missing_required_mappings()
    if missing_mappings:
        errors = tuple(
            IngestionIssue(
                code="missing_required_mapping",
                field=canonical,
                message=f"required canonical field has no source column mapping: {canonical}",
            )
            for canonical in missing_mappings
        )
        return ValidationResult(
            entities=(),
            raw_rows=(),
            warnings=(),
            quarantined=(),
            fatal_errors=errors,
            summary=IngestionSummary(rejected=frame.height),
        )
    missing = [
        (canonical, source)
        for canonical, source in profile.required_source_columns.items()
        if source not in frame.columns
    ]
    if missing:
        errors = tuple(
            IngestionIssue(
                code="missing_required_column",
                field=canonical,
                message=f"required source column is missing: {source}",
            )
            for canonical, source in missing
        )
        return ValidationResult(
            entities=(),
            raw_rows=(),
            warnings=(),
            quarantined=(),
            fatal_errors=errors,
            summary=IngestionSummary(rejected=frame.height),
        )

    entities: list[CanonicalEntity] = []
    entity_rows: dict[int, CanonicalEntity] = {}
    raw_rows: list[dict[str, Any]] = []
    warnings: list[IngestionIssue] = []
    quarantined: list[IngestionIssue] = []
    for row in frame.iter_rows(named=True):
        row_number = int(row["_row_number"])
        raw = {key: _raw_string(value) for key, value in row.items() if key != "_row_number"}
        raw_rows.append({"row_number": row_number, "payload": raw})
        try:
            entity, row_warnings = _map_row(
                raw,
                row_number=row_number,
                profile=profile,
                tenant_id=tenant_id,
                snapshot_id=snapshot_id,
                source_role=source_role,
            )
        except RowMappingError as error:
            quarantined.append(
                IngestionIssue(
                    row_number=row_number,
                    code=error.code,
                    message=str(error),
                    field=error.field,
                    original_value=error.original_value,
                )
            )
            continue
        entities.append(entity)
        entity_rows[row_number] = entity
        warnings.extend(row_warnings)

    if validate_relationships:
        relationship_issues = _relationship_issues(tuple(entities))
        invalid_rows = {issue.row_number for issue in relationship_issues}
        quarantined.extend(relationship_issues)
        entities = [
            entity for row_number, entity in entity_rows.items() if row_number not in invalid_rows
        ]

    quarantined_rows = {issue.row_number for issue in quarantined if issue.row_number is not None}
    warning_rows = {issue.row_number for issue in warnings if issue.row_number is not None}
    fatal_errors: tuple[IngestionIssue, ...] = ()
    if not entities:
        fatal_errors = (
            IngestionIssue(code="no_valid_rows", message="input contains zero valid rows"),
        )
    return ValidationResult(
        entities=tuple(entities),
        raw_rows=tuple(raw_rows),
        warnings=tuple(warnings),
        quarantined=tuple(quarantined),
        fatal_errors=fatal_errors,
        summary=IngestionSummary(
            accepted=len(entities),
            normalized_with_warning=len(warning_rows),
            quarantined=len(quarantined_rows),
            rejected=frame.height if fatal_errors else 0,
        ),
    )


class RowMappingError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: str | None = None,
        original_value: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.original_value = original_value


def _map_row(
    raw: dict[str, str],
    *,
    row_number: int,
    profile: FieldMappingProfile,
    tenant_id: str,
    snapshot_id: UUID,
    source_role: SourceRole,
) -> tuple[CanonicalEntity, tuple[IngestionIssue, ...]]:
    entity_value = _value(raw, profile, "entity_type")
    entity_type = profile.entity_type_values.get(entity_value or "")
    if entity_type is None:
        raise RowMappingError(
            "unknown_entity_type",
            f"unknown entity type: {entity_value or '<empty>'}",
            field="entity_type",
            original_value=entity_value,
        )
    source_id = _required(raw, profile, "source_id")
    name = _required(raw, profile, "name")
    normalized_name = _normalize_text(name)
    parent = _normalize_text(_value(raw, profile, "parent_source_id"))
    phone = _normalize_phone(_value(raw, profile, "phone"))
    email = _normalize_email(_value(raw, profile, "email"))
    warnings = _normalization_warnings(
        row_number,
        {
            "name": (name, normalized_name),
            "phone": (_value(raw, profile, "phone"), phone),
            "email": (_value(raw, profile, "email"), email),
        },
    )
    common = {
        "tenant_id": tenant_id,
        "snapshot_id": snapshot_id,
        "source_role": source_role,
        "source_id": source_id,
        "raw_row_number": row_number,
        "raw_payload": raw,
    }
    try:
        if entity_type is EntityType.ORGANIZATION_UNIT:
            entity: CanonicalEntity = OrganizationUnit(
                **common,
                name=normalized_name,
                code=_normalize_text(_value(raw, profile, "code")),
                parent_source_id=parent,
                campus_id=_normalize_text(_value(raw, profile, "campus_id")),
            )
        elif entity_type is EntityType.CLASS:
            entity = ClassEntity(
                **common,
                name=normalized_name,
                parent_source_id=parent,
                grade=_normalize_text(_value(raw, profile, "grade")),
                class_name=_normalize_text(_value(raw, profile, "class_name")),
                school_year=_normalize_text(_value(raw, profile, "school_year")),
            )
        elif entity_type is EntityType.TEACHER:
            entity = Teacher(
                **common,
                name=normalized_name,
                employee_number=_normalize_text(_value(raw, profile, "employee_number")),
                department_source_id=parent,
                subject=_normalize_text(_value(raw, profile, "subject")),
                phone=phone,
                email=email,
                extra=_normalize_text(_value(raw, profile, "extra")),
            )
        elif entity_type is EntityType.STUDENT:
            entity = Student(
                **common,
                name=normalized_name,
                student_number=_normalize_text(_value(raw, profile, "student_number")),
                class_source_id=parent,
                grade=_normalize_text(_value(raw, profile, "grade")),
                class_name=_normalize_text(_value(raw, profile, "class_name")),
                phone=phone,
                email=email,
                extra=_normalize_text(_value(raw, profile, "extra")),
            )
        else:
            entity = Membership(
                **common,
                member_source_id=_required(raw, profile, "member_source_id"),
                container_source_id=_required(raw, profile, "container_source_id"),
                role=_required(raw, profile, "role"),
            )
    except ValidationError as error:
        raise RowMappingError("invalid_row", str(error)) from error
    return entity, warnings


def _relationship_issues(entities: tuple[CanonicalEntity, ...]) -> list[IngestionIssue]:
    issues: list[IngestionIssue] = []
    grouped: dict[tuple[EntityType, str], list[CanonicalEntity]] = {}
    for entity in entities:
        grouped.setdefault(
            (entity.entity_type, _matching_identifier(entity.source_id)),
            [],
        ).append(entity)
    duplicate_rows: set[int] = set()
    for group in grouped.values():
        if len(group) > 1:
            duplicate_rows.update(entity.raw_row_number for entity in group)
            issues.extend(
                IngestionIssue(
                    row_number=entity.raw_row_number,
                    code="duplicate_source_id",
                    field="source_id",
                    message=f"duplicate source id for {entity.entity_type}: {entity.source_id}",
                    original_value=entity.source_id,
                )
                for entity in group
            )
    available = {key for key, group in grouped.items() if len(group) == 1}
    for entity in entities:
        if entity.raw_row_number in duplicate_rows:
            continue
        references = _references(entity)
        for expected_types, reference in references:
            matching_reference = _matching_identifier(reference) if reference else None
            matching_types = tuple(
                entity_type
                for entity_type in expected_types
                if (entity_type, matching_reference) in available
            )
            if reference and not matching_types:
                issues.append(
                    IngestionIssue(
                        row_number=entity.raw_row_number,
                        code="orphan_reference",
                        field="parent_source_id",
                        message=f"referenced parent does not exist: {reference}",
                        original_value=reference,
                    )
                )
                break
            if reference and len(matching_types) > 1:
                issues.append(
                    IngestionIssue(
                        row_number=entity.raw_row_number,
                        code="ambiguous_reference",
                        field="parent_source_id",
                        message=f"reference matches multiple entity types: {reference}",
                        original_value=reference,
                    )
                )
                break
    cycle_ids = _organization_cycle_ids(entities)
    issues.extend(
        IngestionIssue(
            row_number=entity.raw_row_number,
            code="hierarchy_cycle",
            field="parent_source_id",
            message=f"organization hierarchy cycle contains: {entity.source_id}",
            original_value=entity.parent_source_id,
        )
        for entity in entities
        if isinstance(entity, OrganizationUnit)
        and _matching_identifier(entity.source_id) in cycle_ids
    )
    return issues


def _references(
    entity: CanonicalEntity,
) -> tuple[tuple[tuple[EntityType, ...], str | None], ...]:
    if isinstance(entity, OrganizationUnit):
        return (((EntityType.ORGANIZATION_UNIT,), entity.parent_source_id),)
    if isinstance(entity, ClassEntity):
        return (((EntityType.ORGANIZATION_UNIT,), entity.parent_source_id),)
    if isinstance(entity, Teacher):
        return (((EntityType.ORGANIZATION_UNIT,), entity.department_source_id),)
    if isinstance(entity, Student):
        return (((EntityType.CLASS,), entity.class_source_id),)
    return (
        (member_entity_types_for_role(entity.role), entity.member_source_id),
        ((EntityType.ORGANIZATION_UNIT, EntityType.CLASS), entity.container_source_id),
    )


def _organization_cycle_ids(entities: tuple[CanonicalEntity, ...]) -> set[str]:
    parents = {
        _matching_identifier(entity.source_id): _matching_identifier(entity.parent_source_id)
        if entity.parent_source_id
        else None
        for entity in entities
        if isinstance(entity, OrganizationUnit)
    }
    cycles: set[str] = set()
    for start in parents:
        path: list[str] = []
        positions: dict[str, int] = {}
        current: str | None = start
        while current in parents and current not in positions:
            positions[current] = len(path)
            path.append(current)
            current = parents[current]
        if current in positions:
            cycles.update(path[positions[current] :])
    return cycles


def _matching_identifier(value: str) -> str:
    return normalize_identifier(value) or value


def _required(raw: dict[str, str], profile: FieldMappingProfile, field: str) -> str:
    value = _normalize_text(_value(raw, profile, field))
    if not value:
        raise RowMappingError(
            "missing_required_value",
            f"{field} is required",
            field=field,
            original_value=_value(raw, profile, field),
        )
    return value


def _value(raw: dict[str, str], profile: FieldMappingProfile, field: str) -> str | None:
    source_column = profile.columns.get(field)
    return raw.get(source_column, "") if source_column else ""


def _raw_string(value: object) -> str:
    return "" if value is None else str(value)


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or None


def _normalize_phone(value: str | None) -> str | None:
    normalized = _normalize_text(value)
    if not normalized:
        return None
    digits = re.sub(r"\D", "", normalized)
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    return digits or None


def _normalize_email(value: str | None) -> str | None:
    normalized = _normalize_text(value)
    return normalized.casefold() if normalized else None


def _normalization_warnings(
    row_number: int,
    values: dict[str, tuple[str | None, str | None]],
) -> tuple[IngestionIssue, ...]:
    changed = [
        (field, original, normalized)
        for field, (original, normalized) in values.items()
        if (original or "") != (normalized or "")
    ]
    if not changed:
        return ()
    fields = ", ".join(field for field, _, _ in changed)
    return (
        IngestionIssue(
            row_number=row_number,
            code="normalized_value",
            message=f"recoverable values normalized: {fields}",
        ),
    )
