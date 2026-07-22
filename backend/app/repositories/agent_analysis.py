import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.repository import run_claim_is_active
from app.models.agent_analysis import (
    AgentFindingRecord,
    AgentFindingSolutionRecord,
    AgentIdentityPostingRecord,
    AgentInputRecord,
    AgentModelAttemptRecord,
    AgentModelBatchItemRecord,
    AgentModelBatchRecord,
    AgentWorkItemRecord,
)
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot
from app.schemas.agent_ingestion import AgentContractRecord
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
        if not run_claim_is_active(run, worker_id=worker_id, lease_token=run_lease_token, now=now):
            return None
        if batch.lease_expires_at is not None and batch.lease_expires_at >= now:
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
        if batch.status == "completed":
            if batch.output_hash == output_hash:
                return batch
            raise ReplayConflict("completed batch cannot be overwritten")
        now = datetime.now(UTC)
        run = await self.session.get(AgentRunRecord, batch.run_id)
        if not run_claim_is_active(run, worker_id=worker_id, lease_token=run_lease_token, now=now):
            raise ReplayConflict("Agent run claim is no longer active")
        if (
            batch.lease_owner != worker_id
            or batch.lease_token != lease_token
            or batch.lease_expires_at is None
            or batch.lease_expires_at < now
        ):
            raise ReplayConflict("model batch claim is no longer active")
        allowed = set(
            await self.session.scalars(
                select(AgentModelBatchItemRecord.work_item_id).where(
                    AgentModelBatchItemRecord.batch_id == batch_id
                )
            )
        )
        if any(finding.work_item_id not in allowed for finding in findings):
            raise ReplayConflict("finding does not belong to model batch")
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
            self.session.add_all(
                AgentFindingSolutionRecord(
                    id=uuid4(),
                    finding_id=saved.id,
                    ordinal=ordinal,
                    operation=solution.operation,
                    risk=solution.risk,
                    solution_zh=solution.solution_zh,
                )
                for ordinal, solution in enumerate(finding.solutions, start=1)
            )
        batch.status = "completed"
        batch.output_hash = output_hash
        batch.lease_owner = None
        batch.lease_token = None
        batch.lease_expires_at = None
        await self.session.flush()
        return batch

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


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
