"""Deterministic frozen API evidence to Agent input projection."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import anyio
from pydantic import ValidationError

from app.api_connectors.contracts import FrozenApiRecord
from app.ingestion.agent_contract import AgentContractMapper
from app.ingestion.agent_csv_adapter import AgentIngestionOutcome
from app.repositories.agent_analysis import ReplayConflict
from app.schemas.agent_ingestion import (
    AgentContractRecord,
    AgentEntityKind,
    AgentInputMark,
    AgentSourceRole,
)

_CONTRACT_VERSION = "api-authority-jsonl-v1"
_PROJECTION_VERSION = "organization-six-fields-v1"
_PROJECTION_FIELDS = frozenset(
    {"category", "name", "number", "class_name", "phone", "email"}
)


@dataclass(frozen=True, slots=True)
class ApiArtifactBinding:
    task_id: UUID
    tenant_id: str
    api_source_id: UUID
    connection_id: UUID
    provider_id: str
    source_file_id: UUID
    snapshot_id: UUID
    selection_hash: str
    selected_entities: frozenset[AgentEntityKind]
    manifest_version: str
    adapter_version: str
    projection_version: str
    content_sha256: str
    size_bytes: int


class AgentApiIngestionAdapter:
    """Read a complete immutable JSONL artifact without contacting its provider."""

    def __init__(self, mapper: AgentContractMapper | None = None) -> None:
        self._mapper = mapper or AgentContractMapper()

    async def extract(
        self,
        *,
        path: Path,
        run_id: UUID,
        binding: ApiArtifactBinding,
    ) -> AgentIngestionOutcome:
        content = await anyio.Path(path).read_bytes()
        if (
            len(content) != binding.size_bytes
            or hashlib.sha256(content).hexdigest() != binding.content_sha256
        ):
            raise ReplayConflict("API artifact integrity check failed")
        try:
            lines = content.decode("utf-8").splitlines()
            payloads = tuple(json.loads(line) for line in lines)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReplayConflict("API artifact is not valid JSONL") from error
        if len(payloads) < 2 or not all(isinstance(item, dict) for item in payloads):
            raise ReplayConflict("API artifact is incomplete")
        header = payloads[0]
        self._validate_header(header, binding=binding)
        raw_records = payloads[1:]
        if header.get("record_count") != len(raw_records):
            raise ReplayConflict("API artifact record count changed")

        records: list[AgentContractRecord] = []
        marks: list[AgentInputMark] = []
        previous_key: tuple[str, str] | None = None
        for stable_order, payload in enumerate(raw_records, start=1):
            if payload.get("record_type") != "record":
                raise ReplayConflict("API artifact contains an unknown record")
            try:
                frozen = FrozenApiRecord.model_validate(
                    {
                        key: value
                        for key, value in payload.items()
                        if key != "record_type"
                    }
                )
            except ValidationError as error:
                raise ReplayConflict("API artifact record contract changed") from error
            key = (frozen.entity_kind.value, frozen.external_id)
            if previous_key is not None and key <= previous_key:
                raise ReplayConflict("API artifact stable order changed")
            previous_key = key
            if frozen.entity_kind not in binding.selected_entities:
                raise ReplayConflict("API artifact contains an unselected entity")
            unavailable = self._validate_projection(frozen)
            projected = self._project(
                frozen,
                run_id=run_id,
                stable_order=stable_order,
                binding=binding,
            )
            records.append(projected)
            marks.extend(
                self._marks(
                    projected,
                    unavailable_fields=unavailable,
                )
            )
        return AgentIngestionOutcome(records=tuple(records), marks=tuple(marks))

    @staticmethod
    def _validate_header(
        header: dict[str, object],
        *,
        binding: ApiArtifactBinding,
    ) -> None:
        expected = {
            "record_type": "header",
            "contract_version": _CONTRACT_VERSION,
            "task_id": str(binding.task_id),
            "tenant_id": binding.tenant_id,
            "api_source_id": str(binding.api_source_id),
            "connection_id": str(binding.connection_id),
            "provider_id": binding.provider_id,
            "source_file_id": str(binding.source_file_id),
            "snapshot_id": str(binding.snapshot_id),
            "selected_entities": sorted(
                entity.value for entity in binding.selected_entities
            ),
            "selection_hash": binding.selection_hash,
            "manifest_version": binding.manifest_version,
            "adapter_version": binding.adapter_version,
            "projection_version": binding.projection_version,
        }
        if any(header.get(key) != value for key, value in expected.items()):
            raise ReplayConflict("API artifact header does not match its frozen binding")
        page_count = header.get("page_count")
        record_count = header.get("record_count")
        if (
            binding.projection_version != _PROJECTION_VERSION
            or not isinstance(page_count, int)
            or isinstance(page_count, bool)
            or page_count <= 0
            or not isinstance(record_count, int)
            or isinstance(record_count, bool)
            or record_count < 0
        ):
            raise ReplayConflict("API artifact header contract is unsupported")

    @staticmethod
    def _validate_projection(record: FrozenApiRecord) -> frozenset[str]:
        if set(record.projected_fields) != _PROJECTION_FIELDS:
            raise ReplayConflict("API artifact projection is not the fixed six fields")
        unavailable = frozenset(record.unavailable_fields)
        if (
            len(unavailable) != len(record.unavailable_fields)
            or not unavailable <= _PROJECTION_FIELDS
            or any(record.projected_fields[field] is not None for field in unavailable)
        ):
            raise ReplayConflict("API artifact unavailable fields are inconsistent")
        return unavailable

    def _project(
        self,
        record: FrozenApiRecord,
        *,
        run_id: UUID,
        stable_order: int,
        binding: ApiArtifactBinding,
    ) -> AgentContractRecord:
        projected = self._mapper.map_row(
            task_id=binding.task_id,
            run_id=run_id,
            snapshot_id=binding.snapshot_id,
            tenant_id=binding.tenant_id,
            source_role=AgentSourceRole.AUTHORITATIVE,
            row_number=stable_order + 1,
            row=record.projected_fields,
            field_mapping={field: field for field in _PROJECTION_FIELDS},
        )
        if projected.entity_kind is not record.entity_kind:
            raise ReplayConflict("API artifact entity category changed")
        return projected.model_copy(
            update={
                "stable_locator": (
                    f"api:{binding.connection_id}:{record.entity_kind.value}:"
                    f"{quote(record.external_id, safe='')}"
                ),
                "stable_order": stable_order,
                "raw_row_number": None,
            }
        )

    @staticmethod
    def _marks(
        record: AgentContractRecord,
        *,
        unavailable_fields: frozenset[str],
    ) -> tuple[AgentInputMark, ...]:
        marks: list[AgentInputMark] = []
        if unavailable_fields:
            ordered = tuple(sorted(unavailable_fields))
            marks.append(
                AgentInputMark(
                    input_record_id=UUID(int=0),
                    reason_code="authority_field_unavailable",
                    affected_fields=ordered,
                    inclusion_state="included",
                    report_disposition="source_field_unavailable",
                    safe_evidence={
                        "code": "authority_field_unavailable",
                        "entity_kind": record.entity_kind.value,
                        "missing_count": len(ordered),
                        "missing_fields": ",".join(ordered),
                        "row_number": record.stable_order,
                        "source_role": record.source_role.value,
                    },
                )
            )
        required = ["category", "name"]
        missing = tuple(
            field
            for field in required
            if getattr(record, field) is None and field not in unavailable_fields
        )
        if missing:
            marks.append(
                AgentInputMark(
                    input_record_id=UUID(int=0),
                    reason_code="api_authority_required_fields_missing",
                    affected_fields=missing,
                    inclusion_state="excluded",
                    report_disposition="mandatory_ai_anomaly",
                    safe_evidence={
                        "code": "api_authority_required_fields_missing",
                        "entity_kind": record.entity_kind.value,
                        "missing_count": len(missing),
                        "missing_fields": ",".join(missing),
                        "row_number": record.stable_order,
                        "source_role": record.source_role.value,
                    },
                )
            )
        return tuple(marks)
