"""Deterministic identity evidence primitives for new Agent tasks."""

import hashlib
import json
from collections import defaultdict
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.repository import AgentRuntimeRepository
from app.models.agent_analysis import (
    AgentClarificationRecord,
    AgentIdentityClaimRecord,
    AgentIdentityPostingRecord,
    AgentInputMarkRecord,
    AgentInputRecord,
    AgentWorkItemRecord,
)
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot
from app.repositories.agent_analysis import AgentAnalysisRepository
from app.repositories.agent_governance import AgentGovernanceRepository
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
    fields = ["category", "name", "number"]
    if authority.entity_kind == AgentEntityKind.STUDENT:
        fields.append("class_name")
    fields.extend(("phone", "email"))
    return tuple(
        field
        for field in fields
        if _semantic_field_value(authority, field)
        != _semantic_field_value(target, field)
    )


def _semantic_field_value(record: AgentIdentityRecord, field: str) -> object:
    if field == "category":
        return str(record.entity_kind)
    return getattr(record, field)


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
        conflicted_authority: set[UUID] = set()
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
                work = await self._persist_work(
                    run,
                    source,
                    target,
                    target_record,
                    "identity_conflict",
                    tuple(sorted(candidate_ids)),
                )
                task = await self._session.get(ReconciliationTask, run.task_id)
                if task is None:
                    raise LookupError("Agent task not found")
                clarification = await AgentGovernanceRepository(
                    self._session
                ).create_clarification(
                    run=run,
                    task=task,
                    work_item_id=work.id,
                    candidates=tuple(
                        _masked_candidate(authority_by_id[candidate_id])
                        for candidate_id in sorted(candidate_ids, key=str)
                    ),
                    allowed_outcomes=("use_candidate", "target_extra"),
                )
                await AgentRuntimeRepository(self._session).append_event(
                    run.id,
                    "clarification_required",
                    {
                        "clarification_id": str(clarification.id),
                        "work_item_id": str(work.id),
                        "masked_evidence": clarification.masked_candidates,
                        "allowed_outcomes": clarification.allowed_outcomes,
                    },
                )
                conflicted_authority.update(candidate_ids)
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
            if (
                authority_record.id not in claimed_authority
                and authority_record.id not in conflicted_authority
            ):
                await self._persist_work(
                    run, source, target, authority_record, "target_missing", ("unclaimed",)
                )
        for authority_record in authority:
            if authority_record.id in excluded_ids:
                await self._persist_work(
                    run, source, target, authority_record, "authority_invalid", ("excluded",)
                )

    async def resolve_confirmed_conflicts(
        self,
        *,
        run_id: UUID,
    ) -> tuple[AgentWorkItemRecord, ...]:
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
            raise ValueError("identity resolution requires paired snapshots")
        clarifications = tuple(
            await self._session.scalars(
                select(AgentClarificationRecord)
                .where(
                    AgentClarificationRecord.run_id == run_id,
                    AgentClarificationRecord.status == "confirmed",
                )
                .order_by(
                    AgentClarificationRecord.created_at,
                    AgentClarificationRecord.id,
                )
            )
        )
        resolved_by_id: dict[UUID, AgentWorkItemRecord] = {}
        for clarification in clarifications:
            interpretation = dict(clarification.interpretation or {})
            replay_ids = interpretation.get("resolved_work_item_ids")
            if isinstance(replay_ids, list) and replay_ids:
                replayed = [
                    await self._session.get(AgentWorkItemRecord, UUID(str(work_id)))
                    for work_id in replay_ids
                ]
                if any(item is None or item.run_id != run_id for item in replayed):
                    raise ValueError("identity resolution replay is incomplete")
                resolved_by_id.update(
                    (item.id, item) for item in replayed if item is not None
                )
                continue

            conflict = await self._session.get(
                AgentWorkItemRecord,
                clarification.work_item_id,
            )
            if (
                conflict is None
                or conflict.run_id != run_id
                or conflict.kind != "identity_conflict"
            ):
                raise ValueError("confirmed clarification has no identity conflict work")
            subject = await self._session.get(AgentInputRecord, conflict.subject_input_id)
            if subject is None or subject.source_role != "target":
                raise ValueError("identity conflict target record is missing")
            candidate_ids = tuple(
                UUID(str(candidate["id"]))
                for candidate in clarification.masked_candidates
                if candidate.get("id") is not None
            )
            outcome = interpretation.get("outcome")
            selected_candidate_id = (
                UUID(str(interpretation["candidate_id"]))
                if interpretation.get("candidate_id")
                else None
            )
            if outcome == "use_candidate":
                if selected_candidate_id not in candidate_ids:
                    raise ValueError("confirmed identity candidate is outside frozen evidence")
                authority = await self._session.get(AgentInputRecord, selected_candidate_id)
                if (
                    authority is None
                    or authority.run_id != run_id
                    or authority.source_role != "authoritative"
                ):
                    raise ValueError("confirmed identity candidate is unavailable")
                existing_claim = await self._session.scalar(
                    select(AgentIdentityClaimRecord).where(
                        AgentIdentityClaimRecord.run_id == run_id,
                        AgentIdentityClaimRecord.authority_input_id == authority.id,
                    )
                )
                if existing_claim is None:
                    differences = ordinary_field_differences(authority, subject)
                    resolution_kind = "field_difference" if differences else "correct"
                    primary = await self._persist_work(
                        run,
                        source,
                        target,
                        subject,
                        resolution_kind,
                        (
                            "confirmed_identity_candidate",
                            str(clarification.id),
                            str(authority.id),
                            *differences,
                        ),
                    )
                    await self._repository.persist_identity_claim(
                        run_id=run.id,
                        task_id=run.task_id,
                        source_snapshot_id=source.id,
                        target_snapshot_id=target.id,
                        authority_input_id=authority.id,
                        target_input_id=subject.id,
                        work_item_id=primary.id,
                    )
                else:
                    resolution_kind = "target_duplicate"
                    primary = await self._persist_work(
                        run,
                        source,
                        target,
                        subject,
                        resolution_kind,
                        (
                            "confirmed_identity_candidate_already_claimed",
                            str(clarification.id),
                            str(authority.id),
                        ),
                    )
            elif outcome == "target_extra":
                resolution_kind = "target_extra"
                primary = await self._persist_work(
                    run,
                    source,
                    target,
                    subject,
                    resolution_kind,
                    ("confirmed_identity_target_extra", str(clarification.id)),
                )
            else:
                raise ValueError("confirmed clarification has no supported outcome")

            resolution_items = [primary]
            claimed_ids = set(
                await self._session.scalars(
                    select(AgentIdentityClaimRecord.authority_input_id).where(
                        AgentIdentityClaimRecord.run_id == run_id
                    )
                )
            )
            for candidate_id in candidate_ids:
                if candidate_id in claimed_ids:
                    continue
                authority = await self._session.get(AgentInputRecord, candidate_id)
                if (
                    authority is None
                    or authority.run_id != run_id
                    or authority.source_role != "authoritative"
                ):
                    raise ValueError("frozen identity candidate is unavailable")
                resolution_items.append(
                    await self._persist_work(
                        run,
                        source,
                        target,
                        authority,
                        "target_missing",
                        ("unclaimed_after_identity_resolution",),
                    )
                )

            interpretation["resolved_kind"] = resolution_kind
            interpretation["resolved_work_item_id"] = str(primary.id)
            interpretation["resolved_work_item_ids"] = [
                str(item.id) for item in resolution_items
            ]
            clarification.interpretation = interpretation
            resolved_by_id.update((item.id, item) for item in resolution_items)
        await self._session.flush()
        return tuple(resolved_by_id.values())

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


def _masked_candidate(record: AgentInputRecord) -> dict[str, object]:
    return {
        "id": str(record.id),
        "entity_kind": record.entity_kind,
        "category": record.category,
        "name": record.name,
        "number": record.number,
        "class_name": record.class_name,
        "phone": "***" + record.phone[-4:] if record.phone else None,
        "email": _masked_email(record.email),
    }


def _masked_email(value: str | None) -> str | None:
    if not value or "@" not in value:
        return "已保护" if value else None
    local_part, domain = value.rsplit("@", 1)
    if not local_part or not domain:
        return "已保护"
    return f"{local_part[0]}***@{domain}"
