import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.repository import run_claim_is_active
from app.agent_runtime.state_machine import AgentPhase, AgentRunStatus
from app.models.agent_analysis import (
    AgentConnectorCapabilityRecord,
    AgentFindingDependencyRecord,
    AgentFindingRecord,
    AgentFindingSolutionRecord,
    AgentIdentityClaimRecord,
    AgentIdentityEvidenceRecord,
    AgentIdentityPostingRecord,
    AgentInputMarkRecord,
    AgentInputRecord,
    AgentModelAttemptRecord,
    AgentModelBatchItemRecord,
    AgentModelBatchRecord,
    AgentWorkItemRecord,
)
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot
from app.schemas.agent_ingestion import AgentContractRecord, AgentInputMark
from app.schemas.agent_reconciliation import AgentFindingPayload


class ReplayConflict(ValueError):
    pass


class AgentAnalysisRepository:
    """Durable, replay-safe storage shared by the new Agent phase handlers."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def persist_inputs(
        self, records: tuple[AgentContractRecord, ...]
    ) -> tuple[AgentInputRecord, ...]:
        persisted: list[AgentInputRecord] = []
        for record in records:
            await self._validate_input_context(record)
            digest = _hash(record.model_dump(mode="json"))
            existing = await self.session.scalar(
                select(AgentInputRecord).where(
                    AgentInputRecord.run_id == record.run_id,
                    AgentInputRecord.source_role == record.source_role.value,
                    AgentInputRecord.stable_locator == record.stable_locator,
                )
            )
            if existing is not None:
                if existing.input_hash != digest:
                    raise ReplayConflict("stable locator replay has a different input hash")
                persisted.append(existing)
                continue
            by_order = await self.session.scalar(
                select(AgentInputRecord).where(
                    AgentInputRecord.run_id == record.run_id,
                    AgentInputRecord.source_role == record.source_role.value,
                    AgentInputRecord.stable_order == record.stable_order,
                )
            )
            if by_order is not None:
                raise ReplayConflict("stable order is already bound to a different locator")
            values = record.model_dump()
            values["source_role"] = record.source_role.value
            values["entity_kind"] = record.entity_kind.value
            saved = AgentInputRecord(id=uuid4(), **values, input_hash=digest)
            self.session.add(saved)
            await self.session.flush()
            persisted.append(saved)
        return tuple(persisted)

    async def list_inputs(self, run_id: UUID, source_role: str) -> tuple[AgentInputRecord, ...]:
        rows = await self.session.scalars(
            select(AgentInputRecord)
            .where(AgentInputRecord.run_id == run_id, AgentInputRecord.source_role == source_role)
            .order_by(AgentInputRecord.stable_order, AgentInputRecord.id)
        )
        return tuple(rows)

    async def persist_capability(
        self,
        *,
        run_id: UUID,
        task_id: UUID,
        tenant_id: str,
        source_role: str,
        connector_kind: str,
        capabilities: dict[str, bool],
    ) -> AgentConnectorCapabilityRecord:
        await self._validate_run_context(run_id, task_id, tenant_id)
        if source_role not in {"authoritative", "target"}:
            raise ValueError("unsupported source role")
        digest = _hash(
            {
                "connector_kind": connector_kind,
                "capabilities": capabilities,
            }
        )
        existing = await self.session.scalar(
            select(AgentConnectorCapabilityRecord).where(
                AgentConnectorCapabilityRecord.run_id == run_id,
                AgentConnectorCapabilityRecord.source_role == source_role,
                AgentConnectorCapabilityRecord.capability_hash == digest,
            )
        )
        if existing is not None:
            if (
                existing.task_id != task_id
                or existing.tenant_id != tenant_id
                or existing.connector_kind != connector_kind
                or existing.capabilities != capabilities
            ):
                raise ReplayConflict("connector capability replay changed context")
            return existing
        record = AgentConnectorCapabilityRecord(
            id=uuid4(),
            run_id=run_id,
            task_id=task_id,
            tenant_id=tenant_id,
            source_role=source_role,
            connector_kind=connector_kind,
            capability_hash=digest,
            capabilities=capabilities,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def persist_marks(
        self, marks: tuple[AgentInputMark, ...]
    ) -> tuple[AgentInputMarkRecord, ...]:
        persisted: list[AgentInputMarkRecord] = []
        for mark in marks:
            input_record = await self.session.get(AgentInputRecord, mark.input_record_id)
            if input_record is None:
                raise ReplayConflict("mark input record does not exist")
            values = mark.model_dump(mode="json", exclude={"input_record_id"})
            existing = await self.session.scalar(
                select(AgentInputMarkRecord).where(
                    AgentInputMarkRecord.input_record_id == mark.input_record_id,
                    AgentInputMarkRecord.reason_code == mark.reason_code,
                )
            )
            if existing is not None:
                existing_values = {
                    "reason_code": existing.reason_code,
                    "affected_fields": existing.affected_fields,
                    "inclusion_state": existing.inclusion_state,
                    "report_disposition": existing.report_disposition,
                    "safe_evidence": existing.safe_evidence,
                }
                if existing_values != values:
                    raise ReplayConflict("input mark replay changed content")
                persisted.append(existing)
                continue
            record = AgentInputMarkRecord(
                id=uuid4(), input_record_id=mark.input_record_id, **values
            )
            self.session.add(record)
            await self.session.flush()
            persisted.append(record)
        return tuple(persisted)

    async def persist_work_item(
        self,
        *,
        run_id: UUID,
        task_id: UUID,
        tenant_id: str,
        source_snapshot_id: UUID,
        target_snapshot_id: UUID,
        subject_input_id: UUID,
        entity_kind: str,
        kind: str,
        idempotency_hash: str,
        evidence_hash: str,
    ) -> AgentWorkItemRecord:
        await self._validate_run_context(run_id, task_id, tenant_id)
        source = await self.session.get(Snapshot, source_snapshot_id)
        target = await self.session.get(Snapshot, target_snapshot_id)
        subject = await self.session.get(AgentInputRecord, subject_input_id)
        if (
            source is None
            or target is None
            or source.task_id != task_id
            or target.task_id != task_id
            or source.source_role != "authoritative"
            or target.source_role != "target"
            or subject is None
            or subject.run_id != run_id
            or subject.task_id != task_id
            or subject.tenant_id != tenant_id
            or subject.entity_kind != entity_kind
            or subject.snapshot_id not in {source_snapshot_id, target_snapshot_id}
        ):
            raise ReplayConflict("work item contains cross-context evidence")
        values = {
            "task_id": task_id,
            "tenant_id": tenant_id,
            "source_snapshot_id": source_snapshot_id,
            "target_snapshot_id": target_snapshot_id,
            "subject_input_id": subject_input_id,
            "entity_kind": entity_kind,
            "kind": kind,
            "state": "pending",
            "evidence_hash": evidence_hash,
        }
        existing = await self.session.scalar(
            select(AgentWorkItemRecord).where(
                AgentWorkItemRecord.run_id == run_id,
                AgentWorkItemRecord.idempotency_hash == idempotency_hash,
            )
        )
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in values.items()):
                raise ReplayConflict("work-item replay changed content")
            return existing
        record = AgentWorkItemRecord(
            id=uuid4(),
            run_id=run_id,
            idempotency_hash=idempotency_hash,
            **values,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def persist_identity_evidence(
        self,
        *,
        work_item_id: UUID,
        posting_id: UUID,
        evidence_hash: str,
    ) -> AgentIdentityEvidenceRecord:
        work_item = await self.session.get(AgentWorkItemRecord, work_item_id)
        posting = await self.session.get(AgentIdentityPostingRecord, posting_id)
        if (
            work_item is None
            or posting is None
            or work_item.run_id != posting.run_id
            or work_item.task_id != posting.task_id
            or work_item.tenant_id != posting.tenant_id
            or posting.snapshot_id
            not in {work_item.source_snapshot_id, work_item.target_snapshot_id}
        ):
            raise ReplayConflict("identity evidence is cross-context")
        existing = await self.session.scalar(
            select(AgentIdentityEvidenceRecord).where(
                AgentIdentityEvidenceRecord.work_item_id == work_item_id,
                AgentIdentityEvidenceRecord.posting_id == posting_id,
            )
        )
        if existing is not None:
            if existing.evidence_hash != evidence_hash:
                raise ReplayConflict("identity evidence replay changed content")
            return existing
        record = AgentIdentityEvidenceRecord(
            id=uuid4(),
            work_item_id=work_item_id,
            posting_id=posting_id,
            key_kind=posting.key_kind,
            normalized_value=posting.normalized_value,
            evidence_hash=evidence_hash,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def persist_identity_claim(
        self,
        *,
        run_id: UUID,
        task_id: UUID,
        source_snapshot_id: UUID,
        target_snapshot_id: UUID,
        authority_input_id: UUID,
        target_input_id: UUID,
        work_item_id: UUID,
    ) -> AgentIdentityClaimRecord:
        authority = await self.session.get(AgentInputRecord, authority_input_id)
        target = await self.session.get(AgentInputRecord, target_input_id)
        work_item = await self.session.get(AgentWorkItemRecord, work_item_id)
        expected = (
            run_id,
            task_id,
            source_snapshot_id,
            target_snapshot_id,
            authority_input_id,
            target_input_id,
            work_item_id,
        )
        if (
            authority is None
            or target is None
            or work_item is None
            or authority.source_role != "authoritative"
            or target.source_role != "target"
            or authority.run_id != run_id
            or target.run_id != run_id
            or work_item.run_id != run_id
            or authority.task_id != task_id
            or target.task_id != task_id
            or work_item.task_id != task_id
            or authority.snapshot_id != source_snapshot_id
            or target.snapshot_id != target_snapshot_id
            or work_item.source_snapshot_id != source_snapshot_id
            or work_item.target_snapshot_id != target_snapshot_id
        ):
            raise ReplayConflict("identity claim is cross-context")
        existing = await self.session.scalar(
            select(AgentIdentityClaimRecord).where(
                AgentIdentityClaimRecord.work_item_id == work_item_id
            )
        )
        if existing is not None:
            actual = (
                existing.run_id,
                existing.task_id,
                existing.source_snapshot_id,
                existing.target_snapshot_id,
                existing.authority_input_id,
                existing.target_input_id,
                existing.work_item_id,
            )
            if actual != expected:
                raise ReplayConflict("identity claim replay changed content")
            return existing
        record = AgentIdentityClaimRecord(
            id=uuid4(),
            run_id=run_id,
            task_id=task_id,
            source_snapshot_id=source_snapshot_id,
            target_snapshot_id=target_snapshot_id,
            authority_input_id=authority_input_id,
            target_input_id=target_input_id,
            work_item_id=work_item_id,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def persist_identity_postings(
        self,
        *,
        run_id: UUID,
        task_id: UUID,
        tenant_id: str,
        snapshot_id: UUID,
        postings: tuple[tuple[UUID, str, str], ...],
    ) -> tuple[AgentIdentityPostingRecord, ...]:
        saved: list[AgentIdentityPostingRecord] = []
        for input_record_id, key_kind, normalized_value in postings:
            input_record = await self.session.get(AgentInputRecord, input_record_id)
            if (
                input_record is None
                or input_record.run_id != run_id
                or input_record.task_id != task_id
                or input_record.tenant_id != tenant_id
                or input_record.snapshot_id != snapshot_id
            ):
                raise LookupError("identity posting input record is not in this run")
            existing = await self.session.scalar(
                select(AgentIdentityPostingRecord).where(
                    AgentIdentityPostingRecord.run_id == run_id,
                    AgentIdentityPostingRecord.input_record_id == input_record_id,
                    AgentIdentityPostingRecord.key_kind == key_kind,
                )
            )
            if existing is not None:
                if existing.normalized_value != normalized_value:
                    raise ReplayConflict("identity posting replay has a different normalized value")
                saved.append(existing)
                continue
            row = AgentIdentityPostingRecord(
                id=uuid4(),
                run_id=run_id,
                task_id=task_id,
                tenant_id=tenant_id,
                snapshot_id=snapshot_id,
                input_record_id=input_record_id,
                entity_kind=input_record.entity_kind,
                key_kind=key_kind,
                normalized_value=normalized_value,
            )
            self.session.add(row)
            await self.session.flush()
            saved.append(row)
        return tuple(saved)

    async def create_or_get_batch(
        self,
        *,
        run_id: UUID,
        task_id: UUID,
        tenant_id: str,
        entity_kind: str,
        input_hash: str,
        work_item_ids: tuple[UUID, ...],
    ) -> AgentModelBatchRecord:
        if not 1 <= len(work_item_ids) <= 50 or len(set(work_item_ids)) != len(work_item_ids):
            raise ValueError("model batches require 1..50 distinct work items")
        run = await self.session.get(AgentRunRecord, run_id)
        if run is None or run.task_id != task_id or run.tenant_id != tenant_id:
            raise ReplayConflict("model batch context does not match its Agent run")
        work_items = tuple(
            await self.session.scalars(
                select(AgentWorkItemRecord).where(AgentWorkItemRecord.id.in_(work_item_ids))
            )
        )
        if len(work_items) != len(work_item_ids) or any(
            item.run_id != run_id
            or item.task_id != task_id
            or item.tenant_id != tenant_id
            or item.entity_kind != entity_kind
            for item in work_items
        ):
            raise ReplayConflict("model batch contains a forged or cross-context work item")
        existing = await self.session.scalar(
            select(AgentModelBatchRecord).where(
                AgentModelBatchRecord.run_id == run_id,
                AgentModelBatchRecord.input_hash == input_hash,
            )
        )
        if existing is not None:
            if (
                existing.task_id != task_id
                or existing.tenant_id != tenant_id
                or existing.entity_kind != entity_kind
                or existing.item_count != len(work_item_ids)
            ):
                raise ReplayConflict("model batch replay changed manifest context")
            item_ids = tuple(
                await self.session.scalars(
                    select(AgentModelBatchItemRecord.work_item_id)
                    .where(AgentModelBatchItemRecord.batch_id == existing.id)
                    .order_by(AgentModelBatchItemRecord.ordinal)
                )
            )
            if item_ids != work_item_ids:
                raise ReplayConflict("model batch replay has different work-item membership")
            return existing
        batch = AgentModelBatchRecord(
            id=uuid4(),
            run_id=run_id,
            task_id=task_id,
            tenant_id=tenant_id,
            entity_kind=entity_kind,
            input_hash=input_hash,
            item_count=len(work_item_ids),
            status="pending",
        )
        self.session.add(batch)
        await self.session.flush()
        self.session.add_all(
            AgentModelBatchItemRecord(batch_id=batch.id, work_item_id=item_id, ordinal=index)
            for index, item_id in enumerate(work_item_ids, start=1)
        )
        await self.session.flush()
        return batch

    async def claim_batch(
        self,
        batch_id: UUID,
        *,
        worker_id: str,
        run_lease_token: UUID,
        lease_seconds: int,
    ) -> AgentModelBatchRecord | None:
        now = datetime.now(UTC)
        batch = await self.session.scalar(
            select(AgentModelBatchRecord)
            .where(AgentModelBatchRecord.id == batch_id)
            .with_for_update(skip_locked=True)
        )
        if batch is None or batch.status == "completed":
            return None
        run = await self.session.get(AgentRunRecord, batch.run_id)
        if not self._analysis_run_is_active(
            run, worker_id=worker_id, lease_token=run_lease_token, now=now
        ):
            return None
        if batch.lease_expires_at is not None and _as_utc(batch.lease_expires_at) >= now:
            return None
        attempt_count = await self.session.scalar(
            select(func.count())
            .select_from(AgentModelAttemptRecord)
            .where(AgentModelAttemptRecord.batch_id == batch_id)
        )
        if int(attempt_count or 0) >= 4:
            return None
        batch.status = "claimed"
        batch.lease_owner = worker_id
        batch.lease_token = uuid4()
        batch.lease_expires_at = now + timedelta(seconds=lease_seconds)
        await self.session.flush()
        return batch

    async def finalize_batch(
        self,
        *,
        batch_id: UUID,
        worker_id: str,
        run_lease_token: UUID,
        lease_token: UUID,
        output_hash: str,
        findings: tuple[AgentFindingPayload, ...],
    ) -> AgentModelBatchRecord:
        batch = await self.session.scalar(
            select(AgentModelBatchRecord)
            .where(AgentModelBatchRecord.id == batch_id)
            .with_for_update()
        )
        if batch is None:
            raise LookupError("model batch not found")
        canonical_output_hash = _hash([finding.model_dump(mode="json") for finding in findings])
        if batch.status == "completed":
            if batch.output_hash == canonical_output_hash:
                return batch
            raise ReplayConflict("completed batch cannot be overwritten")
        now = datetime.now(UTC)
        run = await self.session.get(AgentRunRecord, batch.run_id)
        if not self._analysis_run_is_active(
            run, worker_id=worker_id, lease_token=run_lease_token, now=now
        ):
            raise ReplayConflict("Agent run claim is no longer active")
        if (
            batch.lease_owner != worker_id
            or batch.lease_token != lease_token
            or batch.lease_expires_at is None
            or _as_utc(batch.lease_expires_at) < now
        ):
            raise ReplayConflict("model batch claim is no longer active")
        membership = tuple(
            await self.session.scalars(
                select(AgentModelBatchItemRecord.work_item_id)
                .where(AgentModelBatchItemRecord.batch_id == batch_id)
                .order_by(AgentModelBatchItemRecord.ordinal)
            )
        )
        finding_ids = tuple(finding.work_item_id for finding in findings)
        if len(set(finding_ids)) != len(finding_ids):
            raise ReplayConflict("model result contains duplicate work-item findings")
        if set(finding_ids) != set(membership) or len(finding_ids) != len(membership):
            raise ReplayConflict("model result must contain exactly one finding per batch item")
        for finding in findings:
            for solution in finding.solutions:
                if any(
                    dependency_id not in membership or dependency_id == finding.work_item_id
                    for dependency_id in solution.dependency_finding_ids
                ):
                    raise ReplayConflict(
                        "finding dependency is forged, cross-run, or self-referential"
                    )
        attempt_number = (
            int(
                (
                    await self.session.scalar(
                        select(func.count())
                        .select_from(AgentModelAttemptRecord)
                        .where(AgentModelAttemptRecord.batch_id == batch_id)
                    )
                )
                or 0
            )
            + 1
        )
        attempt = AgentModelAttemptRecord(
            id=uuid4(), batch_id=batch_id, attempt_number=attempt_number, status="succeeded"
        )
        self.session.add(attempt)
        saved_by_work_item: dict[UUID, AgentFindingRecord] = {}
        for finding in findings:
            content_hash = _hash(finding.model_dump(mode="json"))
            duplicate = await self.session.scalar(
                select(AgentFindingRecord).where(
                    AgentFindingRecord.work_item_id == finding.work_item_id
                )
            )
            if duplicate is not None:
                if duplicate.content_hash != content_hash:
                    raise ReplayConflict("work item already has a different finding")
                saved_by_work_item[finding.work_item_id] = duplicate
                continue
            saved = AgentFindingRecord(
                id=uuid4(),
                run_id=batch.run_id,
                task_id=batch.task_id,
                work_item_id=finding.work_item_id,
                batch_id=batch.id,
                kind=finding.kind,
                category_zh=finding.category_zh,
                analysis_zh=finding.analysis_zh,
                evidence_refs=list(finding.evidence_refs),
                content_hash=content_hash,
            )
            self.session.add(saved)
            saved_by_work_item[finding.work_item_id] = saved
            self.session.add_all(
                AgentFindingSolutionRecord(
                    id=uuid4(),
                    finding_id=saved.id,
                    ordinal=ordinal,
                    operation=solution.operation,
                    risk=solution.risk,
                    solution_zh=solution.solution_zh,
                    recommended=solution.recommended,
                )
                for ordinal, solution in enumerate(finding.solutions, start=1)
            )
        await self.session.flush()
        dependency_pairs = {
            (finding.work_item_id, dependency_id)
            for finding in findings
            for solution in finding.solutions
            for dependency_id in solution.dependency_finding_ids
        }
        self.session.add_all(
            AgentFindingDependencyRecord(
                finding_id=saved_by_work_item[work_item_id].id,
                depends_on_finding_id=saved_by_work_item[dependency_id].id,
            )
            for work_item_id, dependency_id in dependency_pairs
        )
        batch.status = "completed"
        batch.output_hash = canonical_output_hash
        batch.lease_owner = None
        batch.lease_token = None
        batch.lease_expires_at = None
        await self.session.flush()
        return batch

    async def append_failed_attempt(
        self,
        *,
        batch_id: UUID,
        worker_id: str,
        run_lease_token: UUID,
        lease_token: UUID,
        provider: str,
        model: str,
        skill_name: str,
        skill_version: str,
        prompt_version: str,
        safe_error_code: str,
        gateway_request_id: str | None = None,
        usage: dict[str, int] | None = None,
    ) -> AgentModelAttemptRecord:
        batch = await self.session.scalar(
            select(AgentModelBatchRecord)
            .where(AgentModelBatchRecord.id == batch_id)
            .with_for_update()
        )
        if batch is None:
            raise LookupError("model batch not found")
        now = datetime.now(UTC)
        run = await self.session.get(AgentRunRecord, batch.run_id)
        if not self._analysis_run_is_active(
            run, worker_id=worker_id, lease_token=run_lease_token, now=now
        ):
            raise ReplayConflict("Agent run claim is no longer active")
        if (
            batch.status != "claimed"
            or batch.lease_owner != worker_id
            or batch.lease_token != lease_token
            or batch.lease_expires_at is None
            or _as_utc(batch.lease_expires_at) < now
        ):
            raise ReplayConflict("model batch claim is no longer active")
        count = int(
            (
                await self.session.scalar(
                    select(func.count())
                    .select_from(AgentModelAttemptRecord)
                    .where(AgentModelAttemptRecord.batch_id == batch_id)
                )
            )
            or 0
        )
        if count >= 4:
            raise ReplayConflict("model batch exhausted four attempts")
        attempt = AgentModelAttemptRecord(
            id=uuid4(),
            batch_id=batch_id,
            attempt_number=count + 1,
            status="failed",
            provider=provider,
            model=model,
            skill_name=skill_name,
            skill_version=skill_version,
            prompt_version=prompt_version,
            gateway_request_id=gateway_request_id,
            usage=dict(usage or {}),
            safe_error_code=safe_error_code,
        )
        self.session.add(attempt)
        batch.status = "blocked" if count + 1 == 4 else "pending"
        batch.lease_owner = None
        batch.lease_token = None
        batch.lease_expires_at = None
        await self.session.flush()
        return attempt

    async def persist_dependency(
        self, *, finding_id: UUID, depends_on_finding_id: UUID
    ) -> AgentFindingDependencyRecord:
        if finding_id == depends_on_finding_id:
            raise ReplayConflict("finding cannot depend on itself")
        finding = await self.session.get(AgentFindingRecord, finding_id)
        dependency = await self.session.get(AgentFindingRecord, depends_on_finding_id)
        if finding is None or dependency is None or finding.run_id != dependency.run_id:
            raise ReplayConflict("finding dependency must stay inside one Agent run")
        existing = await self.session.get(
            AgentFindingDependencyRecord, (finding_id, depends_on_finding_id)
        )
        if existing is not None:
            return existing
        record = AgentFindingDependencyRecord(
            finding_id=finding_id, depends_on_finding_id=depends_on_finding_id
        )
        self.session.add(record)
        await self.session.flush()
        return record

    @staticmethod
    def _analysis_run_is_active(
        run: AgentRunRecord | None,
        *,
        worker_id: str,
        lease_token: UUID,
        now: datetime,
    ) -> bool:
        return bool(
            run is not None
            and run.workflow_version in {"new-agent-v1", "agent-graph-v1"}
            and run.phase == AgentPhase.ANALYZE_BATCHES.value
            and run.status == AgentRunStatus.RUNNING.value
            and run_claim_is_active(run, worker_id=worker_id, lease_token=lease_token, now=now)
        )

    async def _validate_input_context(self, record: AgentContractRecord) -> None:
        run = await self.session.get(AgentRunRecord, record.run_id)
        task = await self.session.get(ReconciliationTask, record.task_id)
        snapshot = await self.session.get(Snapshot, record.snapshot_id)
        if run is None or task is None or run.task_id != record.task_id:
            raise ReplayConflict("input task does not match its Agent run")
        if run.tenant_id != record.tenant_id or task.tenant_id != record.tenant_id:
            raise ReplayConflict("input tenant does not match its Agent run")
        if snapshot is None or snapshot.task_id != record.task_id:
            raise ReplayConflict("input snapshot does not belong to its task")
        if snapshot.source_role != record.source_role.value:
            raise ReplayConflict("input snapshot source role does not match the record")

    async def _validate_run_context(
        self, run_id: UUID, task_id: UUID, tenant_id: str
    ) -> AgentRunRecord:
        run = await self.session.get(AgentRunRecord, run_id)
        task = await self.session.get(ReconciliationTask, task_id)
        if (
            run is None
            or task is None
            or run.task_id != task_id
            or run.tenant_id != tenant_id
            or task.tenant_id != tenant_id
            or run.workflow_version not in {"new-agent-v1", "agent-graph-v1"}
        ):
            raise ReplayConflict("Agent run context does not match task and tenant")
        return run


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
