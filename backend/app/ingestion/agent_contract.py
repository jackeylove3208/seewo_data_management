"""Pure CSV-to-Agent-contract mapping, isolated from legacy validation."""

from collections.abc import Mapping, Sequence
from typing import ClassVar
from uuid import UUID

from app.normalization.identifiers import normalize_email, normalize_identifier, normalize_phone
from app.normalization.text import normalize_null
from app.schemas.agent_ingestion import (
    AgentContractRecord,
    AgentEntityKind,
    AgentInputMark,
    AgentSourceRole,
)


class AgentContractError(ValueError):
    """Raised when input cannot be represented by agent-contract-v1."""


class AgentContractMapper:
    _aliases: ClassVar[dict[str, tuple[str, ...]]] = {
        "category": ("category", "类别", "entity_type", "实体类型"),
        "name": ("name", "姓名", "名称"),
        "number": ("number", "编号", "id", "工号", "学号"),
        "class_name": ("class", "class_name", "班级"),
        "phone": ("phone", "电话", "手机号"),
        "email": ("email", "邮箱", "电子邮箱"),
    }
    _entity_labels: ClassVar[dict[str, AgentEntityKind]] = {
        "department": AgentEntityKind.DEPARTMENT,
        "部门": AgentEntityKind.DEPARTMENT,
        "organization_unit": AgentEntityKind.DEPARTMENT,
        "student": AgentEntityKind.STUDENT,
        "学生": AgentEntityKind.STUDENT,
        "teacher": AgentEntityKind.TEACHER,
        "教师": AgentEntityKind.TEACHER,
    }

    def assert_recognizable_headers(self, headers: Sequence[str]) -> None:
        mapping = self.resolve_header_mapping(headers)
        if "category" not in mapping:
            raise AgentContractError("unrecognizable agent CSV schema: category column is required")
        if not {"number", "phone", "email"}.intersection(mapping):
            raise AgentContractError("unrecognizable agent CSV schema: identity column is required")

    def resolve_header_mapping(self, headers: Sequence[str]) -> dict[str, str]:
        actual_by_normalized: dict[str, list[str]] = {}
        for header in headers:
            actual_by_normalized.setdefault(header.strip().casefold(), []).append(header)
        mapping: dict[str, str] = {}
        for canonical, aliases in self._aliases.items():
            matches = [
                actual
                for alias in aliases
                for actual in actual_by_normalized.get(alias.casefold(), ())
            ]
            if len(matches) > 1:
                raise AgentContractError(
                    f"ambiguous agent CSV schema: {canonical} has multiple columns"
                )
            if matches:
                mapping[canonical] = matches[0]
        return mapping

    def map_row(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        snapshot_id: UUID,
        tenant_id: str,
        source_role: AgentSourceRole,
        row_number: int,
        row: Mapping[str, object],
        field_mapping: Mapping[str, str] | None = None,
    ) -> AgentContractRecord:
        values = {key: self._value(row, key, field_mapping=field_mapping) for key in self._aliases}
        category = normalize_null(values["category"])
        entity_kind = self._entity_labels.get(category.casefold()) if category else None
        if entity_kind is None:
            raise AgentContractError("unrecognizable entity category")
        return AgentContractRecord(
            task_id=task_id,
            run_id=run_id,
            snapshot_id=snapshot_id,
            tenant_id=tenant_id,
            source_role=source_role,
            stable_locator=f"csv:{row_number}",
            stable_order=row_number - 1,
            entity_kind=entity_kind,
            category=category,
            name=normalize_null(values["name"]),
            number=normalize_identifier(values["number"]),
            class_name=(
                normalize_null(values["class_name"])
                if entity_kind is AgentEntityKind.STUDENT
                else None
            ),
            phone=normalize_phone(values["phone"]),
            email=normalize_email(values["email"]),
            raw_row_number=row_number,
        )

    def validation_mark(self, record: AgentContractRecord) -> AgentInputMark | None:
        if record.source_role is AgentSourceRole.AUTHORITATIVE:
            required = ["category", "name", "number", "phone", "email"]
            if record.entity_kind is AgentEntityKind.STUDENT:
                required.append("class_name")
            missing = tuple(field for field in required if getattr(record, field) is None)
            if missing:
                return AgentInputMark(
                    input_record_id=UUID(int=0),
                    reason_code="authority_required_fields_missing",
                    affected_fields=missing,
                    inclusion_state="excluded",
                    report_disposition="mandatory_ai_anomaly",
                    safe_evidence={
                        "code": "authority_required_fields_missing",
                        "entity_kind": record.entity_kind.value,
                        "missing_count": len(missing),
                        "missing_fields": ",".join(missing),
                        "row_number": record.raw_row_number,
                        "source_role": record.source_role.value,
                    },
                )
            return None
        if not any((record.number, record.phone, record.email)):
            return AgentInputMark(
                input_record_id=UUID(int=0),
                reason_code="target_identity_absent",
                affected_fields=("number", "phone", "email"),
                inclusion_state="anomaly",
                report_disposition="target_extra",
                safe_evidence={
                    "code": "target_identity_absent",
                    "entity_kind": record.entity_kind.value,
                    "has_identity": False,
                    "row_number": record.raw_row_number,
                    "source_role": record.source_role.value,
                },
            )
        return None

    def _value(
        self,
        row: Mapping[str, object],
        canonical: str,
        *,
        field_mapping: Mapping[str, str] | None,
    ) -> str | None:
        normalized = {str(key).strip().casefold(): value for key, value in row.items()}
        if field_mapping is not None:
            unknown_fields = set(field_mapping).difference(self._aliases)
            if unknown_fields:
                raise AgentContractError(
                    f"unknown fixed contract field: {sorted(unknown_fields)[0]}"
                )
            physical = field_mapping.get(canonical)
            if physical is None:
                return None
            value = normalized.get(physical.strip().casefold())
            return str(value) if value is not None else None
        for alias in self._aliases[canonical]:
            value = normalized.get(alias.casefold())
            if value is not None:
                return str(value)
        return None
