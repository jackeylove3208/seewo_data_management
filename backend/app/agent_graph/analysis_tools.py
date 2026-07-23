"""Server-owned evidence tools for graph CSV ingestion and reconciliation."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_graph.tools import GraphToolContext, GraphToolHandler
from app.ai.agent_phone_privacy import StudentPhoneTokenizationContext
from app.ai.skills.contracts import (
    AgentFindingBatch,
    NormalizedOrganizationBatch,
    SourceInspectionResult,
)
from app.ingestion.csv_reader import inspect_csv, read_csv_frame
from app.models.agent_analysis import (
    AgentIdentityClaimRecord,
    AgentIdentityPostingRecord,
    AgentInputRecord,
    AgentWorkItemRecord,
)
from app.models.snapshots import Snapshot, SourceFile


class _InputMarkSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    locator: str = Field(min_length=1, max_length=512)
    reason_code: str = Field(min_length=1, max_length=128)
    affected_fields: tuple[str, ...] = ()
    inclusion_state: str = Field(pattern="^(included|excluded|anomaly)$")
    report_disposition: str = Field(min_length=1, max_length=64)


class _InputMarkBatchSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    marks: tuple[_InputMarkSubmission, ...] = Field(max_length=50)


class GraphAnalysisEvidenceTools:
    """Resolve opaque manifest members to bounded, privacy-safe evidence."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        task_id: UUID,
        run_id: UUID,
        tenant_id: str,
        tokenization_secret: str,
    ) -> None:
        self._session = session
        self._task_id = task_id
        self._run_id = run_id
        self._tenant_id = tenant_id
        self._tokenizer = StudentPhoneTokenizationContext(
            secret=tokenization_secret,
            tenant_id=tenant_id,
            task_id=task_id,
        )

    def handlers(self) -> dict[str, GraphToolHandler]:
        return {
            "inspect_configured_source": self.inspect_configured_source,
            "read_connector_page": self.read_connector_page,
            "submit_input_contract_verdict": self.submit_input_contract_verdict,
            "submit_normalized_batch": self.submit_normalized_batch,
            "submit_input_marks": self.submit_input_marks,
            "read_work_item": self.read_work_item,
            "read_paired_record_evidence": self.read_paired_record_evidence,
            "query_identity_postings": self.query_identity_postings,
            "read_claim_state": self.read_claim_state,
            "submit_finding_batch": self.submit_finding_batch,
        }

    def assert_known_phone_tokens(self, values: set[str]) -> None:
        self._tokenizer.assert_known_tokens(values)
        if any(not value.startswith("STUDENT_PHONE_") for value in values):
            raise ValueError("model returned a non-tokenized phone value")

    def resolve_phone_token(self, value: str | None) -> str | None:
        return self._tokenizer.detokenize(value)

    async def inspect_configured_source(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        role, _page = _parse_source_resource(_required_string(arguments, "resource_id"))
        _snapshot, source = await self._source(role)
        inspection = inspect_csv(source_path(source))
        frame = read_csv_frame(source_path(source), inspection)
        return {
            "connector_kind": "csv",
            "source_role": role,
            "detected_fields": list(inspection.headers),
            "record_count": frame.height,
            "stable_order": True,
            "page_size_limit": 50,
            "source_version_ref": f"sha256:{source.sha256}",
        }

    async def read_connector_page(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        resource_id = _required_string(arguments, "resource_id")
        role, page = _parse_source_resource(resource_id)
        _snapshot, source = await self._source(role)
        inspection = inspect_csv(source_path(source))
        frame = read_csv_frame(source_path(source), inspection)
        limit = arguments.get("limit", 50)
        if not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("connector page limit must be between one and fifty")
        page_locator = arguments.get("page_locator")
        if page_locator is not None:
            if not isinstance(page_locator, str) or not page_locator.isdecimal():
                raise ValueError("connector page locator is invalid")
            offset = int(page_locator)
        else:
            offset = (page - 1) * limit
        rows = frame.slice(offset, limit).to_dicts()
        safe_records: list[dict[str, Any]] = []
        for raw in rows:
            row_number = int(raw.pop("_row_number"))
            safe_records.append(
                {
                    "locator": f"csv:{row_number}",
                    "fields": self._tokenize_student_phone(raw),
                }
            )
        next_offset = offset + len(rows)
        return {
            "resource_id": resource_id,
            "source_role": role,
            "records": safe_records,
            "next_page_locator": str(next_offset) if next_offset < frame.height else None,
            "exhausted": next_offset >= frame.height,
        }

    async def submit_input_contract_verdict(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        result = SourceInspectionResult.model_validate(
            _required_mapping(arguments, "submission")
        )
        return {
            "accepted": True,
            "schema_version": result.schema_version,
            "recognized": result.recognized,
        }

    async def submit_normalized_batch(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        result = NormalizedOrganizationBatch.model_validate(
            _required_mapping(arguments, "submission")
        )
        return {
            "accepted": True,
            "schema_version": result.schema_version,
            "record_count": len(result.records),
        }

    async def submit_input_marks(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        result = _InputMarkBatchSubmission.model_validate(
            _required_mapping(arguments, "submission")
        )
        return {"accepted": True, "mark_count": len(result.marks)}

    async def read_work_item(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        work = await self._work_item(_required_string(arguments, "resource_id"))
        subject = await self._require_input(work.subject_input_id)
        return {
            "work_item_id": str(work.id),
            "kind": work.kind,
            "entity_kind": work.entity_kind,
            "subject_locator": subject.stable_locator,
            "subject_source_role": subject.source_role,
            "paired_evidence_ref": f"paired-record:{work.id}",
        }

    async def read_paired_record_evidence(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        evidence_ref = _required_string(arguments, "evidence_ref")
        work_id = _parse_prefixed_uuid(evidence_ref, "paired-record")
        work = await self._require_work(work_id)
        claim = await self._session.scalar(
            select(AgentIdentityClaimRecord).where(
                AgentIdentityClaimRecord.work_item_id == work.id
            )
        )
        subject = await self._require_input(work.subject_input_id)
        authority: AgentInputRecord | None = None
        target: AgentInputRecord | None = None
        if claim is not None:
            authority = await self._require_input(claim.authority_input_id)
            target = await self._require_input(claim.target_input_id)
        elif subject.source_role == "authoritative":
            authority = subject
        else:
            target = subject
        return {
            "evidence_ref": evidence_ref,
            "work_item_id": str(work.id),
            "kind": work.kind,
            "entity_kind": work.entity_kind,
            "authority": self._safe_record(authority),
            "target": self._safe_record(target),
        }

    async def query_identity_postings(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        work = await self._work_item(_required_string(arguments, "resource_id"))
        subject = await self._require_input(work.subject_input_id)
        postings = tuple(
            await self._session.scalars(
                select(AgentIdentityPostingRecord)
                .where(
                    AgentIdentityPostingRecord.run_id == self._run_id,
                    AgentIdentityPostingRecord.input_record_id == subject.id,
                )
                .order_by(AgentIdentityPostingRecord.key_kind)
            )
        )
        return {
            "work_item_id": str(work.id),
            "postings": [
                {
                    "key_kind": posting.key_kind,
                    "value": (
                        self._tokenizer.tokenize(
                            posting.normalized_value,
                            entity_kind=subject.entity_kind,
                        )
                        if posting.key_kind == "phone"
                        else posting.normalized_value
                    ),
                }
                for posting in postings
            ],
        }

    async def read_claim_state(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        work = await self._work_item(_required_string(arguments, "resource_id"))
        claim = await self._session.scalar(
            select(AgentIdentityClaimRecord).where(
                AgentIdentityClaimRecord.work_item_id == work.id
            )
        )
        return {
            "work_item_id": str(work.id),
            "claimed": claim is not None,
            "authority_ref": (
                f"input:{claim.authority_input_id}" if claim is not None else None
            ),
            "target_ref": f"input:{claim.target_input_id}" if claim is not None else None,
        }

    async def submit_finding_batch(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        result = AgentFindingBatch.model_validate(
            _required_mapping(arguments, "submission")
        )
        return {
            "accepted": True,
            "schema_version": result.schema_version,
            "finding_count": len(result.findings),
        }

    async def _source(self, role: str) -> tuple[Snapshot, SourceFile]:
        row = (
            await self._session.execute(
                select(Snapshot, SourceFile)
                .join(SourceFile, SourceFile.id == Snapshot.source_file_id)
                .where(
                    Snapshot.task_id == self._task_id,
                    Snapshot.source_role == role,
                    SourceFile.task_id == self._task_id,
                    SourceFile.source_role == role,
                )
            )
        ).one_or_none()
        if row is None:
            raise LookupError("configured source is unavailable")
        return row[0], row[1]

    async def _work_item(self, resource_id: str) -> AgentWorkItemRecord:
        return await self._require_work(_parse_prefixed_uuid(resource_id, "work-item"))

    async def _require_work(self, work_id: UUID) -> AgentWorkItemRecord:
        work = await self._session.get(AgentWorkItemRecord, work_id)
        if (
            work is None
            or work.run_id != self._run_id
            or work.task_id != self._task_id
            or work.tenant_id != self._tenant_id
        ):
            raise LookupError("work item is outside Agent graph context")
        return work

    async def _require_input(self, input_id: UUID) -> AgentInputRecord:
        record = await self._session.get(AgentInputRecord, input_id)
        if (
            record is None
            or record.run_id != self._run_id
            or record.task_id != self._task_id
            or record.tenant_id != self._tenant_id
        ):
            raise LookupError("input record is outside Agent graph context")
        return record

    def _safe_record(self, record: AgentInputRecord | None) -> dict[str, Any] | None:
        if record is None:
            return None
        return {
            "input_ref": f"input:{record.id}",
            "locator": record.stable_locator,
            "entity_kind": record.entity_kind,
            "category": record.category,
            "name": record.name,
            "number": record.number,
            "class_name": record.class_name,
            "phone_token": self._tokenizer.tokenize(
                record.phone,
                entity_kind=record.entity_kind,
            ),
            "email": record.email,
        }

    def _tokenize_student_phone(self, raw: Mapping[str, object]) -> dict[str, object]:
        safe: dict[str, object] = {}
        phone_aliases = {"phone", "电话", "手机号"}
        for key, value in raw.items():
            if key.strip().casefold() in {item.casefold() for item in phone_aliases}:
                safe[key] = self._tokenizer.tokenize(
                    str(value) if value not in {None, ""} else None,
                    entity_kind="student",
                )
            else:
                safe[key] = value
        return safe

    def _require_context(self, context: GraphToolContext) -> None:
        if (
            context.task_id != self._task_id
            or context.run_id != self._run_id
            or context.tenant_id != self._tenant_id
        ):
            raise PermissionError("tool context is outside configured Agent graph task")


def source_path(source: SourceFile) -> Path:
    return Path(source.storage_path)


def _parse_source_resource(resource_id: str) -> tuple[str, int]:
    parts = resource_id.split(":")
    if (
        len(parts) != 4
        or parts[0] != "source"
        or parts[1] not in {"authoritative", "target"}
        or parts[2] != "page"
        or not parts[3].isdecimal()
        or int(parts[3]) < 1
    ):
        raise ValueError("source resource ID is invalid")
    return parts[1], int(parts[3])


def _parse_prefixed_uuid(value: str, prefix: str) -> UUID:
    marker = f"{prefix}:"
    if not value.startswith(marker):
        raise ValueError(f"{prefix} resource ID is invalid")
    try:
        return UUID(value.removeprefix(marker))
    except ValueError as error:
        raise ValueError(f"{prefix} resource ID is invalid") from error


def _required_string(arguments: Mapping[str, object], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value


def _required_mapping(
    arguments: Mapping[str, object],
    key: str,
) -> Mapping[str, object]:
    value = arguments.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} is required")
    return value
