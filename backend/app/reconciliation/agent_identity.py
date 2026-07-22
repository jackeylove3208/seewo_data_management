"""Deterministic identity evidence primitives for new Agent tasks."""

import hashlib
import json
from collections import defaultdict
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_analysis import (
    AgentIdentityPostingRecord,
    AgentInputMarkRecord,
    AgentInputRecord,
    AgentWorkItemRecord,
)
from app.models.agent_runtime import AgentRunRecord
from app.models.snapshots import Snapshot
from app.repositories.agent_analysis import AgentAnalysisRepository
from app.schemas.agent_ingestion import AgentContractRecord, AgentEntityKind


class AgentIdentityRecord(Protocol):
    number: str | None
    phone: str | None
    email: str | None
    category: str | None
    name: str | None
    class_name: str | None
    entity_kind: str | AgentEntityKind


def identity_postings(record: AgentContractRecord) -> tuple[tuple[str, str], ...]:
    """Return only normalized identity candidates; never use ordinary fields."""
    return tuple(
        (kind, value)
        for kind, value in (
            ("number", record.number),
            ("phone", record.phone),
            ("email", record.email),
        )
        if value is not None
    )


def ordinary_field_differences(
    authority: AgentIdentityRecord, target: AgentIdentityRecord
) -> tuple[str, ...]:
    """Compare governed ordinary fields after identity correspondence is accepted."""
    fields = ["category", "name"]
    if authority.entity_kind == AgentEntityKind.STUDENT:
        fields.append("class_name")
    return tuple(field for field in fields if getattr(authority, field) != getattr(target, field))


class AgentIdentityIndexBuilder:
    """Build ordinary exact indexes and deterministic, reviewable reconciliation work."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = AgentAnalysisRepository(session)

    async def build(self, *, run_id: UUID) -> None:
        run = await self._session.get(AgentRunRecord, run_id)
        if run is None:
            raise LookupError(f"agent run not found: {run_id}")
        snapshots = tuple(
            await self._session.scalars(select(Snapshot).where(Snapshot.task_id == run.task_id))
        )
        by_role = {snapshot.source_role: snapshot for snapshot in snapshots}
        source = by_role.get("authoritative")
        target = by_role.get("target")
        if source is None or target is None:
            raise ValueError("identity work requires paired authoritative and target snapshots")
        authority = await self._repository.list_inputs(run_id, "authoritative")
        targets = await self._repository.list_inputs(run_id, "target")
        excluded_ids = set(
            await self._session.scalars(
                select(AgentInputMarkRecord.input_record_id)
                .join(AgentInputRecord)
                .where(
                    AgentInputRecord.run_id == run_id,
                    AgentInputMarkRecord.inclusion_state == "excluded",
                )
            )
        )
        valid_authority = tuple(record for record in authority if record.id not in excluded_ids)
        await self._persist_postings(run, source, valid_authority)
        await self._persist_postings(run, target, targets)
        postings = tuple(
            await self._session.scalars(
                select(AgentIdentityPostingRecord).where(
                    AgentIdentityPostingRecord.run_id == run_id
                )
            )
        )
        authority_index: dict[tuple[str, str], set[UUID]] = defaultdict(set)
        valid_authority_ids = {record.id for record in valid_authority}
        for posting in postings:
            if posting.input_record_id in valid_authority_ids:
                authority_index[(posting.key_kind, posting.normalized_value)].add(
                    posting.input_record_id
                )
        authority_by_id = {record.id: record for record in valid_authority}
        claimed_authority: set[UUID] = set()
        for target_record in targets:
            candidate_ids = {
                candidate
                for key in _record_postings(target_record)
                for candidate in authority_index.get(key, set())
            }
            if not candidate_ids:
                await self._persist_work(
                    run, source, target, target_record, "target_extra", ("no_authority_match",)
                )
                continue
            if len(candidate_ids) != 1:
                await self._persist_work(
                    run,
                    source,
                    target,
                    target_record,
                    "identity_conflict",
                    tuple(sorted(candidate_ids)),
                )
                continue
            authority_id = next(iter(candidate_ids))
            authority_record = authority_by_id[authority_id]
            if authority_id in claimed_authority:
                await self._persist_work(
                    run, source, target, target_record, "target_duplicate", (str(authority_id),)
                )
                continue
            differences = ordinary_field_differences(authority_record, target_record)
            kind = "field_difference" if differences else "correct"
            work = await self._persist_work(run, source, target, target_record, kind, differences)
            await self._repository.persist_identity_claim(
                run_id=run.id,
                task_id=run.task_id,
                source_snapshot_id=source.id,
                target_snapshot_id=target.id,
                authority_input_id=authority_id,
                target_input_id=target_record.id,
                work_item_id=work.id,
            )
            claimed_authority.add(authority_id)
        for authority_record in valid_authority:
            if authority_record.id not in claimed_authority:
                await self._persist_work(
                    run, source, target, authority_record, "target_missing", ("unclaimed",)
                )
        for authority_record in authority:
            if authority_record.id in excluded_ids:
                await self._persist_work(
                    run, source, target, authority_record, "authority_invalid", ("excluded",)
                )

    async def _persist_postings(
        self, run: AgentRunRecord, snapshot: Snapshot, records: tuple[AgentInputRecord, ...]
    ) -> None:
        await self._repository.persist_identity_postings(
            run_id=run.id,
            task_id=run.task_id,
            tenant_id=run.tenant_id,
            snapshot_id=snapshot.id,
            postings=tuple(
                (record.id, kind, value)
                for record in records
                for kind, value in _record_postings(record)
            ),
        )

    async def _persist_work(
        self,
        run: AgentRunRecord,
        source: Snapshot,
        target: Snapshot,
        subject: AgentInputRecord,
        kind: str,
        evidence: tuple[object, ...],
    ) -> AgentWorkItemRecord:
        values = {"subject": str(subject.id), "kind": kind, "evidence": evidence}
        digest = _hash(values)
        return await self._repository.persist_work_item(
            run_id=run.id,
            task_id=run.task_id,
            tenant_id=run.tenant_id,
            source_snapshot_id=source.id,
            target_snapshot_id=target.id,
            subject_input_id=subject.id,
            entity_kind=subject.entity_kind,
            kind=kind,
            idempotency_hash=digest,
            evidence_hash=digest,
        )


def _record_postings(record: AgentIdentityRecord) -> tuple[tuple[str, str], ...]:
    return tuple(
        (kind, value)
        for kind, value in (
            ("number", record.number),
            ("phone", record.phone),
            ("email", record.email),
        )
        if value is not None
    )


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, default=str, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
